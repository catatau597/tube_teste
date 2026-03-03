# PROMPT DE FIX — TubeWrangler v1.9 (Log unificado em memória)

> **Versão:** v1.9
> **Escopo:** Sistema de log unificado — buffer circular em memória, sem arquivo em disco,
>             captura stderr de ffmpeg/streamlink/yt-dlp, página /logs com filtro e cores
> **Arquivos:** web/main.py, core/config.py, smart_player.py

---

## FIX 1 — web/main.py — Buffer de log + SSE + página /logs

### 1a. Remover referências a arquivos de log no topo do módulo

```python
# REMOVER estas linhas (aproximadamente linha 27-40):
_LOG_FILE = Path("/data/tubewrangler.log")
LOG_FILE_PATH = Path("/data/logs/tubewrangler.log")
# e o handler de arquivo criado fora do lifespan:
logging.FileHandler(_LOG_FILE, mode="a")
```

### 1b. Adicionar imports necessários no topo

```python
import collections  # adicionar se não existir
```

### 1c. Criar _LogBuffer e handler customizado — logo após os imports, antes do lifespan

```python
# ── Buffer circular de log em memória ──────────────────────────────────────
_LOG_BUFFER: collections.deque = collections.deque(maxlen=1000)

class _BufferHandler(logging.Handler):
    """Handler que escreve entradas de log no buffer circular."""
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _LOG_BUFFER.append(self.format(record))
        except Exception:
            pass

_buffer_handler = _BufferHandler()
_buffer_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)-8s %(name)s  %(message)s",
                      datefmt="%Y-%m-%d %H:%M:%S")
)

def _setup_logging(level_str: str = "INFO") -> None:
    """Configura logging global para usar o buffer. Chamado no lifespan."""
    level = getattr(logging, level_str.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    # Remover handlers existentes de arquivo/stream para evitar duplicatas
    for h in root.handlers[:]:
        if isinstance(h, (logging.FileHandler, logging.StreamHandler)):
            root.removeHandler(h)
    # Adicionar buffer + console (stderr para docker logs)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    root.addHandler(_buffer_handler)
    root.addHandler(console)
    # Uvicorn access log no mesmo buffer
    logging.getLogger("uvicorn.access").handlers = [_buffer_handler, console]
    logging.getLogger("uvicorn.access").propagate = False
```

### 1d. Atualizar lifespan — substituir setup de log existente

```python
# No lifespan, substituir o bloco de FileHandler/setup por:
_setup_logging(_config.get_str("log_level") or "INFO")
# Remover:
# LOG_FILE_PATH.parent.mkdir(...)
# file_handler = logging.FileHandler(...)
# logging.getLogger("TubeWrangler").addHandler(file_handler)
```

### 1e. Atualizar api_player_stream — capturar stderr dos processos

```python
# Substituir stream_gen() — capturar stderr em task paralela:
async def stream_gen():
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,   # ← era DEVNULL
        limit=1024 * 1024,
    )

    proc_logger = logging.getLogger("TubeWrangler.player")

    async def _log_stderr():
        """Lê stderr do processo e envia para o logger."""
        try:
            async for line in proc.stderr:
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    proc_logger.debug(f"[{video_id}] {text}")
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
        for tf in temp_files:
            try:
                os.unlink(tf)
            except Exception:
                pass
```

### 1f. Atualizar api_logs_stream — ler do buffer em vez de arquivo

```python
@app.get("/api/logs/stream")
async def api_logs_stream():
    async def event_gen():
        # Enviar buffer atual (snapshot)
        snapshot = list(_LOG_BUFFER)
        for line in snapshot:
            yield f"data: {line}\n\n"
        # Marcar posição atual
        last_len = len(_LOG_BUFFER)
        # Streaming de novas entradas
        while True:
            await asyncio.sleep(1)
            current = list(_LOG_BUFFER)
            current_len = len(current)
            if current_len > last_len:
                for line in current[last_len:]:
                    yield f"data: {line}\n\n"
            elif current_len < last_len:
                # Buffer foi rotacionado (maxlen atingido)
                pass
            last_len = current_len

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

### 1g. Atualizar logs_page() — filtro por nível + cores

```python
@app.get("/logs")
def logs_page():
    return Titled(
        "Logs",
        _nav(),
        Div(
            Select(
                Option("DEBUG", value="DEBUG"),
                Option("INFO",  value="INFO",  selected=True),
                Option("WARNING", value="WARNING"),
                Option("ERROR", value="ERROR"),
                id="log-level-filter",
                style="margin-right:8px;"
            ),
            Button("Limpar", id="btn-clear", style="margin-right:8px;"),
            Label(
                Input(type="checkbox", id="auto-scroll", checked=True,
                      style="margin-right:4px;"),
                "Auto-scroll"
            ),
            style="margin-bottom:8px;"
        ),
        Pre(id="log-output",
            style="height:80vh;overflow-y:auto;background:#111;color:#eee;"
                  "font-size:0.78rem;padding:8px;"),
        Style("""
            .log-DEBUG   { color: #888; }
            .log-INFO    { color: #eee; }
            .log-WARNING { color: #f90; }
            .log-ERROR   { color: #f44; font-weight:bold; }
        """),
        Script("""
            const output = document.getElementById('log-output');
            const filter = document.getElementById('log-level-filter');
            const autoScroll = document.getElementById('auto-scroll');
            document.getElementById('btn-clear').onclick = () => output.innerHTML = '';

            const LEVELS = ['DEBUG','INFO','WARNING','ERROR'];

            function levelOf(line) {
                for (const lv of LEVELS)
                    if (line.includes(' ' + lv + ' ') || line.includes(' ' + lv + '\t')) return lv;
                return 'INFO';
            }

            function appendLine(line) {
                const minLevel = filter.value;
                const lv = levelOf(line);
                if (LEVELS.indexOf(lv) < LEVELS.indexOf(minLevel)) return;
                const span = document.createElement('span');
                span.className = 'log-' + lv;
                span.textContent = line + '\n';
                output.appendChild(span);
                if (autoScroll.checked) output.scrollTop = output.scrollHeight;
            }

            const es = new EventSource('/api/logs/stream');
            es.onmessage = e => appendLine(e.data);
            es.onerror = () => appendLine('[conexao perdida - reconectando...]');

            // Re-filtrar ao mudar nível
            filter.onchange = () => {
                const spans = output.querySelectorAll('span');
                const minLevel = filter.value;
                spans.forEach(span => {
                    const lv = span.className.replace('log-','');
                    span.style.display =
                        LEVELS.indexOf(lv) >= LEVELS.indexOf(minLevel) ? '' : 'none';
                });
            };
        """),
    )
```

---

## FIX 2 — core/config.py — Remover configs de log obsoletos

### Adicionar à _OBSOLETE_KEYS:

```python
"log_to_file",
"smart_player_log_level",
"smart_player_log_to_file",
```

### Remover de DEFAULTS:

```python
# Remover estas entradas:
("log_to_file",             "true",  "logging"),
("smart_player_log_level",  "INFO",  "logging"),
("smart_player_log_to_file","true",  "logging"),
```

### Manter em DEFAULTS:

```python
("log_level", "INFO", "logging"),  # ← única config de log restante
```

---

## FIX 3 — smart_player.py — Unificar no logger TubeWrangler

### Remover configs de log do smart_player

```python
# REMOVER:
SMART_PLAYER_LOG_LEVEL_STR = _cfg.get_str("smart_player_log_level")
SMART_PLAYER_LOG_TO_FILE = _cfg.get_bool("smart_player_log_to_file")
# e toda lógica de FileHandler do smart_player

# SUBSTITUIR logger por:
logger = logging.getLogger("TubeWrangler.smart_player")
# Não configurar handlers — herda do root configurado pelo web/main.py
# Para execução standalone (CLI), adicionar handler básico se root não tiver:
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
```

---

## Validação

```bash
# 1. Restart
docker compose restart && sleep 8

# 2. /api/logs/stream retorna linhas reais
curl -s --max-time 3 http://localhost:8888/api/logs/stream | head -10
# Esperado: data: 2026-02-27 ... INFO TubeWrangler ...

# 3. /logs carrega no browser
# Abrir http://100.98.81.67:8888/logs
# Esperado: logs coloridos com dropdown DEBUG/INFO/WARNING/ERROR

# 4. Testar captura stderr do player — abrir stream e ver log
curl -s --max-time 5 http://localhost:8888/api/player/2zajmVK9DqU > /dev/null &
sleep 3
curl -s --max-time 2 http://localhost:8888/api/logs/stream | grep -i "zajm\|ffmpeg\|player"

# 5. /api/config não tem log_to_file nem smart_player_log*
curl -s http://localhost:8888/api/config | python3 -m json.tool | grep -E "log_to_file|smart_player_log"
# Esperado: nenhuma saída

# 6. Testes
docker compose exec tubewranglerr python3 -m pytest tests/ -q
```

---

## Notas para o agente

- O _setup_logging() remove handlers existentes antes de adicionar — evitar duplicatas entre
  imports de módulo e lifespan
- O SSE usa snapshot do deque + polling por índice — thread-safe pois deque é append-only
  e a leitura é atômica em Python
- smart_player.py pode ser executado standalone (fora do container web) — o basicConfig
  de fallback garante output no terminal nesse caso
- Remover Path("/data/logs") e Path("/data/tubewrangler.log") de todos os imports/usos
- Se tests/ tiver mock de FileHandler ou LOG_FILE_PATH — atualizar
