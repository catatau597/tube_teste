# PROMPT DE IMPLEMENTAÇÃO — TubeWrangler RETROFIT v1.0

> **Versão:** RETROFIT v1.0
> **Projeto:** TubeWranglerr
> **Destino:** Agente autônomo (GitHub Copilot / Cursor)
> **Objetivo:** Implementar modo proxy, API REST, lifecycle de thumbnails e playlists híbridas
> **Pré-requisito:** Refactoring v3.5.1 concluído e validado (branch `dev`, todos os testes passando)

---

## ⚠️ REGRAS HERDADAS (não renegociar)

1. Todo Python executa **dentro do container** via `docker compose exec tubewranglerr`
2. Fastlite rows são dicionários — `row["key"]`, NUNCA `row.key`
3. PROIBIDO: `os.getenv()`, `load_dotenv()`, `Flask`, `web/routes/`
4. PROIBIDO: push direto na `main`
5. Em caso de dúvida → registrar em DECISIONS.md e aguardar

---

## ÍNDICE

1. [Novas variáveis de configuração](#1-novas-variáveis-de-configuração)
2. [Estrutura de arquivos nova](#2-estrutura-de-arquivos-nova)
3. [Etapa A — Thumbnail Manager](#3-etapa-a--thumbnail-manager)
4. [Etapa B — M3UGenerator híbrido](#4-etapa-b--m3ugenerator-híbrido)
5. [Etapa C — Rotas API REST](#5-etapa-c--rotas-api-rest)
6. [Etapa D — /api/player e /api/thumbnail](#6-etapa-d--apiplayer-e-apithumbnail)
7. [Etapa E — Página /logs com SSE](#7-etapa-e--página-logs-com-sse)
8. [Checklist final de validação](#8-checklist-final-de-validação)

---

## 1. Novas variáveis de configuração

Adicionar em `core/config.py` (método `_defaults`):

```python
# Proxy
"proxy_base_url":             "",         # vazio = auto-detect http://{HOST_IP}:8888
"generate_direct_playlists":  "true",     # gerar playlist_*_direct.m3u8
"generate_proxy_playlists":   "true",     # gerar playlist_*_proxy.m3u8
"use_invisible_placeholder":  "true",     # já existia, manter

# Thumbnails
"thumbnail_cache_directory":  "/data/thumbnails",

# Títulos
"prefix_title_with_status":   "true",    # já existia como PREFIXTITLEWITHSTATUS
```

Adicionar também ao `.env` de referência:
```
PROXY_BASE_URL=
GENERATE_DIRECT_PLAYLISTS=true
GENERATE_PROXY_PLAYLISTS=true
THUMBNAIL_CACHE_DIRECTORY=/data/thumbnails
```

---

## 2. Estrutura de arquivos nova

```
tubewranglerr/
├── core/
│   ├── config.py              ← adicionar novos defaults + get_all() + set()
│   ├── state_manager.py       ← adicionar thumbnail lifecycle
│   ├── thumbnail_manager.py   ← NOVO
│   ├── playlist_builder.py    ← refactor M3UGenerator híbrido
│   ├── player_router.py       ← NOVO (extraído do smart_player.py)
│   ├── youtube_api.py         ← sem alteração
│   └── scheduler.py           ← _save_files com novos params
│
├── web/
│   └── main.py                ← adicionar rotas /api/* e /logs
│
└── data/
    ├── thumbnails/            ← NOVO (criado automaticamente)
    ├── m3us/
    │   ├── playlist_live_direct.m3u8
    │   ├── playlist_live_proxy.m3u8
    │   ├── playlist_upcoming_proxy.m3u8   ← nunca direct
    │   ├── playlist_vod_direct.m3u8
    │   └── playlist_vod_proxy.m3u8
    └── epgs/
        └── youtube_epg.xml
```

---

## 3. Etapa A — Thumbnail Manager

### A.1 Criar `core/thumbnail_manager.py`

```python
"""
core/thumbnail_manager.py
Responsabilidade: Download, cache local e exclusão de thumbnails por video_id.
Lifecycle: thumbnail nasce com o stream no StateManager e morre junto com ele.
"""
import logging
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger("TubeWrangler")


class ThumbnailManager:
    def __init__(self, cache_dir: str):
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def get_local_path(self, video_id: str) -> Path:
        return self._cache_dir / f"{video_id}.jpg"

    def get_url(self, video_id: str, base_url: str) -> str:
        return f"{base_url.rstrip('/')}/api/thumbnail/{video_id}"

    def ensure_cached(self, video_id: str, remote_url: str) -> bool:
        """Baixa thumbnail se ainda não existir em cache. Retorna True se ok."""
        local = self.get_local_path(video_id)
        if local.exists():
            return True
        if not remote_url:
            return False
        try:
            urllib.request.urlretrieve(remote_url, local)
            logger.debug(f"Thumbnail cacheada: {video_id}")
            return True
        except Exception as e:
            logger.warning(f"Falha ao cachear thumbnail {video_id}: {e}")
            return False

    def delete(self, video_id: str) -> None:
        """Remove thumbnail do cache. Chamado quando stream é removido do StateManager."""
        local = self.get_local_path(video_id)
        if local.exists():
            try:
                local.unlink()
                logger.debug(f"Thumbnail removida: {video_id}")
            except Exception as e:
                logger.warning(f"Falha ao remover thumbnail {video_id}: {e}")

    def serve(self, video_id: str) -> Optional[bytes]:
        """Retorna bytes da thumbnail local ou None se não existir."""
        local = self.get_local_path(video_id)
        if local.exists():
            return local.read_bytes()
        return None
```

### A.2 Integrar no `StateManager`

Em `core/state_manager.py`:

```python
# No __init__ do StateManager, adicionar:
self._thumbnail_manager = None

# Novo método público:
def set_thumbnail_manager(self, tm) -> None:
    self._thumbnail_manager = tm

# Em prune_ended_streams(), após self.streams.pop(vid, None):
if self._thumbnail_manager:
    self._thumbnail_manager.delete(vid)
```

### A.3 Inicializar no lifespan (`web/main.py`)

```python
from core.thumbnail_manager import ThumbnailManager

# No lifespan, após criar state:
thumb_dir = config.get_str("thumbnail_cache_directory")
thumbnail_manager = ThumbnailManager(thumb_dir)
state.set_thumbnail_manager(thumbnail_manager)
```

### A.4 Checklist Etapa A

```
[ ] core/thumbnail_manager.py criado
[ ] ThumbnailManager.__init__, ensure_cached, delete, serve implementados
[ ] StateManager.set_thumbnail_manager() adicionado
[ ] prune_ended_streams() chama thumbnail_manager.delete(vid) para cada vid removido
[ ] lifespan inicializa ThumbnailManager e passa ao StateManager
[ ] docker compose exec tubewranglerr python3 -c "from core.thumbnail_manager import ThumbnailManager; print('OK')"
[ ] /data/thumbnails criado automaticamente
```

---

## 4. Etapa B — M3UGenerator híbrido

### B.1 Helper de proxy base URL

Adicionar em `core/playlist_builder.py`:

```python
import socket

def _resolve_proxy_base_url(config) -> str:
    """Resolve PROXY_BASE_URL. Se vazio, auto-detecta IP do host."""
    configured = config.get_str("proxy_base_url").strip()
    if configured:
        return configured.rstrip("/")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        port = config.get_int("http_port")
        return f"http://{ip}:{port}"
    except Exception:
        return f"http://localhost:{config.get_int('http_port')}"
```

### B.2 Refactor `M3UGenerator.generate_playlist()`

```python
def generate_playlist(
    self,
    streams: List,
    db: Dict,
    mode: str,                    # "live", "upcoming", "vod"
    mode_type: str = "direct",    # "direct" ou "proxy"
    thumbnail_manager=None,
    proxy_base_url: str = "",
) -> str:
```

**Regras de URL por combinação:**

| mode     | mode_type | URL do stream                        | tvg-logo                            |
|----------|-----------|--------------------------------------|-------------------------------------|
| live     | direct    | https://youtube.com/watch?v=ID       | URL ytimg original                  |
| live     | proxy     | {PROXY_BASE_URL}/api/player/ID       | {PROXY_BASE_URL}/api/thumbnail/ID   |
| upcoming | proxy     | {PROXY_BASE_URL}/api/player/ID       | {PROXY_BASE_URL}/api/thumbnail/ID   |
| vod      | direct    | https://youtube.com/watch?v=ID       | URL ytimg original                  |
| vod      | proxy     | {PROXY_BASE_URL}/api/player/ID       | {PROXY_BASE_URL}/api/thumbnail/ID   |

**REGRA CRÍTICA:** `upcoming` com `mode_type="direct"` deve lançar:
```python
raise ValueError("upcoming nunca pode ser modo direct")
```

**Categoria:** usar `self.get_display_category(s.get("categoryoriginal"), db)`.
NUNCA usar nome do canal como `group-title`.

**Placeholder invisível** quando lista vazia e `use_invisible_placeholder=true`:
```
#EXTINF:-1 tvg-id="PLACEHOLDER_LIVE" ...
#https://placeholder_url
```

**`prefix_title_with_status`** aplica igual para `direct` e `proxy`.

### B.3 Atualizar `_save_files` no Scheduler

```python
def _save_files(state, config, m3u_gen, xmltv_gen, categories_db, thumbnail_manager=None):
    from core.playlist_builder import _resolve_proxy_base_url

    proxy_url  = _resolve_proxy_base_url(config)
    gen_direct = config.get_bool("generate_direct_playlists")
    gen_proxy  = config.get_bool("generate_proxy_playlists")
    all_streams = state.get_all_streams()

    playlist_dir = Path(config.get_str("playlist_save_directory"))
    playlist_dir.mkdir(parents=True, exist_ok=True)

    # Pre-cache thumbnails para versão proxy
    if gen_proxy and thumbnail_manager:
        for s in all_streams:
            vid   = s.get("videoid")
            thumb = s.get("thumbnailurl")
            if vid and thumb:
                thumbnail_manager.ensure_cached(vid, thumb)

    # Playlists direct (live + vod — nunca upcoming)
    if gen_direct:
        for mode in ("live", "vod"):
            content  = m3u_gen.generate_playlist(all_streams, categories_db, mode, "direct")
            (playlist_dir / f"playlist_{mode}_direct.m3u8").write_text(content, encoding="utf-8")

    # Playlists proxy (live + upcoming + vod)
    if gen_proxy:
        for mode in ("live", "upcoming", "vod"):
            content  = m3u_gen.generate_playlist(
                all_streams, categories_db, mode, "proxy",
                thumbnail_manager=thumbnail_manager,
                proxy_base_url=proxy_url,
            )
            (playlist_dir / f"playlist_{mode}_proxy.m3u8").write_text(content, encoding="utf-8")

    # EPG
    xmltv_dir = Path(config.get_str("xmltv_save_directory"))
    xmltv_dir.mkdir(parents=True, exist_ok=True)
    epg = xmltv_gen.generate_xml(state.get_all_channels(), all_streams, categories_db)
    (xmltv_dir / config.get_str("xmltv_filename")).write_text(epg, encoding="utf-8")

    logger.info(f"Arquivos salvos. Direct={gen_direct} Proxy={gen_proxy} ProxyBase={proxy_url}")
```

### B.4 Rotas estáticas na raiz

```python
# Em web/main.py — servir arquivos do disco para players IPTV
PLAYLIST_FILES = [
    "playlist_live_direct.m3u8",
    "playlist_live_proxy.m3u8",
    "playlist_upcoming_proxy.m3u8",
    "playlist_vod_direct.m3u8",
    "playlist_vod_proxy.m3u8",
]

for _fname in PLAYLIST_FILES:
    def _make_route(fname):
        @app.get(f"/{fname}")
        def serve_playlist():
            path = Path(config.get_str("playlist_save_directory")) / fname
            if not path.exists():
                return Response("Playlist não gerada ainda", status_code=404)
            return Response(path.read_text(encoding="utf-8"),
                            media_type="application/vnd.apple.mpegurl")
    _make_route(_fname)

@app.get("/youtube_epg.xml")
def serve_epg_static():
    path = Path(config.get_str("xmltv_save_directory")) / config.get_str("xmltv_filename")
    if not path.exists():
        return Response("EPG não gerado ainda", status_code=404)
    return Response(path.read_text(encoding="utf-8"), media_type="application/xml")
```

### B.5 Checklist Etapa B

```
[ ] _resolve_proxy_base_url() implementado
[ ] generate_playlist() aceita mode_type, thumbnail_manager, proxy_base_url
[ ] upcoming + direct lança ValueError
[ ] URLs corretas para cada combinação (tabela acima)
[ ] tvg-logo usa /api/thumbnail/ID na versão proxy
[ ] group-title usa category_mappings, NUNCA nome do canal
[ ] prefix_title_with_status respeitado em ambos os tipos
[ ] use_invisible_placeholder por playlist individual
[ ] _save_files gera os 5 arquivos corretos
[ ] Rotas estáticas na raiz funcionando
[ ] ls -la /data/m3us/ — 5 arquivos presentes
[ ] curl http://localhost:8888/playlist_live_proxy.m3u8 | head -5 — M3U válido
```

---

## 5. Etapa C — Rotas API REST

### C.1 Adicionar `get_all()` e `set()` ao AppConfig

```python
def get_all(self) -> dict:
    result = {}
    for row in self._db.t.config.rows:
        result[row["key"]] = row["value"]
    return result

def set(self, key: str, value: str) -> None:
    existing = list(self._db.t.config.rows_where("key = ?", [key]))
    if existing:
        self._db.t.config.update({"key": key, "value": value})
    else:
        self._db.t.config.insert({"key": key, "value": value})
```

### C.2 Rotas em `web/main.py`

```python
def _serialize_stream(s: dict) -> dict:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in s.items()}

# Canais
@app.get("/api/channels")
def api_channels_list():
    return JSONResponse([{"id": k, "title": v} for k, v in state.get_all_channels().items()])

@app.post("/api/channels")
async def api_channels_create(req):
    body = await req.json()
    cid  = body.get("id", "").strip()
    title = body.get("title", "").strip()
    if not cid or not title:
        return JSONResponse({"error": "id e title obrigatórios"}, status_code=400)
    state.channels[cid] = title
    state.save_to_disk()
    return JSONResponse({"ok": True, "id": cid})

@app.delete("/api/channels/{channel_id}")
def api_channels_delete(channel_id: str):
    if channel_id not in state.channels:
        return JSONResponse({"error": "não encontrado"}, status_code=404)
    del state.channels[channel_id]
    state.save_to_disk()
    return JSONResponse({"ok": True})

# Streams
@app.get("/api/streams")
def api_streams_list(status: str = ""):
    streams = state.get_all_streams()
    if status:
        streams = [s for s in streams if s.get("status") == status]
    return JSONResponse([_serialize_stream(s) for s in streams])

@app.get("/api/streams/{video_id}")
def api_streams_detail(video_id: str):
    stream = state.streams.get(video_id)
    if not stream:
        return JSONResponse({"error": "não encontrado"}, status_code=404)
    return JSONResponse(_serialize_stream(stream))

# Config
@app.get("/api/config")
def api_config_get():
    return JSONResponse(config.get_all())

@app.put("/api/config")
async def api_config_put(req):
    body  = await req.json()
    key   = body.get("key", "").strip()
    value = str(body.get("value", "")).strip()
    if not key:
        return JSONResponse({"error": "key obrigatório"}, status_code=400)
    config.set(key, value)
    return JSONResponse({"ok": True, "key": key, "value": value})

# Playlists e EPG
@app.put("/api/playlists/refresh")
def api_playlists_refresh():
    scheduler.trigger_now()
    return JSONResponse({"ok": True, "message": "sync agendado"})

@app.get("/api/epg")
def api_epg():
    epg_path = Path(config.get_str("xmltv_save_directory")) / config.get_str("xmltv_filename")
    if not epg_path.exists():
        return JSONResponse({"error": "EPG não gerado ainda"}, status_code=404)
    return Response(epg_path.read_text(encoding="utf-8"), media_type="application/xml")
```

### C.3 Checklist Etapa C

```
[ ] AppConfig.get_all() e AppConfig.set() implementados
[ ] GET  /api/channels → 200 JSON lista
[ ] POST /api/channels → 200 JSON criado
[ ] DELETE /api/channels/{id} → 200 ou 404
[ ] GET  /api/streams → 200 JSON todos
[ ] GET  /api/streams?status=live → filtrado
[ ] GET  /api/streams/{id} → 200 ou 404
[ ] GET  /api/config → 200 JSON todas as vars
[ ] PUT  /api/config → atualiza e persiste no SQLite
[ ] PUT  /api/playlists/refresh → aciona scheduler
[ ] GET  /api/epg → XML ou 404
[ ] curl -s http://localhost:8888/api/streams | python3 -m json.tool | head -20
```

---

## 6. Etapa D — /api/player e /api/thumbnail

### D.1 Criar `core/player_router.py`

```python
"""
core/player_router.py
Responsabilidade: Decisão de qual comando executar dado o status do stream.
Portado do smart_player.py — sem subprocess direto, retorna List[str] de comando.
"""
import json
from pathlib import Path
from typing import Dict, List, Optional


def _escape_ffmpeg_text(text: str) -> str:
    for old, new in [("\\", "\\\\"), ("'", "\\'"), (":", "\\:"), ("%", "\\%"), (",", "\\,")]:
        text = text.replace(old, new)
    return text


def _get_texts_from_cache(video_id: str, texts_cache_path: Path) -> Dict[str, str]:
    try:
        if texts_cache_path.exists():
            data = json.loads(texts_cache_path.read_text(encoding="utf-8"))
            return data.get(video_id, {"line1": "", "line2": ""})
    except Exception:
        pass
    return {"line1": "", "line2": ""}


def build_ffmpeg_placeholder_cmd(
    image_url: str,
    text_line1: str = "",
    text_line2: str = "",
    font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    user_agent: str = "Mozilla/5.0",
) -> List[str]:
    drawtext_filters = []
    if Path(font_path).is_file():
        if text_line1:
            t = _escape_ffmpeg_text(text_line1)
            drawtext_filters.append(
                f"drawtext=fontfile={font_path}:text={t}"
                f":x=(w-text_w)/2:y=h-100:fontsize=48:fontcolor=white:borderw=2:bordercolor=black@0.8"
            )
        if text_line2:
            t = _escape_ffmpeg_text(text_line2)
            drawtext_filters.append(
                f"drawtext=fontfile={font_path}:text={t}"
                f":x=(w-text_w)/2:y=h-50:fontsize=36:fontcolor=white:borderw=2:bordercolor=black@0.8"
            )
    filter_chain = ",".join(drawtext_filters) if drawtext_filters else ""
    video_filter = (
        f"[0:v]fps=25,scale=1280:720,loop=-1:1:0"
        f"{(',' + filter_chain) if filter_chain else ''}[v]"
    )
    return [
        "ffmpeg", "-loglevel", "error",
        "-re", "-user_agent", user_agent,
        "-i", image_url,
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-filter_complex", video_filter,
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest", "-tune", "stillimage",
        "-f", "mpegts", "pipe:1",
    ]


def build_streamlink_cmd(watch_url: str, user_agent: str = "Mozilla/5.0") -> List[str]:
    return [
        "streamlink", "--stdout",
        "--http-header", f"User-Agent={user_agent}",
        "--config", "/dev/null",
        "--no-plugin-sideloading",
        watch_url, "best",
    ]


def build_ytdlp_cmd(watch_url: str, user_agent: str = "Mozilla/5.0") -> List[str]:
    return [
        "yt-dlp", "-f", "best", "-o", "-",
        "--user-agent", user_agent,
        watch_url,
    ]


def build_player_command(
    video_id: str,
    status: Optional[str],
    watch_url: str,
    thumbnail_url: str,
    user_agent: str = "Mozilla/5.0",
    font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    texts_cache_path: Optional[Path] = None,
) -> List[str]:
    if status == "live":
        return build_streamlink_cmd(watch_url, user_agent)
    if status == "none":
        return build_ytdlp_cmd(watch_url, user_agent)
    # upcoming ou desconhecido → placeholder ffmpeg
    texts = _get_texts_from_cache(video_id, texts_cache_path) if texts_cache_path else {}
    return build_ffmpeg_placeholder_cmd(
        image_url=thumbnail_url,
        text_line1=texts.get("line1", ""),
        text_line2=texts.get("line2", ""),
        font_path=font_path,
        user_agent=user_agent,
    )
```

### D.2 Rota `/api/player/{video_id}`

```python
import asyncio
from starlette.responses import StreamingResponse
from core.player_router import build_player_command

TEXTS_CACHE_PATH = Path("/data/textosepg.json")
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

@app.get("/api/player/{video_id}")
async def api_player_stream(video_id: str, user_agent: str = "Mozilla/5.0"):
    stream_info   = state.streams.get(video_id)
    status        = stream_info.get("status") if stream_info else None
    thumbnail_url = stream_info.get("thumbnailurl") if stream_info else None
    watch_url     = f"https://www.youtube.com/watch?v={video_id}"
    placeholder   = config.get_str("placeholder_image_url")
    thumb_for_ph  = thumbnail_url or placeholder

    cmd = build_player_command(
        video_id=video_id,
        status=status,
        watch_url=watch_url,
        thumbnail_url=thumb_for_ph,
        user_agent=user_agent,
        font_path=FONT_PATH,
        texts_cache_path=TEXTS_CACHE_PATH,
    )

    async def stream_from_process():
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                proc.kill()
            except Exception:
                pass

    return StreamingResponse(
        stream_from_process(),
        media_type="video/mp2t",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

### D.3 Rota `/api/thumbnail/{video_id}`

```python
import re
from starlette.responses import RedirectResponse

@app.get("/api/thumbnail/{video_id}")
def api_thumbnail(video_id: str):
    if not re.match(r"^[a-zA-Z0-9_-]+$", video_id):
        return JSONResponse({"error": "video_id inválido"}, status_code=400)
    data = thumbnail_manager.serve(video_id)
    if data:
        return Response(data, media_type="image/jpeg",
                        headers={"Cache-Control": "max-age=3600"})
    return RedirectResponse(
        f"https://i.ytimg.com/vi/{video_id}/maxresdefault_live.jpg",
        status_code=302,
    )
```

### D.4 Atualizar `smart_player.py`

Substituir lógica de construção de comando por importação de `core.player_router`:

```python
from core.player_router import build_player_command

cmd = build_player_command(
    video_id=video_id,
    status=status,
    watch_url=url,
    thumbnail_url=thumb_to_use,
    user_agent=user_agent,
    texts_cache_path=TEXTS_CACHE_PATH,
)
process = subprocess.Popen(cmd, stdout=sys.stdout, stderr=subprocess.PIPE)
_, stderr_output = process.communicate()
if process.returncode != 0 and stderr_output:
    logger.error(f"Stderr: {stderr_output.decode('utf-8', errors='ignore')}")
```

### D.5 Checklist Etapa D

```
[ ] core/player_router.py criado com build_player_command, build_ffmpeg_placeholder_cmd, build_streamlink_cmd, build_ytdlp_cmd
[ ] GET /api/player/{video_id} retorna StreamingResponse MPEG-TS
[ ] GET /api/thumbnail/{video_id} retorna JPEG ou redirect 302
[ ] smart_player.py importa de core.player_router (sem duplicar lógica)
[ ] curl -o /tmp/t.jpg http://localhost:8888/api/thumbnail/VIDEO_ID && file /tmp/t.jpg
[ ] docker compose exec tubewranglerr python3 smart_player.py -i "https://youtube.com/watch?v=VIDEO_ID" > /dev/null
[ ] curl -s "http://localhost:8888/api/player/VIDEO_ID" > /dev/null — sem erro 500
```

---

## 7. Etapa E — Página /logs com SSE

### E.1 Garantir log em arquivo (lifespan de `web/main.py`)

```python
import logging
from pathlib import Path

log_file = Path("/data/logs/tubewrangler.log")
log_file.parent.mkdir(parents=True, exist_ok=True)
file_handler = logging.FileHandler(log_file, encoding="utf-8")
file_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)-8s %(name)s %(message)s",
                      datefmt="%Y-%m-%d %H:%M:%S")
)
logging.getLogger("TubeWrangler").addHandler(file_handler)
```

### E.2 Rota SSE e página

```python
import asyncio
from starlette.responses import StreamingResponse

LOG_FILE_PATH = Path("/data/logs/tubewrangler.log")

@app.get("/api/logs/stream")
async def api_logs_stream():
    async def event_generator():
        # Últimas 100 linhas ao conectar
        if LOG_FILE_PATH.exists():
            lines = LOG_FILE_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-100:]:
                yield f"data: {line}\n\n"
        # Tail em tempo real
        last_size = LOG_FILE_PATH.stat().st_size if LOG_FILE_PATH.exists() else 0
        while True:
            await asyncio.sleep(1)
            if not LOG_FILE_PATH.exists():
                continue
            current_size = LOG_FILE_PATH.stat().st_size
            if current_size > last_size:
                with open(LOG_FILE_PATH, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(last_size)
                    new_lines = f.read()
                last_size = current_size
                for line in new_lines.splitlines():
                    if line.strip():
                        yield f"data: {line}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@app.get("/logs")
def logs_page():
    return Html(
        Head(
            Title("TubeWrangler — Logs"),
            Style("body{font-family:monospace;background:#0d1117;color:#c9d1d9;margin:1rem}"
                  "pre{background:#161b22;padding:1rem;height:80vh;overflow-y:auto;"
                  "font-size:12px;border-radius:6px}"
                  "a{color:#58a6ff}"),
        ),
        Body(
            H2("📋 Logs em tempo real"),
            A("← Home", href="/"), " | ", A("Force Sync", href="/force-sync"),
            Pre(id="log-output"),
            Script("""
                const output = document.getElementById('log-output');
                const es = new EventSource('/api/logs/stream');
                es.onmessage = e => {
                    output.textContent += e.data + '\n';
                    output.scrollTop = output.scrollHeight;
                };
                es.onerror = () => {
                    output.textContent += '\n[conexão perdida — reconectando...]\n';
                };
            """),
        ),
    )
```

### E.3 Checklist Etapa E

```
[ ] Log escrito em /data/logs/tubewrangler.log
[ ] GET /api/logs/stream → SSE com últimas 100 linhas + tail
[ ] GET /logs → página HTML com EventSource no browser
[ ] Abrir /logs, forçar sync → logs aparecem em tempo real
[ ] Generator é async — não bloqueia uvicorn
```

---

## 8. Checklist final de validação

```
ETAPA A — Thumbnails
[ ] ThumbnailManager criado e integrado
[ ] Thumbnails deletadas junto com streams em prune_ended_streams

ETAPA B — Playlists Híbridas
[ ] playlist_live_direct.m3u8 — URLs youtube.com diretas
[ ] playlist_live_proxy.m3u8 — URLs /api/player/ID
[ ] playlist_upcoming_proxy.m3u8 — nunca direct (ValueError se tentar)
[ ] playlist_vod_direct.m3u8 — URLs youtube.com diretas
[ ] playlist_vod_proxy.m3u8 — URLs /api/player/ID
[ ] category_mappings em group-title (nunca nome do canal)
[ ] use_invisible_placeholder por playlist vazia
[ ] Rotas estáticas na raiz servem arquivos do disco

ETAPA C — API REST
[ ] CRUD /api/channels funcional
[ ] GET /api/streams com ?status= funcional
[ ] GET/PUT /api/config persiste no SQLite
[ ] PUT /api/playlists/refresh dispara scheduler.trigger_now()

ETAPA D — Proxy Stream
[ ] /api/player/{id} — MPEG-TS live via streamlink
[ ] /api/player/{id} — MPEG-TS vod via yt-dlp
[ ] /api/player/{id} — placeholder ffmpeg para upcoming
[ ] /api/thumbnail/{id} — JPEG cacheado ou redirect 302
[ ] smart_player.py CLI funciona via core.player_router

ETAPA E — Logs
[ ] /logs página funcional
[ ] /api/logs/stream SSE funcional

MERGE PARA MAIN
[ ] Todos os pytest passando
[ ] docker compose logs sem erros críticos
[ ] DECISIONS.md atualizado com IP detectado pelo proxy auto-detect
[ ] git merge dev → main — ação humana explícita
```

---

## Notas finais para o agente

- **Ordem obrigatória:** A → B → C → D → E. Não pular etapas.
- **Não remover** `smart_player.py` — mantê-lo como CLI funcional usando `core.player_router`.
- **Rotas estáticas** (`/playlist_*.m3u8`, `/youtube_epg.xml`) ficam na raiz — players IPTV esperam URLs limpas.
- **`upcoming` nunca gera direct** — enforçar com `ValueError`.
- **`PROXY_BASE_URL` vazio** → auto-detect via socket — registrar IP detectado em DECISIONS.md.
- **Branch:** toda implementação em `dev`. Merge para `main` só após checklist 100%.
