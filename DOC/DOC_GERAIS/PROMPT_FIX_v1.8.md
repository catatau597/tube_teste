# PROMPT DE FIX — TubeWrangler v1.8 (VOD streaming + logs fix + nav global)

> **Versão:** v1.8
> **Escopo:** 3 fixes
> 1. VOD via yt-dlp → ffmpeg streaming (sem download completo)
> 2. /logs corrigido (path + SSE)
> 3. Nav global em todas as páginas

---

## FIX 1 — core/player_router.py — VOD como streaming real

### Adicionar import no topo

```python
import os  # já deve existir
import subprocess  # adicionar se não existir
```

### Substituir build_ytdlp_cmd por build_ytdlp_ffmpeg_cmd

A abordagem é em 2 fases:
- Fase 1 (síncrona, rápida): `yt-dlp --get-url` → resolve URL CDN do YouTube (não faz download)
- Fase 2: `ffmpeg -i <cdn_url> -c copy -f mpegts pipe:1` → streaming progressivo

```python
def _resolve_ytdlp_url(watch_url: str, user_agent: str = "Mozilla/5.0") -> str:
    """Resolve URL CDN real via yt-dlp --get-url. Retorna string vazia em caso de falha."""
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "-f", "b",
                "--get-url",
                "--no-playlist",
                "--user-agent", user_agent,
                watch_url,
            ],
            capture_output=True, text=True, timeout=20,
        )
        url = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        return url
    except Exception:
        return ""


def build_ytdlp_ffmpeg_cmd(watch_url: str, user_agent: str = "Mozilla/5.0") -> list[str]:
    """
    Resolve URL real via yt-dlp e retorna comando ffmpeg para streaming progressivo.
    Se resolução falhar, retorna comando yt-dlp direto como fallback.
    """
    cdn_url = _resolve_ytdlp_url(watch_url, user_agent)
    if cdn_url:
        return [
            "ffmpeg", "-loglevel", "error",
            "-headers", f"User-Agent: {user_agent}\r\n",
            "-i", cdn_url,
            "-c", "copy",
            "-f", "mpegts",
            "pipe:1",
        ]
    # Fallback: yt-dlp direto (comportamento anterior)
    return [
        "yt-dlp", "-f", "b", "-o", "-",
        "--no-playlist",
        "--user-agent", user_agent,
        watch_url,
    ]
```

### Atualizar build_player_command para usar a nova função

```python
def build_player_command(...) -> tuple[list[str], list[str]]:
    if status == "live":
        return build_streamlink_cmd(watch_url, user_agent), []
    if status == "none":
        return build_ytdlp_ffmpeg_cmd(watch_url, user_agent), []  # ← atualizado
    ...
```

### Manter build_ytdlp_cmd como função interna (usada por _resolve_ytdlp_url) ou remover

> `build_ytdlp_cmd` original pode ser removida — não é mais chamada externamente.
> `_resolve_ytdlp_url` usa subprocess.run síncrono — OK porque é chamada antes de
> criar o processo async, no contexto de preparação do comando.

---

## FIX 2 — web/main.py — /logs corrigido

### 2a. Corrigir LOG_FILE_PATH (linha ~28)

```python
# Verificar o path atual e corrigir para onde o arquivo realmente está:
# O arquivo está em /data/logs/tubewrangler.log
LOG_FILE_PATH = Path("/data/logs/tubewrangler.log")
```

> Se já está correto, verificar se o diretório é criado no entrypoint/Dockerfile.
> Adicionar no entrypoint.sh se necessário:
> `mkdir -p /data/logs`

### 2b. Verificar api_logs_stream — SSE funcionando

```python
# Confirmar que a rota SSE lê o arquivo corretamente:
@app.get("/api/logs/stream")
async def api_logs_stream():
    async def event_gen():
        if not LOG_FILE_PATH.exists():
            yield f"data: [arquivo de log não encontrado: {LOG_FILE_PATH}]\n\n"
            return
        lines = LOG_FILE_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        # Enviar últimas 200 linhas inicialmente
        for line in lines[-200:]:
            yield f"data: {line}\n\n"
        # Polling para novas linhas
        last_size = LOG_FILE_PATH.stat().st_size
        while True:
            await asyncio.sleep(2)
            try:
                current_size = LOG_FILE_PATH.stat().st_size
                if current_size > last_size:
                    content = LOG_FILE_PATH.read_text(encoding="utf-8", errors="replace")
                    new_lines = content.splitlines()[-(current_size - last_size)//80:]
                    for line in new_lines:
                        yield f"data: {line}\n\n"
                    last_size = current_size
            except Exception as e:
                yield f"data: [erro ao ler log: {e}]\n\n"
    return StreamingResponse(event_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

---

## FIX 3 — web/main.py — Nav global em todas as páginas

### Extrair componente _nav()

```python
def _nav():
    return Div(
        A("Dashboard", href="/", style="margin-right:12px;"),
        A("Force Sync", href="/force-sync", style="margin-right:12px;"),
        A("Config", href="/config", style="margin-right:12px;"),
        A("Logs", href="/logs"),
        style="padding:8px 0 16px 0; border-bottom: 1px solid #ccc; margin-bottom:16px;"
    )
```

### Adicionar _nav() em todas as páginas

```python
# home() — já tem nav inline, substituir pelo componente:
return Titled("TubeWrangler", _nav(), ...)

# config_page():
return Titled("Configuracoes", _nav(), Form(...))

# logs_page():
return Titled("Logs", _nav(), ...)
```

---

## Validação

```bash
# 1. Restart
docker compose restart && sleep 8

# 2. Testar resolução de URL VOD
docker compose exec tubewranglerr python3 -c "
import sys; sys.path.insert(0, '.')
from core.player_router import _resolve_ytdlp_url
url = _resolve_ytdlp_url('https://www.youtube.com/watch?v=QOHFg3KK_jA')
print('URL resolvida:', url[:80] if url else 'FALHA')
"
# Esperado: URL CDN googlevideo.com (80+ chars)

# 3. Testar bytes do VOD stream
curl -s --max-time 8 http://localhost:8888/api/player/QOHFg3KK_jA | wc -c
# Esperado: > 100000

# 4. /logs exibindo conteúdo
curl -s --max-time 3 http://localhost:8888/api/logs/stream | head -5
# Esperado: linhas de log via SSE (data: 2026-02-27 ...)

# 5. Nav presente em todas as páginas
curl -s http://localhost:8888/ | grep -c "Force Sync"
curl -s http://localhost:8888/config | grep -c "Force Sync"
curl -s http://localhost:8888/logs | grep -c "Force Sync"
# Esperado: 1 em cada

# 6. Testes
docker compose exec tubewranglerr python3 -m pytest tests/ -q
```

---

## Notas para o agente

- `_resolve_ytdlp_url` é síncrona com timeout=20s — aceitável pois é chamada uma vez
  antes de iniciar o stream. Se o VOD for muito antigo/privado, cai no fallback.
- `subprocess` já pode estar importado em player_router.py — verificar antes de adicionar
- O SSE de logs usa polling simples (2s) — não usa inotify, suficiente para debug
- entrypoint.sh: garantir `mkdir -p /data/logs` antes de iniciar uvicorn
