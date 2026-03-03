# DECISIONS_CONSOLIDADO.md — TubeWrangler

> Gerado em: 2026-03-03  
> Autor: Análise automatizada Claude Sonnet 4.6 + conferência com repositório real (`main`)  
> Base: DECISIONS.md (Etapas 0–9), decisions.md (bugfixes), REFACTORING_TUBEWRANGLERR_v3.5.1.md  
> Referência de código: `web/main.py`, `core/player_router.py` e todos os módulos `core/`

---

## 1. Visão Geral do Projeto

TubeWrangler é uma aplicação Python containerizada que monitora canais do YouTube,
gera playlists M3U e EPG XMLTV, e serve streaming via ffmpeg/streamlink/yt-dlp.

**Stack atual (confirmada no código):**
- Runtime: Python 3.12 em Docker (`python:3.12-slim`)
- Web: FastHTML + Starlette + Uvicorn
- Config: FastLite (SQLite) via `core/config.py`
- Estado: JSON em disco via `core/state_manager.py`
- Streaming: ffmpeg + streamlink + yt-dlp
- Testes: pytest (31 testes passando)

**Princípio arquitetural central:**
> Container-first. Nenhum Python, ffmpeg, streamlink ou yt-dlp roda no host.
> Todo comando validado via `docker compose exec tubewranglerr`.

---

## 2. Status Real dos Módulos (Conferência 2026-03-03)

### 2.1 Estrutura de diretórios confirmada

```
.
├── Dockerfile
├── Makefile                          # ➕ extra (não documentado)
├── docker-compose.yml
├── docker-compose.override.yml
├── docker-compose.override.dev       # ➕ extra (não documentado)
├── requirements.txt
├── .gitignore
├── core/
│   ├── __init__.py
│   ├── config.py                     # ✅ Etapa 1 — 43 chaves SQLite
│   ├── state_manager.py              # ✅ Etapa 2
│   ├── youtube_api.py                # ✅ Etapa 2
│   ├── playlist_builder.py           # ✅ Etapa 2
│   ├── scheduler.py                  # ✅ Etapa 2
│   ├── player_router.py              # ➕ extra — SmartPlayer Fase 1
│   └── thumbnail_manager.py          # ➕ extra
├── web/
│   ├── __init__.py
│   ├── main.py                       # ✅ ~22KB, 600+ linhas
│   └── routes/
│       └── __init__.py               # ⚠️ VAZIO — tech debt
├── scripts/
│   ├── migrate_env.py                # ✅ Etapa 1
│   ├── check_all.sh                  # ➕ extra
│   ├── clean_cache.py                # ➕ extra
│   ├── gen_m3u.py                    # ➕ extra
│   └── manual_schedule.py            # ➕ extra
└── tests/
    ├── test_config.py                # ✅ Etapa 1
    ├── test_state_manager.py         # ➕ extra
    ├── test_playlist_builder.py      # ➕ extra
    ├── test_scheduler.py             # ➕ extra
    ├── test_scheduler_fixes.py       # ➕ extra (~14KB)
    ├── test_youtube_api.py           # ➕ extra
    └── test_web_routes.py            # ✅ Etapa 3 (minimal, desabilitado)
```

Legenda: ✅ Conforme planejado | ➕ Extra (evoluiu além dos docs) | ⚠️ Gap/Desvio

---

### 2.2 Rotas implementadas em `web/main.py`

| Rota | Método | Tipo | Status |
|---|---|---|---|
| `/` | GET | HTML — Dashboard | ✅ |
| `/config` | GET | HTML — Formulário config | ✅ |
| `/config` | POST | Salva config no SQLite | ✅ |
| `/channels` | GET | HTML — Lista canais | ✅ (era 404 nos docs) |
| `/logs` | GET | HTML — Viewer SSE em tempo real | ✅ (era 404 nos docs) |
| `/force-sync` | GET | Dispara sync manual | ✅ |
| `/playlist/{name}.m3u` | GET | M3U dinâmico (5 tipos) | ✅ |
| `/epg.xml` | GET | XMLTV on-the-fly | ✅ (workaround Starlette) |
| `/api/player/{video_id}` | GET | Stream MPEG-TS | ✅ (Starlette append) |
| `/api/thumbnail/{video_id}` | GET | Serve thumbnail local ou redirect | ✅ |
| `/api/logs/stream` | GET | SSE de logs em tempo real | ✅ |
| `/api/channels` | GET/POST/DELETE | CRUD JSON | ✅ |
| `/api/streams` | GET/detail | Listagem JSON | ✅ |
| `/api/config` | GET/PUT | Config JSON | ✅ |
| `/api/epg` | GET | EPG JSON/XML | ✅ |
| `/api/playlists/refresh` | PUT | Força refresh | ✅ |
| `/playlist_live.m3u8` etc. | GET | Legado → redirect 301 | ✅ |

---

## 3. Decisões Arquiteturais Confirmadas

### [DEC-001] Container-first
**Data:** 2026-02-26  
**Status:** ✅ Ativo  
Nenhum processo roda fora do container. Todo binário (ffmpeg, streamlink, yt-dlp)
validado via `docker compose exec tubewranglerr`.

### [DEC-002] FastHTML + Starlette workaround para extensões
**Data:** 2026-02-26  
**Status:** ✅ Ativo  
FastHTML intercepta URLs com extensões (`.xml`, `.m3u`, `.m3u8`) via catch-all interno.
Solução: registrar essas rotas diretamente no roteador Starlette.

```python
# Exemplo confirmado em web/main.py:

# INSERT no início (garante prioridade sobre catch-all do FastHTML)
app.router.routes.insert(0, Route("/epg.xml", endpoint=_epg_route))

# APPEND no fim (funciona para /api/player/{video_id} com path param)
app.routes.append(Route("/api/player/{video_id}", endpoint=api_player_stream))
```

**Regra:** Qualquer nova rota com extensão de arquivo (`.xml`, `.m3u`, `.m3u8`, `.json`)
ou com parâmetros de path que precisem de StreamingResponse deve usar este padrão.

### [DEC-003] AppConfig via FastLite — zero os.getenv()
**Data:** 2026-02-26  
**Status:** ✅ Ativo  
Toda configuração lida via `AppConfig` (SQLite/FastLite). Nenhum `os.getenv()`,
`load_dotenv()` ou `python-dotenv` em `core/` ou `web/`.

```python
# CORRETO:
from core.config import AppConfig
config = AppConfig()
api_key = config.get_str("youtube_api_key")

# PROIBIDO:
import os
api_key = os.getenv("YOUTUBE_API_KEY")  # ❌ nunca
```

### [DEC-004] Player assíncrono — resolução VOD não bloqueia event loop
**Data:** 2026-02-28  
**Status:** ✅ Ativo  
A resolução de URL VOD via `yt-dlp --get-url` usa `asyncio.create_subprocess_exec`
com `asyncio.wait_for(timeout=20)`. Em falha/timeout, fallback em pipeline bash.

```python
# Padrão correto (confirmado em web/main.py):
async def _resolve_vod_url() -> str:
    proc = await asyncio.create_subprocess_exec(
        "yt-dlp", "-f", "b", "--get-url", ...,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=20)
    ...

# Fallback garantido em MPEG-TS:
fallback_cmd = (
    "set -o pipefail; "
    f"yt-dlp -f b -o - --no-playlist ... | ffmpeg ... -f mpegts pipe:1"
)
cmd = ["bash", "-lc", fallback_cmd]
```

**Regra:** Nenhum subprocess que envolva rede pode ser síncrono (`subprocess.run`)
dentro de handlers de rota FastHTML/Starlette.

### [DEC-005] Logging em buffer circular + SSE
**Data:** 2026-02-28 (inferido)  
**Status:** ✅ Ativo  
Logs são acumulados em `collections.deque(maxlen=1000)` via `_BufferHandler`.
A rota `/api/logs/stream` serve os logs ao vivo via Server-Sent Events.
A rota `/logs` é a UI com filtro por nível e auto-scroll.

```python
# Padrão de adição de log em qualquer módulo:
import logging
logger = logging.getLogger("TubeWrangler.meu_modulo")
logger.info("mensagem")   # aparece em /logs automaticamente
```

### [DEC-006] Proxy auto-detect
**Data:** 2026-02-27  
**Status:** ✅ Ativo  
`_resolve_proxy_base_url()` detecta automaticamente o IP do container.
`PROXY_BASE_URL` mantido vazio para auto-detect no ambiente de produção.

---

## 4. Bugs Corrigidos (Histórico)

| ID | Data | Bug | Arquivo | Resolução |
|---|---|---|---|---|
| BUG-001 | 2026-02-26 | TabError tabs/spaces | web/main.py | Limpeza total, apenas espaços |
| BUG-002 | 2026-02-26 | /youtube_epg.xml → 404 | web/main.py | Workaround Starlette insert(0) |
| BUG-003 | 2026-02-26 | POST /config não persistia | web/main.py | Handler tornado async + await req.form() |
| BUG-004 | 2026-02-26 | Canais não carregados no lifespan | web/main.py | resolve_channel_handles_to_ids() no lifespan |
| BUG-005 | 2026-02-26 | force-sync quebrado | web/main.py + scheduler.py | asyncio.Event + trigger_now() + set_force_event() |
| BUG-006 | 2026-02-26 | Logging ausente | web/main.py | _BufferHandler + _setup_logging() |
| BUG-007 | 2026-02-28 | Resolução VOD bloqueava event loop | web/main.py | asyncio.create_subprocess_exec + wait_for(20s) |

---

## 5. Problemas Identificados — A Corrigir

### [FIX-001] 🔴 Duplicidade de lógica VOD entre `player_router.py` e `web/main.py`

**Prioridade:** Alta  
**Impacto:** Manutenibilidade, risco de divergência de comportamento  

**Problema:**  
`core/player_router.py` contém `_resolve_ytdlp_url()` e `build_ytdlp_ffmpeg_cmd()` para o caso `status == "none"`, mas `web/main.py` intercepta esse caso **antes** de chamar `build_player_command()` com sua própria lógica assíncrona. O código do `player_router.py` para `status == "none"` nunca é executado no fluxo normal.

```python
# web/main.py — o if intercepta "none" antes de build_player_command:
if status == "none":
    cdn_url = await _resolve_vod_url()  # lógica própria do main.py
    ...
else:
    cmd, temp_files = build_player_command(...)  # "none" nunca chega aqui
```

```python
# player_router.py — _resolve_ytdlp_url() é síncrono e nunca chamado:
def _resolve_ytdlp_url(watch_url, user_agent) -> str:
    result = subprocess.run(["yt-dlp", ..., watch_url], ...)  # ⚠️ síncrono!
    ...
```

**Solução planejada:**  
Mover toda a lógica VOD (incluindo a assíncrona) para `player_router.py` como função async,
e `web/main.py` apenas chama essa função. Unificar os dois caminhos.

```python
# Proposta: player_router.py
async def resolve_vod_url_async(watch_url: str, user_agent: str, timeout: int = 20) -> str:
    """Resolve URL CDN real via yt-dlp --get-url de forma assíncrona."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "-f", "b", "--get-url", "--no-playlist",
            "--user-agent", user_agent, watch_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        text = out.decode("utf-8", errors="replace").strip()
        return text.splitlines()[0] if text else ""
    except asyncio.TimeoutError:
        return ""
    except Exception:
        return ""

# web/main.py — simplificado:
if status == "none":
    cdn_url = await player_router.resolve_vod_url_async(watch_url, user_agent)
    cmd = build_vod_cmd(cdn_url, watch_url, user_agent)  # também no player_router
```

**Checklist FIX-001:**
- [ ] Criar `resolve_vod_url_async()` em `core/player_router.py`
- [ ] Criar `build_vod_cmd()` em `core/player_router.py`
- [ ] Remover `_resolve_vod_url()` de `web/main.py`
- [ ] Remover `_resolve_ytdlp_url()` síncrono de `core/player_router.py`
- [ ] Atualizar `build_player_command()` para aceitar status `"none"` de forma assíncrona (ou tornar `build_player_command` async)
- [ ] Rodar `pytest -q` → deve continuar 31 passed
- [ ] Testar manualmente `/api/player/{video_id}` com status `none`, `live` e `upcoming`

---

### [FIX-002] 🟡 `web/routes/` vazio — tech debt arquitetural

**Prioridade:** Média  
**Impacto:** Manutenibilidade a longo prazo (`web/main.py` com 600+ linhas)

**Problema:**  
`web/routes/` existe mas só tem `__init__.py`. Todas as 25+ rotas estão em `web/main.py`.
O arquivo já tem 22KB (~600 linhas) e tende a crescer.

**Solução planejada — separação por domínio:**

```
web/routes/
├── __init__.py         # exporta todos os routers
├── playlists.py        # /playlist/{name}.m3u, /epg.xml, /api/epg, /api/playlists/refresh
├── player.py           # /api/player/{video_id}, /api/thumbnail/{video_id}
├── channels.py         # /channels, /api/channels
├── config.py           # /config (GET+POST), /api/config (GET+PUT)
└── logs.py             # /logs, /api/logs/stream
```

**Padrão para cada arquivo de rota:**

```python
# web/routes/channels.py
import logging
from starlette.responses import JSONResponse
from fasthtml.common import *

logger = logging.getLogger("TubeWrangler.routes.channels")

def register(app, get_state, get_config):
    """Registra rotas de canais no app FastHTML.

    Args:
        app: instância FastHTML
        get_state: callable que retorna StateManager atual
        get_config: callable que retorna AppConfig atual
    """

    @app.get("/channels")
    def channels_page():
        state = get_state()
        channels = state.get_all_channels() if state else {}
        return Titled(
            "Canais",
            Ul(*[Li(f"{cid}: {title}") for cid, title in channels.items()])
            if channels else P("Nenhum canal."),
        )

    @app.get("/api/channels")
    def api_channels_list():
        state = get_state()
        return JSONResponse(
            [{"id": k, "title": v} for k, v in state.get_all_channels().items()]
        )

    # ... demais rotas de canais
```

**`web/routes/__init__.py`:**

```python
from .playlists import register as register_playlists
from .player    import register as register_player
from .channels  import register as register_channels
from .config    import register as register_config
from .logs      import register as register_logs

__all__ = [
    "register_playlists",
    "register_player",
    "register_channels",
    "register_config",
    "register_logs",
]
```

**`web/main.py` simplificado após refatoração:**

```python
from web.routes import (
    register_playlists, register_player,
    register_channels, register_config, register_logs
)

# No final de main.py, após app, rt = fast_app(...):
register_playlists(app, lambda: _state, lambda: _config, lambda: _categories_db)
register_player(app, lambda: _state, lambda: _config, lambda: _thumbnail_manager)
register_channels(app, lambda: _state, lambda: _config)
register_config(app, lambda: _config)
register_logs(app)
```

**Checklist FIX-002:**
- [ ] Criar `web/routes/playlists.py`
- [ ] Criar `web/routes/player.py`
- [ ] Criar `web/routes/channels.py`
- [ ] Criar `web/routes/config.py`
- [ ] Criar `web/routes/logs.py`
- [ ] Atualizar `web/routes/__init__.py`
- [ ] Refatorar `web/main.py` para usar `register_*()`
- [ ] Manter globals `_state`, `_config` etc. em `web/main.py` (passados via lambda)
- [ ] Rodar `pytest -q` → deve continuar 31 passed
- [ ] Testar todas as rotas com curl após refatoração

---

### [FIX-003] 🟡 `_resolve_ytdlp_url()` síncrono em `player_router.py`

**Prioridade:** Média (bloqueado por FIX-001)  
**Impacto:** Se `build_ytdlp_ffmpeg_cmd()` for chamado fora do contexto de `web/main.py`
(ex: script, teste), ele executa `subprocess.run` síncrono com timeout=20s, travando a thread.

**Nota:** Este fix é parte do FIX-001. Registrado separadamente para rastreabilidade.

**Checklist FIX-003:** (depende de FIX-001)
- [ ] Confirmar que `_resolve_ytdlp_url()` não é chamado por nenhum outro módulo
- [ ] Remover após FIX-001 estar completo

---

### [FIX-004] 🟢 Adicionar testes para `player_router.py`

**Prioridade:** Baixa  
**Impacto:** Cobertura de testes

`core/player_router.py` não tem arquivo de teste correspondente em `tests/`.
As funções são puras (recebem parâmetros, retornam listas de comandos) — ideais para testes unitários.

```python
# tests/test_player_router.py — exemplo
import pytest
from pathlib import Path
from core.player_router import (
    build_streamlink_cmd,
    build_ffmpeg_placeholder_cmd,
    _escape_ffmpeg_text,
)

def test_streamlink_cmd_contém_best():
    cmd = build_streamlink_cmd("https://youtube.com/watch?v=abc", "Mozilla/5.0")
    assert "best" in cmd
    assert "--stdout" in cmd

def test_placeholder_cmd_sem_fonte_valida():
    """Sem fontfile válido, não deve ter drawtext no filtro."""
    cmd, temp_files = build_ffmpeg_placeholder_cmd(
        image_url="https://example.com/thumb.jpg",
        text_line1="Linha 1",
        font_path="/nao/existe.ttf",  # fonte inválida
    )
    assert "ffmpeg" in cmd
    assert temp_files == []

def test_escape_ffmpeg_text_caracteres_especiais():
    resultado = _escape_ffmpeg_text("título: 50% off, vale 'isso'")
    assert ":" not in resultado.split("escape", 1)[-1]  # : foi escapado
    assert "'" not in resultado  # ' foi escapado
    assert "%" not in resultado  # % foi escapado
```

**Checklist FIX-004:**
- [ ] Criar `tests/test_player_router.py`
- [ ] Cobrir `build_streamlink_cmd`, `build_ffmpeg_placeholder_cmd`, `_escape_ffmpeg_text`
- [ ] Cobrir `build_player_command` com status `live`, `upcoming`, `none`
- [ ] Rodar `pytest -q` → deve ser 31+ passed

---

### [FIX-005] 🟢 Adicionar `test_thumbnail_manager.py`

**Prioridade:** Baixa  
`core/thumbnail_manager.py` também não tem testes. Seguir mesmo padrão de FIX-004.

---

## 6. Plano de Execução

### Ordem recomendada

```
FIX-001 (duplicidade VOD)      — faz o código correto e seguro
  └── FIX-003 (limpa síncron) — consequência natural do FIX-001
FIX-004 (testes player_router) — pode ser feito em paralelo ou após FIX-001
FIX-005 (testes thumbnail)     — pode ser feito a qualquer momento
FIX-002 (routes/ separado)     — maior esforço, fazer por último
```

### Protocolo de validação por fix

Antes de qualquer fix:
```bash
docker compose run --rm tubewranglerr pytest -q
# Esperado: 31 passed
```

Após cada fix:
```bash
# 1. Testes
docker compose run --rm tubewranglerr pytest -q

# 2. Zero dotenv/flask/os.getenv
docker compose exec tubewranglerr sh -c \
  "grep -Rn 'load_dotenv\|from flask\|import flask\|os\.getenv' core/ web/ || echo 'OK'"

# 3. Rotas principais
curl -si http://localhost:8888/ | head -1
curl -si http://localhost:8888/config | head -1
curl -si http://localhost:8888/playlist/live.m3u | head -1
curl -si http://localhost:8888/epg.xml | head -1

# 4. Player (requer stream ativo ou status none)
curl -si "http://localhost:8888/api/player/VIDEO_ID_AQUI" | head -5
```

---

## 7. Checklist Geral do Projeto

| Item | Status | Fix |
|---|---|---|
| Container Docker funcionando | ✅ | — |
| `core/config.py` — 43 chaves, zero dotenv | ✅ | — |
| `core/state_manager.py` — sem Flask/dotenv | ✅ | — |
| `core/youtube_api.py` — sem Flask/dotenv | ✅ | — |
| `core/playlist_builder.py` — sem Flask/dotenv | ✅ | — |
| `core/scheduler.py` — trigger_now + set_force_event | ✅ | — |
| `core/player_router.py` — lógica VOD duplicada | ⚠️ | FIX-001 |
| `core/player_router.py` — sem testes | ⚠️ | FIX-004 |
| `core/thumbnail_manager.py` — sem testes | ⚠️ | FIX-005 |
| `web/main.py` — lifespan correto | ✅ | — |
| `web/main.py` — logging em memória + SSE | ✅ | — |
| `web/main.py` — player assíncrono + fallback | ✅ | — |
| `web/main.py` — lógica VOD duplicada | ⚠️ | FIX-001 |
| `web/routes/` — vazio, tech debt | ⚠️ | FIX-002 |
| POST /config — async + await req.form() | ✅ | BUG-003 ✓ |
| /channels e /logs implementados | ✅ | — |
| pytest 31 testes passando | ✅ | — |
| Scripts operacionais (migrate, check, gen_m3u) | ✅ | — |

---

## 8. Convenções do Projeto

### Nomenclatura de arquivos
- Módulos core: `core/nome_do_modulo.py` (snake_case)
- Rotas: `web/routes/nome_do_dominio.py`
- Testes: `tests/test_nome_do_modulo.py` (espelho do módulo)
- Scripts operacionais: `scripts/verbo_objeto.py` ou `.sh`

### Logger por módulo
```python
# Sempre no topo de cada módulo, após imports:
logger = logging.getLogger("TubeWrangler.nome_do_modulo")
# Exemplos: TubeWrangler.scheduler, TubeWrangler.player, TubeWrangler.routes.channels
```

### Subprocess assíncrono (padrão obrigatório em handlers)
```python
# ✅ CORRETO — dentro de handler async:
proc = await asyncio.create_subprocess_exec(
    "comando", "arg1",
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
)
out, err = await asyncio.wait_for(proc.communicate(), timeout=20)

# ❌ PROIBIDO em handlers/rotas:
result = subprocess.run(["comando", "arg1"], ...)  # bloqueia o event loop
```

### Acesso ao estado global em rotas (após FIX-002)
```python
# ✅ Via callable injetado (não importar globals diretamente):
def register(app, get_state, get_config):
    @app.get("/minha-rota")
    def minha_rota():
        state = get_state()    # acessa _state de main.py
        config = get_config()  # acessa _config de main.py
```

---

*Próxima revisão deste documento: após conclusão de FIX-001 e FIX-004.*
