"""
Environment file loading.

The repository ships a documented `.env.example`, which implies a `.env` beside
it is the way to configure a local run -- but nothing read it, so every setting
in that file was silently inert. This module closes that gap.

It is deliberately a small standard-library parser rather than a dependency. The
platform's stated position is that every runtime dependency is one it actually
imports, and the format in `.env.example` is narrow enough (``KEY="value"``,
comments, blank lines) that parsing it does not warrant one.

Precedence: **a variable already present in the environment always wins.** A file
on disk must never silently override what an operator, a container, or CI set
explicitly -- otherwise a stray `.env` in a working directory could redirect a
production run's database path or turn on a paid model provider.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

#: Version of the configuration loader.
__version__ = "1.0.0"

#: Overrides which file is read. Set to an empty value to disable loading.
ENV_FILE_ENV_VAR = "ENV_FILE"

#: Default filename, resolved against the repository root.
DEFAULT_ENV_FILENAME = ".env"

#: Names whose values must never be echoed, logged, or included in an error.
#: The loader never prints a value regardless; this drives the summary helper.
_SECRET_HINTS = ("key", "secret", "token", "password", "credential")


def _repo_root() -> Path:
    """The directory containing the package, i.e. the repository root."""
    return Path(__file__).resolve().parent.parent


def _unquote(value: str) -> str:
    """
    Strip one matching pair of surrounding quotes.

    An unquoted value additionally has trailing inline comments removed, which a
    quoted value keeps -- a `#` inside quotes is data, not a comment.
    """
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]

    if "#" in value:
        value = value.split("#", 1)[0].rstrip()
    return value


def parse_env_file(path: Path) -> Dict[str, str]:
    """
    Parse a ``.env`` file into a mapping, without touching ``os.environ``.

    Ignores blank lines, comments, and lines with no ``=``. Tolerates a leading
    ``export``. A malformed line is skipped rather than raising: a typo in a
    config file should not make the platform unstartable.
    """
    values: Dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return values

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()

        name, _, value = line.partition("=")
        name = name.strip()
        if not name or not name.replace("_", "").isalnum():
            continue
        values[name] = _unquote(value)
    return values


def load_env_file(path: Optional[Path] = None, override: bool = False) -> Dict[str, str]:
    """
    Load a ``.env`` file into ``os.environ``.

    Args:
        path: File to read. Defaults to ``ENV_FILE`` if set, else ``.env`` in the
            repository root. An explicitly empty ``ENV_FILE`` disables loading.
        override: Whether a file value may replace one already in the
            environment. Defaults to ``False``, and should stay that way outside
            of tests -- see the precedence note in the module docstring.

    Returns:
        The names and values actually applied. Callers must not log the values.
    """
    if path is None:
        configured = os.environ.get(ENV_FILE_ENV_VAR)
        if configured is not None and not configured.strip():
            return {}
        path = Path(configured) if configured else _repo_root() / DEFAULT_ENV_FILENAME

    if not path.is_file():
        return {}

    applied: Dict[str, str] = {}
    for name, value in parse_env_file(path).items():
        if not override and name in os.environ:
            continue
        os.environ[name] = value
        applied[name] = value
    return applied


def describe_loaded(applied: Dict[str, str]) -> str:
    """
    Render a one-line, secret-safe summary of what was loaded.

    Secret-looking names are reported as set without their value, so a startup
    line can confirm configuration was picked up without leaking a key into a
    terminal, a log aggregator, or a screen recording.
    """
    if not applied:
        return "no .env values applied"

    parts = []
    for name in sorted(applied):
        if any(hint in name.lower() for hint in _SECRET_HINTS):
            parts.append(f"{name}=<set>")
        else:
            parts.append(f"{name}={applied[name]}")
    return "loaded from .env: " + ", ".join(parts)
