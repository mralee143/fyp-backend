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
from typing import Any, AsyncIterator, Optional

from agentic import chat_store, prompts
from agentic.agent_tools import TOOL_EXECUTORS, AgentContext, execute_tool, tools_for
from agentic.quick_replies import quick_reply
from agentic.qwen_chat import QwenChatError, chat_stream, scrub_tool_syntax
from config import settings

logger = logging.getLogger(__name__)

# Names the model may leak as pseudo-calls in its prose; used to clean replies.
TOOL_NAMES = tuple(TOOL_EXECUTORS)

# How many tool rounds one user message may trigger before we force an answer.
MAX_TOOL_ROUNDS = 4
# Generation cap per round. This is a *ceiling*, not a target — the model stops
# at its EOS token, so a short answer costs nothing extra. It matters only when
# the model rambles or loops, which on a CPU-bound host is the difference
# between a slow reply and a timeout: MAX_TOOL_ROUNDS rounds at 1024 tokens is
# 4096 tokens of worst-case generation for a single user message.
MAX_REPLY_TOKENS = 384
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


async def stream_turn(
    prisma: Any,
    user_id: int,
    session: dict,
    user_message: str,
) -> AsyncIterator[dict[str, Any]]:
    """Run one full user turn, reporting progress as it happens.

    The same work as :func:`run_turn` — think, call tools, answer, persist — but
    surfaced as events instead of a single return, so the UI can show the reply
    forming rather than a spinner. On local weights a turn is minutes long and
    the first token arrives long before the last.

    Args:
        prisma: Connected Prisma client.
        user_id: The authenticated user.
        session: The chat session row (must include id, video_path, video_name).
        user_message: What the user just said.

    Yields:
        ``{"type": "delta", "text": str}`` as prose is generated;
        ``{"type": "reset"}`` when the text streamed so far turned out to be the
        preamble of a tool call and must be discarded;
        ``{"type": "tool", ...}`` once per tool that actually ran, carrying the
        card the transcript should show;
        ``{"type": "done", "result": {...}}`` exactly once, last, with the
        authoritative reply — the cleaned text, the full trace and any scan id.
        The text in ``done`` is what the transcript should end up holding; the
        deltas are a preview of it, not a substitute.

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

    # "hi" does not need a 5 KB prompt and a round of generation to answer. The
    # exchange is still persisted, so the conversation reads the same as any
    # other — only the wait is gone. Anything unrecognised falls through.
    canned = quick_reply(user_message, session)
    if canned is not None:
        logger.info("agent: session %s answered without the model", session_id)
        await chat_store.add_message(prisma, session_id, "assistant", canned)
        await chat_store.touch_session(prisma, session_id)
        yield {"type": "delta", "text": canned}
        yield {
            "type": "done",
            "result": {
                "reply": canned,
                "tool_calls": [],
                "scan_id": session.get("last_scan_id"),
            },
        }
        return

    ctx = AgentContext(prisma=prisma, user_id=user_id, session=session)
    # The session block is deliberately NOT concatenated onto SYSTEM_PROMPT.
    #
    # The server caches the KV state of a prompt prefix and reuses it whenever
    # the next request starts with the same bytes. SYSTEM_PROMPT plus the tool
    # schemas is ~1.3k tokens of *constant* text, and re-evaluating it on CPU
    # costs about a minute. Splicing the video name and last scan id into that
    # first message made it differ for every conversation — and change again
    # each time a scan finished — so the cache never hit and every turn paid
    # the full cost. Kept as its own message, the expensive prefix stays
    # byte-identical across all chats and only this short block is re-read.
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": _session_state_block(session)},
        *_replay_history(history_rows),
        {"role": "user", "content": user_message},
    ]

    trace: list[dict[str, Any]] = []
    reply_text = ""
    # Every distinct tool call made this turn, by name + arguments.
    #
    # A tool that comes back with an error reads to the model as "that didn't
    # work, try again", and with four rounds available it would re-issue the
    # identical call until they ran out — three "Analysed the video / failed"
    # cards for one question, each one paying the detector's full runtime. The
    # answer never changes, so the second and third call are served from here:
    # the model still gets a tool response for its call id, but nothing is
    # re-run and no duplicate card reaches the transcript.
    seen: dict[str, dict[str, Any]] = {}

    # Only the tools this conversation can actually act on — a chat with no
    # upload is not offered the three that need one, so it neither pays for
    # their schemas nor spends a round being told the video isn't there.
    available_tools = tools_for(session)

    for round_index in range(MAX_TOOL_ROUNDS):
        # Last round: drop the tools so the model is forced to answer.
        tools = available_tools if round_index < MAX_TOOL_ROUNDS - 1 else None

        reply = None
        streamed = False
        async for event in chat_stream(
            messages=messages,
            tools=tools,
            temperature=0.3,
            max_tokens=MAX_REPLY_TOKENS,
        ):
            if event["type"] == "delta":
                streamed = True
                yield {"type": "delta", "text": event["text"]}
            else:
                reply = event["reply"]

        if reply is None:
            raise QwenChatError("Qwen streamed no reply.")

        if not reply.tool_calls:
            reply_text = scrub_tool_syntax(reply.content, TOOL_NAMES)
            break

        # This round was a tool call after all. Whatever the gate let through
        # before that became clear is preamble, not answer — take it back
        # rather than leave half a thought above the tool card.
        if streamed:
            yield {"type": "reset"}

        messages.append(
            {
                "role": "assistant",
                "content": scrub_tool_syntax(reply.content, TOOL_NAMES),
                "tool_calls": _as_openai_tool_calls(reply.tool_calls),
            }
        )

        for call in reply.tool_calls:
            signature = f"{call.name}:{json.dumps(call.arguments, sort_keys=True, default=str)}"
            repeat = seen.get(signature)

            if repeat is not None:
                logger.info(
                    "agent: session %s repeated %s(%s) — serving the first result",
                    session_id,
                    call.name,
                    call.arguments,
                )
                result = {
                    **repeat,
                    "repeated_call": True,
                    "note": (
                        "You already made this exact call in this turn. This is "
                        "the same result — running it again cannot change it. "
                        "Answer the user from what you have."
                    ),
                }
            else:
                logger.info(
                    "agent: session %s calling %s(%s)", session_id, call.name, call.arguments
                )
                yield {"type": "tool_started", "name": call.name}
                result = await execute_tool(ctx, call.name, call.arguments)
                seen[signature] = result

                # Only a call that actually ran earns a card and a trace entry.
                await chat_store.add_message(
                    prisma,
                    session_id,
                    "tool",
                    content=call.name,
                    tool_name=call.name,
                    tool_payload={"arguments": call.arguments, "result": result},
                )
                trace.append(
                    {"name": call.name, "arguments": call.arguments, "result": result}
                )
                # The card is worth showing the moment it exists — detection can
                # take a while, and its frames are the answer to most questions
                # long before the model has finished describing them.
                yield {
                    "type": "tool",
                    "name": call.name,
                    "arguments": call.arguments,
                    "result": result,
                }

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

    yield {
        "type": "done",
        "result": {
            "reply": reply_text,
            "tool_calls": trace,
            "scan_id": session.get("last_scan_id"),
        },
    }


async def safe_stream_turn(
    prisma: Any, user_id: int, session: dict, user_message: str
) -> AsyncIterator[dict[str, Any]]:
    """``stream_turn`` with backend failures delivered as a normal reply.

    The streaming counterpart of :func:`safe_run_turn`: a dead Qwen endpoint
    ends the stream with an explanation the user can act on, not a broken
    connection halfway through a sentence.
    """
    try:
        async for event in stream_turn(prisma, user_id, session, user_message):
            yield event
    except QwenChatError as e:
        logger.error("agent: Qwen backend unavailable: %s", e)
        message = _backend_down_message(e)
        await chat_store.add_message(prisma, int(session["id"]), "assistant", message)
        # Reset first: the turn may have streamed a few tokens before the
        # endpoint dropped, and half a sentence above the error reads as a bug.
        yield {"type": "reset"}
        yield {
            "type": "done",
            "result": {
                "reply": message,
                "tool_calls": [],
                "scan_id": session.get("last_scan_id"),
                "error": True,
            },
        }


async def run_turn(
    prisma: Any,
    user_id: int,
    session: dict,
    user_message: str,
) -> dict:
    """Run one full user turn: think, call tools, answer, persist.

    The blocking form of :func:`stream_turn`, kept for the callers that want the
    finished turn in one piece — the video readout in ``routes/chat.py`` among
    them. It drives the same generator and returns only its final result, so
    there is one implementation of a turn, not two.

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
    async for event in stream_turn(prisma, user_id, session, user_message):
        if event["type"] == "done":
            return event["result"]
    raise QwenChatError("The agent finished without producing a reply.")


def _backend_down_message(error: Exception) -> str:
    """What to tell the user when the chat model cannot be reached."""
    # Name the model actually configured, not a hardcoded one: pointing the
    # user at `qwen2.5:7b` when QWEN_CHAT_MODEL is something else sends them
    # to download several GB of the wrong weights.
    return (
        "I can't reach the Qwen chat model right now, so I can't answer. "
        f"({error}) Start your Qwen server and run "
        f"`ollama pull {settings.qwen_chat_model}`, or check "
        f"QWEN_CHAT_BASE_URL ({settings.qwen_chat_base_url}), then try again."
    )


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
        message = _backend_down_message(e)
        await chat_store.add_message(prisma, int(session["id"]), "assistant", message)
        return {"reply": message, "tool_calls": [], "scan_id": session.get("last_scan_id"), "error": True}
