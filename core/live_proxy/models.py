from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ClientSession:
    client_id: str
    ip: str
    user_agent: str
    connected_at: float
    last_active: float
    bytes_sent: int = 0
    current_index: int = 0
    stall_start: float | None = None
    late_since: float | None = None


@dataclass(frozen=True)
class LiveProxyHandle:
    video_id: str
    ingress_type: str
    process_pid: int | None
    started_at: float


@dataclass
class ManagedStreamState:
    video_id: str
    ingress_type: str
    ingress_cmd: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)
