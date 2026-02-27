from contextlib import asynccontextmanager
from fasthtml.common import *
from starlette.routing import Route
from starlette.requests import Request as _SReq
from starlette.responses import Response as _SR
import asyncio
from core.config import AppConfig
from core.state_manager import StateManager
from core.youtube_api import YouTubeAPI
from core.scheduler import Scheduler
import logging
import sys
from pathlib import Path

def _get_log_level(s: str) -> int:
    return getattr(logging, s.upper(), logging.INFO)

_LOG_FILE = Path("/data/tubewrangler.log")

logging.basicConfig(
    format   = "%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt  = "%Y-%m-%d %H:%M:%S",
    level    = logging.INFO,
    force    = True,
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_LOG_FILE, mode="a"),
    ]
)
logger = logging.getLogger("TubeWrangler")

_config    = None
_state     = None
_scheduler = None

@asynccontextmanager
async def lifespan(app):
    global _config, _state, _scheduler

    _config = AppConfig()

    # Ajustar nível de log conforme banco
    logging.getLogger().setLevel(_get_log_level(_config.get_str("log_level")))
    logger.info("=== TubeWrangler iniciando ===")

    _state       = StateManager(_config)
    cache_loaded = _state.load_from_disk()
    logger.info(f"Cache carregado do disco: {cache_loaded} | "
                f"canais={len(_state.get_all_channels())} "
                f"streams={len(_state.get_all_streams())}")

    api_key  = _config.get_str("youtube_api_key")
    handles  = [h.strip() for h in _config.get_str("target_channel_handles").split(",") if h.strip()]
    chan_ids = [i.strip() for i in _config.get_str("target_channel_ids").split(",")    if i.strip()]
    logger.info(f"Handles configurados : {handles}")
    logger.info(f"IDs configurados     : {chan_ids}")

    scraper = YouTubeAPI(api_key)

    # Resolver handles → IDs (igual ao get_streams.py original)
    all_target_ids = set(chan_ids)
    if handles:
        logger.info(f"Resolvendo {len(handles)} handle(s) via API...")
        resolved = scraper.resolve_channel_handles_to_ids(handles, _state)
        all_target_ids.update(resolved.keys())
        logger.info(f"Handles resolvidos: {resolved}")

    # Garantir títulos para todos os IDs
    if all_target_ids:
        final_channels = scraper.ensure_channel_titles(all_target_ids, _state)
        logger.info(f"Canais prontos: {len(final_channels)} — {list(final_channels.values())}")
    else:
        logger.warning("Nenhum canal alvo. Verifique target_channel_handles / target_channel_ids no /config.")

    # Criar Scheduler e injetar evento de force-sync
    _force_event = asyncio.Event()
    _scheduler   = Scheduler(_config, scraper, _state)
    _scheduler.set_force_event(_force_event)

    task = asyncio.create_task(_scheduler.run())
    logger.info("Scheduler iniciado.")

    yield

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    _state.save_to_disk()
    logger.info("=== TubeWrangler encerrado ===")

async def _playlist_live(req: _SReq):
    from pathlib import Path
    path = Path(_config.get_str("playlist_save_directory")) / _config.get_str("playlist_live_filename")
    content = path.read_text(encoding="utf-8") if path.exists() else "#EXTM3U\n"
    return _SR(content, media_type="application/vnd.apple.mpegurl")

async def _playlist_upcoming(req: _SReq):
    from pathlib import Path
    path = Path(_config.get_str("playlist_save_directory")) / _config.get_str("playlist_upcoming_filename")
    content = path.read_text(encoding="utf-8") if path.exists() else "#EXTM3U\n"
    return _SR(content, media_type="application/vnd.apple.mpegurl")

async def _playlist_vod(req: _SReq):
    from pathlib import Path
    path = Path(_config.get_str("playlist_save_directory")) / _config.get_str("playlist_vod_filename")
    content = path.read_text(encoding="utf-8") if path.exists() else "#EXTM3U\n"
    return _SR(content, media_type="application/vnd.apple.mpegurl")

async def _epg_xml(req: _SReq):
    from pathlib import Path
    path = Path(_config.get_str("xmltv_save_directory")) / _config.get_str("xmltv_filename")
    content = path.read_text(encoding="utf-8") if path.exists() else '<?xml version="1.0"?><tv></tv>'
    return _SR(content, media_type="application/xml")

app, rt = fast_app(
    lifespan=lifespan,
    hdrs=[Link(rel="stylesheet",
               href="https://cdn.jsdelivr.net/npm/pico.css@2/css/pico.min.css")]
)

app.router.routes.insert(0, Route("/playlist_live.m3u8",     _playlist_live))
app.router.routes.insert(0, Route("/playlist_upcoming.m3u8", _playlist_upcoming))
app.router.routes.insert(0, Route("/playlist_vod.m3u8",      _playlist_vod))
app.router.routes.insert(0, Route("/youtube_epg.xml",        _epg_xml))

@app.get("/")
def home():
    from core.playlist_builder import ContentGenerator

    streams  = _state.get_all_streams()  if _state else []
    channels = _state.get_all_channels() if _state else {}

    def truncate(text, n=60):
        text = text or ""
        return text[:n] + ("..." if len(text) > n else "")

    def get_tipo(s):
        if ContentGenerator.is_live(s):
            return "live"
        elif s.get("status") == "upcoming":
            return "upcoming"
        else:
            return "none"

    def sort_key(s):
        tipo = get_tipo(s)
        if tipo == "live":
            return (0, 0)
        elif tipo == "upcoming":
            t = s.get("scheduledstarttimeutc")
            return (1, t.timestamp() if t else 0)
        else:
            t = s.get("fetchtime")
            return (2, -(t.timestamp() if t else 0))

    from core.playlist_builder import ContentGenerator
    from datetime import datetime, timezone, timedelta
    from collections import defaultdict

    # Aplicar filtros de VOD conforme configuração
    keep_vod      = _config.get_bool("keep_recorded_streams")
    max_per_ch    = _config.get_int("max_recorded_per_channel")
    retention     = _config.get_int("recorded_retention_days")
    cutoff        = datetime.now(timezone.utc) - timedelta(days=retention)

    live_streams     = [s for s in streams if ContentGenerator.is_live(s)]
    upcoming_streams = [s for s in streams if s.get("status") == "upcoming"]

    if keep_vod:
        vod_candidates = [
            s for s in streams
            if ContentGenerator.is_vod(s)
            and isinstance(s.get("fetchtime"), datetime)
            and s["fetchtime"] >= cutoff
        ]
        per_channel = defaultdict(list)
        for s in vod_candidates:
            per_channel[s.get("channelid", "")].append(s)
        vod_streams = []
        for ch_streams in per_channel.values():
            ch_streams.sort(
                key=lambda x: x.get("fetchtime") or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True
            )
            vod_streams.extend(ch_streams[:max_per_ch])
    else:
        vod_streams = []

    display_streams = live_streams + upcoming_streams + vod_streams
    sorted_streams  = sorted(display_streams, key=sort_key)

    BADGE = {
        "live":     ("🔴 LIVE",     "background:#d32f2f;color:#fff;font-weight:bold;padding:3px 8px;border-radius:4px;"),
        "upcoming": ("🕐 Agendado", "background:#1976d2;color:#fff;font-weight:bold;padding:3px 8px;border-radius:4px;"),
        "none":     ("📹 VOD",      "background:#757575;color:#fff;font-weight:bold;padding:3px 8px;border-radius:4px;"),
    }

    rows = []
    for s in sorted_streams:
        tipo = get_tipo(s)
        label, badge_style = BADGE.get(tipo, BADGE["none"])
        vid = s.get("videoid", "")
        url = s.get("watchurl") or f"https://www.youtube.com/watch?v={vid}"
        rows.append(
            Tr(
                Td(truncate(s.get("title", "—"))),
                Td(Span(label, style=badge_style)),
                Td(A("▶ Abrir", href=url, target="_blank")),
            )
        )

    canais_items = [Li(f"{name} ({cid})") for cid, name in channels.items()]

    return Titled("TubeWrangler",
                Div(
                        A("🏠 Dashboard", href="/",
                            style="margin-right:16px;padding:6px 14px;background:#388e3c;color:#fff;border-radius:4px;text-decoration:none;font-weight:bold;"),
                        A("🔄 Force Sync", href="/force-sync",
                            style="margin-right:16px;padding:6px 14px;background:#1976d2;color:#fff;border-radius:4px;text-decoration:none;font-weight:bold;"),
                        A("⚙️ Configurações", href="/config",
                            style="padding:6px 14px;background:#555;color:#fff;border-radius:4px;text-decoration:none;font-weight:bold;"),
                        style="margin-bottom:20px;"
                ),
        H2("Canais monitorados"),
        Ul(*canais_items),
        H2(f"Streams ({len(sorted_streams)})"),
        Table(
            Thead(Tr(
                Th("Evento"),
                Th("Tipo",    style="text-align:center;"),
                Th("Assistir",style="text-align:center;"),
            )),
            Tbody(*rows),
            style="width:100%;border-collapse:collapse;font-family:sans-serif;",
        ),
    )

@app.get("/config")
def config_page():
    sections = _config.get_all_by_section() if _config else {}
    fields   = []
    for section, rows in sections.items():
        fields.append(H3(section))
        for row in rows:
            fields.append(
                Label(row["key"],
                    Input(name=f"{section}__{row['key']}",
                          value=row["value"],
                          type="text"))
            )
    return Titled("Configuracoes",
        Form(*fields, Button("Salvar", type="submit"), method="post", action="/config")
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
    from starlette.responses import RedirectResponse
    return RedirectResponse("/config", status_code=303)

@app.get("/channels")
def channels_page():
    channels = _state.get_all_channels() if _state else []
    return Titled("Canais",
        Ul(*[Li(c) for c in channels]) if channels else P("Nenhum canal.")
    )

@app.get("/logs")
def logs_page():
    return Titled("Logs",
        Pre("Aguardando logs...", id="log-output"),
        Script("""
            const pre = document.getElementById('log-output');
            const es  = new EventSource('/logs-stream');
            es.onmessage = e => {
                pre.textContent += e.data + '\\n';
                pre.scrollTop = pre.scrollHeight;
            };
        """)
    )

@app.get("/force-sync")
def force_sync():
    if _scheduler:
        _scheduler.trigger_now()
    from starlette.responses import RedirectResponse
    return RedirectResponse("/", status_code=303)
