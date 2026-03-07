from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LiveProxyConfig:
    """Runtime knobs for the live proxy core."""

    chunk_size: int = 65536
    buffer_maxlen: int = 600

    # Client policy (derived from Dispatcharr behavior and current proxy defaults)
    initial_behind_bytes: int = 512 * 1024
    client_jump_threshold_bytes: int = 4 * 1024 * 1024
    min_batch_chunks: int = 4
    max_batch_chunks: int = 24
    target_batch_bytes: int = 256 * 1024
    max_batch_bytes: int = 768 * 1024
    live_preroll_bytes: int = 512 * 1024
    live_preroll_wait_s: float = 4.0
    client_timeout_s: float = 30.0

    # Stream lifecycle
    init_timeout_s: float = 15.0
    stream_idle_stop_s: float = 30.0
