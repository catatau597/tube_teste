# REFACTORING_TUBEWRANGLERR.md

> **Versão:** 1.0  
> **Projeto:** TubeWranglerr  
> **Destino:** Agente autônomo GitHub Copilot  
> **Objetivo:** Refatoração completa para stack FastHTML + SQLite em container standalone  

---

## ⚠️ LEIA ANTES DE QUALQUER AÇÃO

Este documento é a **única fonte de verdade** para o agente. Toda decisão deve ser tomada com base nele. Se houver ambiguidade, o agente deve **parar e registrar a dúvida** no `DECISIONS.md` antes de prosseguir.

---

## 📋 ÍNDICE

1. [Visão Geral e Contexto](#1-visão-geral-e-contexto)
2. [Regras Absolutas do Agente](#2-regras-absolutas-do-agente)
3. [Estrutura Final do Projeto](#3-estrutura-final-do-projeto)
4. [Etapa 1 — core/config.py (SQLite)](#4-etapa-1--coreconfigpy-sqlite)
5. [Etapa 2 — Separação de Módulos](#5-etapa-2--separação-de-módulos)
6. [Etapa 3 — Interface FastHTML](#6-etapa-3--interface-fasthtml)
7. [Etapa 4 — Docker e Container](#7-etapa-4--docker-e-container)
8. [Etapa 5 — smart_player.py](#8-etapa-5--smart_playerpy)
9. [Testes entre Etapas](#9-testes-entre-etapas)
10. [Revisão Final de Migração](#10-revisão-final-de-migração)
11. [Protocolo DECISIONS.md](#11-protocolo-decisionsmd)

---

## 1. Visão Geral e Contexto

### O que é o TubeWranglerr

Sistema Python que monitora canais do YouTube, detecta lives/streams agendados e gera playlists M3U + EPG XMLTV para consumo por players IPTV (ex: Jellyfin, Kodi, TiviMate).

### Arquivos originais (ponto de partida)

| Arquivo | Responsabilidade atual | Destino |
|---|---|---|
| `get_streams.py` | Tudo misturado: config, scheduler, API YouTube, geração de playlist, servidor Flask | Será desmembrado em `core/` |
| `smart_player.py` | Roteador de streams: chama ffmpeg/streamlink/yt-dlp conforme status | Migrar config de `.env` para SQLite |
| `file.env` | 30+ variáveis de configuração | Substituído por `core/config.py` + SQLite |

### Stack de destino

- **Backend/Core:** Python 3.12, asyncio nativo
- **Web:** FastHTML + FastLite (SQLite nativo)  
- **Config:** SQLite via FastLite (substitui `.env`)
- **Container:** Docker single-process, volume `/data`
- **Dependências a remover:** `Flask`, `python-dotenv`
- **Dependências a adicionar:** `python-fasthtml`

---

## 2. Regras Absolutas do Agente

### 🚫 PROIBIÇÕES — nunca faça sem aprovação explícita

```
PROIBIDO: Apagar qualquer arquivo original (.py, .env) antes da Etapa 10 (revisão final)
PROIBIDO: Usar os.getenv() ou load_dotenv() em qualquer arquivo novo
PROIBIDO: Importar Flask em qualquer arquivo novo
PROIBIDO: Criar variáveis globais de configuração (ex: API_KEY = os.getenv(...))
PROIBIDO: Usar threading.Thread para o servidor web (FastHTML usa uvicorn/asyncio)
PROIBIDO: Misturar lógica de negócio dentro de rotas FastHTML
PROIBIDO: Usar requests (síncrono) para chamadas à API YouTube — manter google-api-python-client
PROIBIDO: Alterar a lógica de geração de M3U/XMLTV sem sinalizar em DECISIONS.md
PROIBIDO: Usar pickle ou shelve para persistência
PROIBIDO: Fazer commit sem que os testes da etapa correspondente passem
```

### ✅ OBRIGAÇÕES — sempre faça

```
OBRIGATÓRIO: Registrar toda decisão de design em DECISIONS.md antes de implementar
OBRIGATÓRIO: Cada módulo novo deve ter seu bloco de teste correspondente (ver Seção 9)
OBRIGATÓRIO: AppConfig deve ser passado como parâmetro — nunca importado como singleton global
OBRIGATÓRIO: Manter backward compatibility do state_cache.json durante a migração
OBRIGATÓRIO: Todo arquivo novo começa com docstring explicando sua responsabilidade
OBRIGATÓRIO: Usar type hints em todas as funções públicas
OBRIGATÓRIO: Rodar os testes de cada etapa antes de avançar para a próxima
OBRIGATÓRIO: Ao final de cada etapa, atualizar o checklist em DECISIONS.md
```

### 📐 Convenções de código

```python
# CORRETO — config como parâmetro injetado
class Scheduler:
    def __init__(self, config: AppConfig, scraper: YouTubeAPI, state: StateManager):
        self.config = config

# ERRADO — config como global
SCHEDULER_INTERVAL = int(os.getenv("SCHEDULER_MAIN_INTERVAL_HOURS", 4))
class Scheduler:
    def run(self):
        time.sleep(SCHEDULER_INTERVAL * 3600)  # ← PROIBIDO
```

```python
# CORRETO — módulo com docstring
"""
core/scheduler.py
-----------------
Responsabilidade: Loop assíncrono de agendamento de buscas.
Depende de: AppConfig, YouTubeAPI, StateManager
NÃO depende de: Flask, FastHTML, os.getenv
"""

# ERRADO — sem docstring
import asyncio
INTERVAL = 4
```

---

## 3. Estrutura Final do Projeto

O agente DEVE criar exatamente esta estrutura. Nenhum arquivo fora dela deve ser criado sem registro em `DECISIONS.md`.

```
tubewranglerr/
│
├── core/                          # Lógica de negócio — zero dependência web
│   ├── __init__.py
│   ├── config.py                  # AppConfig + SQLite (substitui .env)
│   ├── state_manager.py           # StateManager (extraído de get_streams.py)
│   ├── youtube_api.py             # APIScraper (extraído de get_streams.py)
│   ├── playlist_builder.py        # M3UGenerator + XMLTVGenerator + ContentGenerator
│   └── scheduler.py               # Scheduler + saveloop (extraído de get_streams.py)
│
├── web/                           # Interface FastHTML — zero lógica de negócio
│   ├── __init__.py
│   ├── main.py                    # Entry point: FastHTML app + lifespan
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── dashboard.py           # GET / — status geral, streams ativos
│   │   ├── config.py              # GET/POST /config — formulário de configuração
│   │   ├── channels.py            # GET/POST /channels — gerenciar handles/IDs
│   │   ├── logs.py                # GET /logs — visualização de logs (SSE)
│   │   └── playlists.py           # GET /playlist/*.m3u8, /epg/*.xml
│   └── components/
│       ├── __init__.py
│       ├── layout.py              # Header, nav, shell HTML
│       └── forms.py               # Componentes de formulário reutilizáveis
│
├── data/                          # Volume Docker — NUNCA versionar
│   ├── config.db                  # SQLite principal (config + state)
│   ├── m3us/                      # Playlists M3U geradas
│   ├── epgs/                      # EPG XML gerado
│   └── logs/                      # Arquivos de log
│
├── tests/
│   ├── test_config.py
│   ├── test_state_manager.py
│   ├── test_youtube_api.py
│   ├── test_playlist_builder.py
│   ├── test_scheduler.py
│   └── test_web_routes.py
│
├── smart_player.py                # Mantido na raiz (invocado externamente)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .github/
│   └── copilot-instructions.md   # Instruções globais para Copilot
├── DECISIONS.md                   # Gerado/atualizado pelo agente
└── REFACTORING_TUBEWRANGLERR.md  # Este arquivo
```

### `.gitignore` obrigatório
```
data/
*.db
*.log
.env
__pycache__/
*.pyc
.venv/
```

---

## 4. Etapa 1 — core/config.py (SQLite)

**Esta é a etapa mais crítica. Nada mais deve ser tocado antes dela estar completa e testada.**

### 4.1 Objetivo

Substituir **todos** os `os.getenv()` do projeto por uma classe `AppConfig` que lê e persiste no SQLite. A configuração deve ser **recarregável em runtime** sem reiniciar o processo.

### 4.2 Schema SQLite

O agente deve criar a tabela `config` com o seguinte schema exato via FastLite:

```python
# core/config.py
"""
core/config.py
--------------
Responsabilidade: Única fonte de verdade para configurações da aplicação.
Substitui completamente o arquivo .env e os.getenv() em todo o projeto.
Depende de: fastlite (SQLite)
NÃO depende de: Flask, FastHTML (apenas fastlite), os.getenv
"""

from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from fastlite import database

DB_PATH = Path("/data/config.db")

@dataclass
class ConfigRow:
    key: str
    value: str
    section: str
    description: str
    value_type: str  # "str" | "int" | "bool" | "list" | "mapping"

DEFAULTS: dict = {
    # --- Seção 1: Credenciais ---
    "youtube_api_key":              ("", "credentials", "Chave de API do YouTube", "str"),
    "target_channel_handles":       ("", "credentials", "Handles dos canais separados por vírgula", "list"),
    "target_channel_ids":           ("", "credentials", "IDs diretos de canais separados por vírgula", "list"),

    # --- Seção 2: Agendador ---
    "scheduler_main_interval_hours":        ("4",  "scheduler", "Intervalo principal em horas", "int"),
    "scheduler_pre_event_window_hours":     ("2",  "scheduler", "Janela pré-evento em horas", "int"),
    "scheduler_pre_event_interval_minutes": ("5",  "scheduler", "Intervalo pré-evento em minutos", "int"),
    "scheduler_post_event_interval_minutes":("5",  "scheduler", "Intervalo pós-evento em minutos", "int"),
    "enable_scheduler_active_hours":        ("true","scheduler", "Ativar horário de atividade", "bool"),
    "scheduler_active_start_hour":          ("7",  "scheduler", "Hora de início do horário ativo", "int"),
    "scheduler_active_end_hour":            ("22", "scheduler", "Hora de fim do horário ativo", "int"),
    "full_sync_interval_hours":             ("48", "scheduler", "Intervalo de full sync em horas", "int"),
    "resolve_handles_ttl_hours":            ("24", "scheduler", "TTL do cache de handles em horas", "int"),
    "initial_sync_days":                    ("2",  "scheduler", "Dias para busca inicial (0=tudo)", "int"),

    # --- Seção 3: Filtros ---
    "max_schedule_hours":           ("72", "filters", "Limite futuro em horas para agendamentos", "int"),
    "max_upcoming_per_channel":     ("6",  "filters", "Máximo de agendamentos por canal", "int"),
    "title_filter_expressions":     ("ao vivo,AO VIVO,cortes,react,JOGO COMPLETO", "filters", "Expressões a remover dos títulos", "list"),
    "prefix_title_with_channel_name":("true","filters","Prefixar título com nome do canal", "bool"),
    "prefix_title_with_status":     ("true","filters","Prefixar título com status", "bool"),
    "category_mappings":            ("Sports|ESPORTES,Gaming|JOGOS,News & Politics|NOTICIAS", "filters", "Mapeamento de categorias (API|Display)", "mapping"),
    "channel_name_mappings":        ("Canal GOAT|GOAT,TNT Sports Brasil|TNT Sports", "filters", "Mapeamento de nomes de canais (Longo|Curto)", "mapping"),
    "epg_description_cleanup":      ("true","filters","Limpar descrição EPG (primeiro parágrafo)", "bool"),
    "filter_by_category":           ("true","filters","Filtrar por categoria da API", "bool"),
    "allowed_category_ids":         ("17",  "filters","IDs de categoria permitidos", "list"),
    "keep_recorded_streams":        ("true","filters","Manter streams gravados", "bool"),
    "max_recorded_per_channel":     ("2",   "filters","Máximo de gravações por canal", "int"),
    "recorded_retention_days":      ("2",   "filters","Dias de retenção de gravações", "int"),

    # --- Seção 4: Saída ---
    "playlist_save_directory":      ("/data/m3us",        "output", "Diretório de playlists M3U", "str"),
    "playlist_live_filename":       ("playlist_live.m3u8","output", "Nome do arquivo live M3U", "str"),
    "playlist_upcoming_filename":   ("playlist_upcoming.m3u8","output","Nome do arquivo upcoming M3U","str"),
    "playlist_vod_filename":        ("playlist_vod.m3u8", "output", "Nome do arquivo VOD M3U", "str"),
    "xmltv_save_directory":         ("/data/epgs",        "output", "Diretório do EPG XML", "str"),
    "xmltv_filename":               ("youtube_epg.xml",   "output", "Nome do arquivo EPG XML", "str"),
    "placeholder_image_url":        ("",                  "output", "URL da imagem placeholder", "str"),
    "use_invisible_placeholder":    ("true",              "output", "Usar placeholder invisível no M3U", "bool"),

    # --- Seção 5: Técnico ---
    "http_port":                    ("8888",             "technical", "Porta HTTP do servidor", "int"),
    "state_cache_filename":         ("state_cache.json", "technical", "Nome do arquivo de cache de estado", "str"),
    "stale_hours":                  ("6",                "technical", "Horas para considerar stream stale", "int"),
    "use_playlist_items":           ("true",             "technical", "Usar playlistItems API", "bool"),
    "local_timezone":               ("America/Sao_Paulo","technical", "Fuso horário local", "str"),

    # --- Seção 6: Logs ---
    "log_level":                    ("INFO",  "logging", "Nível de log do get_streams", "str"),
    "log_to_file":                  ("true",  "logging", "Salvar log em arquivo", "bool"),
    "smart_player_log_level":       ("INFO",  "logging", "Nível de log do smart_player", "str"),
    "smart_player_log_to_file":     ("true",  "logging", "Salvar log do smart_player em arquivo", "bool"),
}


class AppConfig:
    """
    Classe de configuração da aplicação.
    Lê e persiste no SQLite. Recarregável em runtime via reload().
    NUNCA use os.getenv() fora desta classe.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self._db = database(db_path)
        self._table = self._db.t.config
        self._ensure_table()
        self._cache: dict = {}
        self.reload()

    def _ensure_table(self):
        if self._table not in self._db.t:
            self._table.create(
                key=str, value=str, section=str,
                description=str, value_type=str, pk="key"
            )
        # Insere defaults apenas para chaves que ainda não existem
        existing = {row.key for row in self._table.rows}
        for key, (default_val, section, desc, vtype) in DEFAULTS.items():
            if key not in existing:
                self._table.insert(ConfigRow(
                    key=key, value=default_val,
                    section=section, description=desc, value_type=vtype
                ))

    def reload(self):
        """Recarrega todas as configurações do banco. Chame após salvar via web."""
        self._cache = {row.key: row for row in self._table.rows}

    def get_raw(self, key: str) -> str:
        return self._cache[key].value if key in self._cache else DEFAULTS.get(key, ("",))[0]

    def get_str(self, key: str) -> str:
        return self.get_raw(key)

    def get_int(self, key: str) -> int:
        return int(self.get_raw(key))

    def get_bool(self, key: str) -> bool:
        return self.get_raw(key).lower() == "true"

    def get_list(self, key: str) -> list:
        raw = self.get_raw(key)
        return [x.strip() for x in raw.split(",") if x.strip()]

    def get_mapping(self, key: str) -> dict:
        raw = self.get_raw(key)
        result = {}
        for item in raw.split(","):
            if "|" in item:
                k, v = item.rsplit("|", 1)
                result[k.strip()] = v.strip()
        return result

    def update(self, key: str, value: str):
        """Atualiza uma chave no banco E no cache."""
        if key in self._cache:
            self._table.update({"key": key, "value": value})
            self._cache[key].value = value
        else:
            raise KeyError(f"Chave de configuração desconhecida: {key}")

    def update_many(self, updates: dict):
        """Atualiza múltiplas chaves atomicamente."""
        for key, value in updates.items():
            self.update(key, str(value))

    def get_all_by_section(self) -> dict:
        """Retorna configurações agrupadas por seção para o formulário web."""
        sections = {}
        for row in self._cache.values():
            sections.setdefault(row.section, []).append(row)
        return sections

    def import_from_env_file(self, env_path: Path):
        """
        Utilitário de migração única: importa valores de um .env existente.
        Mapeia nomes UPPER_SNAKE para lower_snake automaticamente.
        Usado apenas durante o processo de migração inicial.
        """
        if not env_path.exists():
            return
        mapping = {k.upper(): k for k in DEFAULTS.keys()}
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, _, v = line.partition("=")
                    k = k.strip().strip('"')
                    v = v.strip().strip('"')
                    lower_key = mapping.get(k.upper())
                    if lower_key:
                        try:
                            self.update(lower_key, v)
                        except KeyError:
                            pass
```

### 4.3 Utilitário de migração inicial

O agente deve criar um script `scripts/migrate_env.py` que importa o `.env` existente para o SQLite:

```python
# scripts/migrate_env.py
"""
Script de migração única: importa file.env → SQLite.
Execute uma vez após criar o banco: python scripts/migrate_env.py
"""
from pathlib import Path
from core.config import AppConfig

if __name__ == "__main__":
    cfg = AppConfig()
    cfg.import_from_env_file(Path("file.env"))
    print("✅ Migração do .env para SQLite concluída.")
    for section, rows in cfg.get_all_by_section().items():
        print(f"\n[{section}]")
        for row in rows:
            print(f"  {row.key} = {row.value}")
```

### 4.4 Checklist Etapa 1

Antes de avançar para Etapa 2, todos os itens abaixo devem ser `[x]`:

```
[ ] core/__init__.py criado
[ ] core/config.py criado com AppConfig completo
[ ] Todos os 30+ campos do DEFAULTS mapeados (conferir com file.env)
[ ] scripts/migrate_env.py criado e funcional
[ ] python scripts/migrate_env.py executa sem erro
[ ] tests/test_config.py passa (ver Seção 9.1)
[ ] Nenhum os.getenv() em core/config.py
[ ] DECISIONS.md atualizado com decisões desta etapa
```

---

## 5. Etapa 2 — Separação de Módulos

**Pré-requisito:** Etapa 1 completa e testada.

A separação é essencialmente um **recorte estruturado** do `get_streams.py`. As classes já existem — o trabalho é movê-las para módulos independentes e substituir os globals de configuração por `AppConfig`.

### 5.1 core/state_manager.py

Extrair a classe `StateManager` de `get_streams.py`.

**Regras específicas:**
- Remover toda referência a `STALE_HOURS`, `KEEP_RECORDED_STREAMS`, `MAX_RECORDED_PER_CHANNEL`, `RECORDED_RETENTION_DAYS` como globais
- Receber `config: AppConfig` no `__init__`
- O arquivo `state_cache.json` deve usar o path de `config.get_str("state_cache_filename")`

```python
# core/state_manager.py
"""
core/state_manager.py
---------------------
Responsabilidade: Gerenciamento do estado em memória de streams e canais.
Persiste em state_cache.json (JSON) para compatibilidade com smart_player.py.
Depende de: AppConfig
NÃO depende de: Flask, FastHTML, YouTube API
"""
from pathlib import Path
from core.config import AppConfig
# ... resto da classe StateManager sem alteração de lógica
```

### 5.2 core/youtube_api.py

Extrair a classe `APIScraper` de `get_streams.py`.

**Regras específicas:**
- `RESOLVE_HANDLES_TTL_HOURS` e `USE_PLAYLIST_ITEMS` devem vir de `config`
- O `__init__` recebe `api_key: str` (não lê do config — o chamador passa)
- Manter 100% da lógica de paginação e batch

```python
# core/youtube_api.py
"""
core/youtube_api.py
-------------------
Responsabilidade: Comunicação com a API do YouTube v3.
Métodos: resolve_handles, fetch_streams_by_ids, fetch_all_streams_for_channels.
Depende de: google-api-python-client, AppConfig
NÃO depende de: Flask, FastHTML, StateManager
"""
from core.config import AppConfig
from googleapiclient.discovery import build
# ... APIScraper com config injetado
```

### 5.3 core/playlist_builder.py

Extrair as classes `ContentGenerator`, `M3UGenerator`, `XMLTVGenerator`.

**Regras específicas:**
- `TITLE_FILTER_EXPRESSIONS`, `CATEGORY_MAPPINGS`, `CHANNEL_NAME_MAPPINGS` etc. devem vir de `config`
- Métodos de geração recebem `config: AppConfig` como parâmetro (não como global)
- Manter 100% da lógica de formatação, limpeza e filtragem

```python
# core/playlist_builder.py
"""
core/playlist_builder.py
------------------------
Responsabilidade: Geração de playlists M3U8 e EPG XMLTV a partir do estado.
Classes: ContentGenerator, M3UGenerator, XMLTVGenerator.
Depende de: AppConfig, StateManager (recebe dados como parâmetro)
NÃO depende de: Flask, FastHTML, YouTube API
"""
```

### 5.4 core/scheduler.py

Extrair a classe `Scheduler` + função `save_loop` de `get_streams.py`.

**Regras específicas:**
- O scheduler deve expor método `reload_config(new_config: AppConfig)` para atualização sem restart
- A função `save_loop` vira método `async def _save_loop(self)` da classe
- Remover toda referência ao servidor Flask
- O `asyncio.run()` principal sai daqui e vai para `web/main.py`

```python
# core/scheduler.py
"""
core/scheduler.py
-----------------
Responsabilidade: Loop assíncrono de agendamento de buscas à API YouTube.
Gerencia: busca principal, pré-evento, pós-evento, full sync, save loop.
Depende de: AppConfig, YouTubeAPI, StateManager
NÃO depende de: Flask, FastHTML
"""
import asyncio
from core.config import AppConfig
from core.youtube_api import YouTubeAPI
from core.state_manager import StateManager

class Scheduler:
    def __init__(self, config: AppConfig, scraper: YouTubeAPI, state: StateManager):
        self.config = config
        self.scraper = scraper
        self.state = state
        self._task: asyncio.Task | None = None

    def reload_config(self, new_config: AppConfig):
        """Atualiza config em runtime sem parar o loop."""
        self.config = new_config

    async def run(self, initial_run_delay: bool = False):
        """Loop principal assíncrono. Chamado via asyncio.create_task() no lifespan."""
        # ... lógica extraída de get_streams.py sem alteração
```

### 5.5 Checklist Etapa 2

```
[ ] core/state_manager.py criado — zero os.getenv()
[ ] core/youtube_api.py criado — zero os.getenv()
[ ] core/playlist_builder.py criado — zero os.getenv()
[ ] core/scheduler.py criado — zero os.getenv(), zero Flask
[ ] Importar cada módulo individualmente no Python REPL sem erro
[ ] get_streams.py original NÃO foi apagado
[ ] file.env original NÃO foi apagado
[ ] tests/test_state_manager.py passa
[ ] tests/test_youtube_api.py passa (mocks)
[ ] tests/test_playlist_builder.py passa
[ ] tests/test_scheduler.py passa (mocks asyncio)
[ ] DECISIONS.md atualizado
```

---

## 6. Etapa 3 — Interface FastHTML

**Pré-requisito:** Etapa 2 completa e testada.

### 6.1 web/main.py — Entry point e lifespan

```python
# web/main.py
"""
web/main.py
-----------
Responsabilidade: Entry point da aplicação. Integra FastHTML com o core.
Gerencia: lifespan (startup/shutdown), instâncias singleton do core,
          rotas de playlists/EPG (servidas diretamente, sem UI).
"""
from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
from fasthtml.common import *
from core.config import AppConfig
from core.state_manager import StateManager
from core.youtube_api import YouTubeAPI
from core.scheduler import Scheduler
from core.playlist_builder import M3UGenerator, XMLTVGenerator
from web.routes import dashboard, config_routes, channels, logs, playlists

# Instâncias globais do core (criadas no lifespan)
_app_config: AppConfig | None = None
_state: StateManager | None = None
_scheduler: Scheduler | None = None
_m3u_gen: M3UGenerator | None = None
_xmltv_gen: XMLTVGenerator | None = None

@asynccontextmanager
async def lifespan(app):
    global _app_config, _state, _scheduler, _m3u_gen, _xmltv_gen

    _app_config = AppConfig()
    _state = StateManager(_app_config)
    _state.load_from_disk()

    scraper = YouTubeAPI(_app_config.get_str("youtube_api_key"))
    _scheduler = Scheduler(_app_config, scraper, _state)
    _m3u_gen = M3UGenerator()
    _xmltv_gen = XMLTVGenerator()

    scheduler_task = asyncio.create_task(_scheduler.run())
    yield
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass
    _state.save_to_disk()

app, rt = fast_app(lifespan=lifespan, hdrs=[
    Link(rel="stylesheet", href="https://cdn.jsdelivr.net/npm/pico.css@2/css/pico.min.css")
])

# Registrar rotas de cada módulo
# (ver web/routes/*.py)
```

### 6.2 Páginas obrigatórias da interface

| Rota | Arquivo | Conteúdo |
|---|---|---|
| `GET /` | `routes/dashboard.py` | Status geral: streams live/upcoming/VOD, próxima execução do scheduler, quota estimada |
| `GET /config` | `routes/config.py` | Formulário com abas por seção (credentials, scheduler, filters, output, technical, logging) |
| `POST /config` | `routes/config.py` | Salva config no SQLite, chama `scheduler.reload_config()`, redireciona |
| `GET /channels` | `routes/channels.py` | Lista canais monitorados, adicionar/remover handles e IDs |
| `GET /logs` | `routes/logs.py` | Exibe tail dos arquivos de log via SSE |
| `GET /force-sync` | `routes/dashboard.py` | Força execução imediata do scheduler |
| `GET /{playlist_live_filename}` | `routes/playlists.py` | Serve M3U live |
| `GET /{playlist_upcoming_filename}` | `routes/playlists.py` | Serve M3U upcoming |
| `GET /{playlist_vod_filename}` | `routes/playlists.py` | Serve M3U VOD |
| `GET /{xmltv_filename}` | `routes/playlists.py` | Serve EPG XML |

### 6.3 Regras de componentes FastHTML

```python
# CORRETO — componente FastHTML puro Python
def config_form(sections: dict) -> FT:
    tabs = []
    for section_name, rows in sections.items():
        fields = [
            Label(row.description,
                  Input(name=row.key, value=row.value,
                        type="number" if row.value_type == "int" else
                             "checkbox" if row.value_type == "bool" else "text"))
            for row in rows
        ]
        tabs.append(Details(Summary(section_name.title()), *fields))
    return Form(*tabs, Button("Salvar", type="submit"), method="post", action="/config")

# ERRADO — HTML como string dentro de FastHTML
def config_form():
    return "<form>...</form>"  # ← PROIBIDO
```

### 6.4 Logs em tempo real (SSE)

```python
# web/routes/logs.py
"""Exibe logs via Server-Sent Events — sem WebSocket, sem polling agressivo."""
from fasthtml.common import *
import asyncio
from pathlib import Path

async def log_stream(log_path: Path):
    """Generator assíncrono que faz tail do arquivo de log."""
    with open(log_path, "r") as f:
        f.seek(0, 2)  # vai para o fim
        while True:
            line = f.readline()
            if line:
                yield f"data: {line.strip()}\n\n"
            else:
                await asyncio.sleep(1)
```

### 6.5 Checklist Etapa 3

```
[ ] web/main.py com lifespan funcional
[ ] Scheduler sobe como asyncio.Task no lifespan
[ ] GET / retorna dashboard com contagem de streams
[ ] GET /config retorna formulário com todas as seções do DEFAULTS
[ ] POST /config salva e recarrega config sem restart do processo
[ ] GET /channels funcional
[ ] GET /logs com SSE funcional
[ ] Rotas de playlist retornam M3U com mimetype correto
[ ] Rota EPG retorna XML com mimetype correto
[ ] Pico.css carregado (responsivo por padrão)
[ ] tests/test_web_routes.py passa
[ ] DECISIONS.md atualizado
```

---

## 7. Etapa 4 — Docker e Container

**Pré-requisito:** Etapa 3 completa e testada localmente.

### 7.1 requirements.txt

```txt
python-fasthtml>=0.12.0
google-api-python-client>=2.0.0
pytz>=2024.1
streamlink>=6.0.0
```

**Removidos obrigatoriamente:**
```txt
# REMOVIDOS — não devem aparecer no requirements.txt final
# Flask
# python-dotenv
# Werkzeug
```

### 7.2 Dockerfile

```dockerfile
FROM python:3.12-slim

# Dependências do sistema
RUN apt-get update && apt-get install -y \
    ffmpeg \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código
COPY core/ ./core/
COPY web/ ./web/
COPY smart_player.py .
COPY scripts/ ./scripts/

# Volume para dados persistentes
VOLUME ["/data"]

# Porta da interface web
EXPOSE 8888

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8888/')"

# Entry point
CMD ["python", "-m", "uvicorn", "web.main:app", "--host", "0.0.0.0", "--port", "8888"]
```

### 7.3 docker-compose.yml

```yaml
services:
  tubewranglerr:
    build: .
    container_name: tubewranglerr
    restart: unless-stopped
    ports:
      - "8888:8888"
    volumes:
      - ./data:/data
    environment:
      - PYTHONUNBUFFERED=1
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8888/')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
```

### 7.4 Checklist Etapa 4

```
[ ] requirements.txt sem Flask e sem python-dotenv
[ ] Dockerfile build sem erros: docker build -t tubewranglerr .
[ ] docker-compose up sobe sem erros
[ ] http://localhost:8888/ acessível no browser
[ ] Volume /data persiste entre restarts (testar: docker-compose restart)
[ ] config.db criado em data/ no primeiro boot
[ ] Playlists M3U acessíveis via URL externa
[ ] Health check passa: docker inspect tubewranglerr --format="{{.State.Health.Status}}"
[ ] DECISIONS.md atualizado
```

---

## 8. Etapa 5 — smart_player.py

**Pré-requisito:** Etapas 1-4 completas.

`smart_player.py` é invocado externamente (por players IPTV como Jellyfin) como processo separado. Ele lê `state_cache.json` e `textosepg.json` diretamente do disco.

### 8.1 Mudanças necessárias

1. Substituir `load_dotenv()` + `os.getenv("PLACEHOLDER_IMAGE_URL")` por leitura do SQLite via `AppConfig`
2. Manter `argparse` intacto — a interface de linha de comando não muda
3. O `SCRIPT_DIR` deve apontar para `/data` onde o `state_cache.json` é salvo

```python
# smart_player.py — trecho a modificar
# ANTES:
from dotenv import load_dotenv
load_dotenv(dotenv_path=SCRIPT_DIR / ".env")
PLACEHOLDER_IMAGE_URL = os.getenv("PLACEHOLDER_IMAGE_URL", "")

# DEPOIS:
from core.config import AppConfig
_cfg = AppConfig()
PLACEHOLDER_IMAGE_URL = _cfg.get_str("placeholder_image_url")
```

### 8.2 Checklist Etapa 5

```
[ ] import load_dotenv removido de smart_player.py
[ ] Todos os os.getenv() substituídos por AppConfig
[ ] Testar invocação manual: python smart_player.py -i <url_youtube>
[ ] Testar com URL de placeholder
[ ] Testar com URL de stream live
[ ] Testar com URL de upcoming (thumbnail)
[ ] DECISIONS.md atualizado
```

---

## 9. Testes entre Etapas

### 9.1 tests/test_config.py (Etapa 1)

```python
import pytest
from pathlib import Path
import tempfile
from core.config import AppConfig, DEFAULTS

@pytest.fixture
def tmp_config(tmp_path):
    return AppConfig(db_path=tmp_path / "test_config.db")

def test_defaults_criados(tmp_config):
    """Todos os defaults do DEFAULTS devem existir no banco após criação."""
    for key in DEFAULTS:
        assert tmp_config.get_raw(key) is not None, f"Chave ausente: {key}"

def test_get_int(tmp_config):
    assert tmp_config.get_int("scheduler_main_interval_hours") == 4

def test_get_bool_true(tmp_config):
    assert tmp_config.get_bool("enable_scheduler_active_hours") is True

def test_get_bool_false(tmp_config):
    tmp_config.update("enable_scheduler_active_hours", "false")
    assert tmp_config.get_bool("enable_scheduler_active_hours") is False

def test_get_list(tmp_config):
    result = tmp_config.get_list("allowed_category_ids")
    assert "17" in result

def test_get_mapping(tmp_config):
    result = tmp_config.get_mapping("category_mappings")
    assert "Sports" in result
    assert result["Sports"] == "ESPORTES"

def test_update_persiste(tmp_config, tmp_path):
    tmp_config.update("http_port", "9999")
    cfg2 = AppConfig(db_path=tmp_path / "test_config.db")
    assert cfg2.get_int("http_port") == 9999

def test_update_key_inexistente(tmp_config):
    with pytest.raises(KeyError):
        tmp_config.update("chave_que_nao_existe", "valor")

def test_import_env_file(tmp_config, tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_text('YOUTUBE_API_KEY="minha_chave_teste"\n')
    tmp_config.import_from_env_file(env_file)
    assert tmp_config.get_str("youtube_api_key") == "minha_chave_teste"

def test_get_all_by_section(tmp_config):
    sections = tmp_config.get_all_by_section()
    assert "credentials" in sections
    assert "scheduler" in sections
    assert "filters" in sections
    assert "output" in sections
    assert "technical" in sections
    assert "logging" in sections

def test_cobertura_total_defaults(tmp_config):
    """Garante que nenhuma chave do file.env original ficou sem mapear."""
    expected_keys = list(DEFAULTS.keys())
    assert len(expected_keys) >= 30, f"Esperado 30+ chaves, encontrado {len(expected_keys)}"
```

### 9.2 tests/test_state_manager.py (Etapa 2)

```python
import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone
from core.state_manager import StateManager
from core.config import AppConfig

@pytest.fixture
def mock_config(tmp_path):
    cfg = AppConfig(db_path=tmp_path / "cfg.db")
    return cfg

@pytest.fixture
def state(mock_config, tmp_path):
    return StateManager(mock_config, cache_path=tmp_path / "state.json")

def test_update_streams_adiciona(state):
    stream = {"video_id": "abc123", "status": "live",
               "channel_id": "ch1", "title_original": "Test"}
    state.update_streams([stream])
    assert "abc123" in state.streams

def test_save_load_disk(state, tmp_path):
    stream = {"video_id": "xyz", "status": "upcoming", "channel_id": "ch2",
               "title_original": "Test", "fetch_time": datetime.now(timezone.utc)}
    state.update_streams([stream])
    state.save_to_disk()
    state2 = StateManager(state.config, cache_path=tmp_path / "state.json")
    assert state2.load_from_disk() is True
    assert "xyz" in state2.streams
```

### 9.3 tests/test_playlist_builder.py (Etapa 2)

```python
import pytest
from core.playlist_builder import M3UGenerator, XMLTVGenerator
from core.config import AppConfig

@pytest.fixture
def cfg(tmp_path):
    return AppConfig(db_path=tmp_path / "cfg.db")

@pytest.fixture
def m3u(cfg):
    return M3UGenerator(cfg)

def test_gera_m3u_live(m3u):
    streams = [{"video_id": "v1", "status": "live",
                "title_original": "Jogo ao vivo",
                "channel_name": "Canal Teste",
                "watch_url": "https://youtube.com/watch?v=v1",
                "thumbnail_url": "https://img.com/thumb.jpg",
                "category_original": "17",
                "actual_start_time_utc": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc)}]
    result = m3u.generate_playlist(streams, {}, "live")
    assert "#EXTM3U" in result
    assert "youtube.com" in result

def test_m3u_vazio_retorna_placeholder(m3u):
    result = m3u.generate_playlist([], {}, "live")
    assert "PLACEHOLDER" in result or "#EXTM3U" in result
```

### 9.4 tests/test_scheduler.py (Etapa 2)

```python
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from core.scheduler import Scheduler
from core.config import AppConfig

@pytest.fixture
def cfg(tmp_path):
    return AppConfig(db_path=tmp_path / "cfg.db")

@pytest.fixture
def scheduler(cfg, tmp_path):
    scraper = MagicMock()
    state = MagicMock()
    state.get_all_streams.return_value = []
    state.get_all_channels.return_value = {}
    return Scheduler(cfg, scraper, state)

def test_reload_config_atualiza(scheduler, tmp_path):
    new_cfg = AppConfig(db_path=tmp_path / "new.db")
    new_cfg.update("scheduler_main_interval_hours", "8")
    scheduler.reload_config(new_cfg)
    assert scheduler.config.get_int("scheduler_main_interval_hours") == 8

@pytest.mark.asyncio
async def test_run_cancellable(scheduler):
    task = asyncio.create_task(scheduler.run(initial_run_delay=True))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
```

### 9.5 tests/test_web_routes.py (Etapa 3)

```python
import pytest
from fasthtml.testclient import TestClient
from web.main import app

@pytest.fixture
def client():
    return TestClient(app)

def test_dashboard_ok(client):
    r = client.get("/")
    assert r.status_code == 200

def test_config_page_ok(client):
    r = client.get("/config")
    assert r.status_code == 200
    assert "credentials" in r.text.lower() or "youtube" in r.text.lower()

def test_config_post_salva(client):
    r = client.post("/config", data={"http_port": "9000"})
    assert r.status_code in (200, 302)

def test_playlist_live_ok(client):
    r = client.get("/playlist_live.m3u8")
    assert r.status_code == 200
    assert "#EXTM3U" in r.text

def test_epg_ok(client):
    r = client.get("/youtube_epg.xml")
    assert r.status_code == 200
    assert "<?xml" in r.text
```

---

## 10. Revisão Final de Migração

**Pré-requisito:** Etapas 1-5 completas, todos os testes passando.

O agente deve executar esta revisão **de forma autônoma** e registrar o resultado em `DECISIONS.md` na seção `## Revisão Final`.

### 10.1 Checklist de eliminação de dependências antigas

Execute os comandos abaixo e confirme saída vazia:

```bash
# Nenhum arquivo novo deve usar os.getenv
grep -r "os.getenv" core/ web/ --include="*.py"
# Esperado: nenhuma saída

# Nenhum arquivo novo deve usar load_dotenv
grep -r "load_dotenv\|from dotenv" core/ web/ --include="*.py"
# Esperado: nenhuma saída

# Nenhum arquivo novo deve importar Flask
grep -r "from flask\|import flask\|import Flask" core/ web/ --include="*.py"
# Esperado: nenhuma saída

# Nenhum arquivo novo deve usar threading para servidor
grep -r "threading.Thread" web/ --include="*.py"
# Esperado: nenhuma saída
```

### 10.2 Checklist de cobertura funcional

Verificar que **cada funcionalidade** do `get_streams.py` original foi migrada:

```
[ ] Resolução de handles → core/youtube_api.py: resolve_channel_handles_to_ids()
[ ] Busca por playlists → core/youtube_api.py: fetch_all_streams_for_channels_using_playlists()
[ ] Busca por search.list → core/youtube_api.py: fetch_all_streams_for_channels()
[ ] Busca por IDs específicos → core/youtube_api.py: fetch_streams_by_ids()
[ ] Pruning de streams antigos → core/state_manager.py: prune_ended_streams()
[ ] Persistência de estado → core/state_manager.py: save_to_disk() / load_from_disk()
[ ] Geração M3U live → core/playlist_builder.py: M3UGenerator.generate_playlist(..., "live")
[ ] Geração M3U upcoming → core/playlist_builder.py: M3UGenerator.generate_playlist(..., "upcoming")
[ ] Geração M3U VOD → core/playlist_builder.py: M3UGenerator.generate_playlist(..., "vod")
[ ] Geração EPG XMLTV → core/playlist_builder.py: XMLTVGenerator.generate_xml()
[ ] Scheduler principal → core/scheduler.py: Scheduler.run()
[ ] Busca pré-evento → core/scheduler.py (lógica de pre_event_ids)
[ ] Busca pós-evento → core/scheduler.py (lógica de post_event_ids)
[ ] Full sync periódico → core/scheduler.py (lógica de time_for_full_sync)
[ ] Horário de atividade → core/scheduler.py (lógica de is_active_time)
[ ] Mapeamento de categorias → core/playlist_builder.py: get_display_category()
[ ] Mapeamento de nomes de canais → core/playlist_builder.py: get_display_title()
[ ] Filtro de expressões no título → core/playlist_builder.py: get_display_title()
[ ] Placeholders invisíveis → core/playlist_builder.py: generate_playlist()
[ ] Textos EPG para smart_player → core/scheduler.py ou state_manager (textosepg.json)
[ ] Serving HTTP de playlists → web/routes/playlists.py
```

### 10.3 Verificação de smart_player.py

```
[ ] python smart_player.py --help executa sem erro
[ ] Nenhum import de dotenv em smart_player.py
[ ] Nenhum os.getenv() em smart_player.py
[ ] Lê PLACEHOLDER_IMAGE_URL do SQLite via AppConfig
[ ] Lê state_cache.json do path correto (/data/)
[ ] ffmpeg, streamlink, yt-dlp ainda são chamados via subprocess (não alterar)
```

### 10.4 Arquivos originais — decisão final

Após confirmar todos os checklists acima como `[x]`, o agente deve:

1. Mover `get_streams.py` → `_archive/get_streams.py.bak`
2. Mover `file.env` → `_archive/file.env.bak`
3. **NÃO apagar** — manter no `_archive/` como referência histórica
4. Adicionar `_archive/` ao `.gitignore`
5. Registrar em `DECISIONS.md`: data, hash dos arquivos originais, confirmação de migração

---

## 11. Protocolo DECISIONS.md

O agente **deve criar este arquivo na primeira ação** e mantê-lo atualizado ao longo de todo o processo.

### 11.1 Template inicial

```markdown
# DECISIONS.md — TubeWranglerr Refactoring Log

Gerado por: GitHub Copilot Agent
Início: [DATA_HORA_INÍCIO]
Documento de referência: REFACTORING_TUBEWRANGLERR.md

---

## Status Geral

| Etapa | Status | Início | Conclusão |
|---|---|---|---|
| 1 — core/config.py | ⏳ Em progresso | | |
| 2 — Separação de módulos | ⬜ Pendente | | |
| 3 — Interface FastHTML | ⬜ Pendente | | |
| 4 — Docker | ⬜ Pendente | | |
| 5 — smart_player.py | ⬜ Pendente | | |
| Revisão Final | ⬜ Pendente | | |

---

## Decisões

### [ETAPA-1] [DATA] Título da decisão
**Contexto:** O que motivou a decisão
**Decisão tomada:** O que foi decidido
**Alternativas consideradas:** O que foi descartado e por quê
**Impacto:** Quais arquivos/módulos foram afetados

---

## Dúvidas e Bloqueios

### [DATA] Título da dúvida
**Situação:** Descrição do problema
**Status:** 🔴 Bloqueado / 🟡 Aguardando / ✅ Resolvido
**Resolução:** Como foi resolvida (preencher quando resolvido)

---

## Revisão Final

[Preenchido pelo agente na Etapa 10]

### Resultado dos greps de verificação
\`\`\`
[colar output dos comandos grep aqui]
\`\`\`

### Arquivos originais arquivados
- get_streams.py → _archive/get_streams.py.bak (SHA256: ...)
- file.env → _archive/file.env.bak (SHA256: ...)
```

### 11.2 Regras do DECISIONS.md

```
OBRIGATÓRIO: Criar DECISIONS.md antes de tocar em qualquer arquivo de código
OBRIGATÓRIO: Registrar toda decisão que não estava explícita neste documento
OBRIGATÓRIO: Atualizar a tabela de Status ao concluir cada etapa
OBRIGATÓRIO: Registrar dúvidas antes de assumir uma solução arbitrária
PROIBIDO: Apagar entradas antigas do DECISIONS.md
PROIBIDO: Marcar etapa como ✅ Concluída sem todos os itens do checklist serem [x]
```

---

## Apêndice — .github/copilot-instructions.md

O agente deve criar este arquivo para que o Copilot mantenha consistência em todo o repositório:

```markdown
# Copilot Instructions — TubeWranglerr

## Contexto do Projeto
Sistema Python de monitoramento de streams YouTube que gera playlists M3U e EPG XMLTV.
Stack: FastHTML + FastLite (SQLite) + asyncio. Container Docker standalone.

## Regras de Código

- Nunca usar os.getenv() — sempre usar AppConfig de core/config.py
- Nunca importar Flask — o projeto usa FastHTML
- Toda lógica de negócio fica em core/ — zero lógica em web/
- Toda configuração vem de AppConfig injetado como parâmetro
- Funções públicas sempre têm type hints

## Stack
- Web: python-fasthtml (FastHTML + HTMX nativo)
- DB: fastlite (SQLite)
- Async: asyncio nativo (sem celery, sem redis, sem rabbitmq)
- YouTube: google-api-python-client (síncrono, chamado em executor quando necessário)

## Estrutura
- core/ → lógica de negócio pura
- web/ → rotas e componentes FastHTML
- data/ → volume Docker (nunca versionar)
- tests/ → pytest + pytest-asyncio

## Convenções
- Arquivos novos começam com docstring de responsabilidade
- Uma classe por arquivo no core/
- Commits só após testes da etapa passarem
```

---

*Este documento é o contrato entre o desenvolvedor e o agente. Qualquer desvio deve ser justificado em DECISIONS.md.*
