"""Imports that outlive their request: each one is written to disk the moment it is asked for, before any work starts.

An import used to exist only as a running job. A restart, a crash or a failed game lost it: nothing said what was
being imported, with which answers, or what was already done, so the user started over (a folder of fifty games
too). Now `<workspace>/.decomp/imports/<id>/import.json` holds everything needed to resume, cancel or just look:
what was picked, the answers given (compiler, scope, options, layout), how far a folder got, and the log so far.
The page opens it at once (`#/import/<id>`) and follows its diagnostics, then moves to the project when it is ready.

The project's own row and folder appear when its split completes: the rest of the app (agents, autopilot, rooms,
the map) assumes a project has functions, so a half-made one must not be visible to it.

States: running, cancelling, failed, cancelled, interrupted (the app ended while it ran), done.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path

LOG_KEPT = 400  # lines of the log kept in the journal (the log file has all of them)
FLUSH_EVERY = 1.0  # seconds between saves of a streaming log: a big import logs thousands of lines
ACTIVE = ("running", "cancelling")
RESUMABLE = ("failed", "interrupted")
FILE = "import.json"


class Imports:
    def __init__(self, workspace: Path):
        self.root = workspace / ".decomp" / "imports"
        self._lock = threading.RLock()
        self._pending: dict[str, float] = {}  # id -> when its log was last saved
        self._buffer: dict[str, list[str]] = {}  # id -> log lines not saved yet

    # ------------------------------------------------------------------ files

    def _file(self, iid: str) -> Path:
        return self.root / iid / FILE

    def _read(self, iid: str) -> dict | None:
        try:
            data = json.loads(self._file(iid).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _write(self, data: dict) -> None:
        path = self._file(data["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(FILE + ".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)  # whole or not at all: a crash mid-save never leaves half a journal

    # ------------------------------------------------------------------ the journal

    def create(self, *, title: str, kind: str, source: str, params: dict, uploaded: bool = False) -> dict:
        """Record an import before it starts. `kind`: "file", "folder" or "demo"; `source`: the path the user picked
        (or the saved upload); `params`: the answers needed to run it again."""
        now = time.time()
        data = {"id": uuid.uuid4().hex[:10], "title": title, "kind": kind, "source": source, "params": params,
                "uploaded": uploaded, "state": "running", "error": None, "log": [], "job": None, "parent": None,
                "done": {}, "failures": [], "slug": None, "projects": [], "created": now, "updated": now, "runs": 0}
        with self._lock:
            self._write(data)
        return data

    def get(self, iid: str) -> dict | None:
        with self._lock:
            return self._read(iid) if iid and iid.isalnum() else None

    def all(self) -> list[dict]:
        with self._lock:
            found = [self._read(p.name) for p in sorted(self.root.iterdir())] if self.root.is_dir() else []
        return sorted((d for d in found if d), key=lambda d: d["created"], reverse=True)

    def pending(self) -> list[dict]:
        """What the sidebar lists: imports not finished (done ones have a project to show instead)."""
        return [d for d in self.all() if d["state"] != "done"]

    def update(self, iid: str, **fields) -> dict | None:
        with self._lock:
            data = self._read(iid)
            if data is None:
                return None
            data.update(fields)
            data["updated"] = time.time()
            self._write(data)
            return data

    def log(self, iid: str, line: str) -> None:
        """Keep the newest lines. Saved at most once a second (and by every state change)."""
        with self._lock:
            data = self._read(iid) if time.time() - self._pending.get(iid, 0) >= FLUSH_EVERY else None
            if data is None:
                self._buffer.setdefault(iid, []).append(str(line))
                return
            data["log"] = (data["log"] + self._buffer.pop(iid, []) + [str(line)])[-LOG_KEPT:]
            data["updated"] = self._pending[iid] = time.time()
            self._write(data)

    def flush(self, iid: str) -> None:
        with self._lock:
            lines = self._buffer.pop(iid, [])
            data = self._read(iid)
            if data is not None and lines:
                data["log"] = (data["log"] + lines)[-LOG_KEPT:]
                self._write(data)

    def begin_run(self, iid: str, job: str | None = None) -> dict | None:
        """A run starts (the first, or a resume): the error of the last one is gone, its log stays."""
        with self._lock:
            data = self._read(iid)
            if data is None:
                return None
            data.update(state="running", error=None, job=job, runs=data["runs"] + 1, updated=time.time())
            self._write(data)
            return data

    def finish(self, iid: str, state: str, **fields) -> dict | None:
        self.flush(iid)
        return self.update(iid, state=state, **fields)

    def interrupt_running(self) -> None:
        """At start: an import the last run of the app left running is `interrupted`, and can be resumed. Finished
        ones are swept: their project is the record now."""
        for data in self.all():
            if data["state"] in ACTIVE:
                self.update(data["id"], state="interrupted", error="decompdecomp closed while this was running.")
            elif data["state"] == "done":
                self.discard(data["id"])

    def drop_upload(self, iid: str) -> None:
        """The copy of an uploaded game was only kept to resume from."""
        data = self.get(iid)
        if data and data.get("uploaded") and data.get("source"):
            upload = Path(data["source"]).parent
            if upload.parent.name == "uploads":  # (only what the app itself saved there)
                shutil.rmtree(upload, ignore_errors=True)

    def discard(self, iid: str) -> None:
        """Forget an import and its leftovers: the projects a folder import made stay (they are real projects)."""
        with self._lock:
            self.drop_upload(iid)
            self._buffer.pop(iid, None)
            self._pending.pop(iid, None)
            shutil.rmtree(self.root / iid, ignore_errors=True)
