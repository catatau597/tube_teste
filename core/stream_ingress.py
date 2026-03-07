"""
Camada de entrada do stream.

Responsabilidade:
- decidir qual estrategia de ingest usar
- retornar um plano de ingest pronto para o manager/endpoint executar

Nao gerencia buffer, clientes ou respostas HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from core.player_router import (
    build_live_hls_ffmpeg_cmd,
    build_player_command_async,
    resolve_live_hls_url_async,
)


@dataclass(frozen=True)
class StreamIngressPlan:
    kind: str
    cmd: List[str]
    temp_files: List[str] = field(default_factory=list)
    source_url: str = ""

    @property
    def is_placeholder(self) -> bool:
        return self.kind == "placeholder"

    @property
    def is_live(self) -> bool:
        return self.kind in {"live_hls", "live_streamlink"}


async def resolve_proxy_ingress_plan(
    *,
    video_id: str,
    status: str | None,
    watch_url: str,
    thumbnail_url: str,
    user_agent: str,
    font_path: str,
    texts_cache_path: Path,
    debug_enabled: bool,
) -> StreamIngressPlan:
    """Resolve o plano de ingest para o pipeline proxy compartilhado."""
    if status == "live":
        hls_url = await resolve_live_hls_url_async(
            watch_url,
            user_agent=user_agent,
            debug_enabled=debug_enabled,
        )
        if hls_url:
            return StreamIngressPlan(
                kind="live_hls",
                cmd=build_live_hls_ffmpeg_cmd(
                    hls_url,
                    user_agent=user_agent,
                    debug_enabled=debug_enabled,
                ),
                source_url=hls_url,
            )

    cmd, temp_files = await build_player_command_async(
        video_id=video_id,
        status=status,
        watch_url=watch_url,
        thumbnail_url=thumbnail_url,
        user_agent=user_agent,
        font_path=font_path,
        texts_cache_path=texts_cache_path,
        debug_enabled=debug_enabled,
    )

    if status == "live":
        kind = "live_streamlink"
    elif status in ("vod", "none", "ended", "completed"):
        kind = "vod"
    else:
        kind = "placeholder"

    return StreamIngressPlan(kind=kind, cmd=cmd, temp_files=temp_files)


async def build_live_fallback_ingress_plan(
    *,
    watch_url: str,
    user_agent: str,
    debug_enabled: bool,
) -> StreamIngressPlan | None:
    """Resolve fallback explicito para live apos fast-fail do stream primario."""
    hls_url = await resolve_live_hls_url_async(
        watch_url,
        user_agent=user_agent,
        debug_enabled=debug_enabled,
    )
    if not hls_url:
        return None

    return StreamIngressPlan(
        kind="live_hls",
        cmd=build_live_hls_ffmpeg_cmd(
            hls_url,
            user_agent=user_agent,
            debug_enabled=debug_enabled,
        ),
        source_url=hls_url,
    )
