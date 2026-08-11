"""
The orchestrator agent — the chatbot the user actually talks to.

Qwen drives the conversation (agentic/qwen_chat.py) and decides when to reach
for a tool (agentic/agent_tools.py):

    user ──▶ Qwen orchestrator ──┬──▶ analyze_video ──▶ YOLO / OWLv2 / VLM
                                 └──▶ query_detection_database ──▶ DB agent
                                                                   (Qwen → SQL)

Conversation history is replayed as plain user/assistant turns; the live tool
round-trip happens inside a single request. Anything the agent already learned
about the session (attached video, last scan) is injected into the system
prompt instead, so replays stay small and never desynchronise tool ids.
"""

import json
import logging
from typing import Any, Optional

from fastapi.concurrency import run_in_threadpool

from agentic import chat_store, prompts
from agentic.agent_tools import TOOL_EXECUTORS, TOOL_SCHEMAS, AgentContext, execute_tool
from agentic.qwen_chat import QwenChatError, chat, scrub_tool_syntax

logger = logging.getLogger(__name__)

# Names the model may leak as pseudo-calls in its prose; used to clean replies.
TOOL_NAMES = tuple(TOOL_EXECUTORS)

# How many tool rounds one user message may trigger before we force an answer.
MAX_TOOL_ROUNDS = 4
# How many past turns to replay into the prompt.
HISTORY_TURNS = 20

# The orchestrator's instructions and tool policy. Edit the text, not this line:
# agentic/prompts/orchestrator/system.md.
SYSTEM_PROMPT = prompts.load("orchestrator/system")


def _session_state_block(session: dict) -> str:
    """Describe the live session so the model knows what it's working with."""
    lines = ["# Current session"]
    if session.get("video_name"):
        lines.append(f"Attached video: {session['video_name']}")
    else:
        lines.append("Attached video: none — the user has not uploaded one yet.")
    if session.get("last_scan_id"):
        lines.append(
            f"This video HAS ALREADY BEEN ANALYSED (scan_id "
            f"{session['last_scan_id']}) and the findings are in the messages "
            "above. Answer from them. Do not call analyze_video again unless "
            "the user explicitly asks for a re-scan or a different model."
        )
    else:
        lines.append("No analysis has been run in this chat yet.")
    return "\n".join(lines)


def _replay_history(rows: list[dict]) -> list[dict[str, Any]]:
    """Turn stored messages into prompt turns (user/assistant text only)."""
    turns: list[dict[str, Any]] = []
    for row in rows:
        if row["role"] == "user":
            turns.append({"role": "user", "content": row["content"]})
        elif row["role"] == "assistant" and row.get("content"):
            # Scrub on the way in too: messages stored before this was fixed
            # would otherwise teach the model to keep leaking tool syntax.
            cleaned = scrub_tool_syntax(row["content"], TOOL_NAMES)
            if cleaned:
                turns.append({"role": "assistant", "content": cleaned})
    return turns[-HISTORY_TURNS:]


def _as_openai_tool_calls(calls: list[Any]) -> list[dict[str, Any]]:
    """Render ToolCall objects back into OpenAI assistant-message shape."""
    return [
        {
            "id": call.id,
            "type": "function",
            "function": {
                "name": call.name,
                "arguments": json.dumps(call.arguments, ensure_ascii=False),
            },
        }
        for call in calls
    ]


def _title_from(message: str) -> str:
    """Derive a short session title from the user's first message."""
    text = " ".join(message.split())
    return (text[:57] + "...") if len(text) > 60 else (text or "New chat")


async def run_turn(
    prisma: Any,
    user_id: int,
    session: dict,
    user_message: str,
) -> dict:
    """Run one full user turn: think, call tools, answer, persist.

    Args:
        prisma: Connected Prisma client.
        user_id: The authenticated user.
        session: The chat session row (must include id, video_path, video_name).
        user_message: What the user just said.

    Returns:
        dict with ``reply`` (assistant text), ``tool_calls`` (the trace of what
        ran, each with its result payload) and ``scan_id`` when a new scan was
        produced during the turn.

    Raises:
        QwenChatError: If the Qwen backend could not be reached at all.
    """
    session_id = int(session["id"])
    history_rows = await chat_store.get_messages(prisma, session_id)

    # Persist the user's message before doing any slow work, so a failed turn
    # still leaves the conversation intact.
    await chat_store.add_message(prisma, session_id, "user", user_message)
    if not history_rows:
        await chat_store.set_session_title(prisma, session_id, _title_from(user_message))

    ctx = AgentContext(prisma=prisma, user_id=user_id, session=session)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{_session_state_block(session)}"},
        *_replay_history(history_rows),
        {"role": "user", "content": user_message},
    ]

    trace: list[dict[str, Any]] = []
    reply_text = ""

    for round_index in range(MAX_TOOL_ROUNDS):
        # Last round: drop the tools so the model is forced to answer.
        tools = TOOL_SCHEMAS if round_index < MAX_TOOL_ROUNDS - 1 else None
        reply = await run_in_threadpool(
            chat, messages=messages, tools=tools, temperature=0.3, max_tokens=1024
        )

        if not reply.tool_calls:
            reply_text = scrub_tool_syntax(reply.content, TOOL_NAMES)
            break

        messages.append(
            {
                "role": "assistant",
                "content": scrub_tool_syntax(reply.content, TOOL_NAMES),
                "tool_calls": _as_openai_tool_calls(reply.tool_calls),
            }
        )

        for call in reply.tool_calls:
            logger.info("agent: session %s calling %s(%s)", session_id, call.name, call.arguments)
            result = await execute_tool(ctx, call.name, call.arguments)

            await chat_store.add_message(
                prisma,
                session_id,
                "tool",
                content=call.name,
                tool_name=call.name,
                tool_payload={"arguments": call.arguments, "result": result},
            )
            trace.append({"name": call.name, "arguments": call.arguments, "result": result})
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                }
            )
    else:
        # Every round asked for a tool — summarise what we have instead of looping.
        reply_text = scrub_tool_syntax(reply.content, TOOL_NAMES)

    if not reply_text.strip():
        reply_text = (
            "I ran the analysis but couldn't phrase a summary. "
            "The tool results are attached above."
        ) if trace else "I'm not sure how to help with that — could you rephrase?"

    await chat_store.add_message(prisma, session_id, "assistant", reply_text)
    await chat_store.touch_session(prisma, session_id)

    return {
        "reply": reply_text,
        "tool_calls": trace,
        "scan_id": session.get("last_scan_id"),
    }


async def safe_run_turn(
    prisma: Any, user_id: int, session: dict, user_message: str
) -> dict:
    """``run_turn`` with backend failures surfaced as a chat message.

    A dead Qwen endpoint shouldn't 500 the UI — the user gets an explanation
    they can act on, and the exchange stays in their history.
    """
    try:
        return await run_turn(prisma, user_id, session, user_message)
    except QwenChatError as e:
        logger.error("agent: Qwen backend unavailable: %s", e)
        message = (
            "I can't reach the Qwen chat model right now, so I can't answer. "
            f"({e}) Start your Qwen server (e.g. `ollama serve` after "
            "`ollama pull qwen2.5:7b`) or check QWEN_CHAT_BASE_URL, then try again."
        )
        await chat_store.add_message(prisma, int(session["id"]), "assistant", message)
        return {"reply": message, "tool_calls": [], "scan_id": session.get("last_scan_id"), "error": True}
