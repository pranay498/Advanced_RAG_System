
-- =========================================
-- 1. DOCUMENTS TABLE
-- =========================================

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY,

    tenant_id TEXT NOT NULL,
    title TEXT NOT NULL,
    owner_sub TEXT NOT NULL,

    allowed_groups TEXT[] NOT NULL,

    s3_key TEXT NOT NULL UNIQUE,

    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (
            status IN (
                'pending',
                'processing',
                'ready',
                'failed',
                'retired'
            )
        ),

    current_version INTEGER NOT NULL DEFAULT 1
        CHECK (current_version >= 1),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =========================================
-- 2. DOCUMENTS INDEX
-- =========================================

CREATE INDEX IF NOT EXISTS documents_acl_idx
ON documents (tenant_id, status);


-- =========================================
-- 3. FEEDBACK TABLE
-- =========================================

CREATE TABLE IF NOT EXISTS feedback (
    id BIGSERIAL PRIMARY KEY,

    tenant_id TEXT NOT NULL,
    actor TEXT NOT NULL,

    rating INTEGER NOT NULL
        CHECK (rating BETWEEN -1 AND 1),

    note TEXT NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- =========================================
-- 4. AUDIT EVENTS TABLE
-- =========================================

CREATE TABLE IF NOT EXISTS audit_events (
    id BIGSERIAL PRIMARY KEY,

    actor TEXT NOT NULL,
    tenant_id TEXT NOT NULL,

    action TEXT NOT NULL,
    detail JSONB NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- =========================================
-- 5. AUDIT EVENTS INDEX
-- =========================================

CREATE INDEX IF NOT EXISTS audit_tenant_time
ON audit_events (tenant_id, created_at DESC);


-- =========================================
-- 6. APPROVALS TABLE
-- =========================================

CREATE TABLE IF NOT EXISTS approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id TEXT NOT NULL,

    requester TEXT NOT NULL,
    question TEXT NOT NULL,

    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (
            status IN (
                'pending',
                'approved',
                'rejected'
            )
        ),

    reviewer TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_at TIMESTAMPTZ
);


-- =========================================
-- 7. APPROVALS INDEX
-- =========================================

CREATE INDEX IF NOT EXISTS approvals_tenant_idx
ON approvals (tenant_id, status);


-- =========================================
-- 8. CONVERSATIONS TABLE
-- =========================================

CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY,

    tenant_id TEXT NOT NULL,
    owner_sub TEXT NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- =========================================
-- 9. CONVERSATIONS INDEX
-- =========================================

CREATE INDEX IF NOT EXISTS conversations_tenant_owner_idx
ON conversations (tenant_id, owner_sub, created_at DESC);


-- =========================================
-- 10. CHAT MESSAGES TABLE
-- =========================================

-- Persistent short-term conversation memory
-- Stored in PostgreSQL / Aurora.

CREATE TABLE IF NOT EXISTS chat_messages (
    id BIGSERIAL PRIMARY KEY,

    tenant_id TEXT NOT NULL,
    conversation_id UUID NOT NULL
        REFERENCES conversations(id) ON DELETE CASCADE,

    role TEXT NOT NULL
        CHECK (role IN ('user', 'assistant')),

    content TEXT NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- =========================================
-- 11. CHAT MESSAGES INDEX
-- =========================================

CREATE INDEX IF NOT EXISTS chat_messages_session_idx
ON chat_messages (tenant_id, conversation_id, id DESC);


-- =========================================
-- 12. DOCUMENT CHUNKS
-- =========================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS document_chunks (
    chunk_id TEXT PRIMARY KEY,

    document_id UUID NOT NULL
        REFERENCES documents(id) ON DELETE CASCADE,

    tenant_id TEXT NOT NULL,

    version INTEGER NOT NULL,

    chunk_index INTEGER NOT NULL,

    title TEXT NOT NULL,
    page INTEGER NOT NULL,
    text TEXT NOT NULL,

    owner_sub TEXT NOT NULL,
    allowed_groups TEXT[] NOT NULL DEFAULT '{}',

    embedding VECTOR(1024) NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (document_id, version, chunk_index)
);


-- =========================================
-- 13. DOCUMENT CHUNKS INDEXES
-- =========================================

CREATE INDEX IF NOT EXISTS chunks_tenant_idx
ON document_chunks (tenant_id);

CREATE INDEX IF NOT EXISTS chunks_document_idx
ON document_chunks (document_id, version);

CREATE INDEX IF NOT EXISTS chunks_embedding_idx
ON document_chunks
USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS document_chunks_bm25_idx
ON document_chunks
USING paradedb (chunk_id, title, text)
WITH (key_field = 'chunk_id');