"""
S³ Core — Persistent Backtest Storage
======================================
Filesystem-based storage for backtest results. Each run gets a unique run_id
directory containing all artifacts needed to reproduce and review the run.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


RESULTS_ROOT = Path("results") / "backtests"


def _ensure_results_root() -> Path:
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    return RESULTS_ROOT


def generate_run_id() -> str:
    """Generate a unique run ID: timestamp + short uuid."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    uid = uuid.uuid4().hex[:8]
    return f"{ts}_{uid}"


def get_run_dir(run_id: str) -> Path:
    """Get the directory for a specific run_id, creating if needed."""
    return (_ensure_results_root() / run_id)


def save_config(run_id: str, config: dict) -> Path:
    """Save the run configuration as JSON."""
    run_dir = get_run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "config.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_json_serializable(config), f, indent=2, default=str)
    return path


def save_metrics(run_id: str, metrics: dict) -> Path:
    """Save performance metrics as JSON."""
    run_dir = get_run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "metrics.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_json_serializable(metrics), f, indent=2, default=str)
    return path


def save_execution_metadata(run_id: str, metadata: dict) -> Path:
    """Save execution metadata (timestamps, versions, etc.)."""
    run_dir = get_run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "execution_metadata.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_json_serializable(metadata), f, indent=2, default=str)
    return path


def save_trades_parquet(run_id: str, trades_df: pd.DataFrame) -> Path:
    """Save per-trade data as Parquet."""
    run_dir = get_run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "trades.parquet"
    trades_df.to_parquet(path, index=False)
    return path


def save_portfolio_nav_parquet(run_id: str, nav_df: pd.DataFrame) -> Path:
    """Save portfolio NAV/equity data as Parquet."""
    run_dir = get_run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "portfolio_nav.parquet"
    nav_df.to_parquet(path, index=False)
    return path


def save_dataframe_parquet(run_id: str, df: pd.DataFrame, filename: str) -> Path:
    """Save a generic DataFrame as Parquet with a custom filename."""
    run_dir = get_run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / filename
    df.to_parquet(path, index=False)
    return path


def load_dataframe_parquet(run_id: str, filename: str) -> pd.DataFrame | None:
    """Load a generic DataFrame from Parquet by filename."""
    path = get_run_dir(run_id) / filename
    if not path.exists():
        return None
    return pd.read_parquet(path)


def save_excel_report(run_id: str, excel_bytes: bytes) -> Path:
    """Save the generated Excel report."""
    run_dir = get_run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "report.xlsx"
    with open(path, "wb") as f:
        f.write(excel_bytes)
    return path


def load_config(run_id: str) -> dict | None:
    """Load run configuration."""
    path = get_run_dir(run_id) / "config.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_metrics(run_id: str) -> dict | None:
    """Load performance metrics."""
    path = get_run_dir(run_id) / "metrics.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_execution_metadata(run_id: str) -> dict | None:
    """Load execution metadata."""
    path = get_run_dir(run_id) / "execution_metadata.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_trades_parquet(run_id: str) -> pd.DataFrame | None:
    """Load per-trade data."""
    path = get_run_dir(run_id) / "trades.parquet"
    if not path.exists():
        return None
    return pd.read_parquet(path)


def load_portfolio_nav_parquet(run_id: str) -> pd.DataFrame | None:
    """Load portfolio NAV/equity data."""
    path = get_run_dir(run_id) / "portfolio_nav.parquet"
    if not path.exists():
        return None
    return pd.read_parquet(path)


def load_excel_report(run_id: str) -> bytes | None:
    """Load the Excel report as bytes."""
    path = get_run_dir(run_id) / "report.xlsx"
    if not path.exists():
        return None
    with open(path, "rb") as f:
        return f.read()


def list_runs() -> list[dict]:
    """List all available backtest runs with basic metadata."""
    _ensure_results_root()
    runs = []
    for run_dir in sorted(RESULTS_ROOT.iterdir(), key=lambda p: p.name, reverse=True):
        if not run_dir.is_dir():
            continue
        meta = load_execution_metadata(run_dir.name)
        cfg = load_config(run_dir.name)
        runs.append({
            "run_id": run_dir.name,
            "config": cfg,
            "metadata": meta,
            "has_report": (run_dir / "report.xlsx").exists(),
            "has_trades": (run_dir / "trades.parquet").exists(),
            "has_nav": (run_dir / "portfolio_nav.parquet").exists(),
        })
    return runs


def delete_run(run_id: str) -> bool:
    """Delete a backtest run directory."""
    import shutil
    run_dir = get_run_dir(run_id)
    if not run_dir.exists():
        return False
    shutil.rmtree(run_dir)
    return True


def _json_serializable(obj: Any) -> Any:
    """Convert objects to JSON-serializable form."""
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_json_serializable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _json_serializable(v) for k, v in obj.items()}
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, pd.DataFrame):
        return obj.to_dict(orient="records")
    if isinstance(obj, pd.Series):
        return obj.to_dict()
    if hasattr(obj, "__dict__"):
        return _json_serializable(obj.__dict__)
    return str(obj)