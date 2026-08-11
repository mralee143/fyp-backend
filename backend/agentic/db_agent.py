"""
Database insights agent — the second agent behind the chatbot.

The orchestrator (agentic/chat_agent.py) delegates any question about *past
detections* to this agent. It:

  1. Asks Qwen to write a read-only SQL SELECT against a small, safe view of
     the user's data (scans, per-frame object detections, incident events).
  2. Validates the SQL — the model never touches real tables directly.
  3. Executes it and asks Qwen to turn the rows into a short insight.

Safety model: the generated SELECT runs on top of a fixed CTE prelude whose
every branch is filtered by ``user_id = $1``. The model can only name the CTEs
(``scans``, ``frames``, ``events``); anything else is rejected before execution,
so one user can never read another's footage data.
"""

import datetime
import decimal
import json
import logging
import re
from typing import Any, Optional

from agentic import prompts
from agentic.qwen_chat import QwenChatError, chat

logger = logging.getLogger(__name__)

# Row cap applied to every generated query, and to what we feed back to Qwen.
MAX_ROWS = 200
SUMMARY_ROWS = 40

# The only relations the generated SQL may reference.
ALLOWED_TABLES = {"scans", "frames", "events"}

# Anything that could mutate, escalate or read outside the CTE prelude.
_BANNED = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|"
    r"vacuum|analyze|reindex|call|do|merge|set|begin|commit|rollback|"
    r"pg_sleep|pg_read_file|pg_catalog|information_schema|current_setting|"
    r"detection_scans|agent_chat_sessions|agent_chat_messages|chat_sessions|"
    r"chat_messages|users|images)\b",
    re.I,
)
_SQL_COMMENT = re.compile(r"(--[^\n]*)|(/\*.*?\*/)", re.S)
_FROM_JOIN = re.compile(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.I)
_CODE_FENCE = re.compile(r"```(?:sql)?\s*(.*?)\s*```", re.S)
_LIMIT = re.compile(r"\blimit\s+\d+\s*$", re.I)

# Scoped, read-only view of the caller's data. $1 is always the user id.
CTE_PRELUDE = """
WITH scans AS (
    SELECT id AS scan_id, filename, model, violence_detected, summary,
           labels, created_at
    FROM detection_scans
    WHERE user_id = $1
),
frames AS (
    SELECT s.id AS scan_id, s.filename, s.model, s.created_at,
           (d->>'second')::float        AS second,
           d->>'timestamp'              AS timestamp,
           d->>'label'                  AS label,
           (d->>'score')::float         AS score
    FROM detection_scans s
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE WHEN jsonb_typeof(s.result->'detections') = 'array'
             THEN s.result->'detections' ELSE '[]'::jsonb END
    ) AS d
    WHERE s.user_id = $1
),
events AS (
    SELECT s.id AS scan_id, s.filename, s.model, s.created_at,
           e->>'label'                  AS label,
           e->>'category'               AS category,
           e->>'description'            AS description,
           (e->>'start_time')::float    AS start_time,
           (e->>'end_time')::float      AS end_time,
           (e->>'confidence')::float    AS confidence
    FROM detection_scans s
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE WHEN jsonb_typeof(s.result->'segments') = 'array'
             THEN s.result->'segments' ELSE '[]'::jsonb END
    ) AS e
    WHERE s.user_id = $1
)
"""

# This agent's three prompts, as text under agentic/prompts/db_agent/ — the
# relations it may name, the SQL-writing rules (a template filled in by
# answer_question below), and how to turn the returned rows into an answer.
SCHEMA_DOC = prompts.load("db_agent/schema")
SQL_SYSTEM = prompts.load("db_agent/sql_system")
INSIGHT_SYSTEM = prompts.load("db_agent/insight_system")


class DbAgentError(Exception):
    """Raised when the insights agent cannot answer."""


def jsonable(value: Any) -> Any:
    """Make query_raw output JSON-serialisable (datetimes, Decimals, ...)."""
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def _clean_sql(raw: str) -> str:
    """Strip fences/comments/semicolons from the model's SQL output."""
    text = raw.strip()
    fence = _CODE_FENCE.search(text)
    if fence:
        text = fence.group(1)
    text = _SQL_COMMENT.sub(" ", text)
    text = text.strip().rstrip(";").strip()
    # Collapse whitespace so the validators see a predictable single line.
    return re.sub(r"\s+", " ", text)


def validate_sql(sql: str) -> str:
    """Validate a generated SELECT and return it with a LIMIT enforced.

    Raises:
        DbAgentError: If the statement is not a safe, single, read-only SELECT
            over the whitelisted relations.
    """
    if not sql:
        raise DbAgentError("The model produced no SQL.")
    if ";" in sql:
        raise DbAgentError("Only a single statement is allowed.")
    if not sql.lower().startswith("select"):
        raise DbAgentError("Only SELECT statements are allowed.")

    banned = _BANNED.search(sql)
    if banned:
        raise DbAgentError(f"Disallowed keyword in query: {banned.group(0)}")

    referenced = {name.lower() for name in _FROM_JOIN.findall(sql)}
    unknown = referenced - ALLOWED_TABLES
    if unknown:
        raise DbAgentError(
            f"Query references unavailable relation(s): {', '.join(sorted(unknown))}"
        )
    if not referenced:
        raise DbAgentError("Query must read from scans, frames or events.")

    if not _LIMIT.search(sql):
        sql = f"{sql} LIMIT {MAX_ROWS}"
    return sql


def _context_block(scan_id: Optional[int], video_name: Optional[str]) -> str:
    """Tell the SQL writer which scan 'this video' refers to."""
    if scan_id:
        return (
            f"\nContext: the user is currently discussing scan_id = {scan_id}"
            + (f" (file '{video_name}')" if video_name else "")
            + ". If they say \"this video\"/\"the video\", filter on that scan_id."
        )
    if video_name:
        return (
            f"\nContext: the user is currently discussing the file "
            f"'{video_name}'. If they say \"this video\", filter on that filename."
        )
    return ""


async def answer_question(
    prisma: Any,
    user_id: int,
    question: str,
    scan_id: Optional[int] = None,
    video_name: Optional[str] = None,
) -> dict:
    """Answer a natural-language question about the user's detection data.

    Args:
        prisma: Connected Prisma client (used for raw SQL).
        user_id: Owner whose data the query is scoped to.
        question: The natural-language question.
        scan_id: Scan the conversation is currently about, if any.
        video_name: Filename the conversation is currently about, if any.

    Returns:
        dict with ``question``, ``sql``, ``row_count``, ``rows`` and ``insight``.
        On a recoverable failure the dict carries an ``error`` string and an
        empty row set rather than raising, so the chat can keep going.
    """
    from fastapi.concurrency import run_in_threadpool

    system = SQL_SYSTEM.format(schema=SCHEMA_DOC, max_rows=MAX_ROWS)
    system += _context_block(scan_id, video_name)

    sql = ""
    rows: list[dict] = []
    last_error = ""

    # Two attempts: the second gets the failure message so Qwen can self-correct.
    for attempt in range(2):
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": question},
        ]
        if last_error:
            messages.append({"role": "assistant", "content": sql})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"That query failed with: {last_error}\n"
                        "Rewrite it, obeying the rules. Output only the SQL."
                    ),
                }
            )

        try:
            reply = await run_in_threadpool(
                chat, messages=messages, temperature=0.0, max_tokens=400
            )
        except QwenChatError as e:
            raise DbAgentError(str(e)) from e

        sql = _clean_sql(reply.content)
        try:
            safe_sql = validate_sql(sql)
            raw_rows = await prisma.query_raw(CTE_PRELUDE + safe_sql, user_id)
            rows = [jsonable(row) for row in raw_rows]
            sql = safe_sql
            last_error = ""
            break
        except DbAgentError as e:
            last_error = str(e)
            logger.info("db_agent: rejected SQL (attempt %s): %s", attempt + 1, e)
        except Exception as e:  # SQL executed but Postgres rejected it
            last_error = str(e)[:300]
            logger.info("db_agent: query failed (attempt %s): %s", attempt + 1, last_error)

    if last_error:
        return {
            "question": question,
            "sql": sql,
            "row_count": 0,
            "rows": [],
            "insight": "",
            "error": f"Could not build a valid query for that question ({last_error}).",
        }

    # Turn the rows into an operator-readable insight.
    preview = rows[:SUMMARY_ROWS]
    try:
        summary_reply = await run_in_threadpool(
            chat,
            messages=[
                {"role": "system", "content": INSIGHT_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"Question: {question}\n\nSQL:\n{sql}\n\n"
                        f"Rows ({len(rows)} total, showing {len(preview)}):\n"
                        f"{json.dumps(preview, ensure_ascii=False, default=str)}"
                    ),
                },
            ],
            temperature=0.2,
            max_tokens=500,
        )
        insight = summary_reply.content.strip()
    except QwenChatError as e:
        logger.warning("db_agent: insight generation failed: %s", e)
        insight = f"Query returned {len(rows)} row(s)."

    return {
        "question": question,
        "sql": sql,
        "row_count": len(rows),
        "rows": preview,
        "insight": insight,
    }
