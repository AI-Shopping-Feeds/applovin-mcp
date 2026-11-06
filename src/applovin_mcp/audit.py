from __future__ import annotations

import contextlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .credentials import ensure_private_dir, redact

DAY = 86400


class AuditLog:
    """Append-only JSONL log of write operations.

    Holds only what is needed to review or undo a change: the write request, a
    before-snapshot of the entities it touched, and the write response. Read
    responses and report rows are never written here. Entries older than
    `retention_days` are pruned (see `prune`); 0 keeps them indefinitely.
    Keys and signed URLs are redacted before anything is written. The folder is
    0700 and the file 0600.
    """

    def __init__(self, path: Path, retention_days: int = 30):
        self.path = path
        self.retention_days = retention_days
        self._lock = threading.Lock()

    def _open_append(self) -> Any:
        ensure_private_dir(self.path.parent)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)
        return os.fdopen(fd, "a", encoding="utf-8")

    def record(
        self,
        *,
        op: str,
        api: str,
        method: str,
        path: str,
        account_id: str | None = None,
        request_body: Any | None = None,
        response_status: int | None = None,
        response_body: Any | None = None,
        before: Any | None = None,
        dry_run: bool = False,
        error: str | None = None,
    ) -> str:
        audit_id = uuid.uuid4().hex
        entry = {
            "audit_id": audit_id,
            "ts": time.time(),
            "op": op,
            "api": api,
            "method": method,
            "path": path,
            "account_id": account_id,
            "request_body": request_body,
            "response_status": response_status,
            "response_body": response_body,
            "before": before,
            "dry_run": dry_run,
            "error": error,
        }
        line = json.dumps(redact(entry), ensure_ascii=False, default=str)
        with self._lock, self._open_append() as f:
            f.write(line + "\n")
        return audit_id

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
        return out

    def find(self, audit_id: str) -> dict[str, Any] | None:
        for entry in self.entries():
            if entry.get("audit_id") == audit_id:
                return entry
        return None

    def tail(self, limit: int) -> list[dict[str, Any]]:
        return self.entries()[-max(0, limit) :] if limit > 0 else []

    def prune(self, now: float | None = None) -> int:
        """Drop entries older than the retention window. Returns how many were removed."""
        if self.retention_days <= 0 or not self.path.exists():
            return 0
        cutoff = (now if now is not None else time.time()) - self.retention_days * DAY
        with self._lock:
            entries = self.entries()
            keep = [e for e in entries if float(e.get("ts", 0)) >= cutoff]
            removed = len(entries) - len(keep)
            if removed:
                tmp = self.path.with_suffix(self.path.suffix + ".tmp")
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    for e in keep:
                        f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")
                tmp.replace(self.path)
        return removed

    def purge(self) -> bool:
        """Delete the whole log. Returns True if a file was removed."""
        with self._lock:
            if self.path.exists():
                self.path.unlink()
                return True
        return False
