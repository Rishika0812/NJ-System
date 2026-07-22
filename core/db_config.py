"""
S³ Core — Database Configuration
=================================
Loads the project-root ``.env`` file into ``os.environ`` (idempotent, no
external dependency on python-dotenv) and exposes small helpers for reading
config values with the same priority order used by the S3 main system:

    1. ``st.secrets``  (Streamlit Cloud "Secrets" UI)
    2. environment variables / ``.env`` file

Relevant keys (see ``.env``)::

    MONGO_URI             mongodb+srv://user:pass@cluster/...   (required)
    MONGO_DB_NAME         database name              (default: "smartbeta")
    MONGO_GRIDFS_BUCKET   GridFS bucket name         (default: "duckdb_store")
    MONGO_DUCKDB_FILE     logical filename in GridFS (default: "market_data.duckdb.gz")
"""
from __future__ import annotations

import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_DEFAULTS = {
    "MONGO_DB_NAME": "smartbeta",
    "MONGO_GRIDFS_BUCKET": "duckdb_store",
    "MONGO_DUCKDB_FILE": "market_data.duckdb.gz",
}

_ENV_LOADED = False


def _load_dotenv() -> None:
    """Load project-root ``.env`` into ``os.environ`` (idempotent, no overwrite)."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    dotenv_path = os.path.join(PROJECT_ROOT, ".env")
    if not os.path.exists(dotenv_path):
        return
    with open(dotenv_path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def get_conf(key: str, default: str | None = None) -> str | None:
    """Read a config value from Streamlit secrets first, then the environment."""
    _load_dotenv()
    try:
        import streamlit as st

        # ``st.secrets`` raises if no secrets file exists; guard with try/except.
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        pass
    return os.environ.get(key, default or _DEFAULTS.get(key))


def mongo_uri() -> str | None:
    return get_conf("MONGO_URI")


def is_configured() -> bool:
    """True if a MongoDB URI is available (secrets or env)."""
    return bool(mongo_uri())


def config_summary() -> dict:
    """Non-secret view of the active Mongo configuration for display."""
    uri = mongo_uri()
    host = ""
    if uri:
        tail = uri.split("@", 1)[-1]
        host = tail.split("/", 1)[0].split("?", 1)[0]
    return {
        "configured": bool(uri),
        "host": host,
        "db_name": get_conf("MONGO_DB_NAME"),
        "bucket": get_conf("MONGO_GRIDFS_BUCKET"),
        "filename": get_conf("MONGO_DUCKDB_FILE"),
    }
