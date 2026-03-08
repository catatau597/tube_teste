"""
web/routes/streaming.py
-----------------------
Rotas de streaming proxy/player e thumbnail.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from pathlib import Path

from fasthtml.common import *
from starlette.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Route

from core.player_router import build_live_hls_ffmpeg_cmd, build_player_command_async, resolve_live_hls_url_async
from core.proxy_manager import (
    CLIENT_TIMEOUT_S,
    INIT_TIMEOUT_S,
    STREAM_IDLE_STOP_S,
    _buffers,
    _managers,
    _placeholder_cmds,
    is_stream_active,
    register_placeholder,
    restart_placeholder_if_needed,
    start_stream_reader,
    stop_stream,
)
from web.app_deps import AppDeps

TEXTS_CACHE_PATH = Path("/data/textosepg.json")
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
STREAMLINK_FAST_FAIL_S = 8
STREAM_CHUNK_BATCH = 64
_start_locks: dict[str, asyncio.Lock] = {}


def _get_start_lock(video_id: str) -> asyncio.Lock:
    lock = _start_locks.get(video_id)
    if lock is None:
        lock = asyncio.Lock()
        _start_locks[video_id] = lock
    return lock


def register_streaming_routes(app, deps: AppDeps) -> None:
    async def api_proxy_stream(request):
        video_id = request.path_params["video_id"]
        user_agent = request.query_params.get("user_agent", "Mozilla/5.0")
        client_id = f"{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
        client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()

        deps.logger.info(f"[{video_id}] nova conexao proxy  client={client_id}  ip={client_ip}")

        if not is_stream_active(video_id):
            async with _get_start_lock(video_id):
                # Recheck após lock: evita corrida de dupla inicialização.
                if not is_stream_active(video_id):
                    if deps.state is None:
                        return Response("Servidor ainda inicializando", status_code=503)

                    stream_info = deps.state.streams.get(video_id)
                    if stream_info is None:
                        deps.logger.warning(f"[{video_id}] video_id nao encontrado no estado")
                    status = stream_info.get("status") if stream_info else None
                    thumbnail_url = stream_info.get("thumbnailurl") if stream_info else None
                    watch_url = f"https://www.youtube.com/watch?v={video_id}"
                    placeholder = deps.config.get_str("placeholder_image_url") if deps.config else ""
                    thumb_for_ph = thumbnail_url or placeholder

                    deps.logger.info(f"[{video_id}] iniciando stream  status={status!r}  url={watch_url}")

                    if deps.thumbnail_manager:
                        local_thumb = Path(deps.thumbnail_manager._cache_dir) / f"{video_id}.jpg"
                        if local_thumb.exists():
                            thumb_for_ph = str(local_thumb)

                    debug_enabled = deps.config.get_bool("streaming_debug_enabled") if deps.config else False

                    try:
                        cmd, _temp = await build_player_command_async(
                            video_id=video_id,
                            status=status,
                            watch_url=watch_url,
                            thumbnail_url=thumb_for_ph,
                            user_agent=user_agent,
                            font_path=FONT_PATH,
                            texts_cache_path=TEXTS_CACHE_PATH,
                            debug_enabled=debug_enabled,
                        )
                    except Exception as exc:
                        deps.logger.error(f"[{video_id}] erro ao montar comando proxy: {exc}")
                        return Response(f"Erro ao inicializar stream: {exc}", status_code=500)

                    start_stream_reader(video_id, cmd)

                    # Placeholder apenas para upcoming/indisponível.
                    # VOD (none/vod/ended/recorded) não deve reiniciar em loop.
                    if status not in ("live", "none", "vod", "ended", "recorded"):
                        register_placeholder(video_id, cmd)
                        deps.logger.debug(f"[{video_id}] placeholder registrado (status={status!r})")

                    deps.logger.info(f"[{video_id}] stream proxy iniciado")

                    if status == "live" and cmd and cmd[0] == "streamlink":
                        ff_deadline = time.monotonic() + STREAMLINK_FAST_FAIL_S
                        while time.monotonic() < ff_deadline:
                            buf = _buffers.get(video_id)
                            if buf and buf.index > 0:
                                break
                            await asyncio.sleep(0.1)
                        else:
                            deps.logger.warning(
                                f"[{video_id}] streamlink fast-fail: sem chunk em {STREAMLINK_FAST_FAIL_S}s → fallback yt-dlp HLS"
                            )
                            stop_stream(video_id)
                            hls_url = await resolve_live_hls_url_async(
                                watch_url, user_agent=user_agent, debug_enabled=debug_enabled
                            )
                            if not hls_url:
                                deps.logger.error(f"[{video_id}] yt-dlp nao resolveu HLS URL")
                                return Response("Stream indisponivel (streamlink + yt-dlp falharam)", status_code=503)
                            cmd = build_live_hls_ffmpeg_cmd(
                                hls_url, user_agent=user_agent, debug_enabled=debug_enabled
                            )
                            start_stream_reader(video_id, cmd)
                            deps.logger.info(f"[{video_id}] fallback HLS ativo: {hls_url[:70]}...")

                    deadline = time.monotonic() + INIT_TIMEOUT_S
                    while time.monotonic() < deadline:
                        buf = _buffers.get(video_id)
                        if buf and buf.index > 0:
                            break
                        await asyncio.sleep(0.1)
                    else:
                        stop_stream(video_id)
                        deps.logger.error(f"[{video_id}] timeout aguardando primeiro chunk")
                        return Response("Stream timeout na inicializacao", status_code=504)

        buf = _buffers[video_id]
        mgr = _managers[video_id]
        mgr.add_client(client_id, client_ip, user_agent)

        async def generate():
            # Janela inicial maior melhora join tardio (upcoming/live) no VLC.
            local_index = max(0, buf.index - 50)
            bytes_sent = 0
            last_yield_time = time.monotonic()
            consecutive_empty = 0
            try:
                while True:
                    if not is_stream_active(video_id) and video_id not in _buffers:
                        break
                    restart_placeholder_if_needed(video_id)
                    is_placeholder = video_id in _placeholder_cmds
                    batch = 8 if is_placeholder else STREAM_CHUNK_BATCH
                    chunks, next_index = buf.get_chunks(local_index, count=batch)
                    if chunks:
                        for chunk in chunks:
                            yield chunk
                            bytes_sent += len(chunk)
                        local_index = next_index
                        last_yield_time = time.monotonic()
                        consecutive_empty = 0
                        mgr.update_activity(client_id, bytes_sent, local_index)
                    else:
                        consecutive_empty += 1
                        # Backoff curto para live: evita "buracos" longos de entrega no VLC.
                        await asyncio.sleep(min(0.01 * consecutive_empty, 0.2))
                        if time.monotonic() - last_yield_time > CLIENT_TIMEOUT_S:
                            mgr.mark_stall(client_id)
                            break
                        lag_limit = 30 if is_placeholder else 100
                        if buf.index - local_index > lag_limit:
                            local_index = max(0, buf.index - 50)
                            consecutive_empty = 0
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                deps.logger.error(f"[{video_id}][{client_id}] erro no generator: {exc}")
            finally:
                remaining = mgr.remove_client(client_id)
                if remaining == 0:
                    async def _delayed_stop():
                        await asyncio.sleep(STREAM_IDLE_STOP_S)
                        mgr2 = _managers.get(video_id)
                        if mgr2 and mgr2.count > 0:
                            return
                        stop_stream(video_id)
                    asyncio.create_task(_delayed_stop())

        return StreamingResponse(
            generate(),
            media_type="video/mp2t",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    app.routes.append(Route("/api/proxy/{video_id}", endpoint=api_proxy_stream))

    async def api_player_stream(request):
        video_id = request.path_params["video_id"]
        user_agent = request.query_params.get("user_agent", "Mozilla/5.0")

        if deps.state is None:
            return Response("Servidor ainda inicializando", status_code=503)

        stream_info = deps.state.streams.get(video_id)
        status = stream_info.get("status") if stream_info else None
        thumbnail_url = stream_info.get("thumbnailurl") if stream_info else None
        watch_url = f"https://www.youtube.com/watch?v={video_id}"
        placeholder = deps.config.get_str("placeholder_image_url") if deps.config else ""
        thumb_for_ph = thumbnail_url or placeholder

        if deps.thumbnail_manager:
            local_thumb = Path(deps.thumbnail_manager._cache_dir) / f"{video_id}.jpg"
            if local_thumb.exists():
                thumb_for_ph = str(local_thumb)

        debug_enabled = deps.config.get_bool("streaming_debug_enabled") if deps.config else False

        async def stream_gen():
            proc_logger = logging.getLogger("TubeWrangler.player")
            temp_files = []
            try:
                cmd, temp_files = await build_player_command_async(
                    video_id=video_id,
                    status=status,
                    watch_url=watch_url,
                    thumbnail_url=thumb_for_ph,
                    user_agent=user_agent,
                    font_path=FONT_PATH,
                    texts_cache_path=TEXTS_CACHE_PATH,
                    debug_enabled=debug_enabled,
                )
            except Exception as exc:
                proc_logger.error(f"[{video_id}] erro ao montar comando: {exc}")
                return

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=1024 * 1024,
            )

            async def _log_stderr():
                try:
                    async for line in proc.stderr:
                        text = line.decode("utf-8", errors="replace").rstrip()
                        if text:
                            proc_logger.info(f"[{video_id}] {text}")
                except Exception:
                    pass

            stderr_task = asyncio.create_task(_log_stderr())

            try:
                while True:
                    chunk = await proc.stdout.read(65536)
                    if not chunk:
                        break
                    yield chunk
            except asyncio.CancelledError:
                pass
            finally:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
                stderr_task.cancel()
                try:
                    await stderr_task
                except (asyncio.CancelledError, Exception):
                    pass
                for tf in temp_files:
                    try:
                        os.unlink(tf)
                    except Exception:
                        pass

        return StreamingResponse(
            stream_gen(),
            media_type="video/mp2t",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    app.routes.append(Route("/api/player/{video_id}", endpoint=api_player_stream))

    @app.get("/api/thumbnail/{video_id}")
    def api_thumbnail(video_id: str):
        if not re.match(r"^[a-zA-Z0-9_-]+$", video_id):
            return JSONResponse({"error": "video_id invalido"}, status_code=400)
        if deps.thumbnail_manager is None:
            return JSONResponse({"error": "thumbnail manager não inicializado"}, status_code=503)
        data = deps.thumbnail_manager.serve(video_id)
        if data:
            return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})
        return RedirectResponse(f"https://i.ytimg.com/vi/{video_id}/maxresdefault_live.jpg", status_code=302)
