from __future__ import annotations

import time
from pathlib import Path

from applovin_mcp.audit import AuditLog
from applovin_mcp.credentials import register_secret

from .conftest import audit_entries


def test_record_redacts_keys_and_signed_urls(tmp_path: Path) -> None:
    register_secret("super-secret-key-123")
    log = AuditLog(tmp_path / "a.jsonl")
    log.record(
        op="x",
        api="ads",
        method="POST",
        path="/x",
        request_body={
            "note": "uses super-secret-key-123",
            "api_key": "abc",
            "link": "https://s3.amazonaws.com/f.csv?X-Amz-Signature=deadbeef&b=1",
        },
    )
    [entry] = audit_entries(log.path)
    body = entry["request_body"]
    assert body["api_key"] == "***"
    assert "super-secret-key-123" not in body["note"]
    assert "deadbeef" not in body["link"]


def test_find_and_tail(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "a.jsonl")
    ids = [log.record(op=f"op{i}", api="max", method="POST", path="/r") for i in range(3)]
    found = log.find(ids[1])
    assert found and found["op"] == "op1"
    assert [e["op"] for e in log.tail(2)] == ["op1", "op2"]
    assert log.find("nope") is None


def test_prune_drops_entries_past_retention(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "a.jsonl", retention_days=30)
    log.record(op="old", api="ads", method="POST", path="/r")
    later = time.time() + 31 * 86400
    assert log.prune(now=later) == 1
    assert audit_entries(log.path) == []
    log.record(op="fresh", api="ads", method="POST", path="/r")
    assert log.prune() == 0
    assert [e["op"] for e in audit_entries(log.path)] == ["fresh"]


def test_zero_retention_keeps_everything(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "a.jsonl", retention_days=0)
    log.record(op="x", api="ads", method="POST", path="/r")
    assert log.prune(now=time.time() + 10_000 * 86400) == 0


def test_purge(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "a.jsonl")
    log.record(op="x", api="ads", method="POST", path="/r")
    assert log.purge() is True
    assert not log.path.exists()
    assert log.purge() is False
