"""
Answers for the messages that never needed a model.

"hi" took the same road as "give me a full threat analysis": a 5 KB system
prompt, every tool schema, and up to 1024 tokens of generation — tens of seconds
on local weights to produce one line of greeting. This module recognises the
handful of messages whose answer is fixed and returns it directly, so the
trivial turns are instant and the model is left for the questions that need it.

Matching is deliberately narrow. The whole message must *be* the greeting, not
merely contain one: "hi" is answered from here, "hi, is there a gun at 0:12?"
is not, and goes to the agent like any other question. Anything unrecognised
returns None, which is the signal to run the full turn — the failure mode of a
missed match is only the old latency, never a wrong answer.
"""

import re
import unicodedata
from typing import Optional

# Punctuation and emoji are stripped before matching, so "Hello!!" and "hi 👋"
# land on the same key as "hello".
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")

# Roman Urdu sits alongside English throughout: these chats are held in both,
# often in the same sentence, and a greeting in one is no less trivial than in
# the other.
_GREETINGS = frozenset(
    {
        "hi",
        "hii",
        "hello",
        "helo",
        "hey",
        "yo",
        "hi there",
        "hello there",
        "good morning",
        "good afternoon",
        "good evening",
        "salam",
        "salaam",
        "slam",
        "assalam o alaikum",
        "assalamu alaikum",
        "asalam o alaikum",
        "aoa",
        "kya haal hai",
        "kya hal hai",
        "kaise ho",
        "kaisay ho",
        "how are you",
        "how are you doing",
        "hows it going",
        "whats up",
        "sup",
    }
)

_THANKS = frozenset(
    {
        "thanks",
        "thank you",
        "thanks a lot",
        "thank you so much",
        "thx",
        "ty",
        "shukriya",
        "shukria",
        "bohot shukriya",
        "jazakallah",
        "great",
        "nice",
        "perfect",
        "awesome",
        "good job",
        "well done",
        "zabardast",
        "bohot acha",
        "bohot achha",
    }
)

_ACKS = frozenset(
    {
        "ok",
        "okay",
        "k",
        "kk",
        "fine",
        "alright",
        "got it",
        "understood",
        "sure",
        "yes",
        "yeah",
        "yep",
        "no",
        "nope",
        "theek hai",
        "thik hai",
        "acha",
        "achha",
        "han",
        "haan",
        "ji",
        "ji han",
        "nahi",
    }
)

_FAREWELLS = frozenset(
    {
        "bye",
        "byee",
        "goodbye",
        "good bye",
        "see you",
        "see ya",
        "cya",
        "good night",
        "gn",
        "khuda hafiz",
        "allah hafiz",
        "phir milte hain",
    }
)

# "What can you do" is fixed knowledge about this system, not something the
# model should be left to improvise — it hallucinated capabilities it does not
# have often enough to be worth answering from here.
_CAPABILITIES = frozenset(
    {
        "help",
        "help me",
        "what can you do",
        "what can u do",
        "what do you do",
        "who are you",
        "what are you",
        "tum kya kar sakte ho",
        "tum kya kar sakty ho",
        "aap kya kar sakte hain",
        "ap kya kar sakty hain",
        "kya kar sakte ho",
        "commands",
        "options",
    }
)

_CAPABILITIES_REPLY = (
    "I'm the SentinelAI agent. Here's what I can do:\n\n"
    "- **Analyse a video** — attach one with the paperclip and send. It's "
    "scanned on arrival for weapons, fighting, theft, harassment and "
    "accidents, and I'll write up what was found and when.\n"
    "- **Show you the moment** — ask for a timestamp or an incident and I'll "
    "seek the player to it, or capture the exact frame.\n"
    "- **Re-scan with another model** — say which one (weapons, actions, "
    "Gemini, Qwen) and I'll run it again.\n"
    "- **Answer from your scan history** — questions like \"how many threats "
    "did I find this week\" are answered from your past scans, no upload "
    "needed.\n\n"
    "Verify anything critical against the report page."
)


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation and emoji, and collapse whitespace."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return _WHITESPACE.sub(" ", _NON_WORD.sub(" ", folded)).strip()


def _greeting_reply(video_name: Optional[str]) -> str:
    """Say hello, and point at whichever step is actually next."""
    if video_name:
        return (
            f"Hello — **{video_name}** is attached and analysed. Ask me what "
            "happens in it, when an incident occurs, or to show you the exact "
            "frame."
        )
    return (
        "Hello — I'm the SentinelAI agent. Attach a video with the paperclip "
        "and send it; I'll analyse it and show you the exact frames. You can "
        "also ask about your past scans without uploading anything."
    )


def quick_reply(message: str, session: dict) -> Optional[str]:
    """The fixed answer for `message`, or None to run the full agent turn.

    Args:
        message: What the user just sent, raw.
        session: The chat session row, read for the attached video's name so
            the greeting can point at the right next step.

    Returns:
        The reply to persist and return, or None when the message needs the
        model — which is every message this does not recognise outright.
    """
    text = _normalise(message)
    if not text:
        return None

    # A long message is never one of these, whatever it starts with. Checked
    # before the sets purely so a pasted paragraph doesn't get normalised and
    # compared for nothing.
    if len(text) > 40:
        return None

    video_name = session.get("video_name")

    if text in _GREETINGS:
        return _greeting_reply(video_name)

    if text in _CAPABILITIES:
        return _CAPABILITIES_REPLY

    if text in _THANKS:
        return (
            "Anytime. Ask me anything else about the footage, or about your "
            "scan history."
        )

    if text in _FAREWELLS:
        return "Goodbye — this conversation and your scans stay saved."

    if text in _ACKS:
        if video_name:
            return f"Got it. Ask me anything about **{video_name}** whenever you're ready."
        return "Got it. Attach a video when you're ready, or ask about your past scans."

    return None
