"""
S³ Core — Database Provisioning
=================================
Ensures the analytical DuckDB file (built by the S3 main system) is present
on local disk, downloading it from MongoDB GridFS on first run if needed —
exactly the same mechanism the S3 main system uses. Ported here so the
Momentum system can read the *same* shared market-data store instead of
requiring manual Data.xlsx / Dates.xlsx uploads.

* **Local dev** — if you've already run the S3 main system here, the file is
  on disk under ``storage/`` and this is a no-op.
* **Cloud (Streamlit Community Cloud, etc.)** — downloads the (gzip
  compressed) file from GridFS to local disk on first startup.
"""
from __future__ import annotations

import gzip
import os

from core.db_config import PROJECT_ROOT, get_conf, mongo_uri

DB_LOCAL_PATH = os.path.join(PROJECT_ROOT, "storage", "market_data.duckdb")

#: Set once provisioning has run so repeated calls (Streamlit reruns) are cheap.
_PROVISIONED = False


def ensure_database(db_path: str | None = None, *, force: bool = False) -> str:
    """Ensure the DuckDB file exists locally, downloading from GridFS if needed.

    Returns the resolved local path. Safe to call repeatedly (idempotent): if
    the file already exists it returns immediately unless ``force`` is set.
    """
    global _PROVISIONED
    dest = db_path or DB_LOCAL_PATH

    if not force and os.path.exists(dest):
        _PROVISIONED = True
        return dest
    if _PROVISIONED and not force:
        return dest

    uri = mongo_uri()
    if not uri:
        raise RuntimeError(
            "MONGO_URI is not configured. Add it to your .env file (see the "
            "S3 main system's .env) or to Streamlit secrets."
        )

    _download_from_gridfs(dest, uri=uri)
    _PROVISIONED = True
    return dest


def test_connection(*, uri: str | None = None) -> dict:
    """Ping the Atlas cluster. Returns {'ok': bool, 'detail': str, 'databases': [...]}.

    Surfaces the *actual* pymongo error (missing dnspython, IP not allow-listed
    on Atlas, bad credentials, network egress blocked, etc.) instead of letting
    ``ensure_database`` fail deep inside the GridFS download with a generic
    traceback. Call this from the sidebar before attempting a download.
    """
    try:
        from pymongo import MongoClient
    except ImportError:
        return {"ok": False, "detail": "pymongo not installed", "databases": []}

    uri = uri or mongo_uri()
    if not uri:
        return {"ok": False, "detail": "MONGO_URI not configured", "databases": []}

    client = None
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=8000)
        client.admin.command("ping")
        dbs = client.list_database_names()
        return {"ok": True, "detail": "ping ok", "databases": dbs}
    except Exception as exc:  # noqa: BLE001 - want the exact driver error surfaced
        detail = f"{type(exc).__name__}: {exc}"
        if "dnspython" in detail.lower():
            detail += " — fix: pip install dnspython (now pinned in requirements.txt)."
        elif "serverselectiontimeout" in type(exc).__name__.lower():
            detail += (" — likely cause: this host's IP isn't on the Atlas cluster's "
                        "Network Access allow-list, or the URI/credentials are stale.")
        return {"ok": False, "detail": detail, "databases": []}
    finally:
        if client is not None:
            client.close()


def gridfs_status(*, uri: str | None = None) -> dict:
    """Whether the DuckDB blob exists in GridFS, plus its size/upload date."""
    try:
        from pymongo import MongoClient
    except ImportError:
        return {"exists": False, "detail": "pymongo not installed"}

    uri = uri or mongo_uri()
    if not uri:
        return {"exists": False, "detail": "MONGO_URI not configured"}

    db_name = get_conf("MONGO_DB_NAME")
    bucket = get_conf("MONGO_GRIDFS_BUCKET")
    filename = get_conf("MONGO_DUCKDB_FILE")

    client = None
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=8000)
        files_coll = client[db_name][f"{bucket}.files"]
        doc = files_coll.find_one({"filename": filename}, sort=[("uploadDate", -1)])
        if not doc:
            return {"exists": False, "detail": "no blob found", "filename": filename}
        return {
            "exists": True,
            "filename": filename,
            "size_mb": doc.get("length", 0) / 1_048_576,
            "upload_date": str(doc.get("uploadDate", "")),
        }
    except Exception as exc:  # noqa: BLE001
        return {"exists": False, "detail": f"{type(exc).__name__}: {exc}"}
    finally:
        if client is not None:
            client.close()


def local_status(db_path: str | None = None) -> dict:
    """Local DuckDB file presence + size."""
    path = db_path or DB_LOCAL_PATH
    exists = os.path.exists(path)
    size_mb = os.path.getsize(path) / 1_048_576 if exists else 0.0
    return {"path": path, "exists": exists, "size_mb": size_mb}


def _download_from_gridfs(dest_path: str, *, uri: str) -> str:
    """Download the DuckDB blob from MongoDB GridFS (mirrors S3-main's
    ``core.data.storage.provisioning.download_from_gridfs``)."""
    try:
        from pymongo import MongoClient
        import gridfs
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "pymongo is required to download the database from MongoDB GridFS. "
            "Add 'pymongo' to requirements.txt."
        ) from exc

    db_name = get_conf("MONGO_DB_NAME")
    bucket = get_conf("MONGO_GRIDFS_BUCKET")
    stored_filename = get_conf("MONGO_DUCKDB_FILE")
    is_compressed = stored_filename.endswith(".gz")

    final_dest = dest_path
    os.makedirs(os.path.dirname(final_dest), exist_ok=True)

    tmp = dest_path + ".part"
    client = None
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=15000)
        fs = gridfs.GridFSBucket(client[db_name], bucket_name=bucket)

        with open(tmp, "wb") as fh:
            fs.download_to_stream_by_name(stored_filename, fh)

        if is_compressed:
            with gzip.open(tmp, "rb") as f_in:
                with open(final_dest, "wb") as f_out:
                    while chunk := f_in.read(8192 * 1024):
                        f_out.write(chunk)
            os.remove(tmp)
        else:
            os.replace(tmp, final_dest)
    except Exception as exc:  # noqa: BLE001 - re-raise with actionable context
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise RuntimeError(
            f"GridFS download failed ({type(exc).__name__}: {exc}). Call "
            "core.db_provisioning.test_connection() to diagnose — common causes: "
            "dnspython missing (now pinned in requirements.txt), the host IP isn't "
            "Atlas-allow-listed (Network Access -> Add 0.0.0.0/0 for cloud hosting), "
            "or MONGO_DB_NAME/MONGO_GRIDFS_BUCKET/MONGO_DUCKDB_FILE in .env don't "
            "match what the S3 main system actually uploaded."
        ) from exc
    finally:
        if client is not None:
            client.close()
    return final_dest
