"""Configuration and credential handling.

Credentials are read from the environment only. Nothing in this repository
contains a real key: `.env` holds them locally and is gitignored, and
`.env.example` is the committed template with placeholders.

This module also owns two safety helpers:

  redact()          strips anything key-shaped out of a string before it is
                    printed, logged, or raised
  require_api_key() fails with a readable message that names the missing
                    variable without ever showing a value
"""

import os
import re
from pathlib import Path


def _load_env_file() -> None:
    """Read .env into the environment.

    Uses python-dotenv when it is installed. Falls back to a small parser when
    it is not, because the demo mode and the test suite are meant to run on a
    bare Python with nothing installed, and an import error here would break
    that promise for the sake of one convenience library.

    Existing environment variables always win, which is what dotenv does too.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        env_path = Path(__file__).resolve().parents[1] / ".env"
        if not env_path.exists():
            return
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            name = name.strip()
            value = value.strip().strip("'\"")
            if name and name not in os.environ:
                os.environ[name] = value
        return
    load_dotenv()


_load_env_file()

# The local development database created by docker-compose.yml. These are not
# secrets: the container is built by this repository, bound to the loopback
# interface, and holds nothing but synthetic data. Override DATABASE_URL in
# .env to point somewhere else.
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/supply_chain"
)

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")

#: Only needed when ANTHROPIC_API_KEY is an organisation-level key rather than
#: one created inside a workspace. Such a key has to say which workspace a
#: request belongs to, or the API returns a 400. Leave it unset for a
#: workspace-scoped key, which is the common case.
ANTHROPIC_WORKSPACE_ID = os.getenv("ANTHROPIC_WORKSPACE_ID", "").strip()
OPENAI_EMBEDDING_MODEL = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

# Key-shaped things, so an error message or a debug print cannot leak one.
_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"),
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-.]{16,}"),
    # An assignment whose name looks like a secret, e.g. api_key='...' or
    # ANTHROPIC_API_KEY=... . No leading \b: underscore is a word character,
    # so \b never matches inside a name like ANTHROPIC_API_KEY.
    re.compile(
        r"(?i)[A-Za-z0-9_]*(?:api[_-]?key|secret|token|password|passwd)"
        r"\s*[:=]\s*['\"]?[A-Za-z0-9_\-.]{8,}['\"]?"
    ),
    # Any URL carrying credentials, e.g. postgresql://user:password@host
    re.compile(r"(?P<scheme>[a-z+]+://)(?P<user>[^:/@\s]+):(?P<pw>[^@/\s]+)@"),
]


def redact(text: object) -> str:
    """Remove anything that looks like a credential from a string.

    Used on anything derived from an exception or a connection string before it
    reaches a terminal, a log file, or a model prompt. It is deliberately
    aggressive: over-redacting a debug message costs nothing, and printing a
    live key costs a key rotation.
    """
    out = str(text)
    for pattern in _SECRET_PATTERNS:
        if "pw" in pattern.groupindex:
            out = pattern.sub(r"\g<scheme>\g<user>:***@", out)
        else:
            out = pattern.sub("***redacted***", out)
    return out


class MissingCredential(RuntimeError):
    """Raised when a required API key is not configured."""


_KEY_HELP = {
    "ANTHROPIC_API_KEY": "https://console.anthropic.com/settings/keys",
    "OPENAI_API_KEY": "https://platform.openai.com/api-keys",
}


#: The placeholder text in .env.example. A copied-but-unedited .env sets the
#: variable to this, which is not a key — treating it as one produces a
#: provider authentication error instead of "you have not filled this in yet".
_PLACEHOLDER_PREFIX = "replace-with-"


def _is_configured(value: str) -> bool:
    value = value.strip()
    return bool(value) and not value.startswith(_PLACEHOLDER_PREFIX)


def require_api_key(name: str) -> str:
    """Return the key, or explain how to set it. Never prints the value."""
    value = os.getenv(name, "").strip()
    if not _is_configured(value):
        where = _KEY_HELP.get(name, "your provider's dashboard")
        state = "is still the placeholder" if value else "is not set"
        raise MissingCredential(
            f"{name} {state}.\n\n"
            f"  1. cp .env.example .env\n"
            f"  2. Get a key from {where}\n"
            f"  3. Put it in .env as {name}=...\n\n"
            f".env is gitignored, so it will not be committed."
        )
    return value


def anthropic_default_headers() -> dict[str, str]:
    """Extra headers the Claude client needs, if any.

    Empty for a workspace-scoped key. Carries the workspace id when the key is
    organisation-level, so that case works without having to issue a new key.
    """
    if ANTHROPIC_WORKSPACE_ID:
        return {"anthropic-workspace-id": ANTHROPIC_WORKSPACE_ID}
    return {}


def missing_api_keys() -> list[str]:
    """Which required keys are absent. For a friendly pre-flight check."""
    return [
        name
        for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")
        if not _is_configured(os.getenv(name, ""))
    ]
