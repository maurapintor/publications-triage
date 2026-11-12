"""Single background job running the pipeline, with progress and cancellation."""
from __future__ import annotations

import logging
import threading
import traceback
from collections import deque
from datetime import datetime, timezone

IDLE, RUNNING, DONE, FAILED, CANCELLED = "idle", "running", "done", "failed", "cancelled"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _LogTail(logging.Handler):
    def __init__(self, sink: deque, on_phase=None):
        super().__init__(logging.INFO)
        self.sink = sink
        self.on_phase = on_phase

    def emit(self, record):
        msg = record.getMessage()
        self.sink.append(f"{record.levelname} {msg}")
        if self.on_phase and msg.startswith("IRIS:"):
            self.on_phase(msg)


class JobManager:
    """run_fn(options, progress(i, n, title), should_stop() -> bool) -> summary dict."""

    def __init__(self, run_fn):
        self.run_fn = run_fn
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.log: deque[str] = deque(maxlen=50)
        self._state = {"state": IDLE, "i": 0, "n": 0, "current": "", "error": None,
                       "started_at": None, "finished_at": None, "summary": None, "options": None}

    @property
    def running(self) -> bool:
        return self._state["state"] == RUNNING

    def start(self, options: dict) -> bool:
        with self._lock:
            if self.running:
                return False
            self._stop.clear()
            self.log.clear()
            self._state.update(state=RUNNING, i=0, n=0, current="", error=None, started_at=_now(),
                               finished_at=None, summary=None, options=options)
            self._thread = threading.Thread(target=self._work, args=(options,), daemon=True, name="pubtriage-run")
            self._thread.start()
            return True

    def cancel(self) -> bool:
        if not self.running:
            return False
        self._stop.set()
        return True

    def wait(self, timeout=None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def status(self) -> dict:
        with self._lock:
            return {**self._state, "log": list(self.log)}

    def _phase(self, msg):
        with self._lock:
            if not self._state["n"]:  # before the first paper: show what is happening (e.g. IRIS harvest)
                self._state["current"] = msg

    def _progress(self, i, n, title):
        with self._lock:
            self._state.update(i=i, n=n, current=title)

    def _work(self, options):
        handler = _LogTail(self.log, self._phase)
        logger = logging.getLogger("triage")
        logger.addHandler(handler)
        if logger.level == logging.NOTSET or logger.level > logging.INFO:
            logger.setLevel(logging.INFO)
        try:
            summary = self.run_fn(options, self._progress, self._stop.is_set)
            final = CANCELLED if self._stop.is_set() else DONE
            with self._lock:
                self._state.update(state=final, summary=summary, finished_at=_now())
        except Exception as exc:  # noqa: BLE001 - report any failure to the UI
            self.log.append(traceback.format_exc(limit=3))
            with self._lock:
                self._state.update(state=FAILED, error=f"{type(exc).__name__}: {exc}", finished_at=_now())
        finally:
            logger.removeHandler(handler)
