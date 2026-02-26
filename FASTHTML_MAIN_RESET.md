Pare tudo. Você está em loop. Cada "correção" está criando um novo problema.

DIAGNÓSTICO REAL:
O problema não é ordem de imports, shadowing de funções, nem insert(0,...).
O problema é que você criou arquivos em web/routes/ (channels.py, logs.py etc)
e está tentando conectá-los ao app principal — isso não funciona no FastHTML
sem uma configuração específica de Mount que ainda não foi feita.

SOLUÇÃO DEFINITIVA:
Deletar web/routes/ inteiro e colocar TUDO em web/main.py.
Um arquivo. Zero imports de submódulos. Zero insert(0,...) para rotas normais.

AÇÃO — reescrever web/main.py do zero com este conteúdo exato:

================================================
from contextlib import asynccontextmanager
from fasthtml.common import *
from starlette.routing import Route
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse
import asyncio
from core.config import AppConfig
from core.state_manager import StateManager
from core.youtube_api import YouTubeAPI
from core.scheduler import Scheduler

_config = None
_state  = None
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

# ── Rotas com extensão: registradas via Starlette (bypass do catch-all do FastHTML)
async def _playlist_live(req: StarletteRequest):
    return StarletteResponse("#EXTM3U
", media_type="application/vnd.apple.mpegurl")

async def _playlist_upcoming(req: StarletteRequest):
    return StarletteResponse("#EXTM3U
", media_type="application/vnd.apple.mpegurl")

async def _playlist_vod(req: StarletteRequest):
    return StarletteResponse("#EXTM3U
", media_type="application/vnd.apple.mpegurl")

async def _epg_xml(req: StarletteRequest):
    xml = '<?xml version="1.0" encoding="UTF-8"?><tv></tv>'
    return StarletteResponse(xml, media_type="application/xml")

# ── App principal
app, rt = fast_app(
    lifespan=lifespan,
    hdrs=[Link(rel="stylesheet",
               href="https://cdn.jsdelivr.net/npm/pico.css@2/css/pico.min.css")]
)

# Inserir rotas com extensão NO TOPO (antes do catch-all interno do FastHTML)
app.router.routes.insert(0, Route("/playlist_live.m3u8",    _playlist_live))
app.router.routes.insert(0, Route("/playlist_upcoming.m3u8",_playlist_upcoming))
app.router.routes.insert(0, Route("/playlist_vod.m3u8",     _playlist_vod))
app.router.routes.insert(0, Route("/youtube_epg.xml",       _epg_xml))

# ── Rotas normais: @app.get / @app.post (sem extensão — funcionam normalmente)

@app.get("/")
def home():
    streams = _state.get_all_streams() if _state else []
    live     = [s for s in streams if s.get("status") == "live"]
    upcoming = [s for s in streams if s.get("status") == "upcoming"]
    vod      = [s for s in streams if s.get("status") == "none"]
    return Titled("TubeWranglerr — Dashboard",
        Article(
            Header(H2("Status")),
            Ul(
                Li(f"🔴 Ao vivo: {len(live)}"),
                Li(f"📅 Agendados: {len(upcoming)}"),
                Li(f"📼 Gravados: {len(vod)}"),
            ),
            Footer(A("⚙️ Configurações", href="/config"), " | ",
                   A("🔄 Forçar sync", href="/force-sync"))
        )
    )

@app.get("/config")
def config_page():
    sections = _config.get_all_by_section() if _config else {}
    fields = []
    for section, rows in sections.items():
        fields.append(H3(section.title()))
        for row in rows:
            fields.append(
                Label(row["description"],
                    Input(name=row["key"], value=row["value"],
                          type="number"   if row["value_type"] == "int"  else
                               "checkbox" if row["value_type"] == "bool" else "text"))
            )
    return Titled("Configurações",
        Form(*fields, Button("Salvar", type="submit"),
             method="post", action="/config")
    )

@app.post("/config")
async def save_config(request):
    form = await request.form()
    if _config:
        _config.update_many({k: v for k, v in form.items()})
        _config.reload()
        if _scheduler:
            _scheduler.reload_config(_config)
    return RedirectResponse("/config", status_code=303)

@app.get("/channels")
def channels_page():
    handles = _config.get_str("target_channel_handles") if _config else ""
    ids     = _config.get_str("target_channel_ids")     if _config else ""
    return Titled("Canais",
        Form(
            Label("Handles (@canal)", Input(name="target_channel_handles", value=handles)),
            Label("IDs diretos",      Input(name="target_channel_ids",     value=ids)),
            Button("Salvar", type="submit"),
            method="post", action="/channels"
        )
    )

@app.post("/channels")
async def save_channels(request):
    form = await request.form()
    if _config:
        if "target_channel_handles" in form:
            _config.update("target_channel_handles", form["target_channel_handles"])
        if "target_channel_ids" in form:
            _config.update("target_channel_ids", form["target_channel_ids"])
        _config.reload()
    return RedirectResponse("/channels", status_code=303)

@app.get("/logs")
def logs_page():
    return Titled("Logs",
        Pre(Id("log-output"), "Aguardando logs..."),
        Script("""
            const pre = document.getElementById('log-output');
            const es = new EventSource('/logs-stream');
            es.onmessage = e => {
                pre.textContent += e.data + '\n';
                pre.scrollTop = pre.scrollHeight;
            };
        """)
    )

@app.get("/force-sync")
def force_sync():
    if _scheduler:
        _scheduler.trigger_now()
    return RedirectResponse("/", status_code=303)
================================================

APÓS reescrever web/main.py com o conteúdo acima:

1. Deletar ou esvaziar web/routes/ — não deve haver nenhum arquivo
   em web/routes/ com decoradores @rt, @app.get ou Route registradas.
   Se o diretório existir, ele deve conter apenas __init__.py vazio.

2. Rebuildar:
   docker compose build --no-cache
   docker compose up -d

3. Aguardar 10 segundos e validar TODAS as rotas:
   docker compose exec tubewranglerr python3 -c "
   import urllib.request
   rotas = ['/', '/config', '/channels', '/logs',
            '/force-sync', '/playlist_live.m3u8',
            '/playlist_upcoming.m3u8', '/playlist_vod.m3u8',
            '/youtube_epg.xml']
   for rota in rotas:
       try:
           r = urllib.request.urlopen(f'http://localhost:8888{rota}')
           print(f'OK  {rota} → {r.status}')
       except urllib.error.HTTPError as e:
           print(f'ERR {rota} → HTTP {e.code}')
       except Exception as e:
           print(f'ERR {rota} → {e}')
   "

Esperado: todas as linhas começando com OK.

REGRAS PARA ESTA ETAPA:
- NÃO criar nenhum novo arquivo em web/routes/
- NÃO usar insert(0,...) para /channels, /logs ou qualquer rota sem extensão
- NÃO importar funções de outros módulos dentro de web/
- Somente avançar quando o script de validação retornar OK em todas as 9 rotas