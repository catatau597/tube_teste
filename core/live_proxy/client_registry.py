from __future__ import annotations

import threading
import time
from typing import Dict, List

from .models import ClientSession


class ClientRegistry:
    """Tracks clients attached to one stream."""

    def __init__(self, video_id: str) -> None:
        self.video_id = video_id
        self._clients: Dict[str, ClientSession] = {}
        self._lock = threading.RLock()
        self._last_disconnect: float | None = None

    def add_client(self, client_id: str, ip: str, user_agent: str) -> None:
        now = time.time()
        with self._lock:
            self._clients[client_id] = ClientSession(
                client_id=client_id,
                ip=ip,
                user_agent=user_agent,
                connected_at=now,
                last_active=now,
            )

    def remove_client(self, client_id: str) -> int:
        with self._lock:
            self._clients.pop(client_id, None)
            remaining = len(self._clients)
            if remaining == 0:
                self._last_disconnect = time.time()
            return remaining

    def update_activity(self, client_id: str, bytes_sent: int, current_index: int) -> None:
        with self._lock:
            info = self._clients.get(client_id)
            if info is None:
                return
            info.last_active = time.time()
            info.bytes_sent = bytes_sent
            info.current_index = current_index
            info.stall_start = None
            info.late_since = None

    def mark_stall(self, client_id: str) -> None:
        with self._lock:
            info = self._clients.get(client_id)
            if info is not None and info.stall_start is None:
                info.stall_start = time.time()

    def mark_late(self, client_id: str) -> float:
        with self._lock:
            info = self._clients.get(client_id)
            if info is None:
                return 0.0
            now = time.time()
            if info.late_since is None:
                info.late_since = now
            return now - info.late_since

    def clear_late(self, client_id: str) -> None:
        with self._lock:
            info = self._clients.get(client_id)
            if info is not None:
                info.late_since = None

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._clients)

    @property
    def idle_since(self) -> float | None:
        with self._lock:
            if self._clients:
                return None
            return self._last_disconnect

    def snapshot(self) -> List[dict]:
        with self._lock:
            return [
                {
                    "client_id": c.client_id,
                    "ip": c.ip,
                    "connected_at": c.connected_at,
                    "bytes_sent": c.bytes_sent,
                }
                for c in self._clients.values()
            ]

    def debug_snapshot(self, buffer_index: int) -> List[dict]:
        now = time.time()
        with self._lock:
            result = []
            for c in self._clients.values():
                duration = max(0.001, now - c.connected_at)
                lag_chunks = max(0, buffer_index - c.current_index)
                stall_time = 0.0 if c.stall_start is None else max(0.0, now - c.stall_start)
                late_for = 0.0 if c.late_since is None else max(0.0, now - c.late_since)
                result.append(
                    {
                        "client_id": c.client_id,
                        "ip": c.ip,
                        "duration_s": round(duration, 1),
                        "bytes_sent": c.bytes_sent,
                        "kbps": round((c.bytes_sent / duration) / 1024, 1),
                        "current_index": c.current_index,
                        "lag_chunks": lag_chunks,
                        "is_stalled": c.stall_start is not None,
                        "stall_time_s": round(stall_time, 1),
                        "late_for_s": round(late_for, 1),
                    }
                )
            return result
