"""
vod_proxy.py — proxy HTTP de VOD com sessão persistente.

Inspirado no modelo do Dispatcharr:
- sessão de VOD por cliente/player
- reuso de requests.Session e URL final após redirect
- suporte a HEAD/GET com Range
- cleanup assíncrono por inatividade
"""

from __future__ import annotations

import logging
import random
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

import requests

logger = logging.getLogger("TubeWrangler.vod_proxy")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0"
)

VOD_PROXY_URL_TTL_S = 900
VOD_SESSION_IDLE_TTL_S = 30


def resolve_vod_proxy_url(
    watch_url: str,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: int = 20,
    debug_enabled: bool = False,
) -> str:
    """Resolve URL reproduzível com áudio para proxy HTTP.

    O alvo aqui não é a melhor combinação para ffmpeg, e sim uma URL única
    que o player consiga consumir por Range sempre que possível.
    """
    args = [
        "yt-dlp",
        "-f",
        (
            "best[protocol^=http][vcodec^=avc1][acodec^=mp4a]/"
            "best[protocol^=http][ext=mp4]/"
            "best[protocol^=http]/best"
        ),
        "--get-url",
        "--no-playlist",
        "--js-runtimes",
        "node",
        "--user-agent",
        user_agent,
        watch_url,
    ]
    if debug_enabled:
        args.insert(-1, "--verbose")

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning(f"[vod_proxy] timeout ao resolver VOD: {watch_url}")
        return ""
    except Exception as exc:
        logger.warning(f"[vod_proxy] erro ao resolver VOD: {watch_url} | {exc}")
        return ""

    if result.returncode != 0:
        err = (result.stderr or "").strip()
        logger.warning(f"[vod_proxy] yt-dlp falhou rc={result.returncode} | {err[:200]}")
        return ""

    text = (result.stdout or "").strip()
    return text.splitlines()[0] if text else ""


@dataclass
class VODSession:
    session_id: str
    video_id: str
    watch_url: str
    user_agent: str
    debug_enabled: bool = False
    stream_url: str = ""
    final_url: str = ""
    content_length: Optional[int] = None
    content_type: str = "video/mp4"
    request_count: int = 0
    last_activity: float = field(default_factory=time.time)
    active_streams: int = 0
    lock: threading.RLock = field(default_factory=threading.RLock)
    session: Optional[requests.Session] = None
    current_response: Optional[requests.Response] = None
    cleanup_timer: Optional[threading.Timer] = None

    def ensure_stream_url(self) -> str:
        if self.stream_url:
            return self.stream_url
        self.stream_url = resolve_vod_proxy_url(
            self.watch_url,
            user_agent=self.user_agent,
            debug_enabled=self.debug_enabled,
        )
        return self.stream_url

    def _build_session(self) -> requests.Session:
        s = requests.Session()
        s.headers.update({"User-Agent": self.user_agent, "Connection": "keep-alive"})
        return s

    def _refresh_stream_url(self) -> bool:
        self.stream_url = resolve_vod_proxy_url(
            self.watch_url,
            user_agent=self.user_agent,
            debug_enabled=self.debug_enabled,
        )
        self.final_url = ""
        self.content_length = None
        return bool(self.stream_url)

    def _normalize_range(self, range_header: Optional[str]) -> Optional[str]:
        if not range_header or not self.content_length:
            return range_header
        if not range_header.startswith("bytes="):
            return range_header

        try:
            raw = range_header[6:]
            start_str, end_str = raw.split("-", 1)
            start = int(start_str) if start_str else 0
            end = int(end_str) if end_str else self.content_length - 1
        except Exception:
            return range_header

        if start >= self.content_length:
            return None
        end = min(end, self.content_length - 1)
        if start > end:
            return None
        return f"bytes={start}-{end}"

    def open(self, method: str = "GET", range_header: Optional[str] = None) -> requests.Response | None:
        with self.lock:
            self.last_activity = time.time()
            self.cancel_cleanup()

            normalized_range = self._normalize_range(range_header)
            if range_header and normalized_range is None:
                return None

            if self.current_response is not None:
                try:
                    self.current_response.close()
                except Exception:
                    pass
                self.current_response = None

            if self.session is None:
                self.session = self._build_session()

            target_url = self.final_url or self.ensure_stream_url()
            if not target_url:
                raise RuntimeError("Nao foi possivel resolver URL de VOD")

            headers = {}
            if normalized_range:
                headers["Range"] = normalized_range

            self.request_count += 1
            allow_redirects = not bool(self.final_url)

            try:
                response = self.session.request(
                    method=method,
                    url=target_url,
                    headers=headers,
                    stream=(method != "HEAD"),
                    timeout=(10, 30),
                    allow_redirects=allow_redirects,
                )
                response.raise_for_status()
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if status == 403 and self._refresh_stream_url():
                    response = self.session.request(
                        method=method,
                        url=self.stream_url,
                        headers=headers,
                        stream=(method != "HEAD"),
                        timeout=(10, 30),
                        allow_redirects=True,
                    )
                    response.raise_for_status()
                else:
                    raise

            self.final_url = response.url
            content_length = response.headers.get("Content-Length")
            if content_length and content_length.isdigit():
                self.content_length = int(content_length)
            self.content_type = response.headers.get("Content-Type", self.content_type)
            self.current_response = response
            return response

    def increment_active(self) -> None:
        with self.lock:
            self.active_streams += 1
            self.last_activity = time.time()

    def decrement_active(self) -> None:
        with self.lock:
            if self.active_streams > 0:
                self.active_streams -= 1
            self.last_activity = time.time()

    def schedule_cleanup(self, manager: "VODProxyManager", delay_s: int = VOD_SESSION_IDLE_TTL_S) -> None:
        with self.lock:
            if self.active_streams > 0:
                return
            if self.cleanup_timer is not None:
                self.cleanup_timer.cancel()

            timer = threading.Timer(delay_s, lambda: manager.cleanup_session(self.session_id))
            timer.daemon = True
            timer.start()
            self.cleanup_timer = timer

    def cancel_cleanup(self) -> None:
        with self.lock:
            if self.cleanup_timer is not None:
                self.cleanup_timer.cancel()
                self.cleanup_timer = None

    def close(self) -> None:
        with self.lock:
            self.cancel_cleanup()
            if self.current_response is not None:
                try:
                    self.current_response.close()
                except Exception:
                    pass
                self.current_response = None
            if self.session is not None:
                try:
                    self.session.close()
                except Exception:
                    pass
                self.session = None


class VODProxyManager:
    def __init__(self) -> None:
        self._sessions: Dict[str, VODSession] = {}
        self._url_cache: Dict[str, tuple[str, float]] = {}
        self._lock = threading.RLock()

    def create_session_id(self, video_id: str) -> str:
        return f"vod_{video_id}_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"

    def get_or_create_session(
        self,
        session_id: str,
        video_id: str,
        watch_url: str,
        user_agent: str,
        debug_enabled: bool = False,
    ) -> VODSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                session = VODSession(
                    session_id=session_id,
                    video_id=video_id,
                    watch_url=watch_url,
                    user_agent=user_agent,
                    debug_enabled=debug_enabled,
                )
                cached = self._url_cache.get(video_id)
                if cached and (time.time() - cached[1]) < VOD_PROXY_URL_TTL_S:
                    session.stream_url = cached[0]
                self._sessions[session_id] = session
            return session

    def maybe_cache_url(self, video_id: str, stream_url: str) -> None:
        if stream_url:
            with self._lock:
                self._url_cache[video_id] = (stream_url, time.time())

    def cleanup_session(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is not None:
            session.close()

    def snapshot(self) -> list[dict]:
        with self._lock:
            result = []
            for session in self._sessions.values():
                with session.lock:
                    result.append({
                        "session_id": session.session_id,
                        "video_id": session.video_id,
                        "type": "vod",
                        "clients": session.active_streams,
                        "process_alive": session.current_response is not None,
                        "process_pid": None,
                        "buffer_chunks": None,
                        "buffer_index": None,
                        "buffer_mb": None,
                        "is_placeholder": False,
                    })
            return result


vod_proxy_manager = VODProxyManager()
