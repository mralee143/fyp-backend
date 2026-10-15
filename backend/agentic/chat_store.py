"""
Persistence for the agent chatbot (`agent_chat_sessions` / `agent_chat_messages`).

Uses raw SQL through the Prisma client for the same reason as
services/scan_store.py: the generated Python client can't be regenerated in
this environment. ``ensure_chat_tables`` creates the tables on startup so no
manual migration step is needed.
"""

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Roles persisted in agent_chat_messages. "tool" rows carry the raw payload of one
# tool result so the UI can render it as an inspectable card.
ROLES = ("user", "assistant", "tool")

_DDL = (
    """
    CREATE TABLE IF NOT EXISTS agent_chat_sessions (
        id           SERIAL PRIMARY KEY,
        user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title        TEXT NOT NULL DEFAULT 'New chat',
        video_path   TEXT,
        video_name   TEXT,
        video_url    TEXT,
        last_scan_id INTEGER,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS agent_chat_sessions_user_id_idx ON agent_chat_sessions (user_id)",
    """
    CREATE TABLE IF NOT EXISTS agent_chat_messages (
        id          SERIAL PRIMARY KEY,
        session_id  INTEGER NOT NULL REFERENCES agent_chat_sessions(id) ON DELETE CASCADE,
        role        TEXT NOT NULL,
        content     TEXT NOT NULL DEFAULT '',
        tool_name   TEXT,
        tool_payload JSONB,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS agent_chat_messages_session_id_idx ON agent_chat_messages (session_id)",
)

# Repairs for databases whose tables were created before these columns and
# constraints existed. `CREATE TABLE IF NOT EXISTS` above is a no-op once the
# table is present -- and `prisma db push` creates both tables from
# schema.prisma, where AgentChatMessage carries a plain `sessionId Int` with no
# relation. Whichever ran first wins, so the DDL's own FK may never have been
# applied. These statements are idempotent and run on every startup.
_MIGRATIONS = (
    # Links a session to the streamed analysis job queued for its video.
    # Without it the job handle is returned to the browser and stored nowhere,
    # so navigating away from a chat loses all track of a run in flight.
    "ALTER TABLE agent_chat_sessions ADD COLUMN IF NOT EXISTS job_id TEXT",
    # Messages whose session is already gone. They are unreachable through any
    # endpoint (every read is scoped by session), so they are pure residue --
    # and the FK below cannot be created while they exist.
    "DELETE FROM agent_chat_messages m WHERE NOT EXISTS "
    "(SELECT 1 FROM agent_chat_sessions s WHERE s.id = m.session_id)",
)

# Added separately: adding a constraint is not `IF NOT EXISTS`-able before
# PostgreSQL 17, so this checks the catalogue first.
_FK_REPAIR = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'agent_chat_messages_session_id_fkey'
          AND conrelid = 'agent_chat_messages'::regclass
    ) THEN
        ALTER TABLE agent_chat_messages
            ADD CONSTRAINT agent_chat_messages_session_id_fkey
            FOREIGN KEY (session_id) REFERENCES agent_chat_sessions(id)
            ON DELETE CASCADE;
    END IF;
END $$
"""


async def ensure_chat_tables(prisma: Any) -> None:
    """Create the chat tables if they don't exist. Safe to call every startup."""
    for statement in _DDL:
        await prisma.execute_raw(statement)
    for statement in _MIGRATIONS:
        await prisma.execute_raw(statement)
    # Never fatal: a missing cascade degrades cleanup, it does not break chat.
    try:
        await prisma.execute_raw(_FK_REPAIR)
    except Exception as exc:  # noqa: BLE001 - diagnostic only
        logger.warning("Could not add agent_chat_messages FK: %s", exc)


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------


async def create_session(prisma: Any, user_id: int, title: str = "New chat") -> dict:
    """Create a chat session and return it."""
    rows = await prisma.query_raw(
        "INSERT INTO agent_chat_sessions (user_id, title) VALUES ($1, $2) "
        "RETURNING id, title, video_name, video_url, last_scan_id, job_id, "
        "created_at, updated_at",
        user_id,
        title,
    )
    return rows[0]


async def list_sessions(prisma: Any, user_id: int, limit: int = 50) -> list[dict]:
    """The user's sessions, newest activity first."""
    return await prisma.query_raw(
        "SELECT s.id, s.title, s.video_name, s.video_url, s.last_scan_id, s.job_id, "
        "s.created_at, s.updated_at, "
        "(SELECT COUNT(*)::int FROM agent_chat_messages m WHERE m.session_id = s.id) AS message_count "
        "FROM agent_chat_sessions s WHERE s.user_id = $1 "
        "ORDER BY s.updated_at DESC LIMIT $2",
        user_id,
        limit,
    )


async def get_session(prisma: Any, user_id: int, session_id: int) -> Optional[dict]:
    """Fetch one session scoped to its owner (None when not found)."""
    rows = await prisma.query_raw(
        "SELECT id, user_id, title, video_path, video_name, video_url, "
        "last_scan_id, job_id, created_at, updated_at "
        "FROM agent_chat_sessions WHERE id = $1 AND user_id = $2",
        session_id,
        user_id,
    )
    return rows[0] if rows else None


async def delete_session(prisma: Any, user_id: int, session_id: int) -> bool:
    """Delete a session (messages cascade). True when a row was removed."""
    deleted = await prisma.execute_raw(
        "DELETE FROM agent_chat_sessions WHERE id = $1 AND user_id = $2",
        session_id,
        user_id,
    )
    return bool(deleted)


async def set_session_video(
    prisma: Any, session_id: int, path: str, name: str, url: str
) -> None:
    """Attach an uploaded video to the session (replaces any previous one).

    `last_scan_id` and `job_id` describe the video being replaced, so both are
    cleared here. Leaving them would let the chat answer questions about the
    OLD footage while showing the new file, and would leave the UI watching a
    job belonging to a video that is no longer attached.
    """
    await prisma.execute_raw(
        "UPDATE agent_chat_sessions SET video_path = $1, video_name = $2, video_url = $3, "
        "last_scan_id = NULL, job_id = NULL, updated_at = NOW() WHERE id = $4",
        path,
        name,
        url,
        session_id,
    )


async def set_session_job(prisma: Any, session_id: int, job_id: Optional[str]) -> None:
    """Remember the analysis job queued for this session's current video.

    This is what lets a chat reopened in a new page load find the run still in
    flight, instead of losing it with the browser state that queued it.
    """
    await prisma.execute_raw(
        "UPDATE agent_chat_sessions SET job_id = $1, updated_at = NOW() WHERE id = $2",
        job_id,
        session_id,
    )


async def set_session_scan(prisma: Any, session_id: int, scan_id: int) -> None:
    """Remember the most recent scan produced inside this session."""
    await prisma.execute_raw(
        "UPDATE agent_chat_sessions SET last_scan_id = $1, updated_at = NOW() WHERE id = $2",
        scan_id,
        session_id,
    )


async def set_session_title(prisma: Any, session_id: int, title: str) -> None:
    """Rename a session (used to auto-title from the first user message)."""
    await prisma.execute_raw(
        "UPDATE agent_chat_sessions SET title = $1, updated_at = NOW() WHERE id = $2",
        title[:120],
        session_id,
    )


async def touch_session(prisma: Any, session_id: int) -> None:
    """Bump ``updated_at`` so the session sorts to the top of the list."""
    await prisma.execute_raw(
        "UPDATE agent_chat_sessions SET updated_at = NOW() WHERE id = $1", session_id
    )


# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------


async def add_message(
    prisma: Any,
    session_id: int,
    role: str,
    content: str = "",
    tool_name: Optional[str] = None,
    tool_payload: Optional[dict] = None,
) -> dict:
    """Append one message to a session and return the stored row."""
    if role not in ROLES:
        raise ValueError(f"Unknown chat role: {role}")
    rows = await prisma.query_raw(
        "INSERT INTO agent_chat_messages (session_id, role, content, tool_name, tool_payload) "
        "VALUES ($1, $2, $3, $4, $5::jsonb) "
        "RETURNING id, role, content, tool_name, tool_payload, created_at",
        session_id,
        role,
        content,
        tool_name,
        json.dumps(tool_payload) if tool_payload is not None else None,
    )
    row = rows[0]
    return _decode_payload(row)


async def get_messages(prisma: Any, session_id: int, limit: int = 200) -> list[dict]:
    """All messages in a session, oldest first."""
    rows = await prisma.query_raw(
        "SELECT id, role, content, tool_name, tool_payload, created_at "
        "FROM agent_chat_messages WHERE session_id = $1 ORDER BY id ASC LIMIT $2",
        session_id,
        limit,
    )
    return [_decode_payload(row) for row in rows]


def _decode_payload(row: dict) -> dict:
    """jsonb may come back as a string — hand the API a real object."""
    payload = row.get("tool_payload")
    if isinstance(payload, str):
        try:
            row["tool_payload"] = json.loads(payload)
        except json.JSONDecodeError:
            row["tool_payload"] = None
    return row
