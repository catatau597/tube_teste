from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import List, Tuple

from .config import LiveProxyConfig


@dataclass
class StreamBuffer:
    """In-memory circular buffer for TS chunks."""

    video_id: str
    config: LiveProxyConfig
    chunks: deque[bytes] = field(init=False)
    index: int = 0
    active: bool = True
    created_at: float = field(default_factory=time.time)
    last_chunk_at: float = field(default_factory=time.time)
    total_bytes: int = 0
    produced_bytes: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self.chunks = deque(maxlen=self.config.buffer_maxlen)

    def mark_active(self) -> None:
        with self._lock:
            self.active = True
            self.created_at = time.time()

    def mark_inactive(self) -> None:
        with self._lock:
            self.active = False

    def add_chunk(self, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            evicted = len(self.chunks[0]) if len(self.chunks) == self.chunks.maxlen else 0
            self.chunks.append(data)
            self.index += 1
            self.total_bytes += len(data) - evicted
            self.produced_bytes += len(data)
            self.last_chunk_at = time.time()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self.chunks)

    @property
    def size_bytes(self) -> int:
        with self._lock:
            return self.total_bytes

    @property
    def produced_bytes_total(self) -> int:
        with self._lock:
            return self.produced_bytes

    def latest_safe_index(self, behind_bytes: int | None = None) -> int:
        target = behind_bytes if behind_bytes is not None else self.config.initial_behind_bytes
        with self._lock:
            if not self.chunks:
                return self.index
            accumulated = 0
            steps_back = 0
            for chunk in reversed(self.chunks):
                accumulated += len(chunk)
                if accumulated >= target:
                    break
                steps_back += 1
            return max(0, self.index - steps_back - 1)

    def ready_for_clients(self, min_bytes: int | None = None) -> bool:
        threshold = min_bytes if min_bytes is not None else self.config.live_preroll_bytes
        with self._lock:
            return self.total_bytes >= threshold or not self.active

    def bytes_behind(self, start_index: int) -> int:
        with self._lock:
            if not self.chunks:
                return 0
            buffer_start = self.index - len(self.chunks)
            if start_index < buffer_start:
                start_index = buffer_start
            offset = start_index - buffer_start
            if offset < 0 or offset >= len(self.chunks):
                return 0
            return sum(len(chunk) for chunk in list(self.chunks)[offset:])

    def get_optimized_client_data(
        self,
        start_index: int,
        *,
        target_batch_bytes: int | None = None,
        max_batch_bytes: int | None = None,
    ) -> Tuple[List[bytes], int]:
        """Adaptive fetch policy based on client lag."""
        cfg = self.config
        max_batch = max_batch_bytes if max_batch_bytes is not None else cfg.max_batch_bytes
        with self._lock:
            if not self.chunks:
                return [], start_index

            buffer_start = self.index - len(self.chunks)
            if start_index < buffer_start:
                start_index = buffer_start

            chunks_behind = self.index - start_index
            if chunks_behind <= 0:
                return [], start_index

            chunks_list = list(self.chunks)
            offset = start_index - buffer_start
            if offset < 0 or offset >= len(chunks_list):
                return [], start_index

            bytes_behind = sum(len(chunk) for chunk in chunks_list[offset:])
            if bytes_behind <= 0:
                return [], start_index

            if bytes_behind <= 512 * 1024:
                count = chunks_behind
                target = min(target_batch_bytes or 512 * 1024, max_batch)
            elif bytes_behind <= cfg.initial_behind_bytes:
                count = min(chunks_behind, 8)
                target = min(target_batch_bytes or 1024 * 1024, max_batch)
            elif bytes_behind <= cfg.client_jump_threshold_bytes // 2:
                count = min(chunks_behind, 24)
                target = min(target_batch_bytes or cfg.target_batch_bytes, max_batch)
            else:
                count = min(chunks_behind, cfg.max_batch_chunks)
                target = max_batch

            selected: List[bytes] = []
            total = 0
            for chunk in chunks_list[offset:]:
                if len(selected) >= count:
                    break
                selected.append(chunk)
                total += len(chunk)
                if len(selected) >= cfg.min_batch_chunks and total >= target:
                    break
                if total >= max_batch:
                    break

            return selected, start_index + len(selected)
