import uuid
from app.db import connection
from app.config import settings


class ConversationNotFoundError(Exception):
    """Raised when a conversation does not exist."""
    pass


class ConversationAccessDeniedError(Exception):
    """Raised when conversation ownership or tenant verification fails."""
    pass


def get_conversation(conn, conversation_id: uuid.UUID):
    """Retrieve conversation metadata by ID."""
    return conn.execute(
        """SELECT id, tenant_id, owner_sub, created_at, updated_at
           FROM conversations
           WHERE id = %s""",
        (conversation_id,)
    ).fetchone()


def verify_conversation_access(tenant: str, conversation_id: uuid.UUID, owner_sub: str, conn=None):
    """
    Verify that the conversation exists and belongs to the given tenant and owner_sub.
    Handles legacy conversations missing ownership safely by denying unassigned access.
    """
    if not owner_sub:
        raise ConversationAccessDeniedError("Owner identifier is required.")

    def _verify(c):
        conv = get_conversation(c, conversation_id)
        if not conv:
            # Check if this conversation exists in legacy chat_messages without ownership record
            legacy = c.execute(
                "SELECT 1 FROM chat_messages WHERE conversation_id = %s LIMIT 1",
                (conversation_id,)
            ).fetchone()
            if legacy:
                raise ConversationAccessDeniedError("Conversation ownership is missing or unassigned.")
            raise ConversationNotFoundError("Conversation not found.")

        if conv["tenant_id"] != tenant:
            raise ConversationAccessDeniedError("Tenant mismatch.")

        if not conv["owner_sub"] or conv["owner_sub"] != owner_sub:
            raise ConversationAccessDeniedError("Access denied: not conversation owner.")

        return conv

    if conn is not None:
        return _verify(conn)
    with connection() as c:
        return _verify(c)


def ensure_conversation(tenant: str, conversation_id: uuid.UUID, owner_sub: str, conn=None):
    """
    Ensure a conversation exists and is owned by owner_sub in tenant.
    Creates a new conversation if it does not exist.
    If it exists, verifies that tenant and owner_sub match.
    """
    if not owner_sub:
        raise ConversationAccessDeniedError("Owner identifier is required.")

    def _ensure(c):
        conv = get_conversation(c, conversation_id)
        if conv:
            if conv["tenant_id"] != tenant:
                raise ConversationAccessDeniedError("Tenant mismatch.")
            if not conv["owner_sub"] or conv["owner_sub"] != owner_sub:
                raise ConversationAccessDeniedError("Access denied: not conversation owner.")
            return conv

        # Guard against claiming a legacy unassigned conversation
        legacy = c.execute(
            "SELECT 1 FROM chat_messages WHERE conversation_id = %s LIMIT 1",
            (conversation_id,)
        ).fetchone()
        if legacy:
            raise ConversationAccessDeniedError("Conversation ownership is missing or unassigned.")

        c.execute(
            """INSERT INTO conversations (id, tenant_id, owner_sub, created_at, updated_at)
               VALUES (%s, %s, %s, NOW(), NOW())
               ON CONFLICT (id) DO NOTHING""",
            (conversation_id, tenant, owner_sub)
        )

        conv = get_conversation(c, conversation_id)
        if not conv:
            raise RuntimeError("Failed to create conversation record.")

        if conv["tenant_id"] != tenant:
            raise ConversationAccessDeniedError("Tenant mismatch.")
        if not conv["owner_sub"] or conv["owner_sub"] != owner_sub:
            raise ConversationAccessDeniedError("Access denied: not conversation owner.")

        return conv

    if conn is not None:
        return _ensure(conn)
    with connection() as c:
        return _ensure(c)


def history(tenant: str, conversation_id: uuid.UUID, owner_sub: str):
    """
    Fetch recent conversation history, verifying tenant and owner_sub first.
    """
    with connection() as conn:
        verify_conversation_access(tenant, conversation_id, owner_sub, conn=conn)
        rows = conn.execute(
            """SELECT role,content 
            FROM (
                SELECT id,role,content,created_at FROM chat_messages
                WHERE tenant_id=%s AND conversation_id=%s ORDER BY id DESC LIMIT %s
            ) recent ORDER BY id ASC""",
            (tenant, conversation_id, settings().memory_turns * 2)
        ).fetchall()
    return rows


def save_turn(tenant: str, conversation_id: uuid.UUID, question: str, response: str, owner_sub: str):
    """
    Save user question and assistant response, verifying tenant and owner_sub first.
    """
    with connection() as conn:
        verify_conversation_access(tenant, conversation_id, owner_sub, conn=conn)
        conn.execute(
            """INSERT INTO chat_messages(tenant_id,conversation_id,role,content)
               VALUES (%s,%s,'user',%s),(%s,%s,'assistant',%s)""",
            (tenant, conversation_id, question, tenant, conversation_id, response)
        )
        conn.execute(
            "UPDATE conversations SET updated_at = NOW() WHERE id = %s AND tenant_id = %s",
            (conversation_id, tenant)
        )


def list_messages(tenant: str, conversation_id: uuid.UUID, owner_sub: str):
    """
    List messages for a conversation, verifying tenant and owner_sub first.
    """
    with connection() as conn:
        verify_conversation_access(tenant, conversation_id, owner_sub, conn=conn)
        return conn.execute(
            """SELECT role,content,created_at FROM chat_messages
              WHERE tenant_id=%s AND conversation_id=%s ORDER BY id ASC LIMIT 100""",
            (tenant, conversation_id)
        ).fetchall()


def delete_conversation(tenant: str, conversation_id: uuid.UUID, owner_sub: str):
    """
    Delete a conversation and its messages (via cascade), verifying ownership first.
    """
    with connection() as conn:
        verify_conversation_access(tenant, conversation_id, owner_sub, conn=conn)
        conn.execute(
            "DELETE FROM conversations WHERE id = %s AND tenant_id = %s",
            (conversation_id, tenant)
        )
