from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from .client_registry import ClientRegistry
from .config import LiveProxyConfig
from .models import LiveProxyHandle, ManagedStreamState
from .stream_buffer import StreamBuffer
from .stream_generator import StreamGenerator


@dataclass
class _ManagedStream:
    state: ManagedStreamState
    buffer: StreamBuffer
    clients: ClientRegistry
    process: subprocess.Popen | None = None
    start_time: float = field(default_factory=time.time)
    placeholder_cmd: list[str] | None = None


class LiveProxyManager:
    """New live proxy core, adapted from Dispatcharr ts_proxy architecture."""

    def __init__(self, config: LiveProxyConfig | None = None) -> None:
        self.config = config or LiveProxyConfig()
        self.logger = logging.getLogger("TubeWrangler.live_proxy")
        self._streams: dict[str, _ManagedStream] = {}
        self._start_locks: dict[str, asyncio.Lock] = {}
        self._idle_tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = threading.RLock()

    async def ensure_stream(
        self,
        *,
        video_id: str,
        ingress_plan: Any,
        metadata: dict[str, Any] | None = None,
    ) -> LiveProxyHandle:
        """Start stream process if needed and return a shared handle."""
        lock = self._start_locks.setdefault(video_id, asyncio.Lock())
        async with lock:
            existing = self.get_stream(video_id)
            if existing and self._is_process_alive(existing.process):
                return self._to_handle(existing)

            if existing:
                self.stop_stream(video_id)

            cmd = list(getattr(ingress_plan, "cmd", []) or [])
            kind = str(getattr(ingress_plan, "kind", "unknown"))
            if not cmd:
                raise ValueError(f"ingress command is empty for video_id={video_id}")

            stream = _ManagedStream(
                state=ManagedStreamState(
                    video_id=video_id,
                    ingress_type=kind,
                    ingress_cmd=cmd,
                    metadata=dict(metadata or {}),
                ),
                buffer=StreamBuffer(video_id=video_id, config=self.config),
                clients=ClientRegistry(video_id=video_id),
                placeholder_cmd=cmd if kind == "placeholder" else None,
            )
            with self._lock:
                self._streams[video_id] = stream

            self._spawn_process(stream, cmd)
            await self._wait_first_chunk_or_timeout(video_id)
            await self._wait_preroll_if_live(video_id, kind)
            return self._to_handle(stream)

    def attach_client(
        self,
        *,
        video_id: str,
        client_id: str,
        ip: str,
        user_agent: str,
    ) -> AsyncIterator[bytes]:
        stream = self.get_stream(video_id)
        if stream is None:
            raise KeyError(f"stream not found: {video_id}")
        generator = StreamGenerator(
            manager=self,
            video_id=video_id,
            client_id=client_id,
            client_ip=ip,
            client_user_agent=user_agent,
        )
        return generator.generate()

    def detach_client(self, *, video_id: str, client_id: str) -> None:
        stream = self.get_stream(video_id)
        if stream is None:
            return
        stream.clients.remove_client(client_id)

    def stream_exists(self, video_id: str) -> bool:
        with self._lock:
            return video_id in self._streams

    def is_stream_active(self, video_id: str) -> bool:
        stream = self.get_stream(video_id)
        if stream is None:
            return False
        return self._is_process_alive(stream.process)

    def get_stream(self, video_id: str) -> _ManagedStream | None:
        with self._lock:
            return self._streams.get(video_id)

    def stop_stream(self, video_id: str) -> bool:
        with self._lock:
            stream = self._streams.pop(video_id, None)
            idle_task = self._idle_tasks.pop(video_id, None)
        if idle_task is not None:
            idle_task.cancel()
        if stream is None:
            return False

        proc = stream.process
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=5)
            except Exception as exc:
                self.logger.warning("[%s] failed terminating process: %s", video_id, exc)
        stream.buffer.mark_inactive()
        return True

    def schedule_idle_stop(self, video_id: str) -> None:
        existing = self._idle_tasks.get(video_id)
        if existing is not None and not existing.done():
            return

        async def _idle_stop() -> None:
            await asyncio.sleep(self.config.stream_idle_stop_s)
            stream = self.get_stream(video_id)
            if stream is None:
                return
            if stream.clients.count == 0:
                self.stop_stream(video_id)

        self._idle_tasks[video_id] = asyncio.create_task(_idle_stop())

    def restart_placeholder_if_needed(self, video_id: str) -> bool:
        stream = self.get_stream(video_id)
        if stream is None or not stream.placeholder_cmd:
            return False
        if stream.clients.count == 0:
            return False
        if self._is_process_alive(stream.process):
            return False
        self._spawn_process(stream, stream.placeholder_cmd)
        return True

    def snapshot(self, video_id: str | None = None) -> dict | list[dict]:
        if video_id is not None:
            stream = self.get_stream(video_id)
            return {} if stream is None else self._snapshot_stream(stream)
        with self._lock:
            streams = list(self._streams.values())
        return [self._snapshot_stream(stream) for stream in streams]

    def debug_snapshot(self, video_id: str) -> dict | None:
        stream = self.get_stream(video_id)
        if stream is None:
            return None

        now = time.time()
        proc = stream.process
        process_alive = self._is_process_alive(proc)
        start_time = stream.start_time
        clients = stream.clients.debug_snapshot(stream.buffer.index)
        for client in clients:
            lag_bytes = stream.buffer.bytes_behind(client.get("current_index", 0))
            client["lag_bytes"] = lag_bytes
            client["lag_mb"] = round(lag_bytes / 1024 / 1024, 2)

        return {
            "video_id": stream.state.video_id,
            "ingress_type": stream.state.ingress_type,
            "process": {
                "pid": proc.pid if proc else None,
                "alive": process_alive,
                "returncode": proc.returncode if proc and not process_alive else None,
                "uptime_s": round(now - start_time, 1),
            },
            "buffer": {
                "chunks_total": stream.buffer.index,
                "chunks_in_buffer": stream.buffer.size,
                "produced_bytes_total": stream.buffer.produced_bytes_total,
                "buffer_bytes": stream.buffer.size_bytes,
                "buffer_mb": round(stream.buffer.size_bytes / 1024 / 1024, 2),
                "time_since_last_chunk_s": round(now - stream.buffer.last_chunk_at, 1),
                "is_active": stream.buffer.active,
            },
            "clients_count": stream.clients.count,
            "clients": clients,
        }

    def shutdown(self) -> None:
        with self._lock:
            video_ids = list(self._streams.keys())
        for video_id in video_ids:
            self.stop_stream(video_id)

    def _snapshot_stream(self, stream: _ManagedStream) -> dict:
        proc = stream.process
        return {
            "type": "live",
            "video_id": stream.state.video_id,
            "ingress_type": stream.state.ingress_type,
            "buffer_chunks": stream.buffer.size,
            "buffer_index": stream.buffer.index,
            "buffer_bytes": stream.buffer.size_bytes,
            "buffer_mb": round(stream.buffer.size_bytes / 1024 / 1024, 2),
            "clients": stream.clients.count,
            "clients_info": stream.clients.snapshot(),
            "process_alive": self._is_process_alive(proc),
            "process_pid": proc.pid if proc else None,
            "uptime": round(time.time() - stream.start_time, 1),
            "is_placeholder": stream.placeholder_cmd is not None,
        }

    async def _wait_first_chunk_or_timeout(self, video_id: str) -> None:
        deadline = time.monotonic() + self.config.init_timeout_s
        while time.monotonic() < deadline:
            stream = self.get_stream(video_id)
            if stream is None:
                raise RuntimeError(f"stream disappeared while initializing: {video_id}")
            if stream.buffer.index > 0:
                return
            proc = stream.process
            if proc is not None and proc.poll() is not None:
                raise RuntimeError(f"ingest exited early with rc={proc.returncode}")
            await asyncio.sleep(0.1)
        self.stop_stream(video_id)
        raise TimeoutError(f"timeout waiting first chunk for {video_id}")

    async def _wait_preroll_if_live(self, video_id: str, ingress_type: str) -> None:
        if ingress_type != "live_hls" and ingress_type != "live_streamlink" and ingress_type != "live":
            return
        deadline = time.monotonic() + self.config.live_preroll_wait_s
        while time.monotonic() < deadline:
            stream = self.get_stream(video_id)
            if stream is None:
                return
            if stream.buffer.ready_for_clients(self.config.live_preroll_bytes):
                return
            await asyncio.sleep(0.1)

    def _spawn_process(self, stream: _ManagedStream, cmd: list[str]) -> None:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        stream.process = process
        stream.start_time = time.time()
        stream.buffer.mark_active()

        video_id = stream.state.video_id
        self.logger.info("[%s] live proxy process started pid=%s", video_id, process.pid)

        def _read_stdout() -> None:
            try:
                assert process.stdout is not None
                while True:
                    chunk = process.stdout.read(self.config.chunk_size)
                    if not chunk:
                        break
                    stream.buffer.add_chunk(chunk)
            except Exception as exc:
                self.logger.error("[%s] stdout reader error: %s", video_id, exc)
            finally:
                stream.buffer.mark_inactive()

        def _read_stderr() -> None:
            try:
                assert process.stderr is not None
                for raw in process.stderr:
                    line = raw.decode("utf-8", errors="replace").rstrip()
                    if not line:
                        continue
                    low = line.lower()
                    if any(kw in low for kw in ("error", "fail", "fatal", "invalid", "unable")):
                        self.logger.warning("[%s] stderr: %s", video_id, line)
                    else:
                        self.logger.info("[%s] stderr: %s", video_id, line)
            except Exception as exc:
                self.logger.debug("[%s] stderr reader stopped: %s", video_id, exc)

        threading.Thread(target=_read_stdout, daemon=True, name=f"liveproxy-stdout-{video_id}").start()
        threading.Thread(target=_read_stderr, daemon=True, name=f"liveproxy-stderr-{video_id}").start()

    @staticmethod
    def _is_process_alive(process: subprocess.Popen | None) -> bool:
        return process is not None and process.poll() is None

    @staticmethod
    def _to_handle(stream: _ManagedStream) -> LiveProxyHandle:
        proc = stream.process
        return LiveProxyHandle(
            video_id=stream.state.video_id,
            ingress_type=stream.state.ingress_type,
            process_pid=proc.pid if proc else None,
            started_at=stream.start_time,
        )
