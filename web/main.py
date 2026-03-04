from contextlib import asynccontextmanager
import asyncio
import collections
import logging
import os
import re
import time
import random
from pathlib import Path

from fasthtml.common import *
from starlette.routing import Route
from starlette.responses import JSONResponse, RedirectResponse, Response, StreamingResponse

from core.config import AppConfig
from core.player_router import build_player_command_async
from core.playlist_builder import M3UGenerator, XMLTVGenerator, _resolve_proxy_base_url
from core.proxy_manager import (
    start_stream_reader, stop_stream, is_stream_active, streams_status,
    register_placeholder, restart_placeholder_if_needed,
    _buffers, _managers, _processes,
    INIT_TIMEOUT_S, CLIENT_TIMEOUT_S, STREAM_IDLE_STOP_S,
)
from core.scheduler import Scheduler
from core.state_manager import StateManager
from core.thumbnail_manager import ThumbnailManager
from core.youtube_api import YouTubeAPI
from web.routes.proxy_dashboard import proxy_dashboard_page

TEXTS_CACHE_PATH = Path("/data/textosepg.json")
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

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


def _setup_logging(level_str: str = "INFO") -> None:
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


logger = logging.getLogger("TubeWrangler")

# ---------------------------------------------------------------------------
# Estado global
# ---------------------------------------------------------------------------

_config: Optional[AppConfig] = None
_state: Optional[StateManager] = None
_scheduler: Optional[Scheduler] = None
_thumbnail_manager: Optional[ThumbnailManager] = None
_m3u_generator: Optional[M3UGenerator] = None
_xmltv_generator: Optional[XMLTVGenerator] = None
_categories_db: dict = {}

_PLAYLIST_ROUTES = {
    "live.m3u":          ("live",     "direct"),
    "live-proxy.m3u":    ("live",     "proxy"),
    "upcoming-proxy.m3u":("upcoming", "proxy"),
    "vod.m3u":           ("vod",      "direct"),
    "vod-proxy.m3u":     ("vod",      "proxy"),
}

_LEGACY_REDIRECTS = {
    "/playlist_live_direct.m3u8": "/playlist/live.m3u",
    "/playlist_live_proxy.m3u8":  "/playlist/live-proxy.m3u",
    "/playlist_vod_direct.m3u8":  "/playlist/vod.m3u",
    "/playlist_vod_proxy.m3u8":   "/playlist/vod-proxy.m3u",
    "/youtube_epg.xml":            "/epg.xml",
}


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app):
    global _config, _state, _scheduler, _thumbnail_manager
    global _m3u_generator, _xmltv_generator, _categories_db

    _config = AppConfig()
    _setup_logging(_config.get_str("log_level") or "INFO")
    logger.info("=== TubeWrangler iniciando ===")

    _state = StateManager(_config)
    cache_loaded = _state.load_from_disk()
    logger.info(
        f"Cache disco: {cache_loaded} | "
        f"canais={len(_state.get_all_channels())} "
        f"streams={len(_state.get_all_streams())}"
    )

    thumb_dir = _config.get_str("thumbnail_cache_directory")
    _thumbnail_manager = ThumbnailManager(thumb_dir)
    _state.set_thumbnail_manager(_thumbnail_manager)

    api_key  = _config.get_str("youtube_api_key")
    handles  = [h.strip() for h in _config.get_str("target_channel_handles").split(",") if h.strip()]
    chan_ids = [i.strip() for i in _config.get_str("target_channel_ids").split(",") if i.strip()]
    logger.info(f"Handles configurados : {handles}")
    logger.info(f"IDs configurados     : {chan_ids}")

    scraper = YouTubeAPI(api_key)

    all_target_ids = set(chan_ids)
    if handles:
        logger.info(f"Resolvendo {len(handles)} handle(s) via API...")
        resolved = scraper.resolve_channel_handles_to_ids(handles, _state)
        all_target_ids.update(resolved.keys())
        logger.info(f"Handles resolvidos: {resolved}")

    if all_target_ids:
        final_channels = scraper.ensure_channel_titles(all_target_ids, _state)
        logger.info(f"Canais prontos: {len(final_channels)}")
    else:
        logger.warning("Nenhum canal alvo. Verifique target_channel_handles / target_channel_ids no /config.")

    _force_event = asyncio.Event()
    _scheduler = Scheduler(_config, scraper, _state)
    _scheduler.set_force_event(_force_event)
    _scheduler.set_thumbnail_manager(_thumbnail_manager)
    _m3u_generator = M3UGenerator(_config)
    _xmltv_generator = XMLTVGenerator(_config)
    _categories_db = {}
    _scheduler.set_categories_db(_categories_db)
    task = asyncio.create_task(_scheduler.run())
    logger.info("Scheduler iniciado.")

    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        for vid in list(_processes.keys()):
            stop_stream(vid)
        _state.save_to_disk()
        logger.info("=== TubeWrangler encerrado ===")


# ---------------------------------------------------------------------------
# App FastHTML
# ---------------------------------------------------------------------------

app, rt = fast_app(
    lifespan=lifespan,
    hdrs=[Link(rel="stylesheet", href="https://cdn.jsdelivr.net/npm/pico.css@2/css/pico.min.css")],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize_stream(s: dict) -> dict:
    data = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in s.items()}
    cat_id   = str(s.get("categoryoriginal") or "")
    cat_name = (_categories_db or {}).get(cat_id, "") if cat_id else ""
    if not cat_name and _config is not None and cat_id:
        cat_name = _config.get_mapping("category_mappings").get(cat_id, "")
    data["category_display"] = f"{cat_id} | {cat_name}" if cat_name else (cat_id or "\u2014")
    return data


def _get_base_url(request) -> str:
    """
    Resolve a URL base usando o host do request (header Host).
    Usado APENAS para paginas web e links gerados com request disponivel.
    NAO usa proxy_base_url — esse parametro e exclusivo para playlists M3U
    geradas sem request (ex: agendador, API externa).
    Assim o acesso via qualquer IP (Tailscale, LAN, localhost) sempre funciona.
    """
    host = request.headers.get("host", "")
    if host:
        scheme = "https" if request.url.scheme == "https" else "http"
        return f"{scheme}://{host}"
    # Fallback caso Host nao venha no header
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    port      = request.url.port or 8888
    return f"http://{client_ip}:{port}"


def _nav():
    return Div(
        A("Dashboard",  href="/",           style="margin-right:12px;"),
        A("Proxy",      href="/proxy",      style="margin-right:12px;"),
        A("Force Sync", href="/force-sync", style="margin-right:12px;"),
        A("Config",     href="/config",     style="margin-right:12px;"),
        A("Logs",       href="/logs"),
        style="padding:8px 0 16px 0; border-bottom: 1px solid #ccc; margin-bottom:16px;",
    )


def _serve_playlist_onthefly(mode: str, mode_type: str, request=None) -> Response:
    if _m3u_generator is None or _state is None:
        return Response("Servidor ainda inicializando", status_code=503)
    streams    = list(_state.get_all_streams())
    cats       = _categories_db if _categories_db else {}
    proxy_base = ""
    if mode_type == "proxy":
        if request is not None:
            # Usa o IP/host exato do request para que os links M3U
            # funcionem independente de qual IP foi usado para baixar a playlist
            proxy_base = _get_base_url(request)
        else:
            # Sem request (chamada interna) usa proxy_base_url configurado ou auto-deteccao
            proxy_base = _resolve_proxy_base_url(_config)
    content = _m3u_generator.generate_playlist(
        streams, cats, mode=mode, mode_type=mode_type,
        thumbnail_manager=_thumbnail_manager,
        proxy_base_url=proxy_base,
    )
    logger.debug(f"Playlist [{mode}/{mode_type}] gerada  base_url={proxy_base!r}  total={len(streams)}")
    return Response(content, media_type="audio/x-mpegurl")


# ---------------------------------------------------------------------------
# Rotas de playlist — Starlette direto para receber request
# ---------------------------------------------------------------------------

for _playlist_name, (_mode, _mode_type) in _PLAYLIST_ROUTES.items():
    def _make_playlist_route(mode=_mode, mode_type=_mode_type, playlist_name=_playlist_name):
        async def _playlist_route(request):
            return _serve_playlist_onthefly(mode, mode_type, request=request)
        app.routes.append(Route(f"/playlist/{playlist_name}", endpoint=_playlist_route))
    _make_playlist_route()


@app.get("/epg.xml")
def serve_epg_onthefly():
    if _xmltv_generator is None or _state is None:
        return Response("Servidor ainda inicializando", status_code=503)
    channels = _state.get_all_channels()
    streams  = list(_state.get_all_streams())
    cats     = _categories_db if _categories_db else {}
    content  = _xmltv_generator.generate_xml(channels, streams, cats)
    return Response(content, media_type="application/xml")


async def _epg_route(request):
    return serve_epg_onthefly()


app.router.routes.insert(0, Route("/epg.xml", endpoint=_epg_route))


for _old, _new in _LEGACY_REDIRECTS.items():
    def _make_redirect(old=_old, new=_new):
        @app.get(old)
        def _redirect():
            return RedirectResponse(url=new, status_code=301)
    _make_redirect()


# ---------------------------------------------------------------------------
# Rotas HTML
# ---------------------------------------------------------------------------

@app.get("/")
def home(request: Request):
    streams  = _state.get_all_streams() if _state else []
    channels = _state.get_all_channels() if _state else {}

    rows = []
    for s in streams:
        vid      = s.get("videoid", "")
        url      = s.get("watchurl") or f"https://www.youtube.com/watch?v={vid}"
        cat_id   = str(s.get("categoryoriginal") or "")
        cat_name = (_categories_db or {}).get(cat_id, "") if cat_id else ""
        if not cat_name and _config is not None and cat_id:
            cat_name = _config.get_mapping("category_mappings").get(cat_id, "")
        cat_cell = f"{cat_id} | {cat_name}" if cat_name else (cat_id or "\u2014")
        status   = s.get("status") or "none"
        channel  = (s.get("channelname") or "")[:30] or "\u2014"
        title    = (s.get("title")       or "")[:70] or f"[{vid}]"
        rows.append(Tr(
            Td(channel),
            Td(title),
            Td(vid, style="font-size:0.8em;color:#888;"),
            Td(status),
            Td(cat_cell),
            Td(A("\u25b6", href=url, target="_blank")),
        ))

    channel_items = [Li(f"{name} ({cid})") for cid, name in channels.items()]
    return Titled(
        "TubeWrangler",
        _nav(),
        H2("Canais monitorados"),
        Ul(*channel_items) if channel_items else P("Nenhum canal."),
        H2(f"Streams ({len(streams)})"),
        P(A("Ver Playlists e Proxy \u2192", href="/proxy"), style="font-size:0.9em;"),
        Table(
            Thead(Tr(Th("Canal"), Th("Evento"), Th("Video ID"), Th("Status"), Th("Categoria"), Th(""))),
            Tbody(*rows),
        ),
    )


@app.get("/proxy")
def proxy_page(request: Request):
    return proxy_dashboard_page(
        request=request,
        playlist_routes=_PLAYLIST_ROUTES,
        base_url=_get_base_url(request),
    )


@app.get("/config")
def config_page():
    sections = _config.get_all_by_section() if _config else {}
    fields = []
    for section, rows in sections.items():
        fields.append(H3(section))
        for row in rows:
            fields.append(
                Label(row["key"], Input(name=f"{section}__{row['key']}", value=row["value"], type="text"))
            )
    return Titled(
        "Configuracoes",
        _nav(),
        Form(*fields, Button("Salvar", type="submit"), method="post", action="/config"),
    )


@app.post("/config")
async def config_save(req):
    form = await req.form()
    updates = {}
    for k, v in form.items():
        if "__" in k:
            _, key = k.split("__", 1)
            updates[key] = v
    if updates and _config:
        _config.update_many(updates)
    return RedirectResponse("/config", status_code=303)


@app.get("/channels")
def channels_page():
    channels = _state.get_all_channels() if _state else {}
    return Titled(
        "Canais",
        _nav(),
        Ul(*[Li(f"{cid}: {title}") for cid, title in channels.items()])
        if channels else P("Nenhum canal."),
    )


@app.get("/force-sync")
def force_sync():
    if _scheduler:
        _scheduler.trigger_now()
        logger.info("Force-sync acionado pelo usuario.")
    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------------------
# API JSON — channels
# ---------------------------------------------------------------------------

@app.get("/api/channels")
def api_channels_list():
    return JSONResponse([{"id": k, "title": v} for k, v in _state.get_all_channels().items()])


@app.post("/api/channels")
async def api_channels_create(req):
    body  = await req.json()
    cid   = body.get("id",    "").strip()
    title = body.get("title", "").strip()
    if not cid or not title:
        return JSONResponse({"error": "id e title obrigatorios"}, status_code=400)
    _state.channels[cid] = title
    _state.save_to_disk()
    return JSONResponse({"ok": True, "id": cid})


@app.delete("/api/channels/{channel_id}")
def api_channels_delete(channel_id: str):
    if channel_id not in _state.channels:
        return JSONResponse({"error": "nao encontrado"}, status_code=404)
    del _state.channels[channel_id]
    _state.save_to_disk()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# API JSON — streams
# ---------------------------------------------------------------------------

@app.get("/api/streams")
def api_streams_list(status: str = ""):
    streams = _state.get_all_streams()
    if status:
        streams = [s for s in streams if s.get("status") == status]
    return JSONResponse([_serialize_stream(s) for s in streams])


@app.get("/api/streams/{video_id}")
def api_streams_detail(video_id: str):
    stream = _state.streams.get(video_id)
    if not stream:
        return JSONResponse({"error": "nao encontrado"}, status_code=404)
    return JSONResponse(_serialize_stream(stream))


# ---------------------------------------------------------------------------
# API JSON — config
# ---------------------------------------------------------------------------

@app.get("/api/config")
def api_config_get():
    return JSONResponse(_config.get_all())


@app.put("/api/config")
async def api_config_put(req):
    body  = await req.json()
    key   = body.get("key",   "").strip()
    value = str(body.get("value", "")).strip()
    if not key:
        return JSONResponse({"error": "key obrigatorio"}, status_code=400)
    _config.update(key, value)
    return JSONResponse({"ok": True, "key": key, "value": value})


# ---------------------------------------------------------------------------
# API JSON — playlists / EPG
# ---------------------------------------------------------------------------

@app.put("/api/playlists/refresh")
def api_playlists_refresh():
    _scheduler.trigger_now()
    return JSONResponse({"ok": True, "message": "sync agendado"})


@app.get("/api/epg")
def api_epg():
    if _xmltv_generator is None or _state is None:
        return JSONResponse({"error": "Servidor ainda inicializando"}, status_code=503)
    channels = _state.get_all_channels()
    streams  = list(_state.get_all_streams())
    cats     = _categories_db if _categories_db else {}
    content  = _xmltv_generator.generate_xml(channels, streams, cats)
    return Response(content, media_type="application/xml")


# ---------------------------------------------------------------------------
# API — proxy status e controle
# ---------------------------------------------------------------------------

@app.get("/api/proxy/status")
def api_proxy_status():
    return JSONResponse({"streams": streams_status(), "count": len(_buffers)})


@app.delete("/api/proxy/{video_id}")
def api_proxy_stop(video_id: str):
    if not is_stream_active(video_id):
        return JSONResponse({"error": "stream nao encontrado ou ja parado"}, status_code=404)
    stop_stream(video_id)
    return JSONResponse({"ok": True, "video_id": video_id})


# ---------------------------------------------------------------------------
# API — proxy streaming
# ---------------------------------------------------------------------------

async def api_proxy_stream(request):
    video_id   = request.path_params["video_id"]
    user_agent = request.query_params.get("user_agent", "Mozilla/5.0")
    client_id  = f"{int(time.time()*1000)}_{random.randint(1000,9999)}"
    client_ip  = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()

    logger.info(f"[{video_id}] nova conexao proxy  client={client_id}  ip={client_ip}")

    if not is_stream_active(video_id):
        if _state is None:
            return Response("Servidor ainda inicializando", status_code=503)

        stream_info   = _state.streams.get(video_id)
        if stream_info is None:
            logger.warning(f"[{video_id}] video_id nao encontrado no estado")
        status        = stream_info.get("status")       if stream_info else None
        thumbnail_url = stream_info.get("thumbnailurl") if stream_info else None
        watch_url     = f"https://www.youtube.com/watch?v={video_id}"
        placeholder   = _config.get_str("placeholder_image_url")
        thumb_for_ph  = thumbnail_url or placeholder

        logger.info(f"[{video_id}] iniciando stream  status={status!r}  url={watch_url}")

        local_thumb = Path(_thumbnail_manager._cache_dir) / f"{video_id}.jpg"
        if local_thumb.exists():
            thumb_for_ph = str(local_thumb)

        try:
            cmd, _temp = await build_player_command_async(
                video_id=video_id,
                status=status,
                watch_url=watch_url,
                thumbnail_url=thumb_for_ph,
                user_agent=user_agent,
                font_path=FONT_PATH,
                texts_cache_path=TEXTS_CACHE_PATH,
            )
        except Exception as exc:
            logger.error(f"[{video_id}] erro ao montar comando proxy: {exc}")
            return Response(f"Erro ao inicializar stream: {exc}", status_code=500)

        start_stream_reader(video_id, cmd)

        # Registra placeholder para restart automatico se nao for live nem VOD.
        # live  → streamlink corre indefinidamente (sem restart necessario)
        # none  → VOD, tambem corre ate o fim (nao e placeholder)
        # qualquer outro status (upcoming, None, etc.) → e placeholder com -t finito
        if status not in ("live", "none"):
            register_placeholder(video_id, cmd)
            logger.debug(f"[{video_id}] placeholder registrado (status={status!r})")

        logger.info(f"[{video_id}] stream proxy iniciado")

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
        local_index       = max(0, buf.index - 10)
        bytes_sent        = 0
        last_yield_time   = time.monotonic()
        consecutive_empty = 0

        try:
            while True:
                if not is_stream_active(video_id) and video_id not in _buffers:
                    logger.warning(f"[{video_id}][{client_id}] stream encerrado, saindo")
                    break

                # Reinicia placeholder se o processo encerrou naturalmente
                # e ainda ha clientes (incluindo este).
                restart_placeholder_if_needed(video_id)

                chunks, next_index = buf.get_chunks(local_index, count=5)

                if chunks:
                    for chunk in chunks:
                        yield chunk
                        bytes_sent += len(chunk)
                    local_index       = next_index
                    last_yield_time   = time.monotonic()
                    consecutive_empty = 0
                    mgr.update_activity(client_id, bytes_sent)
                else:
                    consecutive_empty += 1
                    await asyncio.sleep(min(0.05 * consecutive_empty, 1.0))

                    if time.monotonic() - last_yield_time > CLIENT_TIMEOUT_S:
                        logger.warning(f"[{video_id}][{client_id}] timeout {CLIENT_TIMEOUT_S}s sem dados")
                        break

                    if buf.index - local_index > 100:
                        logger.warning(f"[{video_id}][{client_id}] cliente atrasado, pulando para frente")
                        local_index       = max(0, buf.index - 10)
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
                        # Novos clientes conectaram durante a espera — nao para
                        return
                    logger.info(f"[{video_id}] sem clientes por {STREAM_IDLE_STOP_S}s, parando stream")
                    stop_stream(video_id)
                asyncio.create_task(_delayed_stop())

    return StreamingResponse(
        generate(),
        media_type="video/mp2t",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


app.routes.append(Route("/api/proxy/{video_id}", endpoint=api_proxy_stream))


# ---------------------------------------------------------------------------
# API — player streaming (legado)
# ---------------------------------------------------------------------------

async def api_player_stream(request):
    video_id   = request.path_params["video_id"]
    user_agent = request.query_params.get("user_agent", "Mozilla/5.0")

    stream_info   = _state.streams.get(video_id)
    status        = stream_info.get("status")       if stream_info else None
    thumbnail_url = stream_info.get("thumbnailurl") if stream_info else None
    watch_url     = f"https://www.youtube.com/watch?v={video_id}"
    placeholder   = _config.get_str("placeholder_image_url")
    thumb_for_ph  = thumbnail_url or placeholder

    local_thumb = Path(_thumbnail_manager._cache_dir) / f"{video_id}.jpg"
    if local_thumb.exists():
        thumb_for_ph = str(local_thumb)

    async def stream_gen():
        proc_logger = logging.getLogger("TubeWrangler.player")
        temp_files  = []
        try:
            cmd, temp_files = await build_player_command_async(
                video_id=video_id,
                status=status,
                watch_url=watch_url,
                thumbnail_url=thumb_for_ph,
                user_agent=user_agent,
                font_path=FONT_PATH,
                texts_cache_path=TEXTS_CACHE_PATH,
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


# ---------------------------------------------------------------------------
# API — thumbnail
# ---------------------------------------------------------------------------

@app.get("/api/thumbnail/{video_id}")
def api_thumbnail(video_id: str):
    if not re.match(r"^[a-zA-Z0-9_-]+$", video_id):
        return JSONResponse({"error": "video_id invalido"}, status_code=400)
    data = _thumbnail_manager.serve(video_id)
    if data:
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})
    return RedirectResponse(
        f"https://i.ytimg.com/vi/{video_id}/maxresdefault_live.jpg",
        status_code=302,
    )


# ---------------------------------------------------------------------------
# Logs SSE + pagina /logs
# ---------------------------------------------------------------------------

@app.get("/api/logs/stream")
async def api_logs_stream():
    async def event_gen():
        snapshot = list(_LOG_BUFFER)
        for _, line in snapshot:
            yield f"data: {line}\n\n"
        last_seq = snapshot[-1][0] if snapshot else 0
        while True:
            await asyncio.sleep(1)
            current     = list(_LOG_BUFFER)
            new_entries = [(seq, line) for seq, line in current if seq > last_seq]
            for seq, line in new_entries:
                yield f"data: {line}\n\n"
                last_seq = seq

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/logs")
def logs_page():
    return Titled(
        "Logs",
        _nav(),
        Div(
            Select(
                Option("DEBUG",   value="DEBUG"),
                Option("INFO",    value="INFO",    selected=True),
                Option("WARNING", value="WARNING"),
                Option("ERROR",   value="ERROR"),
                id="log-level-filter",
                style="margin-right:8px;",
            ),
            Button("Limpar", id="btn-clear", type="button", style="margin-right:8px;"),
            Label(
                Input(type="checkbox", id="auto-scroll", checked=True, style="margin-right:4px;"),
                "Auto-scroll",
            ),
            style="margin-bottom:8px;",
        ),
        Pre(
            id="log-output",
            style="height:80vh;overflow-y:auto;background:#111;color:#eee;font-size:0.78rem;padding:8px;",
        ),
        Style("""
            .log-DEBUG   { color: #888; }
            .log-INFO    { color: #eee; }
            .log-WARNING { color: #f90; }
            .log-ERROR   { color: #f44; font-weight:bold; }
        """),
        Script("""
            const output     = document.getElementById('log-output');
            const filter     = document.getElementById('log-level-filter');
            const autoScroll = document.getElementById('auto-scroll');
            const btnClear   = document.getElementById('btn-clear');
            btnClear.onclick = () => { output.innerHTML = ''; };

            const LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR'];

            function levelOf(line) {
                for (const lv of LEVELS) {
                    if (line.includes(' ' + lv + ' ') || line.includes(' ' + lv + '\\t')) return lv;
                }
                return 'INFO';
            }

            function applyVisibility(span) {
                const minLevel = filter.value;
                const lv = span.dataset.level || 'INFO';
                span.style.display = LEVELS.indexOf(lv) >= LEVELS.indexOf(minLevel) ? '' : 'none';
            }

            function appendLine(line) {
                const span = document.createElement('span');
                const lv   = levelOf(line);
                span.className    = 'log-' + lv;
                span.dataset.level = lv;
                span.textContent   = line + '\\n';
                output.appendChild(span);
                applyVisibility(span);
                if (autoScroll.checked) output.scrollTop = output.scrollHeight;
            }

            const es = new EventSource('/api/logs/stream');
            es.onmessage = e => appendLine(e.data);
            es.onerror   = () => appendLine('[conexao perdida - reconectando...]');

            filter.onchange = () => {
                output.querySelectorAll('span').forEach(applyVisibility);
            };
        """),
    )
