"""
Qwen chat completion client — the "brain" of the agent chatbot.

Talks to any OpenAI-compatible endpoint serving a Qwen chat model (Ollama,
vLLM, llama.cpp, DashScope). Configure with ``QWEN_CHAT_BASE_URL`` /
``QWEN_CHAT_MODEL``; the default targets a local Ollama (``ollama pull
qwen2.5:7b``).

Tool calling is normalised across two protocols so it works on servers with and
without native function-calling support:

  1. Native OpenAI ``tools`` / ``tool_calls``.
  2. Qwen's own text protocol — the model emits ``<tool_call>{...}</tool_call>``
     which we parse out of the reply content.

If the endpoint is unreachable and ``QWEN_CHAT_ALLOW_LOCAL_FALLBACK`` is on, we
fall back to running Qwen locally inside ``qwen_env`` (see qwen_chat_infer.py) —
fully offline, but slow. That path only supports protocol (2).
"""

import asyncio
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

# Local fallback interpreter/script (mirrors services/qwen_detection.py).
QWEN_PYTHON = os.getenv("QWEN_PYTHON", str(Path("..") / "qwen_env" / "Scripts" / "python.exe"))
QWEN_CHAT_SCRIPT = os.getenv("QWEN_CHAT_SCRIPT", str(Path("agentic") / "qwen_chat_infer.py"))
QWEN_CHAT_SENTINEL = "__QWEN_CHAT__"
QWEN_CHAT_LOCAL_TIMEOUT_S = int(os.getenv("QWEN_CHAT_LOCAL_TIMEOUT_S", "900"))

# Matches Qwen's text tool-call protocol, e.g.
#   <tool_call>{"name": "analyze_video", "arguments": {...}}</tool_call>
_TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
# Fallback: a fenced ```json {"name": ..., "arguments": ...} ``` block.
_FENCED_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)

# Smaller models sometimes narrate a call instead of making one, e.g.
#   [control_video_playback action="play_segment" start_time=7 end_time=8]
# We recognise the shape so it becomes a real call rather than chat text.
_BRACKET_CALL_RE = re.compile(
    r"""\[\s*(\w+)((?:\s+\w+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s\]]+))+)\s*\]"""
)
_KWARG_RE = re.compile(r"""(\w+)\s*=\s*("[^"]*"|'[^']*'|[^\s\]]+)""")
# Leftovers to strip from an answer: half-written protocol tags, tool-response
# echoes, and fenced JSON (always an arguments blob here — answers are prose).
_SCRUB_RES = (
    re.compile(r"</?tool_call>", re.I),
    re.compile(r"</?tool_response[^>]*>", re.I),
    re.compile(r"```(?:json|tool_call|python)?\s*\{.*?\}\s*```", re.S),
)
_BLANK_RUN_RE = re.compile(r"\n{3,}")
# Frames and clips are rendered by the UI, from real paths the tools returned.
# A link in the prose is therefore always one the model invented — small models
# reliably offer "![](https://media.example.com/media/frames/....jpg)" — and it
# either 404s or leaks raw markdown into the transcript. Images go entirely,
# links keep their text, and the URL alone is dropped where it stands.
_LINK_SCRUB_RES = (
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), ""),
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),
    (re.compile(r"\(\s*<?https?://[^)]*>?\s*\)"), ""),
    (re.compile(r"<?https?://\S+>?"), ""),
)


class QwenChatError(Exception):
    """Raised when the Qwen chat model cannot be reached or understood."""


class ToolCall:
    """One tool invocation requested by the model."""

    def __init__(self, name: str, arguments: dict[str, Any], call_id: str = ""):
        self.name = name
        self.arguments = arguments
        self.id = call_id or f"call_{name}"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ToolCall({self.name}, {self.arguments})"


class ChatReply:
    """Normalised assistant turn: free text plus any tool calls."""

    def __init__(self, content: str, tool_calls: list[ToolCall]):
        self.content = content
        self.tool_calls = tool_calls


def _tool_names(tools: Optional[list[dict[str, Any]]]) -> list[str]:
    """Names of the tools on offer, for recognising text-protocol calls."""
    return [
        str(name)
        for tool in (tools or [])
        if (name := (tool.get("function") or tool).get("name"))
    ]


def _tools_as_text(tools: list[dict[str, Any]]) -> str:
    """Render tool schemas into a system-prompt block for the text protocol."""
    lines = [
        "# Tools",
        "",
        "You can call the following tools. To call one, reply with ONLY this "
        "block and nothing else:",
        '<tool_call>{"name": "<tool>", "arguments": {<json args>}}</tool_call>',
        "",
        "Available tools (JSON schema):",
    ]
    for tool in tools:
        lines.append(json.dumps(tool.get("function", tool), ensure_ascii=False))
    lines += [
        "",
        "After a tool returns, you will see its result in a `tool` message. "
        "Then either call another tool or answer the user in plain language. "
        "Never invent tool results. An answer to the user is prose only: no "
        "<tool_call> block, no tool name, no JSON, no code fence, and no "
        "bracketed command such as [tool_name arg=value] — to use a tool, emit "
        "the block above instead of describing it.",
    ]
    return "\n".join(lines)


def _as_action(blob: str) -> Optional[dict[str, Any]]:
    """Parse a JSON blob into a {name, arguments} action, or None."""
    try:
        payload = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or not payload.get("name"):
        return None
    args = payload.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except json.JSONDecodeError:
            args = {}
    return {"name": str(payload["name"]), "arguments": args if isinstance(args, dict) else {}}


def _as_scalar(raw: str) -> Any:
    """Turn a bracket-call value token into a Python scalar."""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    lowered = value.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _bracket_matches(
    content: str, tool_names: Iterable[str]
) -> list[tuple[tuple[int, int], dict[str, Any]]]:
    """Find `[tool_name key=value ...]` pseudo-calls for the tools we know."""
    known = set(tool_names)
    found = []
    for match in _BRACKET_CALL_RE.finditer(content):
        if match.group(1) not in known:
            continue
        args = {k: _as_scalar(v) for k, v in _KWARG_RE.findall(match.group(2))}
        found.append((match.span(), {"name": match.group(1), "arguments": args}))
    return found


def _parse_text_tool_calls(
    content: str, tool_names: Iterable[str] = ()
) -> tuple[str, list[ToolCall]]:
    """Extract text-protocol tool calls, returning (remaining_text, calls)."""
    # Prefer the tagged protocol; only if it's absent do we treat a bracketed
    # pseudo-call or a fenced JSON action as a call, so ordinary fenced JSON in
    # an answer stays untouched.
    matches = [(m.span(), _as_action(m.group(1))) for m in _TOOL_CALL_RE.finditer(content)]
    if not matches:
        matches = _bracket_matches(content, tool_names)
    if not matches:
        matches = [
            (m.span(), _as_action(m.group(1)))
            for m in _FENCED_RE.finditer(content)
            if _as_action(m.group(1))
        ]
    if not matches:
        return content.strip(), []

    calls: list[ToolCall] = []
    for index, (_, action) in enumerate(matches):
        if action is None:
            logger.warning("Unparseable tool_call block in: %s", content[:200])
            continue
        calls.append(ToolCall(action["name"], action["arguments"], f"call_{index}"))

    # Strip the matched spans back-to-front so earlier offsets stay valid.
    remaining = content
    for (start, end), _ in reversed(matches):
        remaining = remaining[:start] + remaining[end:]
    return remaining.strip(), calls


def scrub_tool_syntax(text: str, tool_names: Iterable[str] = ()) -> str:
    """Strip tool mechanics out of an answer meant for the user.

    Weaker models leak their plumbing into the prose — a stray `<tool_call>`
    tag, a `[control_video_playback action="seek" ...]` pseudo-call, a fenced
    JSON arguments blob, a made-up link to the frame they were shown. The user
    should never see any of it, so anything that survived parsing gets removed
    before the reply is shown or stored.
    """
    for span, _ in reversed(_bracket_matches(text, tool_names)):
        text = text[: span[0]] + text[span[1] :]
    for pattern in _SCRUB_RES:
        text = pattern.sub("", text)
    for pattern, replacement in _LINK_SCRUB_RES:
        text = pattern.sub(replacement, text)
    return _BLANK_RUN_RE.sub("\n\n", text).strip()


def _parse_native_tool_calls(raw: list[dict[str, Any]]) -> list[ToolCall]:
    """Normalise OpenAI-style ``tool_calls`` into ToolCall objects."""
    calls: list[ToolCall] = []
    for index, item in enumerate(raw or []):
        fn = item.get("function") or {}
        name = fn.get("name")
        if not name:
            continue
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except json.JSONDecodeError:
                args = {}
        calls.append(
            ToolCall(str(name), args if isinstance(args, dict) else {}, str(item.get("id") or f"call_{index}"))
        )
    return calls


def _loads_or(value: Any) -> Any:
    """Decode a JSON string, passing through anything already decoded."""
    if isinstance(value, str):
        try:
            return json.loads(value or "{}")
        except json.JSONDecodeError:
            return value
    return value


def _messages_for_text_protocol(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Inline the tool catalogue into the system prompt and flatten tool turns."""
    out: list[dict[str, Any]] = []
    tool_block = _tools_as_text(tools)
    injected = False

    for message in messages:
        role = message.get("role")
        if role == "system" and not injected:
            out.append({"role": "system", "content": f"{message['content']}\n\n{tool_block}"})
            injected = True
        elif role == "tool":
            # Servers without native tool support reject role="tool".
            name = message.get("name", "tool")
            out.append(
                {
                    "role": "user",
                    "content": f"<tool_response name=\"{name}\">\n{message.get('content', '')}\n</tool_response>",
                }
            )
        elif role == "assistant" and message.get("tool_calls"):
            rendered = "\n".join(
                "<tool_call>"
                + json.dumps(
                    {
                        "name": tc["function"]["name"],
                        # OpenAI carries arguments as a JSON string; unwrap it so
                        # the rendered block isn't doubly-encoded.
                        "arguments": _loads_or(tc["function"].get("arguments")),
                    },
                    ensure_ascii=False,
                )
                + "</tool_call>"
                for tc in message["tool_calls"]
            )
            out.append({"role": "assistant", "content": (message.get("content") or "") + rendered})
        else:
            out.append({"role": role, "content": message.get("content", "")})

    if not injected:
        out.insert(0, {"role": "system", "content": tool_block})
    return out


def _post(payload: dict[str, Any]) -> dict[str, Any]:
    """POST to the chat-completions endpoint, raising QwenChatError on failure."""
    url = settings.qwen_chat_base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if settings.qwen_chat_api_key:
        headers["Authorization"] = f"Bearer {settings.qwen_chat_api_key}"

    response = httpx.post(
        url, json=payload, headers=headers, timeout=settings.qwen_chat_timeout_s
    )
    if response.status_code >= 400:
        raise QwenChatError(
            f"Qwen endpoint returned {response.status_code}: {response.text[:300]}"
        )
    return response.json()


def _stderr_summary(stderr: str, limit: int = 300) -> str:
    """Pull the readable cause out of a subprocess traceback.

    Slicing the raw tail chopped messages mid-word (`...dispatch the mod|el on
    the CPU`), so take the final traceback line — the exception type and
    message — and trim from the end instead.
    """
    lines = [ln.strip() for ln in stderr.strip().splitlines() if ln.strip()]
    if not lines:
        return "unknown error"
    summary = lines[-1]
    return summary if len(summary) <= limit else summary[: limit - 1] + "…"


def _local_generate(messages: list[dict[str, Any]], max_tokens: int) -> str:
    """Run Qwen offline inside ``qwen_env`` as a subprocess. Slow but keyless."""
    if not Path(QWEN_PYTHON).exists():
        raise QwenChatError(f"Qwen environment not found at {QWEN_PYTHON}.")
    if not Path(QWEN_CHAT_SCRIPT).exists():
        raise QwenChatError(f"Qwen chat script not found: {QWEN_CHAT_SCRIPT}")

    payload = json.dumps({"messages": messages, "max_tokens": max_tokens})
    try:
        proc = subprocess.run(
            [QWEN_PYTHON, QWEN_CHAT_SCRIPT],
            input=payload,
            capture_output=True,
            text=True,
            timeout=QWEN_CHAT_LOCAL_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as e:
        raise QwenChatError("Local Qwen chat timed out.") from e

    if proc.returncode != 0:
        logger.error("Local Qwen chat failed: %s", proc.stderr[-800:])
        raise QwenChatError(f"Local Qwen chat failed: {_stderr_summary(proc.stderr)}")

    for line in proc.stdout.splitlines():
        if line.startswith(QWEN_CHAT_SENTINEL):
            return json.loads(line[len(QWEN_CHAT_SENTINEL):]).get("content", "")
    raise QwenChatError("Local Qwen chat produced no result.")


def chat(
    messages: list[dict[str, Any]],
    tools: Optional[list[dict[str, Any]]] = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> ChatReply:
    """Run one Qwen chat turn.

    Args:
        messages: OpenAI-style message list (system/user/assistant/tool).
        tools: OpenAI-style tool definitions the model may call.
        temperature: Sampling temperature; low keeps tool arguments stable.
        max_tokens: Generation cap for the reply.

    Returns:
        ChatReply with the assistant text and any requested tool calls.

    Raises:
        QwenChatError: If no Qwen backend could produce a reply.
    """
    body: dict[str, Any] = {
        "model": settings.qwen_chat_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if tools:
        body["tools"] = tools

    try:
        data = _post(body)
    except QwenChatError as e:
        # Servers that don't understand `tools` 400 on it — retry via the
        # text protocol, which any Qwen instruct model can follow.
        if tools and "400" in str(e):
            logger.info("Qwen endpoint rejected native tools; using text protocol")
            body.pop("tools", None)
            body["messages"] = _messages_for_text_protocol(messages, tools)
            data = _post(body)
        else:
            raise
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
        if not settings.qwen_chat_allow_local_fallback:
            raise QwenChatError(
                f"Cannot reach the Qwen chat endpoint at "
                f"{settings.qwen_chat_base_url}. Start Ollama and run "
                f"`ollama pull {settings.qwen_chat_model}`, or set "
                f"QWEN_CHAT_BASE_URL."
            ) from e
        logger.warning("Qwen endpoint unreachable (%s); falling back to local qwen_env", e)
        local_messages = _messages_for_text_protocol(messages, tools or [])
        content = _local_generate(local_messages, max_tokens)
        text, calls = _parse_text_tool_calls(content, _tool_names(tools))
        return ChatReply(text, calls)

    choices = data.get("choices") or []
    if not choices:
        raise QwenChatError("Qwen returned no choices.")
    message = choices[0].get("message") or {}
    content = message.get("content") or ""

    native = _parse_native_tool_calls(message.get("tool_calls") or [])
    if native:
        return ChatReply(content.strip(), native)

    text, calls = _parse_text_tool_calls(content, _tool_names(tools))
    return ChatReply(text, calls)


# --- Streaming ---------------------------------------------------------------
#
# The blocking `chat()` above answers only once the whole reply exists. On local
# weights that is minutes of a dead spinner, so the agent also has a streaming
# path: same protocols, same parsing, but the text is handed over as it is
# generated. See `chat_agent.stream_turn` for how a turn uses it.


class _StreamGate:
    """Holds back text that might still be growing into a text-protocol tool call.

    Qwen announces a tool call inside the reply body — ``<tool_call>{...}``, a
    fenced JSON block, or ``[control_video_playback action="play"]``. Streaming
    those characters into the transcript as they arrive would flash raw protocol
    at the user a moment before the round is recognised as a tool call. So while
    the reply so far could still become one of those openers nothing is
    released; once it clearly cannot, the gate opens for the rest of the round.
    """

    # Openers that mean "this is a tool call, not prose".
    _OPENERS = ("<tool_call>", "```")
    # A bracket call, still unclosed. Prose in brackets is released once it closes.
    _BRACKET = re.compile(r"^\[\w*[^\]]*$")
    # Never hold more than this — ambiguous prose must not stall the stream.
    _MAX_HOLD = 200

    def __init__(self) -> None:
        self._buffer = ""
        self._open = False
        self._suppressed = False

    def push(self, piece: str) -> str:
        """Take one delta; return whatever is safe to show now (often empty)."""
        if self._suppressed:
            return ""
        if self._open:
            return piece
        self._buffer += piece
        head = self._buffer.lstrip()
        if not head:
            return ""
        for opener in self._OPENERS:
            if head.startswith(opener):
                self._suppressed = True
                self._buffer = ""
                return ""
            if opener.startswith(head):
                return ""
        if self._BRACKET.match(head) and len(head) < self._MAX_HOLD:
            return ""
        self._open = True
        out, self._buffer = self._buffer, ""
        return out

    def flush(self) -> str:
        """Release anything still held — the round turned out to be prose."""
        if self._suppressed:
            return ""
        out, self._buffer = self._buffer, ""
        self._open = True
        return out


def _merge_tool_call_deltas(
    acc: dict[int, dict[str, Any]], deltas: list[dict[str, Any]]
) -> None:
    """Fold streamed ``tool_calls`` fragments into whole calls, keyed by index.

    A streamed call arrives in pieces — the name in one chunk, its JSON
    arguments a few characters at a time across the next several. The index is
    the only field repeated on every fragment, so it is what identifies a call.
    """
    for item in deltas or []:
        slot = acc.setdefault(
            int(item.get("index") or 0),
            {"id": "", "function": {"name": "", "arguments": ""}},
        )
        if item.get("id"):
            slot["id"] = str(item["id"])
        fn = item.get("function") or {}
        if fn.get("name"):
            slot["function"]["name"] = str(fn["name"])
        if fn.get("arguments"):
            slot["function"]["arguments"] += str(fn["arguments"])


async def _stream_post(payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """Yield each decoded SSE payload from the chat-completions endpoint."""
    url = settings.qwen_chat_base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if settings.qwen_chat_api_key:
        headers["Authorization"] = f"Bearer {settings.qwen_chat_api_key}"

    # Generous read timeout, short connect: prompt evaluation on CPU can leave
    # the socket silent for minutes before the first token, but a server that is
    # not listening should be reported at once rather than after the whole
    # inference budget has elapsed.
    timeout = httpx.Timeout(settings.qwen_chat_timeout_s, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, json=payload, headers=headers) as response:
            if response.status_code >= 400:
                body = (await response.aread()).decode("utf-8", "replace")
                raise QwenChatError(
                    f"Qwen endpoint returned {response.status_code}: {body[:300]}"
                )
            async for line in response.aiter_lines():
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    return
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    # A keepalive or a half line; the next one carries the token.
                    continue


async def chat_stream(
    messages: list[dict[str, Any]],
    tools: Optional[list[dict[str, Any]]] = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> AsyncIterator[dict[str, Any]]:
    """Run one Qwen turn, yielding the reply as it is generated.

    Yields:
        ``{"type": "delta", "text": str}`` for each piece of prose safe to show,
        then exactly one ``{"type": "reply", "reply": ChatReply}`` carrying the
        authoritative scrubbed turn, including any tool calls. A caller that
        streamed the deltas into a transcript should still replace that text
        with the reply's, which is the cleaned version.

    Raises:
        QwenChatError: If no Qwen backend could produce a reply.
    """
    body: dict[str, Any] = {
        "model": settings.qwen_chat_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    if tools:
        body["tools"] = tools

    parts: list[str] = []
    acc: dict[int, dict[str, Any]] = {}
    gate = _StreamGate()

    async def _attempt(request: dict[str, Any]) -> AsyncIterator[str]:
        """Drive one request, yielding the text the gate lets through."""
        async for chunk in _stream_post(request):
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            if delta.get("tool_calls"):
                _merge_tool_call_deltas(acc, delta["tool_calls"])
            piece = delta.get("content") or ""
            if piece:
                parts.append(piece)
                safe = gate.push(piece)
                if safe:
                    yield safe

    emitted = False
    try:
        async for text in _attempt(body):
            emitted = True
            yield {"type": "delta", "text": text}
    except QwenChatError as e:
        # Servers that do not understand `tools` 400 on it — retry via the text
        # protocol, which any Qwen instruct model can follow. Safe only because
        # that rejection lands before generation, so nothing was shown yet.
        if tools and "400" in str(e) and not emitted:
            logger.info("Qwen endpoint rejected native tools; using text protocol")
            parts.clear()
            acc.clear()
            gate = _StreamGate()
            retry = dict(body)
            retry.pop("tools", None)
            retry["messages"] = _messages_for_text_protocol(messages, tools)
            async for text in _attempt(retry):
                yield {"type": "delta", "text": text}
        else:
            raise
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
        if not settings.qwen_chat_allow_local_fallback:
            raise QwenChatError(
                f"Cannot reach the Qwen chat endpoint at "
                f"{settings.qwen_chat_base_url}. Start Ollama and run "
                f"`ollama pull {settings.qwen_chat_model}`, or set "
                f"QWEN_CHAT_BASE_URL."
            ) from e
        # The offline path is a subprocess that only ever returns a whole
        # answer, so this turn arrives in one piece rather than streaming.
        logger.warning("Qwen endpoint unreachable (%s); falling back to local qwen_env", e)
        local_messages = _messages_for_text_protocol(messages, tools or [])
        content = await asyncio.to_thread(_local_generate, local_messages, max_tokens)
        text, calls = _parse_text_tool_calls(content, _tool_names(tools))
        if not calls and text:
            yield {"type": "delta", "text": text}
        yield {"type": "reply", "reply": ChatReply(text, calls)}
        return

    content = "".join(parts)
    native = _parse_native_tool_calls([acc[i] for i in sorted(acc)])
    if native:
        reply = ChatReply(content.strip(), native)
    else:
        text, calls = _parse_text_tool_calls(content, _tool_names(tools))
        reply = ChatReply(text, calls)

    # Nothing was a tool call after all — release whatever the gate still holds.
    if not reply.tool_calls:
        tail = gate.flush()
        if tail:
            yield {"type": "delta", "text": tail}

    yield {"type": "reply", "reply": reply}


def health() -> dict[str, Any]:
    """Report whether the Qwen chat backend is reachable (for the UI banner)."""
    url = settings.qwen_chat_base_url.rstrip("/") + "/models"
    headers = {}
    if settings.qwen_chat_api_key:
        headers["Authorization"] = f"Bearer {settings.qwen_chat_api_key}"
    try:
        response = httpx.get(url, headers=headers, timeout=5)
        online = response.status_code < 400
    except Exception:
        online = False
    return {
        "online": online,
        "model": settings.qwen_chat_model,
        "base_url": settings.qwen_chat_base_url,
        "local_fallback": settings.qwen_chat_allow_local_fallback
        and Path(QWEN_PYTHON).exists(),
    }
