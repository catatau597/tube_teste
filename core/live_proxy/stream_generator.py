from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, AsyncIterator

if TYPE_CHECKING:
    from .stream_manager import LiveProxyManager


class StreamGenerator:
    """Per-client async stream iterator inspired by Dispatcharr stream_generator."""

    def __init__(
        self,
        *,
        manager: "LiveProxyManager",
        video_id: str,
        client_id: str,
        client_ip: str,
        client_user_agent: str,
    ) -> None:
        self.manager = manager
        self.video_id = video_id
        self.client_id = client_id
        self.client_ip = client_ip
        self.client_user_agent = client_user_agent
        self.logger = logging.getLogger("TubeWrangler.live_proxy.generator")

    async def generate(self) -> AsyncIterator[bytes]:
        stream = self.manager.get_stream(self.video_id)
        if stream is None:
            return

        cfg = self.manager.config
        registered = False
        local_index = stream.buffer.latest_safe_index(cfg.initial_behind_bytes)
        bytes_sent = 0
        last_yield_time = time.monotonic()
        consecutive_empty = 0

        try:
            while True:
                if not self.manager.stream_exists(self.video_id):
                    break

                self.manager.restart_placeholder_if_needed(self.video_id)
                chunks, next_index = stream.buffer.get_optimized_client_data(
                    local_index,
                    target_batch_bytes=cfg.target_batch_bytes,
                    max_batch_bytes=cfg.max_batch_bytes,
                )

                if chunks:
                    if not registered:
                        stream.clients.add_client(self.client_id, self.client_ip, self.client_user_agent)
                        registered = True
                    payload = b"".join(chunks) if len(chunks) > 1 else chunks[0]
                    yield payload
                    bytes_sent += len(payload)
                    local_index = next_index
                    last_yield_time = time.monotonic()
                    consecutive_empty = 0
                    stream.clients.update_activity(self.client_id, bytes_sent, local_index)
                    continue

                consecutive_empty += 1
                await asyncio.sleep(min(0.05 * consecutive_empty, 1.0))

                if time.monotonic() - last_yield_time > cfg.client_timeout_s:
                    stream.clients.mark_stall(self.client_id)
                    break

                lag = stream.buffer.bytes_behind(local_index)
                if lag > cfg.client_jump_threshold_bytes:
                    late_for = stream.clients.mark_late(self.client_id)
                    if late_for < 2.0:
                        continue
                    local_index = stream.buffer.latest_safe_index(cfg.initial_behind_bytes)
                    consecutive_empty = 0
                else:
                    stream.clients.clear_late(self.client_id)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.logger.error(
                "[%s][%s] generator error: %s",
                self.video_id,
                self.client_id,
                exc,
            )
        finally:
            if registered:
                remaining = stream.clients.remove_client(self.client_id)
                if remaining == 0:
                    self.manager.schedule_idle_stop(self.video_id)
