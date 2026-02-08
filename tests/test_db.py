"""Tests for agent_gate.db — SQLite storage for audit log and pending requests."""

import os
import platform
import stat
import time
from datetime import UTC, datetime

import pytest

from agent_gate.db import Database
from agent_gate.models import AuditEntry


@pytest.fixture()
async def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    database = Database(db_path)
    await database.initialize()
    yield database
    await database.close()


class TestInitialize:
    async def test_creates_tables(self, db):
        # Verify tables exist by querying sqlite_master
        conn = db._get_conn()
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in await cursor.fetchall()]
        assert "audit_log" in tables
        assert "pending_requests" in tables

    async def test_creates_indexes(self, db):
        conn = db._get_conn()
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        )
        indexes = [row[0] for row in await cursor.fetchall()]
        assert "idx_audit_timestamp" in indexes
        assert "idx_audit_tool" in indexes
        assert "idx_pending_expires" in indexes

    @pytest.mark.skipif(platform.system() == "Windows", reason="Unix permissions")
    async def test_file_permissions_0600(self, tmp_path):
        db_path = str(tmp_path / "perms.db")
        database = Database(db_path)
        await database.initialize()
        mode = stat.S_IMODE(os.stat(db_path).st_mode)
        assert mode == 0o600
        await database.close()


class TestAuditLog:
    async def test_log_and_query(self, db):
        entry = AuditEntry(
            request_id="req-1",
            tool_name="ha_get_state",
            args={"entity_id": "sensor.temp"},
            signature="ha_get_state(sensor.temp)",
            decision="allow",
            resolution="executed",
            resolved_by="policy",
        )
        await db.log_audit(entry)

        entries = await db.get_audit_log()
        assert len(entries) == 1
        assert isinstance(entries[0], AuditEntry)
        assert entries[0].request_id == "req-1"
        assert entries[0].tool_name == "ha_get_state"
        assert entries[0].decision == "allow"
        assert entries[0].resolution == "executed"
        assert entries[0].resolved_by == "policy"
        assert entries[0].args == {"entity_id": "sensor.temp"}
        assert entries[0].signature == "ha_get_state(sensor.temp)"

    async def test_timestamp_round_trips(self, db):
        now = time.time()
        entry = AuditEntry(request_id="req-1", timestamp=now, tool_name="test", decision="allow")
        await db.log_audit(entry)

        entries = await db.get_audit_log()
        assert isinstance(entries[0], AuditEntry)
        # Timestamps should be close (within 1 second due to ISO truncation)
        assert abs(entries[0].timestamp - now) < 1.0

    async def test_args_round_trip(self, db):
        args = {"entity_id": "sensor.temp", "extra": "val"}
        entry = AuditEntry(request_id="req-1", args=args, decision="allow")
        await db.log_audit(entry)

        entries = await db.get_audit_log()
        assert isinstance(entries[0], AuditEntry)
        assert entries[0].args == args

    async def test_reverse_chronological_order(self, db):
        for i in range(3):
            entry = AuditEntry(request_id=f"req-{i}", decision="allow")
            await db.log_audit(entry)

        entries = await db.get_audit_log()
        ids = [e.request_id for e in entries]
        assert ids == ["req-2", "req-1", "req-0"]

    async def test_limit(self, db):
        for i in range(5):
            entry = AuditEntry(request_id=f"req-{i}", decision="allow")
            await db.log_audit(entry)

        entries = await db.get_audit_log(limit=2)
        assert len(entries) == 2

    async def test_empty_audit_log(self, db):
        entries = await db.get_audit_log()
        assert entries == []

    async def test_execution_result_round_trip(self, db):
        result = {"state": "on", "attributes": {"brightness": 255}}
        entry = AuditEntry(
            request_id="req-1",
            tool_name="ha_call_service",
            decision="allow",
            execution_result=result,
        )
        await db.log_audit(entry)

        entries = await db.get_audit_log()
        assert isinstance(entries[0], AuditEntry)
        assert entries[0].execution_result == result

    async def test_resolved_at_round_trip(self, db):
        now = time.time()
        entry = AuditEntry(
            request_id="req-1",
            decision="allow",
            resolved_at=now,
        )
        await db.log_audit(entry)

        entries = await db.get_audit_log()
        assert isinstance(entries[0], AuditEntry)
        assert entries[0].resolved_at is not None
        assert abs(entries[0].resolved_at - now) < 1.0


class TestPendingRequests:
    async def test_insert_and_get(self, db):
        expires = datetime.now(UTC).isoformat()
        await db.insert_pending(
            request_id="req-1",
            tool_name="ha_call_service",
            args={"domain": "light"},
            signature="ha_call_service(light.turn_on, light.bedroom)",
            expires_at=expires,
        )
        pending = await db.get_pending("req-1")
        assert pending is not None
        assert pending["request_id"] == "req-1"
        assert pending["tool_name"] == "ha_call_service"
        assert pending["signature"] == "ha_call_service(light.turn_on, light.bedroom)"

    async def test_get_missing_returns_none(self, db):
        result = await db.get_pending("nonexistent")
        assert result is None

    async def test_delete(self, db):
        expires = datetime.now(UTC).isoformat()
        await db.insert_pending("req-1", "test", {}, "test", expires)
        await db.delete_pending("req-1")
        assert await db.get_pending("req-1") is None

    async def test_cleanup_stale_removes_expired(self, db):
        # Insert an expired request (expires_at in the past)
        past = "2020-01-01T00:00:00Z"
        await db.insert_pending("req-old", "test", {}, "test", past)

        # Insert a fresh request
        future = "2099-01-01T00:00:00Z"
        await db.insert_pending("req-new", "test", {}, "test", future)

        stale = await db.cleanup_stale_requests()
        assert len(stale) == 1
        assert stale[0]["request_id"] == "req-old"

        # Old one removed, new one remains
        assert await db.get_pending("req-old") is None
        assert await db.get_pending("req-new") is not None

    async def test_cleanup_stale_no_expired(self, db):
        future = "2099-01-01T00:00:00Z"
        await db.insert_pending("req-1", "test", {}, "test", future)
        stale = await db.cleanup_stale_requests()
        assert stale == []
