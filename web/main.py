from contextlib import asynccontextmanager
import asyncio
import collections
import logging
import os
import re
from pathlib import Path

from fasthtml.common import *
from starlette.routing import Route
from starlette.responses import JSONResponse, RedirectResponse, Response, StreamingResponse

from core.config import AppConfig
from core.player_router import build_player_command_async
from core.playlist_builder import M3UGenerator, XMLTVGenerator, _resolve_proxy_base_url
from core.scheduler import Scheduler
from core.state_manager import StateManager
from core.thumbnail_manager import ThumbnailManager
from core.youtube_api import YouTubeAPI

TEXTS_CACHE_PATH = Path("/data/textosepg.json")
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# ---------------------------------------------------------------------------
# Logging — buffer circular (SSE /api/logs/stream) + console
# ---------------------------------------------------------------------------

_LOG_BUFFER: collections.deque[tuple[int, str]] = collections.deque(maxlen=1000)
_LOG_SEQ = 0


class _BufferHandler(logging.Handler):
    """Escreve entradas de log no buffer circular para SSE."""

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
    """Configura logging global: buffer circular + console. Idempotente."""
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
# Estado global da aplicacao (inicializado no lifespan)
# ---------------------------------------------------------------------------

_config: Optional[AppConfig] = None
_state: Optional[StateManager] = None
_scheduler: Optional[Scheduler] = None
_thumbnail_manager: Optional[ThumbnailManager] = None
_m3u_generator: Optional[M3UGenerator] = None
_xmltv_generator: Optional[XMLTVGenerator] = None
_categories_db: dict = {}

# Mapeamento: sufixo da rota -> (mode, mode_type)
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
# Lifespan — inicializacao e finalizacao
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


def _nav():
    return Div(
        A("Dashboard",  href="/",           style="margin-right:12px;"),
        A("Force Sync", href="/force-sync", style="margin-right:12px;"),
        A("Config",     href="/config",     style="margin-right:12px;"),
        A("Logs",       href="/logs"),
        style="padding:8px 0 16px 0; border-bottom: 1px solid #ccc; margin-bottom:16px;",
    )


def _serve_playlist_onthefly(mode: str, mode_type: str) -> Response:
    if _m3u_generator is None or _state is None:
        return Response("Servidor ainda inicializando", status_code=503)
    streams    = list(_state.get_all_streams())
    cats       = _categories_db if _categories_db else {}
    proxy_base = _resolve_proxy_base_url(_config) if mode_type == "proxy" else ""
    content    = _m3u_generator.generate_playlist(
        streams, cats, mode=mode, mode_type=mode_type, proxy_base_url=proxy_base
    )
    return Response(content, media_type="audio/x-mpegurl")


# ---------------------------------------------------------------------------
# Rotas de playlist (dinamicas)
# ---------------------------------------------------------------------------

for _playlist_name, (_mode, _mode_type) in _PLAYLIST_ROUTES.items():
    def _make_playlist_route(mode=_mode, mode_type=_mode_type, playlist_name=_playlist_name):
        @app.get(f"/playlist/{playlist_name}")
        def _playlist_route():
            return _serve_playlist_onthefly(mode, mode_type)
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


# Workaround: FastHTML intercepta URLs com extensao — registrar via Starlette
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
def home():
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
        rows.append(Tr(
            Td((s.get("channelname") or "")[:30]),
            Td((s.get("title")       or "")[:70]),
            Td(s.get("status") or "none"),
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
        Table(
            Thead(Tr(Th("Canal"), Th("Evento"), Th("Status"), Th("Categoria"), Th(""))),
            Tbody(*rows),
        ),
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
# API — player streaming (Starlette direto — contorna catch-all FastHTML)
# ---------------------------------------------------------------------------

async def api_player_stream(request):
    """Handler de streaming MPEG-TS para /api/player/{video_id}.

    Delega toda a logica de comando para build_player_command_async(),
    que e a unica fonte de verdade para todos os status (live, none, placeholder).
    """
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
            # Unica chamada — player_router decide o comando para qualquer status
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


# Workaround: rota com path param precisa ser append no Starlette
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
