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
from core.player_router import (
    build_player_command_async,
    build_live_hls_ffmpeg_cmd,
    resolve_live_hls_url_async,
)
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

def _config_form_fields(rows: list, section: str) -> list:
    """Gera campos de formulário genéricos para uma seção de config."""
    fields = []
    for row in rows:
        key   = row["key"]
        value = row["value"]
        desc  = row.get("description", "")
        vtype = row.get("value_type", "str")
        if vtype == "bool":
            checked = value.lower() == "true"
            fields.append(
                Label(
                    Input(type="checkbox", name=key, value="true",
                          checked=checked, id=f"field_{key}"),
                    f" {desc or key}",
                    style="display:flex;align-items:center;gap:8px;margin-bottom:14px;",
                )
            )
        else:
            fields.append(
                Label(
                    Span(desc or key, style="display:block;margin-bottom:4px;"),
                    Input(name=key, value=value, type="text", id=f"field_{key}"),
                )
            )
    return fields


def _config_page(section_key: str, title: str, active_key: str, saved: bool = False):
    """Renderiza página genérica de config para uma seção."""
    sections = _config.get_all_by_section() if _config else {}
    rows = sections.get(section_key, [])
    fields = _config_form_fields(rows, section_key)
    alert = Div("✅ Configurações salvas com sucesso.",
                cls="alert alert-success") if saved else ""
    return _page_shell(
        title, active_key,
        alert,
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


# ---------------------------------------------------------------------------
# Rotas HTML — Dashboard
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
        badge_cls = f"badge badge-{status}" if status in ("live","upcoming","vod") else "badge badge-none"
        rows.append(Tr(
            Td(channel),
            Td(title),
            Td(Code(vid)),
            Td(Span(status, cls=badge_cls)),
            Td(cat_cell),
            Td(A("\u25b6", href=url, target="_blank")),
        ))

    channel_items = [Li(f"{name} ({cid})") for cid, name in channels.items()]
    return _page_shell(
        "Dashboard", "dashboard",
        Div(
            H2("Canais monitorados"),
            Ul(*channel_items) if channel_items else P("Nenhum canal.", cls="text-muted"),
            H2(f"Streams ({len(streams)})"),
            P(A("Ver Playlists e Proxy →", href="/proxy"), cls="text-muted"),
            Table(
                Thead(Tr(
                    Th("Canal"), Th("Evento"), Th("Video ID"),
                    Th("Status"), Th("Categoria"), Th(""),
                )),
                Tbody(*rows),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Rotas HTML — Proxy
# ---------------------------------------------------------------------------

@app.get("/proxy")
def proxy_page(request: Request):
    return proxy_dashboard_page(
        request=request,
        playlist_routes=_PLAYLIST_ROUTES,
        base_url=_get_base_url(request),
    )


# ---------------------------------------------------------------------------
# Rotas HTML — Config (redirect + sub-páginas)
# ---------------------------------------------------------------------------

@app.get("/config")
def config_redirect():
    return RedirectResponse("/config/credentials", status_code=302)


@app.get("/config/credentials")
def config_credentials(saved: str = ""):
    return _config_page("credentials", "Credenciais", "config_credentials", saved == "1")


@app.post("/config/credentials")
async def config_credentials_save(req):
    form = await req.form()
    if _config:
        _config.update_many(dict(form))
    return RedirectResponse("/config/credentials?saved=1", status_code=303)


@app.get("/config/scheduler")
def config_scheduler(saved: str = ""):
    return _config_page("scheduler", "Agendador", "config_scheduler", saved == "1")


@app.post("/config/scheduler")
async def config_scheduler_save(req):
    form = await req.form()
    if _config:
        _config.update_many(dict(form))
    return RedirectResponse("/config/scheduler?saved=1", status_code=303)


@app.get("/config/output")
def config_output(saved: str = ""):
    return _config_page("output", "Output", "config_output", saved == "1")


@app.post("/config/output")
async def config_output_save(req):
    form = await req.form()
    if _config:
        _config.update_many(dict(form))
    return RedirectResponse("/config/output?saved=1", status_code=303)


@app.get("/config/technical")
def config_technical(saved: str = ""):
    return _config_page("technical", "Técnico", "config_technical", saved == "1")


@app.post("/config/technical")
async def config_technical_save(req):
    form = await req.form()
    if _config:
        _config.update_many(dict(form))
    return RedirectResponse("/config/technical?saved=1", status_code=303)


# ---------------------------------------------------------------------------
# Rota HTML — /config/filters (UI dedicada)
# ---------------------------------------------------------------------------

@app.get("/config/filters")
def config_filters(saved: str = ""):
    if not _config:
        return _page_shell("Filtros", "config_filters", P("Config não inicializado."))

    cfg = _config
    alert = Div("✅ Filtros salvos com sucesso.", cls="alert alert-success") if saved == "1" else ""

    # --- Valores atuais ---
    filter_by_cat       = cfg.get_bool("filter_by_category")
    allowed_ids         = cfg.get_raw("allowed_category_ids")
    cat_mappings        = cfg.get_raw("category_mappings")
    channel_mappings    = cfg.get_raw("channel_name_mappings")
    shorts_max_s        = cfg.get_raw("shorts_max_duration_s")
    shorts_words_raw    = cfg.get_raw("shorts_block_words")
    shorts_words        = [w.strip() for w in shorts_words_raw.split(",") if w.strip()]
    title_exprs_raw     = cfg.get_raw("title_filter_expressions")
    title_exprs         = [w.strip() for w in title_exprs_raw.split(",") if w.strip()]
    prefix_channel      = cfg.get_bool("prefix_title_with_channel_name")
    prefix_status       = cfg.get_bool("prefix_title_with_status")
    epg_cleanup         = cfg.get_bool("epg_description_cleanup")
    keep_recorded       = cfg.get_bool("keep_recorded_streams")
    max_recorded        = cfg.get_raw("max_recorded_per_channel")
    retention_days      = cfg.get_raw("recorded_retention_days")
    max_schedule        = cfg.get_raw("max_schedule_hours")
    max_upcoming        = cfg.get_raw("max_upcoming_per_channel")

    def _tag_list_with_input(words: list, field_name: str, hidden_name: str) -> Div:
        """Renderiza lista de tags editáveis + campo hidden que armazena o valor."""
        tags = []
        for w in words:
            tags.append(
                Span(
                    Span(w, cls="tag-text"),
                    Button("×", cls="remove-tag", type="button",
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

    # JS para tags interativas — definido FORA do Form para evitar
    # positional-after-keyword no FastHTML
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

        // Enter no input também adiciona
        document.querySelectorAll('[id^="input_"]').forEach(inp => {
            inp.addEventListener('keydown', e => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    const hiddenName = inp.id.replace('input_new_', '');
                    addTag(inp.id.replace('input_', ''), hiddenName);
                }
            });
        });

        // Checkboxes: quando desmarcado, envia "false"
        document.querySelectorAll('form input[type=checkbox]').forEach(cb => {
            const form = cb.closest('form');
            form.addEventListener('submit', () => {
                if (!cb.checked) {
                    const hidden = document.createElement('input');
                    hidden.type  = 'hidden';
                    hidden.name  = cb.name;
                    hidden.value = 'false';
                    form.appendChild(hidden);
                }
            });
        });
    """)

    return _page_shell(
        "Filtros", "config_filters",
        alert,
        Form(
            # ---- 1. Categoria ----
            Div(
                H2("Filtro de Categoria"),
                P("Quando ativo, apenas streams com IDs de categoria permitidos entram na playlist.",
                  cls="text-muted"),
                Label(
                    Input(type="checkbox", name="filter_by_category", value="true",
                          checked=filter_by_cat, id="field_filter_by_category"),
                    " Ativar filtro por categoria",
                    style="display:flex;align-items:center;gap:8px;margin-bottom:14px;",
                ),
                Label(
                    Span("IDs permitidos (vírgula) — ex: 17,22,25",
                         style="display:block;margin-bottom:4px;"),
                    Input(name="allowed_category_ids", value=allowed_ids, type="text"),
                ),
                Label(
                    Span("Renomear categorias: ID|Nome (vírgula) — não filtra, só exibe",
                         style="display:block;margin-bottom:4px;"),
                    Input(name="category_mappings", value=cat_mappings, type="text"),
                ),
                cls="card",
            ),

            # ---- 2. Filtro de Shorts ----
            Div(
                H2("Filtro de Shorts"),
                P(
                    "Shorts com duração conhecida são bloqueados pelo campo de segundos. "
                    "Para upcoming/live (duração ainda desconhecida), use palavras-chave.",
                    cls="text-muted",
                ),
                Label(
                    Span("Duração máxima (s) — 0 = desativado",
                         style="display:block;margin-bottom:4px;"),
                    Input(name="shorts_max_duration_s", value=shorts_max_s,
                          type="number", style="width:140px;"),
                ),
                Label(
                    Span("Palavras bloqueadas (título/tags)",
                         style="display:block;margin-bottom:4px;"),
                    _tag_list_with_input(shorts_words, "new_short_word", "shorts_block_words"),
                ),
                cls="card",
            ),

            # ---- 3. Títulos ----
            Div(
                H2("Títulos"),
                Label(
                    Span("Expressões a remover dos títulos",
                         style="display:block;margin-bottom:4px;"),
                    _tag_list_with_input(title_exprs, "new_title_expr", "title_filter_expressions"),
                ),
                Label(
                    Input(type="checkbox", name="prefix_title_with_channel_name", value="true",
                          checked=prefix_channel, id="field_prefix_channel"),
                    " Prefixar título com nome do canal",
                    style="display:flex;align-items:center;gap:8px;margin-bottom:14px;margin-top:14px;",
                ),
                Label(
                    Input(type="checkbox", name="prefix_title_with_status", value="true",
                          checked=prefix_status, id="field_prefix_status"),
                    " Prefixar título com status [Ao Vivo] / [Agendado]",
                    style="display:flex;align-items:center;gap:8px;margin-bottom:14px;",
                ),
                Label(
                    Span("Mapeamento de nomes de canal: Nome Longo|Nome Curto (vírgula)",
                         style="display:block;margin-bottom:4px;"),
                    Input(name="channel_name_mappings", value=channel_mappings, type="text"),
                ),
                cls="card",
            ),

            # ---- 4. VOD / Gravações ----
            Div(
                H2("VOD / Gravações"),
                Label(
                    Input(type="checkbox", name="keep_recorded_streams", value="true",
                          checked=keep_recorded, id="field_keep_recorded"),
                    " Manter streams gravados (ex-live) na playlist VOD",
                    style="display:flex;align-items:center;gap:8px;margin-bottom:14px;",
                ),
                Label(
                    Input(type="checkbox", name="epg_description_cleanup", value="true",
                          checked=epg_cleanup, id="field_epg_cleanup"),
                    " Manter apenas o primeiro parágrafo da descrição no EPG",
                    style="display:flex;align-items:center;gap:8px;margin-bottom:14px;",
                ),
                Label(
                    Span("Máximo de gravações por canal",
                         style="display:block;margin-bottom:4px;"),
                    Input(name="max_recorded_per_channel", value=max_recorded,
                          type="number", style="width:100px;"),
                ),
                Label(
                    Span("Dias de retenção de gravados",
                         style="display:block;margin-bottom:4px;"),
                    Input(name="recorded_retention_days", value=retention_days,
                          type="number", style="width:100px;"),
                ),
                cls="card",
            ),

            # ---- 5. Agendamentos futuros ----
            Div(
                H2("Agendamentos futuros"),
                Label(
                    Span("Limite futuro em horas para upcoming (ex: 72)",
                         style="display:block;margin-bottom:4px;"),
                    Input(name="max_schedule_hours", value=max_schedule,
                          type="number", style="width:100px;"),
                ),
                Label(
                    Span("Máximo de agendamentos por canal",
                         style="display:block;margin-bottom:4px;"),
                    Input(name="max_upcoming_per_channel", value=max_upcoming,
                          type="number", style="width:100px;"),
                ),
                cls="card",
            ),

            Div(
                Button("Salvar filtros", type="submit"),
                style="margin-top:8px;",
            ),
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

    bool_keys = [
        "filter_by_category",
        "prefix_title_with_channel_name",
        "prefix_title_with_status",
        "epg_description_cleanup",
        "keep_recorded_streams",
    ]
    updates = dict(form)
    for k in bool_keys:
        if k not in updates:
            updates[k] = "false"

    _config.update_many(updates)
    return RedirectResponse("/config/filters?saved=1", status_code=303)


# ---------------------------------------------------------------------------
# Rotas HTML — Canais e Force-sync
# ---------------------------------------------------------------------------

@app.get("/channels")
def channels_page():
    channels = _state.get_all_channels() if _state else {}
    return _page_shell(
        "Canais", "dashboard",
        Ul(*[Li(f"{cid}: {title}") for cid, title in channels.items()])
        if channels else P("Nenhum canal.", cls="text-muted"),
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
                    f"[{video_id}] streamlink fast-fail: sem chunk em "
                    f"{STREAMLINK_FAST_FAIL_S}s \u2192 fallback yt-dlp HLS"
                )
                stop_stream(video_id)
                hls_url = await resolve_live_hls_url_async(watch_url)
                if not hls_url:
                    logger.error(f"[{video_id}] yt-dlp nao resolveu HLS URL")
                    return Response(
                        "Stream indisponivel (streamlink + yt-dlp falharam)",
                        status_code=503,
                    )
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
        local_index       = max(0, buf.index - 10)
        bytes_sent        = 0
        last_yield_time   = time.monotonic()
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
                    local_index       = next_index
                    last_yield_time   = time.monotonic()
                    consecutive_empty = 0
                    mgr.update_activity(client_id, bytes_sent)
                else:
                    consecutive_empty += 1
                    await asyncio.sleep(min(0.05 * consecutive_empty, 1.0))
                    if time.monotonic() - last_yield_time > CLIENT_TIMEOUT_S:
                        break
                    if buf.index - local_index > 100:
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
                        return
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
    local_thumb   = Path(_thumbnail_manager._cache_dir) / f"{video_id}.jpg"
    if local_thumb.exists():
        thumb_for_ph = str(local_thumb)

    async def stream_gen():
        proc_logger = logging.getLogger("TubeWrangler.player")
        temp_files  = []
        try:
            cmd, temp_files = await build_player_command_async(
                video_id=video_id, status=status, watch_url=watch_url,
                thumbnail_url=thumb_for_ph, user_agent=user_agent,
                font_path=FONT_PATH, texts_cache_path=TEXTS_CACHE_PATH,
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
# Logs SSE + página /logs com painel de nível inline
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
        return JSONResponse({"error": "Nível inválido"}, status_code=400)
    _setup_logging(level)
    if _config:
        try:
            _config.update("log_level", level)
        except Exception:
            pass
    logger.info(f"Nível de log alterado para {level} via UI.")
    return JSONResponse({"ok": True, "level": level})


@app.get("/logs")
def logs_page():
    current_level = logging.getLevelName(logging.getLogger().level)

    logging_panel = Div(
        Details(
            Summary(
                "🔧 Configuração de Logging",
                style="cursor:pointer;font-weight:600;color:#58a6ff;font-size:0.95rem;",
            ),
            Div(
                P(
                    "Nível atual: ",
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
                            '\\u2705 Nível alterado para ' + d.level;
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
                    document.getElementById('level-feedback').textContent = '\\u274c Erro ao alterar nível.';
                    document.getElementById('level-feedback').style.color = '#f85149';
                });
            }
        """),
    )
