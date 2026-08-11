"""
Every prompt the agents send to a model, as editable text files.

One folder per agent, mirroring the modules in `agentic/`:

    orchestrator/   the chatbot the user talks to   (agentic/chat_agent.py)
    db_agent/       natural language -> SQL analyst (agentic/db_agent.py)
    scan_chat/      the per-scan grounded agent     (agentic/scan_chat.py)

Prompt wording is the main lever on answer quality, so it lives here rather
than buried in a module constant: you can reword a rule, restart the API and
see the difference without touching Python. Nothing else about a prompt file is
special — it is read verbatim and sent as the system message.

Usage:

    from agentic import prompts

    prompts.load("orchestrator/system")
    prompts.render("db_agent/sql_system", schema=..., max_rows=200)

Files are resolved relative to this package, not the working directory, so the
API, the ARQ worker and anything under scripts/ all read the same text.
"""

from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def load(name: str) -> str:
    """
    Return the prompt stored at ``<name>.md``, e.g. ``"db_agent/schema"``.

    Cached, so a prompt is read from disk once per process. Raises
    FileNotFoundError on a typo rather than silently sending an empty system
    message — an agent with no instructions fails in confusing ways.
    """
    path = PROMPT_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"No prompt file at {path}")
    return path.read_text(encoding="utf-8").strip()


def render(name: str, **values: object) -> str:
    """
    Load a prompt and fill its ``{placeholder}`` fields.

    Only for prompts that declare placeholders (currently
    ``db_agent/sql_system``). Literal braces in a prompt must be doubled, the
    same as any str.format template.
    """
    return load(name).format(**values)
