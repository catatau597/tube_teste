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

_config    = None
_state     = None
_scheduler = None

@asynccontextmanager
async def lifespan(app):
    global _config, _state, _scheduler
    _config    = AppConfig()
    _state     = StateManager(_config)
    _state.load_from_disk()
    scraper    = YouTubeAPI(_config.get_str("youtube_api_key"))
    _scheduler = Scheduler(_config, scraper, _state)
    task = asyncio.create_task(_scheduler.run())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    _state.save_to_disk()

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
def config_save(req):
    data = dict(req.query_params)
    updates = {}
    for k, v in data.items():
        if "__" in k:
            section, key = k.split("__", 1)
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
