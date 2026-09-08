"""
Mini Agentic AI Platform: Multi-agent incident remediation system.

Importing this package loads a `.env` file from the repository root, if one
exists, into the process environment. That happens here rather than in each
entrypoint because several modules read their configuration at import time --
`SQLITE_DB_PATH`, `CHECKPOINT_DB_PATH`, `SESSION_ENVIRONMENT` are all captured
into module constants -- so loading any later would be too late to affect them.

Variables already present in the environment are never overwritten, so an
operator, a container, or CI always outranks a file on disk. Tests disable the
load entirely by setting `ENV_FILE` to an empty value before importing, which
keeps the suite hermetic regardless of what happens to be in a developer's
working directory.
"""
from .config import load_env_file

__version__ = "1.3.0"

#: Names and values applied from `.env` at import. Exposed so an entrypoint can
#: report what was picked up; values must be rendered with
#: `mini_platform.config.describe_loaded`, never printed directly.
ENV_FILE_VALUES = load_env_file()
