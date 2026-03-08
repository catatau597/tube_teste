from __future__ import annotations

import asyncio
import collections
import logging
import os
import random
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fasthtml.common import *
from starlette.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Route

from core.config import AppConfig
from core.player_router import build_live_hls_ffmpeg_cmd, build_player_command_async, resolve_live_hls_url_async
from core.proxy_manager import (
    CLIENT_TIMEOUT_S,
    INIT_TIMEOUT_S,
    STREAM_IDLE_STOP_S,
    _buffers,
    _managers,
    _processes,
    is_stream_active,
    register_placeholder,
    restart_placeholder_if_needed,
    set_debug_mode,
    start_stream_reader,
    stop_stream,
)
from core.scheduler import Scheduler
from core.state_manager import StateManager
from core.thumbnail_manager import ThumbnailManager
from core.vod_verifier import VodVerifier
from core.youtube_api import YouTubeAPI
from core.playlist_builder import M3UGenerator, XMLTVGenerator
from web.app_deps import AppDeps
from web.routes.channels import channels_page as _channels_page
from web.routes.config import register_config_routes
from web.routes.dashboard import register_dashboard_routes
from web.routes.eventos import eventos_page as _eventos_page
from web.routes.logs import register_logs_routes
from web.routes.playlists import register_playlists_routes

TEXTS_CACHE_PATH = Path("/data/textosepg.json")
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

STREAMLINK_FAST_FAIL_S = 8

_LOG_BUFFER: collections.deque[tuple[int, str]] = collections.deque(maxlen=1000)
_LOG_SEQ = 0


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        global _LOG_SEQ
        try:
            _LOG_SEQ += 1
            _LOG_BUFFER.append((_LOG_SEQ, self.format(record)))
        except Exception:
            pass


_LOG_FMT = logging.Formatter(
    "%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_buffer_handler = _BufferHandler()
_buffer_handler.setFormatter(_LOG_FMT)


class _AccessLogFilter(logging.Filter):
    _SUPPRESS_PATTERNS = [
        re.compile(r'"GET / HTTP'),
        re.compile(r'"GET /api/logs/stream HTTP'),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(p.search(msg) for p in self._SUPPRESS_PATTERNS)


_access_filter = _AccessLogFilter()


def _setup_logging(level_str: str = "INFO", hide_access: bool = True) -> None:
    level = getattr(logging, level_str.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    for h in root.handlers[:]:
        if isinstance(h, (logging.FileHandler, logging.StreamHandler)):
            root.removeHandler(h)
    console = logging.StreamHandler()
    console.setFormatter(_LOG_FMT)
    root.addHandler(_buffer_handler)
    root.addHandler(console)
    access = logging.getLogger("uvicorn.access")
    access.handlers = [_buffer_handler, console]
    access.setLevel(level)
    access.propagate = False
    if hide_access:
        if _access_filter not in access.filters:
            access.addFilter(_access_filter)
    else:
        try:
            access.removeFilter(_access_filter)
        except Exception:
            pass


logger = logging.getLogger("TubeWrangler")

_PLAYLIST_ROUTES = {
    "live.m3u": ("live", "direct"),
    "live-proxy.m3u": ("live", "proxy"),
    "upcoming-proxy.m3u": ("upcoming", "proxy"),
    "vod.m3u": ("vod", "direct"),
    "vod-proxy.m3u": ("vod", "proxy"),
}

_LEGACY_REDIRECTS = {
    "/playlist_live_direct.m3u8": "/playlist/live.m3u",
    "/playlist_live_proxy.m3u8": "/playlist/live-proxy.m3u",
    "/playlist_vod_direct.m3u8": "/playlist/vod.m3u",
    "/playlist_vod_proxy.m3u8": "/playlist/vod-proxy.m3u",
    "/youtube_epg.xml": "/epg.xml",
    "/proxy": "/playlist",
}


deps = AppDeps(
    logger=logger,
    log_buffer=_LOG_BUFFER,
    setup_logging=_setup_logging,
    playlist_routes=_PLAYLIST_ROUTES,
    legacy_redirects=_LEGACY_REDIRECTS,
)


@asynccontextmanager
async def lifespan(app):
    cfg = AppConfig()
    hide_access = cfg.get_bool("hide_access_logs")
    _setup_logging(cfg.get_str("log_level") or "INFO", hide_access=hide_access)
    logger.info("=== TubeWrangler iniciando ===")

    streaming_debug = cfg.get_bool("streaming_debug_enabled")
    set_debug_mode(streaming_debug)

    state = StateManager(cfg)
    cache_loaded = state.load_from_disk()
    logger.info(
        f"Cache disco: {cache_loaded} | "
        f"canais={len(state.get_all_channels())} "
        f"streams={len(state.get_all_streams())}"
    )

    thumb_dir = cfg.get_str("thumbnail_cache_directory")
    thumbnail_manager = ThumbnailManager(thumb_dir)
    state.set_thumbnail_manager(thumbnail_manager)

    api_keys = cfg.get_list("youtube_api_keys")
    handles = [h.strip() for h in cfg.get_str("target_channel_handles").split(",") if h.strip()]
    chan_ids = [i.strip() for i in cfg.get_str("target_channel_ids").split(",") if i.strip()]
    logger.info(f"Handles configurados : {handles}")
    logger.info(f"IDs configurados     : {chan_ids}")

    scraper = YouTubeAPI(api_keys)

    all_target_ids = set(chan_ids)
    if handles:
        logger.info(f"Resolvendo {len(handles)} handle(s) via API...")
        resolved = scraper.resolve_channel_handles_to_ids(handles, state)
        all_target_ids.update(resolved.keys())
        logger.info(f"Handles resolvidos: {resolved}")

    if all_target_ids:
        final_channels = scraper.ensure_channel_titles(all_target_ids, state)
        logger.info(f"Canais prontos: {len(final_channels)}")
    else:
        logger.warning("Nenhum canal alvo. Verifique /canais ou /config/credentials.")

    force_event = asyncio.Event()
    scheduler = Scheduler(cfg, scraper, state)
    scheduler.set_force_event(force_event)
    scheduler.set_thumbnail_manager(thumbnail_manager)
    m3u_generator = M3UGenerator(cfg)
    xmltv_generator = XMLTVGenerator(cfg)
    categories_db: dict = {}
    scheduler.set_categories_db(categories_db)

    vod_verifier = VodVerifier(scraper, state, cfg)
    scheduler.set_vod_verifier(vod_verifier)

    deps.config = cfg
    deps.state = state
    deps.scheduler = scheduler
    deps.thumbnail_manager = thumbnail_manager
    deps.m3u_generator = m3u_generator
    deps.xmltv_generator = xmltv_generator
    deps.categories_db = categories_db

    task = asyncio.create_task(scheduler.run())
    health_check_task = asyncio.create_task(vod_verifier.run_health_check_loop())
    logger.info("Scheduler e VodVerifier iniciados.")

    try:
        yield
    finally:
        task.cancel()
        health_check_task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        try:
            await health_check_task
        except asyncio.CancelledError:
            pass
        for vid in list(_processes.keys()):
            stop_stream(vid)
        if deps.state:
            deps.state.save_to_disk()
        logger.info("=== TubeWrangler encerrado ===")


app, rt = fast_app(
    lifespan=lifespan,
    hdrs=[],
)


register_dashboard_routes(app, deps)
register_config_routes(app, deps)
register_logs_routes(app, deps)
register_playlists_routes(app, deps)


def _serialize_stream(s: dict) -> dict:
    data = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in s.items()}
    cat_id = str(s.get("categoryoriginal") or "")
    cat_name = (deps.categories_db or {}).get(cat_id, "") if cat_id else ""
    if not cat_name and deps.config is not None and cat_id:
        cat_name = deps.config.get_mapping("category_mappings").get(cat_id, "")
    data["category_display"] = f"{cat_id} | {cat_name}" if cat_name else (cat_id or "—")
    return data


@app.get("/canais")
def canais_page():
    return _channels_page(deps.state, deps.scheduler, config=deps.config)


@app.get("/eventos")
def eventos_page_route():
    return _eventos_page(deps.state)


@app.get("/api/channels")
def api_channels_list():
    if deps.state is None:
        return JSONResponse([])
    return JSONResponse([{"id": k, "title": v} for k, v in deps.state.get_all_channels().items()])


@app.post("/api/channels")
async def api_channels_create(req):
    if deps.state is None:
        return JSONResponse({"error": "Servidor ainda inicializando"}, status_code=503)
    body = await req.json()
    cid = body.get("id", "").strip()
    title = body.get("title", "").strip()
    if not cid or not title:
        return JSONResponse({"error": "id e title obrigatorios"}, status_code=400)
    deps.state.channels[cid] = title
    deps.state.save_to_disk()
    return JSONResponse({"ok": True, "id": cid})


@app.post("/api/channels/add")
async def api_channels_add(req):
    if deps.state is None or deps.config is None:
        return JSONResponse({"error": "Servidor ainda inicializando"}, status_code=503)

    body = await req.json()
    cid = body.get("id", "").strip()
    handle = body.get("handle", "").strip().lstrip("@")
    if not cid and not handle:
        return JSONResponse({"error": "Forneça id ou handle"}, status_code=400)

    api_keys = deps.config.get_list("youtube_api_keys")
    scraper = YouTubeAPI(api_keys)
    if handle and not cid:
        resolved = scraper.resolve_channel_handles_to_ids([handle], deps.state)
        if not resolved:
            return JSONResponse({"error": f"Handle @{handle} não encontrado"}, status_code=404)
        cid = list(resolved.keys())[0]
    titles = scraper.ensure_channel_titles({cid}, deps.state)
    title = titles.get(cid) or cid
    deps.state.channels[cid] = title
    deps.state.save_to_disk()
    logger.info(f"Canal adicionado via UI: {cid} ({title})")
    return JSONResponse({"ok": True, "id": cid, "title": title})


@app.delete("/api/channels/{channel_id}")
def api_channels_delete(channel_id: str):
    if deps.state is None or channel_id not in deps.state.channels:
        return JSONResponse({"error": "nao encontrado"}, status_code=404)
    del deps.state.channels[channel_id]
    frozen = getattr(deps.state, "frozen_channels", set())
    frozen.discard(channel_id)
    deps.state.save_to_disk()
    logger.info(f"Canal deletado via UI: {channel_id}")
    return JSONResponse({"ok": True})


@app.post("/api/channels/{channel_id}/sync")
def api_channels_sync(channel_id: str):
    if deps.scheduler is None:
        return JSONResponse({"error": "Scheduler não disponível"}, status_code=503)
    deps.scheduler.trigger_now()
    logger.info(f"Sync forçado via UI para canal: {channel_id}")
    return JSONResponse({"ok": True, "channel_id": channel_id})


@app.post("/api/channels/{channel_id}/freeze")
def api_channels_freeze(channel_id: str):
    if deps.state is None:
        return JSONResponse({"error": "State não disponível"}, status_code=503)
    if not hasattr(deps.state, "frozen_channels"):
        deps.state.frozen_channels = set()
    if channel_id in deps.state.frozen_channels:
        deps.state.frozen_channels.discard(channel_id)
        frozen = False
    else:
        deps.state.frozen_channels.add(channel_id)
        frozen = True
    deps.state.save_to_disk()
    logger.info(f"Canal {'congelado' if frozen else 'descongelado'} via UI: {channel_id}")
    return JSONResponse({"ok": True, "channel_id": channel_id, "frozen": frozen})


@app.get("/api/streams")
def api_streams_list(status: str = ""):
    if deps.state is None:
        return JSONResponse([])
    streams = deps.state.get_all_streams()
    if status:
        streams = [s for s in streams if s.get("status") == status]
    return JSONResponse([_serialize_stream(s) for s in streams])


@app.get("/api/streams/{video_id}")
def api_streams_detail(video_id: str):
    if deps.state is None:
        return JSONResponse({"error": "Servidor ainda inicializando"}, status_code=503)
    stream = deps.state.streams.get(video_id)
    if not stream:
        return JSONResponse({"error": "nao encontrado"}, status_code=404)
    return JSONResponse(_serialize_stream(stream))


@app.get("/api/config")
def api_config_get():
    if deps.config is None:
        return JSONResponse({})
    return JSONResponse(deps.config.get_all())


@app.put("/api/config")
async def api_config_put(req):
    if deps.config is None:
        return JSONResponse({"error": "Servidor ainda inicializando"}, status_code=503)
    body = await req.json()
    key = body.get("key", "").strip()
    value = str(body.get("value", "")).strip()
    if not key:
        return JSONResponse({"error": "key obrigatorio"}, status_code=400)
    deps.config.update(key, value)
    deps.config.reload()
    if deps.scheduler:
        deps.scheduler.reload_config(deps.config)
    return JSONResponse({"ok": True, "key": key, "value": value})


async def api_proxy_stream(request):
    video_id = request.path_params["video_id"]
    user_agent = request.query_params.get("user_agent", "Mozilla/5.0")
    client_id = f"{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()

    logger.info(f"[{video_id}] nova conexao proxy  client={client_id}  ip={client_ip}")

    if not is_stream_active(video_id):
        if deps.state is None:
            return Response("Servidor ainda inicializando", status_code=503)

        stream_info = deps.state.streams.get(video_id)
        if stream_info is None:
            logger.warning(f"[{video_id}] video_id nao encontrado no estado")
        status = stream_info.get("status") if stream_info else None
        thumbnail_url = stream_info.get("thumbnailurl") if stream_info else None
        watch_url = f"https://www.youtube.com/watch?v={video_id}"
        placeholder = deps.config.get_str("placeholder_image_url") if deps.config else ""
        thumb_for_ph = thumbnail_url or placeholder

        logger.info(f"[{video_id}] iniciando stream  status={status!r}  url={watch_url}")

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
            logger.error(f"[{video_id}] erro ao montar comando proxy: {exc}")
            return Response(f"Erro ao inicializar stream: {exc}", status_code=500)

        start_stream_reader(video_id, cmd)

        if status not in ("live", "none"):
            register_placeholder(video_id, cmd)
            logger.debug(f"[{video_id}] placeholder registrado (status={status!r})")

        logger.info(f"[{video_id}] stream proxy iniciado")

        if status == "live":
            ff_deadline = time.monotonic() + STREAMLINK_FAST_FAIL_S
            while time.monotonic() < ff_deadline:
                buf = _buffers.get(video_id)
                if buf and buf.index > 0:
                    break
                await asyncio.sleep(0.1)
            else:
                logger.warning(
                    f"[{video_id}] streamlink fast-fail: sem chunk em {STREAMLINK_FAST_FAIL_S}s → fallback yt-dlp HLS"
                )
                stop_stream(video_id)
                hls_url = await resolve_live_hls_url_async(watch_url)
                if not hls_url:
                    logger.error(f"[{video_id}] yt-dlp nao resolveu HLS URL")
                    return Response("Stream indisponivel (streamlink + yt-dlp falharam)", status_code=503)
                cmd = build_live_hls_ffmpeg_cmd(hls_url)
                start_stream_reader(video_id, cmd)
                logger.info(f"[{video_id}] fallback HLS ativo: {hls_url[:70]}...")

        deadline = time.monotonic() + INIT_TIMEOUT_S
        while time.monotonic() < deadline:
            buf = _buffers.get(video_id)
            if buf and buf.index > 0:
                break
            await asyncio.sleep(0.1)
        else:
            stop_stream(video_id)
            logger.error(f"[{video_id}] timeout aguardando primeiro chunk")
            return Response("Stream timeout na inicializacao", status_code=504)

    buf = _buffers[video_id]
    mgr = _managers[video_id]
    mgr.add_client(client_id, client_ip, user_agent)

    async def generate():
        local_index = max(0, buf.index - 10)
        bytes_sent = 0
        last_yield_time = time.monotonic()
        consecutive_empty = 0
        try:
            while True:
                if not is_stream_active(video_id) and video_id not in _buffers:
                    break
                restart_placeholder_if_needed(video_id)
                chunks, next_index = buf.get_chunks(local_index, count=5)
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
                    await asyncio.sleep(min(0.05 * consecutive_empty, 1.0))
                    if time.monotonic() - last_yield_time > CLIENT_TIMEOUT_S:
                        mgr.mark_stall(client_id)
                        break
                    if buf.index - local_index > 100:
                        local_index = max(0, buf.index - 10)
                        consecutive_empty = 0
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(f"[{video_id}][{client_id}] erro no generator: {exc}")
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
