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
    return _SR("#EXTM3U\n", media_type="application/vnd.apple.mpegurl")

async def _playlist_upcoming(req: _SReq):
    return _SR("#EXTM3U\n", media_type="application/vnd.apple.mpegurl")

async def _playlist_vod(req: _SReq):
    return _SR("#EXTM3U\n", media_type="application/vnd.apple.mpegurl")

async def _epg_xml(req: _SReq):
    xml = '<?xml version="1.0" encoding="UTF-8"?><tv></tv>'
    return _SR(xml, media_type="application/xml")

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
    streams  = _state.get_all_streams()  if _state else []
    channels = _state.get_all_channels() if _state else []
    return Titled("TubeWrangler",
        Main(
            H2("Canais monitorados"),
            Ul(*[Li(c) for c in channels]) if channels else P("Nenhum canal configurado."),
            H2("Streams ativos"),
            Ul(*[Li(str(s)) for s in streams]) if streams else P("Nenhum stream encontrado."),
        ),
        Footer(
            A("Configuracoes", href="/config"), " | ",
            A("Forcar sync",   href="/force-sync")
        )
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
