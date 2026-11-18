
from app.observability import trace
from app.config import settings
from app.db import connection

import logging
import time
from functools import lru_cache

from pinecone import Pinecone, ServerlessSpec


log = logging.getLogger(__name__)

RRF_K = 60


# 1. PINECONE INITIALIZATION


@lru_cache(maxsize=1)
def index():
    s = settings()

    if not s.pinecone_api_key:
        raise RuntimeError("PINECONE_API_KEY is missing from .env")

    if not s.pinecone_index_name:
        raise RuntimeError("PINECONE_INDEX_NAME is missing from .env")

    pc = Pinecone(api_key=s.pinecone_api_key)
    name = s.pinecone_index_name

    try:
        info = pc.describe_index(name)

    except Exception as exc:
        if getattr(exc, 'status', None) != 404:
            raise

        log.info(
            'Creating Pinecone index %s (dimension=%d, metric=cosine)',
            name,
            s.embed_dim
        )

        try:
            pc.create_index(
                name=name,
                dimension=s.embed_dim,
                metric='cosine',
                spec=ServerlessSpec(
                    cloud=s.pinecone_cloud,
                    region=s.pinecone_region
                )
            )

        except Exception as create_exc:
            if getattr(create_exc, 'status', None) != 409:
                raise

        info = pc.describe_index(name)

    if int(info.dimension) != s.embed_dim:
        raise ValueError(
            f'Pinecone dimension={info.dimension}, '
            f'expected {s.embed_dim}'
        )

    if str(info.metric).lower() != 'cosine':
        raise ValueError(
            f'Pinecone must use cosine metric, got {info.metric}'
        )

    deadline = time.monotonic() + s.pinecone_ready_timeout

    while not pc.describe_index(name).status['ready']:

        if time.monotonic() >= deadline:
            raise TimeoutError(
                f'Pinecone index {name} was not ready within '
                f'{s.pinecone_ready_timeout}s'
            )

        time.sleep(3)

    log.info('Pinecone index %s ready', name)

    return pc.Index(name)



# 2. TENANT NAMESPACE


def namespace(tenant):
    return 'tenant-' + str(tenant)


# =========================================
# 3. VECTOR CONVERSION
# =========================================

def vector_literal(values):
    return '[' + ','.join(
        str(float(value)) for value in values
    ) + ']'


@trace("upsert")
def upsert(tenant, records):

    # Store vectors in PostgreSQL first.
    with connection() as conn:

        for record in records:

            metadata = record['metadata']

            conn.execute(
                """
                INSERT INTO 
                document_chunks,
                chunk_id,
                document_id, 
                tenant_id,version,
                chunk_index,
                title,
                page,
                text,
                owner_sub,
                allowed_groups,
                embedding
                VALUES (%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)
                ON CONFLICT (chunk_id)

                DO UPDATE SET
                    title = EXCLUDED.title,
                    page = EXCLUDED.page,
                    text = EXCLUDED.text,
                    owner_sub = EXCLUDED.owner_sub,
                    allowed_groups = EXCLUDED.allowed_groups,
                    embedding = EXCLUDED.embedding
                """,
                (
                    record['id'],
                    metadata['document_id'],
                    tenant,
                    metadata['version'],
                    metadata['chunk_index'],
                    metadata.get('title', 'Document'),
                    metadata.get('page', 1),
                    metadata.get('text', ''),
                    metadata['owner_sub'],
                    metadata.get('allowed_groups', []),
                    vector_literal(record['values'])
                )
            )

    result = index().upsert(
        vectors=records,
        namespace=namespace(tenant)
    )

    return result


# 5. PINECONE DENSE SEARCH


def pinecone_search(tenant, vector, groups, owner, limit):

    acl = {
        '$or': [
            {'allowed_groups': {'$in': list(groups)}},
            {'owner_sub': {'$eq': owner}}
        ]
    }

    result = index().query(
        vector=vector,
        top_k=limit,
        include_metadata=True,
        namespace=namespace(tenant),
        filter=acl
    )

    hits = []

    for match in result.matches:

        hits.append({
            'id': match.id,
            'score': match.score,
            'metadata': match.metadata or {}
        })

    return hits

# 6. POSTGRESQL PGVECTOR DENSE SEARCH

def pgvector_search(tenant, vector, groups, owner, limit):

    hits = []
    query_vector = vector_literal(vector)

    with connection() as conn:

        rows = conn.execute(
            """
            SELECT chunk_id,
             document_id, 
             title, 
             page,
             text,
             owner_sub,
             allowed_groups, 
             1 - (embedding <=> %s::vector) AS score
            FROM document_chunks
            WHERE tenant_id = %s
            AND(owner_sub = %s OR allowed_groups && %s::text[])
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (
                query_vector,
                tenant,
                owner,
                list(groups),
                query_vector,
                limit
            )
        ).fetchall()

        for row in rows:

            hits.append({
                'id': row['chunk_id'],
                'score': float(row['score']),
                'metadata': {
                    'document_id': str(row['document_id']),
                    'title': row['title'],
                    'page': row['page'],
                    'text': row['text'],
                    'owner_sub': row['owner_sub'],
                    'allowed_groups': row['allowed_groups']
                }
            })

    return hits


# 7.BM25 KEYWORD SEARCH


def bm25_search(tenant, query, groups, owner, limit):

    hits = []

    with connection() as conn:

        rows = conn.execute(
            """
            SELECT
                chunk_id,
                document_id,
                title,
                page,
                text,
                owner_sub,
                allowed_groups,
                pdb.score(chunk_id) AS score
            FROM document_chunks
            WHERE tenant_id = %s
              AND (
                  owner_sub = %s
                  OR allowed_groups && %s::text[]
              )
              AND (
                  title ||| %s
                  OR text ||| %s
              )
            ORDER BY pdb.score(chunk_id) DESC
            LIMIT %s
            """,
            (
                tenant,
                owner,
                list(groups),
                query,
                query,
                limit
            )
        ).fetchall()

        for row in rows:

            hits.append({
                'id': row['chunk_id'],
                'score': float(row['score']),
                'metadata': {
                    'document_id': str(row['document_id']),
                    'title': row['title'],
                    'page': row['page'],
                    'text': row['text'],
                    'owner_sub': row['owner_sub'],
                    'allowed_groups': row['allowed_groups']
                }
            })

    return hits


# =========================================
# 8. RECIPROCAL RANK FUSION
# =========================================

def reciprocal_rank_fusion(result_lists, limit):

    fused = {}

    for results in result_lists:

        seen = set()

        for rank, hit in enumerate(results, start=1):

            chunk_id = hit['id']

            if chunk_id in seen:
                continue

            seen.add(chunk_id)

            if chunk_id not in fused:

                fused[chunk_id] = {
                    'id': chunk_id,
                    'metadata': hit['metadata'],
                    'score': 0.0
                }

            fused[chunk_id]['score'] += (
                1.0 / (RRF_K + rank)
            )

    ranked_results = sorted(
        fused.values(),
        key=lambda item: item['score'],
        reverse=True
    )

    return ranked_results[:limit]


# =========================================
# 9. HYBRID SEARCH
# =========================================

@trace("search")
def search(tenant, query, vector, groups, owner, limit=5):

    candidate_limit = max(limit * 4, 20)

    pinecone_hits = pinecone_search(
        tenant,
        vector,
        groups,
        owner,
        candidate_limit
    )

    pgvector_hits = pgvector_search(
        tenant,
        vector,
        groups,
        owner,
        candidate_limit
    )

    bm25_hits = bm25_search(
        tenant,
        query,
        groups,
        owner,
        candidate_limit
    )

    results = reciprocal_rank_fusion(
        [
            pinecone_hits,
            pgvector_hits,
            bm25_hits
        ],
        limit
    )

    return results

# 10. DELETE DOCUMENT CHUNKS

def delete_document(tenant, ids):

    if not ids:
        return

    index().delete(
        ids=ids,
        namespace=namespace(tenant)
    )


    with connection() as conn:

        conn.execute(
            """
            DELETE FROM document_chunks
            WHERE tenant_id = %s
              AND chunk_id = ANY(%s)
            """,
            (tenant, ids)
        )



# 11. CLEAR TENANT INDEX


@trace("clear_index")
def clear_index(tenant):


    index().delete(
        delete_all=True,
        namespace=namespace(tenant)
    )
    with connection() as conn:

        conn.execute(
            """
            DELETE FROM document_chunks
            WHERE tenant_id = %s
            """,
            (tenant,)
        )
