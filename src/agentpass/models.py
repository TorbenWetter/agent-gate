"""Shared data models for agentpass."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Decision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class ToolRequest:
    """Incoming tool request from an agent."""

    id: str
    tool_name: str
    args: dict[str, Any]
    signature: str = ""


@dataclass
class ToolResult:
    """Result of an executed tool request."""

    request_id: str
    status: str  # "executed" or "denied"
    data: dict[str, Any] | None = None


@dataclass
class PendingApproval:
    """A tool request awaiting human approval."""

    request: ToolRequest
    future: asyncio.Future
    message_id: str | None = None
    timeout_task: asyncio.Task | None = None
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0


@dataclass
class AuditEntry:
    """A record of a tool request and its outcome."""

    request_id: str
    timestamp: float = field(default_factory=time.time)
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    signature: str = ""
    decision: str = ""
    resolution: str | None = None
    resolved_by: str | None = None
    resolved_at: float | None = None
    execution_result: dict[str, Any] | None = None
    agent_id: str = "default"
