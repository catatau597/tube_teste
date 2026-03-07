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

from core.config import AppConfig, DEFAULTS
from core.player_router import build_player_command_async
from core.playlist_builder import M3UGenerator, XMLTVGenerator, _resolve_proxy_base_url
from core.proxy_manager import (
    start_stream_reader, stop_stream, is_stream_active, streams_status,
    register_placeholder, restart_placeholder_if_needed,
    _buffers, _managers, _processes,
    INIT_TIMEOUT_S, CLIENT_TIMEOUT_S, STREAM_IDLE_STOP_S,
    LIVE_PREROLL_BYTES, LIVE_PREROLL_WAIT_S,
    INITIAL_BEHIND_BYTES, TARGET_BATCH_BYTES, MAX_BATCH_BYTES,
    CLIENT_JUMP_THRESHOLD_BYTES,
    set_debug_mode, get_debug_mode, get_stream_debug_info,
)
from core.scheduler import Scheduler
from core.state_manager import StateManager
from core.stream_ingress import build_live_fallback_ingress_plan, resolve_proxy_ingress_plan
from core.thumbnail_manager import ThumbnailManager
from core.vod_proxy import vod_proxy_manager
from core.vod_verifier import VodVerifier
from core.youtube_api import YouTubeAPI
from web.routes.playlist_dashboard import playlist_dashboard_page
from web.routes.channels import channels_page as _channels_page
from web.routes.eventos import eventos_page as _eventos_page
from web.routes.title_format import title_format_page as _title_format_page
from web.routes.proxy_dashboard import scheduler_cards, active_streams_card, dashboard_js
from web.layout import _page_shell

TEXTS_CACHE_PATH = Path("/data/textosepg.json")
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

STREAMLINK_FAST_FAIL_S = 8

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
_proxy_start_locks: dict[str, asyncio.Lock] = {}

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
    "/proxy":                      "/playlist",
}


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app):
    global _config, _state, _scheduler, _thumbnail_manager
    global _m3u_generator, _xmltv_generator, _categories_db

    _config = AppConfig()
    hide_access = _config.get_bool("hide_access_logs")
    _setup_logging(_config.get_str("log_level") or "INFO", hide_access=hide_access)
    logger.info("=== TubeWrangler iniciando ===")

    # Inicializa modo debug de streaming
    streaming_debug = _config.get_bool("streaming_debug_enabled")
    set_debug_mode(streaming_debug)

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

    api_keys = _config.get_list("youtube_api_keys")
    handles  = [h.strip() for h in _config.get_str("target_channel_handles").split(",") if h.strip()]
    chan_ids = [i.strip() for i in _config.get_str("target_channel_ids").split(",") if i.strip()]
    logger.info(f"Handles configurados : {handles}")
    logger.info(f"IDs configurados     : {chan_ids}")

    scraper = YouTubeAPI(api_keys)

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
        logger.warning("Nenhum canal alvo. Verifique /canais ou /config/credentials.")

    _force_event = asyncio.Event()
    _scheduler = Scheduler(_config, scraper, _state)
    _scheduler.set_force_event(_force_event)
    _scheduler.set_thumbnail_manager(_thumbnail_manager)
    _m3u_generator = M3UGenerator(_config)
    _xmltv_generator = XMLTVGenerator(_config)
    _categories_db = {}
    _scheduler.set_categories_db(_categories_db)

    vod_verifier = VodVerifier(scraper, _state, _config)
    _scheduler.set_vod_verifier(vod_verifier)

    task = asyncio.create_task(_scheduler.run())
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
        _state.save_to_disk()
        logger.info("=== TubeWrangler encerrado ===")


# ---------------------------------------------------------------------------
# App FastHTML
# ---------------------------------------------------------------------------

app, rt = fast_app(
    lifespan=lifespan,
    hdrs=[],
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
    host = request.headers.get("host", "")
    if host:
        scheme = "https" if request.url.scheme == "https" else "http"
        return f"{scheme}://{host}"
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    port      = request.url.port or 8888
    return f"http://{client_ip}:{port}"


def _get_stream_info(video_id: str) -> tuple[dict | None, str | None, str | None, str]:
    stream_info = _state.streams.get(video_id) if _state else None
    status = stream_info.get("status") if stream_info else None
    thumbnail_url = stream_info.get("thumbnailurl") if stream_info else None
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    return stream_info, status, thumbnail_url, watch_url


def _serve_playlist_onthefly(mode: str, mode_type: str, request=None) -> Response:
    if _m3u_generator is None or _state is None:
        return Response("Servidor ainda inicializando", status_code=503)
    streams    = list(_state.get_all_streams())
    cats       = _categories_db if _categories_db else {}
    proxy_base = ""
    if mode_type == "proxy":
        if request is not None:
            proxy_base = _get_base_url(request)
        else:
            proxy_base = _resolve_proxy_base_url(_config)
    content = _m3u_generator.generate_playlist(
        streams, cats, mode=mode, mode_type=mode_type,
        thumbnail_manager=_thumbnail_manager,
        proxy_base_url=proxy_base,
    )
    return Response(content, media_type="audio/x-mpegurl")


# ---------------------------------------------------------------------------
# Rotas de playlist
# ---------------------------------------------------------------------------

for _playlist_name, (_mode, _mode_type) in _PLAYLIST_ROUTES.items():
    def _make_playlist_route(mode=_mode, mode_type=_mode_type):
        async def _playlist_route(request):
            return _serve_playlist_onthefly(mode, mode_type, request=request)
        app.routes.append(Route(f"/playlist/{_playlist_name}", endpoint=_playlist_route))
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
# Helpers de formulário de config
# ---------------------------------------------------------------------------

_TOGGLE_STYLE = Style("""
    .bool-toggle {
        display: inline-flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 14px;
        cursor: pointer;
        user-select: none;
    }
    .bool-toggle .toggle-pill {
        display: inline-flex;
        align-items: center;
        padding: 4px 14px;
        border-radius: 999px;
        font-size: 0.82rem;
        font-weight: 600;
        border: 1.5px solid transparent;
        transition: background 0.15s, color 0.15s, border-color 0.15s;
        cursor: pointer;
    }
    .bool-toggle .toggle-pill.on {
        background: #1f6feb;
        color: #fff;
        border-color: #388bfd;
    }
    .bool-toggle .toggle-pill.off {
        background: transparent;
        color: #8b949e;
        border-color: #30363d;
    }
    .bool-toggle .toggle-label {
        font-size: 0.9rem;
        color: #e6edf3;
    }
    .api-method-toggle {
        display: flex;
        gap: 10px;
        margin-bottom: 20px;
        flex-wrap: wrap;
    }
    .api-method-toggle button {
        flex: 1;
        min-width: 220px;
        padding: 12px 18px;
        border: 2px solid #30363d;
        border-radius: 8px;
        background: transparent;
        color: #8b949e;
        font-size: 0.88rem;
        font-weight: 600;
        cursor: pointer;
        transition: all 0.2s;
    }
    .api-method-toggle button.active {
        border-color: #388bfd;
        background: #1f6feb;
        color: #fff;
    }
    .api-method-toggle button .method-title {
        display: block;
        font-size: 0.95rem;
        margin-bottom: 4px;
    }
    .api-method-toggle button .method-desc {
        display: block;
        font-size: 0.75rem;
        opacity: 0.8;
    }
""")

_TOGGLE_JS = Script("""
    function _toggleBool(btn, hiddenId) {
        const hidden = document.getElementById(hiddenId);
        const isOn   = hidden.value === 'true';
        hidden.value = isOn ? 'false' : 'true';
        btn.textContent = isOn ? 'Desligado' : 'Ligado';
        btn.className   = 'toggle-pill ' + (isOn ? 'off' : 'on');
    }
    function _selectApiMethod(value) {
        document.getElementById('hidden_use_playlist_items').value = value;
        document.querySelectorAll('.api-method-toggle button').forEach(b => b.classList.remove('active'));
        document.getElementById('btn-api-' + value).classList.add('active');
    }
""")


def _bool_toggle(key: str, value: bool, label: str) -> Div:
    hidden_id  = f"hidden_{key}"
    pill_cls   = "toggle-pill on" if value else "toggle-pill off"
    pill_label = "Ligado" if value else "Desligado"
    return Div(
        Input(type="hidden", name=key, value="true" if value else "false", id=hidden_id),
        Button(
            pill_label,
            type="button",
            cls=pill_cls,
            onclick=f"_toggleBool(this, '{hidden_id}')",
        ),
        Span(label, cls="toggle-label"),
        cls="bool-toggle",
    )


def _bool_keys_for_section(section_key: str) -> list[str]:
    return [
        k for k, (_, sec, _, vtype) in DEFAULTS.items()
        if sec == section_key and vtype == "bool"
    ]


def _config_form_fields(rows: list, section: str) -> list:
    fields = []
    for row in rows:
        key   = row["key"]
        value = row["value"]
        desc  = row.get("description", "")
        vtype = row.get("value_type", "str")
        if vtype == "bool":
            fields.append(_bool_toggle(key, value.lower() == "true", desc or key))
        else:
            fields.append(
                Label(
                    Span(desc or key, style="display:block;margin-bottom:4px;"),
                    Input(name=key, value=value, type="text", id=f"field_{key}"),
                )
            )
    return fields


def _config_page(section_key: str, title: str, active_key: str, saved: bool = False):
    sections = _config.get_all_by_section() if _config else {}
    rows = sections.get(section_key, [])
    fields = _config_form_fields(rows, section_key)
    alert = Div("\u2705 Configura\u00e7\u00f5es salvas com sucesso.",
                cls="alert alert-success") if saved else ""
    return _page_shell(
        title, active_key,
        alert,
        _TOGGLE_STYLE,
        _TOGGLE_JS,
        Div(
            Form(
                *fields,
                Div(
                    Button("Salvar", type="submit"),
                    style="margin-top:20px;",
                ),
                method="post",
                action=f"/config/{section_key}",
            ),
            cls="card",
        ),
    )


def _apply_bool_defaults(form_data: dict, section_key: str) -> dict:
    updates = dict(form_data)
    for k in _bool_keys_for_section(section_key):
        if k not in updates:
            updates[k] = "false"
    return updates


# ---------------------------------------------------------------------------
# Rotas HTML — Dashboard
# ---------------------------------------------------------------------------

@app.get("/")
def home(request: Request):
    streams  = list(_state.get_all_streams()) if _state else []
    channels = _state.get_all_channels()      if _state else {}

    n_live = sum(1 for s in streams if s.get("status") == "live")
    n_up   = sum(1 for s in streams if s.get("status") == "upcoming")
    n_vod  = sum(1 for s in streams if s.get("status") in ("vod", "recorded"))
    channel_count = len(channels)

    return _page_shell(
        "Dashboard", "dashboard",
        # --- cards de contagem ---
        Div(
            H2("Vis\u00e3o Geral"),
            Div(
                Div(
                    Span(str(channel_count), style="font-size:2rem;font-weight:700;color:#58a6ff;"),
                    Br(),
                    Span("Canais", cls="text-muted"),
                    Br(),
                    A("Gerenciar \u2192", href="/canais", style="font-size:0.82rem;"),
                    cls="card", style="text-align:center;padding:16px 24px;min-width:120px;",
                ),
                Div(
                    Span(str(n_live), style="font-size:2rem;font-weight:700;color:#f85149;"),
                    Br(),
                    Span("\U0001f534 Live", cls="text-muted"),
                    cls="card", style="text-align:center;padding:16px 24px;min-width:120px;",
                ),
                Div(
                    Span(str(n_up), style="font-size:2rem;font-weight:700;color:#d29922;"),
                    Br(),
                    Span("\U0001f7e1 Upcoming", cls="text-muted"),
                    cls="card", style="text-align:center;padding:16px 24px;min-width:120px;",
                ),
                Div(
                    Span(str(n_vod), style="font-size:2rem;font-weight:700;color:#8b949e;"),
                    Br(),
                    Span("\U0001f4fc VOD", cls="text-muted"),
                    cls="card", style="text-align:center;padding:16px 24px;min-width:120px;",
                ),
                style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:8px;",
            ),
            P(
                A("\U0001f4c5 Ver todos os Eventos \u2192", href="/eventos"),
                "   ",
                A("\U0001f4c1 Playlists \u2192", href="/playlist"),
                cls="text-muted",
                style="margin-top:4px;",
            ),
            cls="card",
            style="margin-bottom:20px;",
        ),
        # --- card do scheduler ---
        scheduler_cards(_scheduler),
        # --- tabela de proxy ativos ---
        active_streams_card(),
        # --- JS do dashboard ---
        dashboard_js(),
    )


# ---------------------------------------------------------------------------
# Rotas HTML — Canais
# ---------------------------------------------------------------------------

@app.get("/canais")
def canais_page():
    return _channels_page(_state, _scheduler, config=_config)


@app.get("/force-sync")
def force_sync():
    if _scheduler:
        _scheduler.trigger_now()
        logger.info("Force-sync acionado pelo usuario.")
    return RedirectResponse("/", status_code=303)


# ---------------------------------------------------------------------------
# Rotas HTML — Playlist
# ---------------------------------------------------------------------------

@app.get("/playlist")
def playlist_page(request: Request):
    return playlist_dashboard_page(
        request=request,
        playlist_routes=_PLAYLIST_ROUTES,
        base_url=_get_base_url(request),
    )


# ---------------------------------------------------------------------------
# Rotas HTML — Eventos
# ---------------------------------------------------------------------------

@app.get("/eventos")
def eventos_page_route():
    return _eventos_page(_state)


# ---------------------------------------------------------------------------
# Rotas HTML — Config
# ---------------------------------------------------------------------------

@app.get("/config")
def config_redirect():
    return RedirectResponse("/config/credentials", status_code=302)


@app.get("/config/credentials")
def config_credentials(saved: str = ""):
    if not _config:
        return _page_shell("API & Credenciais", "config_credentials", P("Config n\u00e3o inicializado."))
    alert = Div("\u2705 Configura\u00e7\u00f5es salvas com sucesso.",
                cls="alert alert-success") if saved == "1" else ""
    api_keys_val = _config.get_raw("youtube_api_keys")
    use_playlist_items = _config.get_bool("use_playlist_items")
    method_val = "true" if use_playlist_items else "false"
    return _page_shell(
        "API & Credenciais", "config_credentials",
        alert,
        _TOGGLE_STYLE,
        _TOGGLE_JS,
        Div(
            Form(
                Label(
                    Span("Chaves de API do YouTube (v\u00edrgula para m\u00faltiplas)",
                         style="display:block;margin-bottom:4px;"),
                    Input(name="youtube_api_keys", value=api_keys_val,
                          type="text", id="field_youtube_api_keys",
                          placeholder="AIzaSy..., AIzaSy..."),
                ),
                H3("M\u00e9todo de API", style="margin-top:24px;margin-bottom:10px;"),
                P(
                    "Escolha como buscar v\u00eddeos dos canais. O m\u00e9todo playlistItems \u00e9 mais eficiente "
                    "e economiza quota da API, mas alguns canais podem n\u00e3o funcionar.",
                    cls="text-muted",
                    style="font-size:0.85rem;margin-bottom:12px;",
                ),
                Input(type="hidden", name="use_playlist_items", value=method_val,
                      id="hidden_use_playlist_items"),
                Div(
                    Button(
                        Span("playlistItems", cls="method-title"),
                        Span("\u2705 Menos chamadas \u2022 Mais eficiente \u2022 Recomendado", cls="method-desc"),
                        type="button",
                        id="btn-api-true",
                        cls="active" if use_playlist_items else "",
                        onclick="_selectApiMethod('true')",
                    ),
                    Button(
                        Span("search.list", cls="method-title"),
                        Span("\u26a0\ufe0f Mais chamadas \u2022 Fallback/legado", cls="method-desc"),
                        type="button",
                        id="btn-api-false",
                        cls="active" if not use_playlist_items else "",
                        onclick="_selectApiMethod('false')",
                    ),
                    cls="api-method-toggle",
                ),
                Div(Button("Salvar", type="submit"), style="margin-top:20px;"),
                method="post",
                action="/config/credentials",
            ),
            cls="card",
        ),
    )


@app.post("/config/credentials")
async def config_credentials_save(req):
    form = await req.form()
    if _config:
        data = {k: v for k, v in dict(form).items() if k in ("youtube_api_keys", "use_playlist_items")}
        _config.update_many(data)
    return RedirectResponse("/config/credentials?saved=1", status_code=303)


@app.get("/config/scheduler")
def config_scheduler(saved: str = ""):
    return _config_page("scheduler", "Agendador", "config_scheduler", saved == "1")


@app.post("/config/scheduler")
async def config_scheduler_save(req):
    form = await req.form()
    if _config:
        _config.update_many(_apply_bool_defaults(dict(form), "scheduler"))
    return RedirectResponse("/config/scheduler?saved=1", status_code=303)


@app.get("/config/playlist")
def config_playlist_page(saved: str = ""):
    if not _config:
        return _page_shell("Playlist", "config_playlist", P("Config n\u00e3o inicializado."))
    
    alert = Div("\u2705 Configura\u00e7\u00f5es salvas com sucesso.",
                cls="alert alert-success") if saved == "1" else ""
    
    use_invisible = _config.get_bool("use_invisible_placeholder")
    placeholder_url = _config.get_raw("placeholder_image_url")
    thumb_dir = _config.get_raw("thumbnail_cache_directory")
    
    return _page_shell(
        "Playlist", "config_playlist",
        alert,
        _TOGGLE_STYLE,
        _TOGGLE_JS,
        Div(
            Form(
                _bool_toggle("use_invisible_placeholder", use_invisible,
                             "Usar placeholder invis\u00edvel no M3U"),
                Label(
                    Span("URL da imagem placeholder para streams sem thumb",
                         style="display:block;margin-bottom:4px;"),
                    Input(name="placeholder_image_url", value=placeholder_url, type="text"),
                ),
                Label(
                    Span("Diret\u00f3rio de cache de thumbnails",
                         style="display:block;margin-bottom:4px;"),
                    Input(value=thumb_dir, type="text", disabled=True,
                          style="background:#161b22;color:#8b949e;cursor:not-allowed;"),
                    P(
                        "[BLOQUEADO] Padr\u00e3o do sistema gerenciado automaticamente. "
                        "N\u00e3o recomendamos alterar este valor.",
                        cls="text-muted",
                        style="font-size:0.78rem;margin-top:4px;margin-bottom:0;",
                    ),
                ),
                Div(Button("Salvar", type="submit"), style="margin-top:20px;"),
                method="post",
                action="/config/playlist",
            ),
            cls="card",
        ),
    )


@app.post("/config/playlist")
async def config_playlist_save(req):
    form = await req.form()
    if _config:
        data = _apply_bool_defaults(dict(form), "playlist_output")
        # Remove thumbnail_cache_directory do form se vier (não deve, pois disabled)
        data.pop("thumbnail_cache_directory", None)
        _config.update_many(data)
    return RedirectResponse("/config/playlist?saved=1", status_code=303)


@app.get("/config/technical")
def config_technical(saved: str = ""):
    return _config_page("technical", "T\u00e9cnico", "config_technical", saved == "1")


@app.post("/config/technical")
async def config_technical_save(req):
    form = await req.form()
    if _config:
        _config.update_many(_apply_bool_defaults(dict(form), "technical"))
    return RedirectResponse("/config/technical?saved=1", status_code=303)


# ---------------------------------------------------------------------------
# Rota HTML — /config/vod-verification
# ---------------------------------------------------------------------------

@app.get("/config/vod-verification")
def config_vod_verification(saved: str = ""):
    if not _config:
        return _page_shell("Verificação de VODs", "config_vod_verification", P("Config não inicializado."))

    cfg = _config
    alert = Div("✅ Configurações salvas com sucesso.",
                cls="alert alert-success") if saved == "1" else ""

    post_live_enabled = cfg.get_bool("vod_post_live_check_enabled")
    initial_delay     = cfg.get_raw("vod_post_live_initial_delay_seconds")
    health_enabled    = cfg.get_bool("vod_health_check_enabled")
    health_interval   = cfg.get_raw("vod_health_check_interval_minutes")

    return _page_shell(
        "Verificação de VODs", "config_vod_verification",
        alert,
        _TOGGLE_STYLE,
        _TOGGLE_JS,
        Div(
            H2("Verificação Pós-Live"),
            P(
                "Após uma live terminar, verifica automaticamente se o VOD ficou disponível "
                "antes de incluí-lo na playlist.",
                cls="text-muted",
                style="font-size:0.85rem;margin-bottom:16px;",
            ),
            Form(
                _bool_toggle("vod_post_live_check_enabled", post_live_enabled,
                             "Ativar verificação pós-live"),
                Label(
                    Span("Delay inicial (segundos)",
                         style="display:block;margin-bottom:4px;"),
                    Input(name="vod_post_live_initial_delay_seconds",
                          value=initial_delay,
                          type="number", min="0", step="1",
                          style="max-width:200px;"),
                ),
                Div(
                    "ℹ️ Retries automáticos após falha: ",
                    Strong("2min → 5min → 10min"),
                    cls="alert alert-info",
                    style="margin-top:8px;margin-bottom:16px;font-size:0.85rem;",
                ),
                H2("Health Check Periódico", style="margin-top:24px;"),
                P(
                    "Verifica periodicamente se os VODs no cache ainda estão acessíveis. "
                    "VODs indisponíveis são removidos das playlists geradas.",
                    cls="text-muted",
                    style="font-size:0.85rem;margin-bottom:16px;",
                ),
                _bool_toggle("vod_health_check_enabled", health_enabled,
                             "Ativar health check periódico de VODs"),
                Label(
                    Span("Intervalo de verificação (minutos)",
                         style="display:block;margin-bottom:4px;"),
                    Input(name="vod_health_check_interval_minutes",
                          value=health_interval,
                          type="number", min="1", step="1",
                          style="max-width:200px;"),
                ),
                Div(Button("Salvar", type="submit"), style="margin-top:20px;"),
                method="post",
                action="/config/vod-verification",
            ),
            cls="card",
        ),
    )


@app.post("/config/vod-verification")
async def config_vod_verification_save(req):
    form = await req.form()
    if _config:
        allowed_keys = {
            "vod_post_live_check_enabled",
            "vod_post_live_initial_delay_seconds",
            "vod_health_check_enabled",
            "vod_health_check_interval_minutes",
        }
        data = _apply_bool_defaults(
            {k: v for k, v in dict(form).items() if k in allowed_keys},
            "vod_verification",
        )
        _config.update_many(data)
    return RedirectResponse("/config/vod-verification?saved=1", status_code=303)


# ---------------------------------------------------------------------------
# Rota HTML — /config/filters
# ---------------------------------------------------------------------------

@app.get("/config/filters")
def config_filters(saved: str = ""):
    if not _config:
        return _page_shell("Filtros", "config_filters", P("Config n\u00e3o inicializado."))

    cfg = _config
    alert = Div("\u2705 Filtros salvos com sucesso.", cls="alert alert-success") if saved == "1" else ""

    filter_by_cat       = cfg.get_bool("filter_by_category")
    allowed_ids         = cfg.get_raw("allowed_category_ids")
    cat_mappings        = cfg.get_raw("category_mappings")
    shorts_max_s        = cfg.get_raw("shorts_max_duration_s")
    shorts_words_raw    = cfg.get_raw("shorts_block_words")
    shorts_words        = [w.strip() for w in shorts_words_raw.split(",") if w.strip()]
    epg_cleanup         = cfg.get_bool("epg_description_cleanup")
    keep_recorded       = cfg.get_bool("keep_recorded_streams")
    max_recorded        = cfg.get_raw("max_recorded_per_channel")
    retention_days      = cfg.get_raw("recorded_retention_days")
    max_schedule        = cfg.get_raw("max_schedule_hours")
    max_upcoming        = cfg.get_raw("max_upcoming_per_channel")

    def _tag_list_with_input(words: list, field_name: str, hidden_name: str) -> Div:
        tags = []
        for w in words:
            tags.append(
                Span(
                    Span(w, cls="tag-text"),
                    Button("\u00d7", cls="remove-tag", type="button",
                           onclick=f"removeTag(this, '{hidden_name}')"),
                    cls="tag",
                )
            )
        return Div(
            Input(type="hidden", name=hidden_name,
                  value=",".join(words), id=f"hidden_{hidden_name}"),
            Div(*tags, id=f"tags_{hidden_name}", cls="tag-list"),
            Div(
                Input(type="text", id=f"input_{field_name}",
                      placeholder="Adicionar... (Enter)",
                      style="width:200px;display:inline-block;margin-right:8px;"),
                Button("+ Adicionar", type="button", cls="btn-secondary",
                       onclick=f"addTag('{field_name}', '{hidden_name}')",
                       style="font-size:0.82rem;padding:5px 12px;"),
                style="margin-top:8px;",
            ),
        )

    tags_js = Script("""
        function _syncHidden(hiddenName) {
            const container = document.getElementById('tags_' + hiddenName);
            const hidden    = document.getElementById('hidden_' + hiddenName);
            const texts = Array.from(container.querySelectorAll('.tag-text'))
                               .map(el => el.textContent.trim())
                               .filter(Boolean);
            hidden.value = texts.join(',');
        }

        function removeTag(btn, hiddenName) {
            btn.closest('.tag').remove();
            _syncHidden(hiddenName);
        }

        function addTag(inputId, hiddenName) {
            const inp = document.getElementById('input_' + inputId);
            const val = inp.value.trim();
            if (!val) return;
            const container = document.getElementById('tags_' + hiddenName);
            const tag = document.createElement('span');
            tag.className = 'tag';
            tag.innerHTML = `<span class="tag-text">${val}</span>`
                          + `<button class="remove-tag" type="button"
                              onclick="removeTag(this,'${hiddenName}')">\\u00d7</button>`;
            container.appendChild(tag);
            inp.value = '';
            _syncHidden(hiddenName);
        }

        document.querySelectorAll('[id^="input_"]').forEach(inp => {
            inp.addEventListener('keydown', e => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    const hiddenName = inp.id.replace('input_new_', '');
                    addTag(inp.id.replace('input_', ''), hiddenName);
                }
            });
        });
    """)

    return _page_shell(
        "Filtros", "config_filters",
        alert,
        _TOGGLE_STYLE,
        _TOGGLE_JS,
        Form(
            Div(
                H2("Filtro de Categoria"),
                P("Quando ativo, apenas streams com IDs de categoria permitidos entram na playlist.",
                  cls="text-muted"),
                _bool_toggle("filter_by_category", filter_by_cat, "Ativar filtro por categoria"),
                Label(
                    Span("IDs permitidos (v\u00edrgula) \u2014 ex: 17,22,25",
                         style="display:block;margin-bottom:4px;"),
                    Input(name="allowed_category_ids", value=allowed_ids, type="text"),
                ),
                Label(
                    Span("Renomear categorias: ID|Nome (v\u00edrgula) \u2014 n\u00e3o filtra, s\u00f3 exibe",
                         style="display:block;margin-bottom:4px;"),
                    Input(name="category_mappings", value=cat_mappings, type="text"),
                ),
                cls="card",
            ),
            Div(
                H2("Filtro de Shorts"),
                P(
                    "Shorts com dura\u00e7\u00e3o conhecida s\u00e3o bloqueados pelo campo de segundos. "
                    "Para upcoming/live (dura\u00e7\u00e3o ainda desconhecida), use palavras-chave.",
                    cls="text-muted",
                ),
                Label(
                    Span("Dura\u00e7\u00e3o m\u00e1xima (s) \u2014 0 = desativado",
                         style="display:block;margin-bottom:4px;"),
                    Input(name="shorts_max_duration_s", value=shorts_max_s,
                          type="number", style="width:140px;"),
                ),
                Label(
                    Span("Palavras bloqueadas (t\u00edtulo/tags)",
                         style="display:block;margin-bottom:4px;"),
                    _tag_list_with_input(shorts_words, "new_short_word", "shorts_block_words"),
                ),
                cls="card",
            ),
            Div(
                H2("VOD / Grava\u00e7\u00f5es"),
                _bool_toggle("keep_recorded_streams", keep_recorded, "Manter streams gravados (ex-live) na playlist VOD"),
                _bool_toggle("epg_description_cleanup", epg_cleanup, "Manter apenas o primeiro par\u00e1grafo da descri\u00e7\u00e3o no EPG"),
                Label(
                    Span("M\u00e1ximo de grava\u00e7\u00f5es por canal",
                         style="display:block;margin-bottom:4px;"),
                    Input(name="max_recorded_per_channel", value=max_recorded,
                          type="number", style="width:100px;"),
                ),
                Label(
                    Span("Dias de reten\u00e7\u00e3o de gravados",
                         style="display:block;margin-bottom:4px;"),
                    Input(name="recorded_retention_days", value=retention_days,
                          type="number", style="width:100px;"),
                ),
                cls="card",
            ),
            Div(
                H2("Agendamentos futuros"),
                Label(
                    Span("Limite futuro em horas para upcoming (ex: 72)",
                         style="display:block;margin-bottom:4px;"),
                    Input(name="max_schedule_hours", value=max_schedule,
                          type="number", style="width:100px;"),
                ),
                Label(
                    Span("M\u00e1ximo de agendamentos por canal",
                         style="display:block;margin-bottom:4px;"),
                    Input(name="max_upcoming_per_channel", value=max_upcoming,
                          type="number", style="width:100px;"),
                ),
                cls="card",
            ),
            Div(Button("Salvar filtros", type="submit"), style="margin-top:8px;"),
            method="post",
            action="/config/filters",
        ),
        tags_js,
    )


@app.post("/config/filters")
async def config_filters_save(req):
    form = await req.form()
    if not _config:
        return RedirectResponse("/config/filters", status_code=303)
    _config.update_many(_apply_bool_defaults(dict(form), "filters"))
    return RedirectResponse("/config/filters?saved=1", status_code=303)


# ---------------------------------------------------------------------------
# Rota HTML — /config/title-format
# ---------------------------------------------------------------------------

@app.get("/config/title-format")
def config_title_format(saved: str = ""):
    if not _config:
        return _page_shell("Formato de T\u00edtulo", "config_title_format",
                           P("Config n\u00e3o inicializado."))
    return _title_format_page(_config, saved=saved == "1")


@app.post("/config/title-format")
async def config_title_format_save(req):
    if not _config:
        return RedirectResponse("/config/title-format", status_code=303)

    form = dict(await req.form())

    # Ordem dos componentes
    order_raw = form.get("title_components_order", "channel,status,title")
    _config.update_many({"title_components_order": order_raw})

    # Componentes habilitados
    all_comps = [c.strip() for c in order_raw.split(",") if c.strip()]
    enabled   = []
    for comp in all_comps:
        val = form.get(f"comp_enabled_{comp}", "false")
        if val == "true":
            enabled.append(comp)
    if "title" not in enabled:
        enabled.append("title")
    _config.update_many({"title_components_enabled": ",".join(enabled)})

    # Colchetes por componente
    brackets = [c for c in all_comps if form.get(f"comp_brackets_{c}", "false") == "true"
                for comp in [c]]
    _config.update_many({"title_components_brackets": ",".join(brackets)})

    # Expressoes a remover (campo hidden sincronizado pelo JS de tags)
    exprs_raw = form.get("title_filter_expressions", "")
    _config.update_many({"title_filter_expressions": exprs_raw})

    # Toggle strip emojis
    strip_emojis = form.get("title_strip_emojis", "false")
    _config.update_many({"title_strip_emojis": strip_emojis})

    return RedirectResponse("/config/title-format?saved=1", status_code=303)


# ---------------------------------------------------------------------------
# Rota HTML — /config/channels  (mapeamento de nomes de canal)
# ---------------------------------------------------------------------------

@app.post("/config/channels")
async def config_channels_save(req):
    if not _config:
        return RedirectResponse("/canais", status_code=303)
    form = dict(await req.form())
    mapping_val = form.get("channel_name_mappings", "")
    _config.update_many({"channel_name_mappings": mapping_val})
    return RedirectResponse("/canais?saved=1", status_code=303)


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


@app.post("/api/channels/add")
async def api_channels_add(req):
    if _state is None or _config is None:
        return JSONResponse({"error": "Servidor ainda inicializando"}, status_code=503)
    body   = await req.json()
    cid    = body.get("id",     "").strip()
    handle = body.get("handle", "").strip().lstrip("@")
    if not cid and not handle:
        return JSONResponse({"error": "Forne\u00e7a id ou handle"}, status_code=400)
    api_keys = _config.get_list("youtube_api_keys")
    scraper  = YouTubeAPI(api_keys)
    if handle and not cid:
        resolved = scraper.resolve_channel_handles_to_ids([handle], _state)
        if not resolved:
            return JSONResponse({"error": f"Handle @{handle} n\u00e3o encontrado"}, status_code=404)
        cid = list(resolved.keys())[0]
    titles = scraper.ensure_channel_titles({cid}, _state)
    title  = titles.get(cid) or cid
    _state.channels[cid] = title
    _state.save_to_disk()
    logger.info(f"Canal adicionado via UI: {cid} ({title})")
    return JSONResponse({"ok": True, "id": cid, "title": title})


@app.delete("/api/channels/{channel_id}")
def api_channels_delete(channel_id: str):
    if _state is None or channel_id not in _state.channels:
        return JSONResponse({"error": "nao encontrado"}, status_code=404)
    del _state.channels[channel_id]
    frozen = getattr(_state, "frozen_channels", set())
    frozen.discard(channel_id)
    _state.save_to_disk()
    logger.info(f"Canal deletado via UI: {channel_id}")
    return JSONResponse({"ok": True})


@app.post("/api/channels/{channel_id}/sync")
def api_channels_sync(channel_id: str):
    if _scheduler is None:
        return JSONResponse({"error": "Scheduler n\u00e3o dispon\u00edvel"}, status_code=503)
    _scheduler.trigger_now()
    logger.info(f"Sync for\u00e7ado via UI para canal: {channel_id}")
    return JSONResponse({"ok": True, "channel_id": channel_id})


@app.post("/api/channels/{channel_id}/freeze")
def api_channels_freeze(channel_id: str):
    if _state is None:
        return JSONResponse({"error": "State n\u00e3o dispon\u00edvel"}, status_code=503)
    if not hasattr(_state, "frozen_channels"):
        _state.frozen_channels = set()
    if channel_id in _state.frozen_channels:
        _state.frozen_channels.discard(channel_id)
        frozen = False
    else:
        _state.frozen_channels.add(channel_id)
        frozen = True
    _state.save_to_disk()
    logger.info(f"Canal {'congelado' if frozen else 'descongelado'} via UI: {channel_id}")
    return JSONResponse({"ok": True, "channel_id": channel_id, "frozen": frozen})


# ---------------------------------------------------------------------------
# API JSON — scheduler
# ---------------------------------------------------------------------------

@app.get("/api/scheduler/status")
def api_scheduler_status():
    if _scheduler is None:
        return JSONResponse({"error": "Scheduler n\u00e3o dispon\u00edvel"}, status_code=503)
    paused   = getattr(_scheduler, "paused",   False)
    next_run = getattr(_scheduler, "next_run", None)
    return JSONResponse({
        "paused":   paused,
        "next_run": next_run.timestamp() if next_run and hasattr(next_run, "timestamp") else None,
    })


@app.post("/api/scheduler/force")
def api_scheduler_force():
    if _scheduler is None:
        return JSONResponse({"error": "Scheduler n\u00e3o dispon\u00edvel"}, status_code=503)
    _scheduler.trigger_now()
    logger.info("Busca global for\u00e7ada via Dashboard.")
    return JSONResponse({"ok": True})


@app.post("/api/scheduler/pause")
def api_scheduler_pause():
    if _scheduler is None:
        return JSONResponse({"error": "Scheduler n\u00e3o dispon\u00edvel"}, status_code=503)
    if not hasattr(_scheduler, "paused"):
        _scheduler.paused = False
    _scheduler.paused = not _scheduler.paused
    state = _scheduler.paused
    logger.info(f"Scheduler {'pausado' if state else 'retomado'} via Dashboard.")
    return JSONResponse({"ok": True, "paused": state})


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
    live_streams = streams_status()
    vod_sessions = vod_proxy_manager.snapshot()
    return JSONResponse({
        "streams": live_streams + vod_sessions,
        "count": len(live_streams) + len(vod_sessions),
    })


@app.get("/api/proxy/debug/{video_id}")
def api_proxy_debug(video_id: str):
    """Retorna informações detalhadas de debug para um stream específico."""
    info = get_stream_debug_info(video_id)
    if info is None:
        return JSONResponse({"error": "stream não encontrado"}, status_code=404)
    return JSONResponse(info)


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

    start_lock = _proxy_start_locks.setdefault(video_id, asyncio.Lock())
    async with start_lock:
        if not is_stream_active(video_id):
            if _state is None:
                return Response("Servidor ainda inicializando", status_code=503)

            stream_info, status, thumbnail_url, watch_url = _get_stream_info(video_id)
            if stream_info is None:
                logger.warning(f"[{video_id}] video_id nao encontrado no estado")

            if status in ("vod", "none", "ended", "completed"):
                return RedirectResponse(f"/api/vod/{video_id}", status_code=307)

            placeholder   = _config.get_str("placeholder_image_url")
            thumb_for_ph  = thumbnail_url or placeholder

            logger.info(f"[{video_id}] iniciando stream  status={status!r}  url={watch_url}")

            local_thumb = Path(_thumbnail_manager._cache_dir) / f"{video_id}.jpg"
            if local_thumb.exists():
                thumb_for_ph = str(local_thumb)

            debug_enabled = _config.get_bool("streaming_debug_enabled") if _config else False

            try:
                ingress_plan = await resolve_proxy_ingress_plan(
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

            start_stream_reader(video_id, ingress_plan.cmd)

            if ingress_plan.is_placeholder:
                register_placeholder(video_id, ingress_plan.cmd)
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
                        f"[{video_id}] streamlink fast-fail: sem chunk em "
                        f"{STREAMLINK_FAST_FAIL_S}s \u2192 fallback yt-dlp HLS"
                    )
                    stop_stream(video_id)
                    fallback_plan = await build_live_fallback_ingress_plan(
                        watch_url=watch_url,
                        user_agent=user_agent,
                        debug_enabled=debug_enabled,
                    )
                    if not fallback_plan:
                        logger.error(f"[{video_id}] yt-dlp nao resolveu HLS URL")
                        return Response(
                            "Stream indisponivel (streamlink + yt-dlp falharam)",
                            status_code=503,
                        )
                    start_stream_reader(video_id, fallback_plan.cmd)
                    logger.info(f"[{video_id}] fallback HLS ativo: {fallback_plan.source_url[:70]}...")

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

            if status == "live":
                preroll_deadline = time.monotonic() + LIVE_PREROLL_WAIT_S
                while time.monotonic() < preroll_deadline:
                    buf = _buffers.get(video_id)
                    if buf is None or buf.ready_for_clients(LIVE_PREROLL_BYTES):
                        break
                    await asyncio.sleep(0.1)

    buf = _buffers[video_id]
    mgr = _managers[video_id]

    async def generate():
        registered        = False
        local_index       = buf.latest_safe_index(INITIAL_BEHIND_BYTES)
        bytes_sent        = 0
        last_yield_time   = time.monotonic()
        consecutive_empty = 0
        try:
            while True:
                if not is_stream_active(video_id) and video_id not in _buffers:
                    break
                restart_placeholder_if_needed(video_id)
                chunks, next_index = buf.get_optimized_client_data(
                    local_index,
                    target_bytes_override=TARGET_BATCH_BYTES,
                    max_batch_bytes_override=MAX_BATCH_BYTES,
                )
                if chunks:
                    if not registered:
                        mgr.add_client(client_id, client_ip, user_agent)
                        registered = True
                    payload = b"".join(chunks) if len(chunks) > 1 else chunks[0]
                    yield payload
                    bytes_sent += len(payload)
                    local_index       = next_index
                    last_yield_time   = time.monotonic()
                    consecutive_empty = 0
                    mgr.update_activity(client_id, bytes_sent, local_index)
                else:
                    consecutive_empty += 1
                    await asyncio.sleep(min(0.05 * consecutive_empty, 1.0))
                    if time.monotonic() - last_yield_time > CLIENT_TIMEOUT_S:
                        mgr.mark_stall(client_id)
                        break
                    if buf.bytes_behind(local_index) > CLIENT_JUMP_THRESHOLD_BYTES:
                        late_for = mgr.mark_late(client_id)
                        if late_for < 2.0:
                            continue
                        lag_before = buf.bytes_behind(local_index)
                        new_index = buf.latest_safe_index(INITIAL_BEHIND_BYTES)
                        lag_after = buf.bytes_behind(new_index)
                        logger.warning(
                            f"[{video_id}][{client_id}] jump de catch-up: "
                            f"lag_before={lag_before/1024/1024:.2f}MB "
                            f"late_for={late_for:.1f}s "
                            f"jump_threshold={CLIENT_JUMP_THRESHOLD_BYTES/1024/1024:.2f}MB "
                            f"lag_after={lag_after/1024/1024:.2f}MB"
                        )
                        local_index       = new_index
                        consecutive_empty = 0
                    else:
                        mgr.clear_late(client_id)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(f"[{video_id}][{client_id}] erro no generator: {exc}")
        finally:
            remaining = mgr.remove_client(client_id) if registered else mgr.count
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


async def api_vod_stream(request):
    video_id = request.path_params["video_id"]
    session_id = request.path_params.get("session_id")
    user_agent = request.query_params.get("user_agent", "Mozilla/5.0")
    range_header = request.headers.get("range")
    debug_enabled = _config.get_bool("streaming_debug_enabled") if _config else False

    if _state is None:
        return Response("Servidor ainda inicializando", status_code=503)

    stream_info, status, _thumbnail_url, watch_url = _get_stream_info(video_id)
    if stream_info is None:
        return Response("video_id nao encontrado", status_code=404)

    if status not in ("vod", "none", "ended", "completed"):
        return RedirectResponse(f"/api/proxy/{video_id}", status_code=307)

    if not session_id:
        session_id = vod_proxy_manager.create_session_id(video_id)
        return RedirectResponse(f"/api/vod/{video_id}/{session_id}", status_code=307)

    method = "HEAD" if getattr(request, "method", "GET").upper() == "HEAD" else "GET"

    session = vod_proxy_manager.get_or_create_session(
        session_id=session_id,
        video_id=video_id,
        watch_url=watch_url,
        user_agent=user_agent,
        debug_enabled=debug_enabled,
    )

    try:
        upstream = await asyncio.to_thread(session.open, method, range_header)
    except Exception as exc:
        logger.error(f"[{video_id}][{session_id}] erro no VOD upstream: {exc}")
        vod_proxy_manager.cleanup_session(session_id)
        return Response("Erro no upstream VOD", status_code=502)

    if upstream is None:
        return Response("Range inválido", status_code=416)

    vod_proxy_manager.maybe_cache_url(video_id, session.stream_url)

    response_headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    for header in ("Accept-Ranges", "Content-Length", "Content-Range", "ETag", "Last-Modified"):
        value = upstream.headers.get(header)
        if value:
            response_headers[header] = value

    status_code = getattr(upstream, "status_code", None) or 200
    content_type = upstream.headers.get("Content-Type", "video/mp4")

    if method == "HEAD":
        await asyncio.to_thread(session.close)
        return Response("", status_code=status_code, media_type=content_type, headers=response_headers)

    session.increment_active()

    async def stream_gen():
        iterator = upstream.iter_content(chunk_size=64 * 1024)
        try:
            while True:
                try:
                    chunk = await asyncio.to_thread(next, iterator)
                except StopIteration:
                    break
                if chunk:
                    yield chunk
        finally:
            try:
                upstream.close()
            except Exception:
                pass
            session.decrement_active()
            session.schedule_cleanup(vod_proxy_manager)

    return StreamingResponse(
        stream_gen(),
        status_code=status_code,
        media_type=content_type,
        headers=response_headers,
    )


app.routes.append(Route("/api/vod/{video_id}", endpoint=api_vod_stream))
app.routes.append(Route("/api/vod/{video_id}/{session_id}", endpoint=api_vod_stream))


# ---------------------------------------------------------------------------
# API — player streaming (legado)
# ---------------------------------------------------------------------------

async def api_player_stream(request):
    video_id   = request.path_params["video_id"]
    user_agent = request.query_params.get("user_agent", "Mozilla/5.0")
    stream_info, status, thumbnail_url, watch_url = _get_stream_info(video_id)
    if status in ("vod", "none", "ended", "completed"):
        return RedirectResponse(f"/api/vod/{video_id}", status_code=307)
    if status == "live":
        return RedirectResponse(f"/api/proxy/{video_id}", status_code=307)
    placeholder   = _config.get_str("placeholder_image_url")
    thumb_for_ph  = thumbnail_url or placeholder
    local_thumb   = Path(_thumbnail_manager._cache_dir) / f"{video_id}.jpg"
    if local_thumb.exists():
        thumb_for_ph = str(local_thumb)

    debug_enabled = _config.get_bool("streaming_debug_enabled") if _config else False

    async def stream_gen():
        proc_logger = logging.getLogger("TubeWrangler.player")
        temp_files  = []
        try:
            cmd, temp_files = await build_player_command_async(
                video_id=video_id, status=status, watch_url=watch_url,
                thumbnail_url=thumb_for_ph, user_agent=user_agent,
                font_path=FONT_PATH, texts_cache_path=TEXTS_CACHE_PATH,
                debug_enabled=debug_enabled,
            )
        except Exception as exc:
            proc_logger.error(f"[{video_id}] erro ao montar comando: {exc}")
            return
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=1024*1024,
        )
        async def _log_stderr():
            try:
                async for line in proc.stderr:
                    text = line.decode("utf-8", errors="replace").rstrip()
                    if text: proc_logger.info(f"[{video_id}] {text}")
            except Exception:
                pass
        stderr_task = asyncio.create_task(_log_stderr())
        try:
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk: break
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
            try: await stderr_task
            except (asyncio.CancelledError, Exception): pass
            for tf in temp_files:
                try: os.unlink(tf)
                except Exception: pass

    return StreamingResponse(
        stream_gen(), media_type="video/mp2t",
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
        f"https://i.ytimg.com/vi/{video_id}/maxresdefault_live.jpg", status_code=302,
    )


# ---------------------------------------------------------------------------
# Logs SSE + página /logs
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


@app.get("/api/logs/level")
def api_logs_level_get():
    current = logging.getLogger().level
    return JSONResponse({"level": logging.getLevelName(current)})


@app.post("/api/logs/level")
async def api_logs_level_set(req):
    body  = await req.json()
    level = body.get("level", "INFO").upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        return JSONResponse({"error": "N\u00edvel inv\u00e1lido"}, status_code=400)
    hide_access = _config.get_bool("hide_access_logs") if _config else True
    _setup_logging(level, hide_access=hide_access)
    if _config:
        try:
            _config.update("log_level", level)
        except Exception:
            pass
    
    # Sincroniza modo debug de streaming quando log_level muda
    if _config:
        streaming_debug = _config.get_bool("streaming_debug_enabled")
        set_debug_mode(streaming_debug)
    
    logger.info(f"N\u00edvel de log alterado para {level} via UI.")
    return JSONResponse({"ok": True, "level": level})


@app.post("/api/logs/hide-access")
async def api_logs_hide_access_set(req):
    body        = await req.json()
    hide_access = bool(body.get("hide_access", True))
    if _config:
        _config.update("hide_access_logs", "true" if hide_access else "false")
    current_level = logging.getLevelName(logging.getLogger().level)
    _setup_logging(current_level, hide_access=hide_access)
    logger.info(f"Ocultar logs de acesso HTTP: {hide_access}")
    return JSONResponse({"ok": True, "hide_access": hide_access})


@app.post("/api/logs/streaming-debug")
async def api_logs_streaming_debug_set(req):
    """Ativa/desativa modo debug de streaming."""
    body = await req.json()
    debug_enabled = bool(body.get("debug_enabled", False))
    if _config:
        _config.update("streaming_debug_enabled", "true" if debug_enabled else "false")
    set_debug_mode(debug_enabled)
    logger.info(f"Debug de streaming: {'ATIVADO' if debug_enabled else 'DESATIVADO'}")
    return JSONResponse({"ok": True, "debug_enabled": debug_enabled})


@app.get("/logs")
def logs_page():
    current_level = logging.getLevelName(logging.getLogger().level)
    hide_access   = _config.get_bool("hide_access_logs") if _config else True
    streaming_debug = _config.get_bool("streaming_debug_enabled") if _config else False

    logging_panel = Div(
        Details(
            Summary(
                "\U0001f527 Configura\u00e7\u00e3o de Logging",
                style="cursor:pointer;font-weight:600;color:#58a6ff;font-size:0.95rem;",
            ),
            Div(
                P(
                    "N\u00edvel atual: ",
                    Strong(current_level, id="current-level-badge",
                           style="color:#58a6ff;"),
                    cls="text-muted", style="margin-bottom:12px;",
                ),
                Div(
                    *[
                        Button(
                            lv,
                            type="button",
                            cls="btn-secondary",
                            id=f"btn-level-{lv}",
                            onclick=f"setLogLevel('{lv}')",
                            style=(
                                "margin-right:8px;font-size:0.82rem;padding:5px 14px;"
                                + ("border-color:#58a6ff;color:#58a6ff;"
                                   if lv == current_level else "")
                            ),
                        )
                        for lv in ("DEBUG", "INFO", "WARNING", "ERROR")
                    ],
                    id="level-buttons",
                    style="margin-bottom:14px;",
                ),
                _TOGGLE_STYLE,
                _TOGGLE_JS,
                Div(
                    Input(type="hidden", id="hidden_hide_access_logs",
                          value="true" if hide_access else "false"),
                    Button(
                        "Ligado" if hide_access else "Desligado",
                        type="button",
                        cls="toggle-pill " + ("on" if hide_access else "off"),
                        onclick="_toggleBool(this, 'hidden_hide_access_logs'); toggleHideAccess(document.getElementById('hidden_hide_access_logs').value === 'true')",
                    ),
                    Span("Ocultar logs de acesso HTTP (GET / e /api/logs/stream)",
                         cls="toggle-label",
                         style="font-size:0.85rem;color:#8b949e;"),
                    cls="bool-toggle",
                    style="margin-bottom:10px;",
                ),
                Div(
                    Input(type="hidden", id="hidden_streaming_debug",
                          value="true" if streaming_debug else "false"),
                    Button(
                        "Ligado" if streaming_debug else "Desligado",
                        type="button",
                        cls="toggle-pill " + ("on" if streaming_debug else "off"),
                        onclick="_toggleBool(this, 'hidden_streaming_debug'); toggleStreamingDebug(document.getElementById('hidden_streaming_debug').value === 'true')",
                    ),
                    Span("Debug detalhado de streaming (ffmpeg verbose + métricas de buffer/clientes)",
                         cls="toggle-label",
                         style="font-size:0.85rem;color:#8b949e;"),
                    cls="bool-toggle",
                    style="margin-bottom:10px;",
                ),
                Div(id="level-feedback", style="font-size:0.82rem;color:#3fb950;min-height:18px;"),
                style="padding:12px 0 4px;",
            ),
        ),
        cls="card",
        style="margin-bottom:16px;",
    )

    controls = Div(
        Select(
            Option("DEBUG",   value="DEBUG"),
            Option("INFO",    value="INFO",    selected=True),
            Option("WARNING", value="WARNING"),
            Option("ERROR",   value="ERROR"),
            id="log-level-filter",
            style="margin-right:8px;width:120px;",
        ),
        Button("Limpar", id="btn-clear", type="button",
               cls="btn-secondary",
               style="margin-right:8px;font-size:0.85em;"),
        Label(
            Input(type="checkbox", id="auto-scroll", checked=True,
                  style="margin-right:4px;"),
            "Auto-scroll",
            style="display:inline-flex;align-items:center;font-size:0.85rem;",
        ),
        style="margin-bottom:10px;display:flex;align-items:center;",
    )

    return _page_shell(
        "Logs", "logs",
        logging_panel,
        controls,
        Pre(
            id="log-output",
            style=(
                "height:72vh;overflow-y:auto;"
                "background:#0d1117;color:#eee;"
                "font-size:0.76rem;padding:10px;"
                "border:1px solid #30363d;border-radius:6px;"
            ),
        ),
        Style("""
            .log-DEBUG   { color: #484f58; }
            .log-INFO    { color: #e6edf3; }
            .log-WARNING { color: #d29922; }
            .log-ERROR   { color: #f85149; font-weight:bold; }
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

            function setLogLevel(level) {
                fetch('/api/logs/level', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ level }),
                })
                .then(r => r.json())
                .then(d => {
                    if (d.ok) {
                        document.getElementById('current-level-badge').textContent = d.level;
                        document.getElementById('level-feedback').textContent =
                            '\\u2705 N\u00edvel alterado para ' + d.level;
                        document.querySelectorAll('[id^="btn-level-"]').forEach(b => {
                            b.style.borderColor = '';
                            b.style.color = '';
                        });
                        const active = document.getElementById('btn-level-' + d.level);
                        if (active) { active.style.borderColor='#58a6ff'; active.style.color='#58a6ff'; }
                        setTimeout(() => { document.getElementById('level-feedback').textContent=''; }, 3000);
                    }
                })
                .catch(() => {
                    document.getElementById('level-feedback').textContent = '\\u274c Erro ao alterar n\u00edvel.';
                    document.getElementById('level-feedback').style.color = '#f85149';
                });
            }

            function toggleHideAccess(hide) {
                fetch('/api/logs/hide-access', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ hide_access: hide }),
                })
                .then(r => r.json())
                .then(d => {
                    if (d.ok) {
                        const msg = hide ? '\\u2705 Logs de acesso ocultados.' : '\\u2705 Logs de acesso vis\u00edveis.';
                        document.getElementById('level-feedback').textContent = msg;
                        setTimeout(() => { document.getElementById('level-feedback').textContent=''; }, 3000);
                    }
                })
                .catch(() => {
                    document.getElementById('level-feedback').textContent = '\\u274c Erro ao salvar prefer\\u00eancia.';
                    document.getElementById('level-feedback').style.color = '#f85149';
                });
            }

            function toggleStreamingDebug(enabled) {
                fetch('/api/logs/streaming-debug', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ debug_enabled: enabled }),
                })
                .then(r => r.json())
                .then(d => {
                    if (d.ok) {
                        const msg = enabled ? '\\u2705 Debug de streaming ativado.' : '\\u2705 Debug de streaming desativado.';
                        document.getElementById('level-feedback').textContent = msg;
                        setTimeout(() => { document.getElementById('level-feedback').textContent=''; }, 3000);
                    }
                })
                .catch(() => {
                    document.getElementById('level-feedback').textContent = '\\u274c Erro ao salvar configuração.';
                    document.getElementById('level-feedback').style.color = '#f85149';
                });
            }
        """),
    )
