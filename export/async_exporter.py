"""
S³ Export — Non-blocking Async Excel Export
============================================
Runs Excel generation in a background thread so Streamlit UI stays responsive.
Uses only Python standard library: threading, queue, concurrent.futures.
"""
from __future__ import annotations

import io
import queue
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional

import pandas as pd


@dataclass
class ExportJob:
    """Represents an Excel export job."""
    job_id: str
    run_id: str
    data: Dict[str, Any]
    exporter_func: Callable
    status: str = "pending"  # pending, running, completed, failed
    result: Optional[bytes] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class AsyncExcelExporter:
    """
    Non-blocking Excel exporter using a background thread pool.
    
    Usage:
        exporter = AsyncExcelExporter(max_workers=1)
        job_id = exporter.submit(run_id, data, generate_momentum_excel)
        # ... later check status ...
        status = exporter.get_status(job_id)
        if status == "completed":
            excel_bytes = exporter.get_result(job_id)
    """
    
    def __init__(self, max_workers: int = 1):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="excel_export")
        self._jobs: Dict[str, ExportJob] = {}
        self._lock = threading.Lock()
        self._status_queue: queue.Queue = queue.Queue()
    
    def submit(
        self,
        run_id: str,
        data: Dict[str, Any],
        exporter_func: Callable,
        job_id: Optional[str] = None
    ) -> str:
        """
        Submit an Excel export job.
        
        Args:
            run_id: Unique identifier for the backtest run
            data: Dictionary containing all data needed for export
            exporter_func: Function that takes data dict and returns excel bytes
            job_id: Optional custom job ID (generated if not provided)
            
        Returns:
            Job ID for tracking
        """
        if job_id is None:
            job_id = f"{run_id}_excel_{datetime.now().strftime('%H%M%S%f')}"
        
        job = ExportJob(
            job_id=job_id,
            run_id=run_id,
            data=data.copy(),  # Pass copies to avoid shared state
            exporter_func=exporter_func,
            status="pending"
        )
        
        with self._lock:
            self._jobs[job_id] = job
        
        # Submit to thread pool
        self._executor.submit(self._run_job, job_id)
        
        return job_id
    
    def _run_job(self, job_id: str) -> None:
        """Execute the export job in background thread."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.status = "running"
            job.started_at = datetime.now()
        
        try:
            # Make a deep copy of data to avoid any thread-safety issues
            import copy
            data_copy = copy.deepcopy(job.data)
            
            # Run the exporter function with unpacked data dict
            result = job.exporter_func(**data_copy)
            
            with self._lock:
                job.status = "completed"
                job.result = result
                job.completed_at = datetime.now()
                
        except Exception as e:
            with self._lock:
                job.status = "failed"
                job.error = f"{type(e).__name__}: {e}"
                job.completed_at = datetime.now()
        
        # Notify via queue
        self._status_queue.put(job_id)
    
    def get_status(self, job_id: str) -> Optional[str]:
        """Get job status: pending, running, completed, failed."""
        with self._lock:
            job = self._jobs.get(job_id)
            return job.status if job else None
    
    def get_result(self, job_id: str) -> Optional[bytes]:
        """Get Excel bytes if job completed successfully."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job and job.status == "completed":
                return job.result
            return None
    
    def get_error(self, job_id: str) -> Optional[str]:
        """Get error message if job failed."""
        with self._lock:
            job = self._jobs.get(job_id)
            return job.error if job else None
    
    def wait_for_completion(self, job_id: str, timeout: Optional[float] = None) -> bool:
        """
        Wait for job to complete (blocking).
        
        Returns:
            True if completed successfully, False if timeout or failed
        """
        start = datetime.now()
        while True:
            status = self.get_status(job_id)
            if status in ("completed", "failed"):
                return status == "completed"
            if timeout and (datetime.now() - start).total_seconds() > timeout:
                return False
            import time
            time.sleep(0.1)
    
    def get_job_info(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get full job information."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            return {
                "job_id": job.job_id,
                "run_id": job.run_id,
                "status": job.status,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "error": job.error,
            }
    
    def list_jobs(self, run_id: Optional[str] = None) -> list[Dict[str, Any]]:
        """List all jobs, optionally filtered by run_id."""
        with self._lock:
            jobs = list(self._jobs.values())
        
        if run_id:
            jobs = [j for j in jobs if j.run_id == run_id]
        
        return [
            {
                "job_id": j.job_id,
                "run_id": j.run_id,
                "status": j.status,
                "started_at": j.started_at.isoformat() if j.started_at else None,
                "completed_at": j.completed_at.isoformat() if j.completed_at else None,
                "error": j.error,
            }
            for j in jobs
        ]
    
    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the executor."""
        self._executor.shutdown(wait=wait)


# Global instance for easy access
_global_exporter: Optional[AsyncExcelExporter] = None


def get_async_exporter(max_workers: int = 1) -> AsyncExcelExporter:
    """Get or create the global async exporter instance."""
    global _global_exporter
    if _global_exporter is None:
        _global_exporter = AsyncExcelExporter(max_workers=max_workers)
    return _global_exporter


def shutdown_async_exporter() -> None:
    """Shutdown the global async exporter."""
    global _global_exporter
    if _global_exporter:
        _global_exporter.shutdown()
        _global_exporter = None


# Convenience function for Streamlit integration
def export_excel_async(
    run_id: str,
    data: Dict[str, Any],
    exporter_func: Callable,
    job_id: Optional[str] = None
) -> str:
    """
    Submit an async Excel export job.
    
    Returns job_id immediately. Use get_async_export_status/result to check progress.
    """
    exporter = get_async_exporter()
    return exporter.submit(run_id, data, exporter_func, job_id)


def get_async_export_status(job_id: str) -> Optional[str]:
    """Get status of an async export job."""
    exporter = get_async_exporter()
    return exporter.get_status(job_id)


def get_async_export_result(job_id: str) -> Optional[bytes]:
    """Get Excel bytes if export completed."""
    exporter = get_async_exporter()
    return exporter.get_result(job_id)


def get_async_export_error(job_id: str) -> Optional[str]:
    """Get error if export failed."""
    exporter = get_async_exporter()
    return exporter.get_error(job_id)