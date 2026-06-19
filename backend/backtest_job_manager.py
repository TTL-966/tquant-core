"""Backtest job manager: tracks active workers, progress, result caching, cancel."""

import uuid
import threading
from PySide6.QtCore import QObject, Signal


class BacktestJobManager(QObject):
    """Tracks active backtest workers and their progress/results.

    Lifecycle:
      1. JS calls start_backtest → slot creates worker → start_job → returns job_id
      2. JS polls get_progress every 500ms → returns {status, current, total}
      3. Worker finishes → job_finished signal → JS calls get_result
      4. JS calls cleanup_backtest → removes cached result
    """

    job_finished = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._jobs = {}  # job_id -> {worker, progress, result, status}

    def start_job(self, worker, job_id=None):
        """Register and start a BacktestWorker. Returns job_id."""
        if job_id is None:
            job_id = uuid.uuid4().hex[:12]

        worker.progress.connect(
            lambda cur, tot, jid=job_id: self._on_progress(jid, cur, tot)
        )
        worker.finished.connect(
            lambda result, jid=job_id: self._on_finished(jid, result)
        )

        with self._lock:
            self._jobs[job_id] = {
                "worker": worker,
                "progress": (0, 1),
                "result": None,
                "status": "running",
            }

        worker.start()
        return job_id

    def cancel_job(self, job_id):
        """Request cancellation of a running job."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job and job["status"] == "running":
                job["worker"].cancel()
                job["status"] = "cancelling"

    def get_progress(self, job_id):
        """Return {status, current, total} dict for JS polling."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return {"status": "not_found", "current": 0, "total": 0}
            cur, tot = job["progress"]
            return {"status": job["status"], "current": cur, "total": tot}

    def get_result(self, job_id):
        """Return cached result dict, or None if not yet finished."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            return job.get("result")

    def cleanup_job(self, job_id):
        """Remove a finished job from tracking (free memory)."""
        with self._lock:
            if job_id in self._jobs:
                del self._jobs[job_id]

    def cancel_all(self):
        """Cancel all running jobs (e.g., on app shutdown)."""
        with self._lock:
            for job_id, job in list(self._jobs.items()):
                if job["status"] == "running":
                    try:
                        job["worker"].cancel()
                    except Exception:
                        pass

    # ── internal ──

    def _on_progress(self, job_id, current, total):
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job["progress"] = (current, total)

    def _on_finished(self, job_id, result):
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job["result"] = result
                job["status"] = "finished"
                job["progress"] = job["progress"][1], job["progress"][1]
        self.job_finished.emit(job_id)
