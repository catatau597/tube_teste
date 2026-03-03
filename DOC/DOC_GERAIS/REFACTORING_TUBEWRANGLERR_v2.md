# REFACTORING_TUBEWRANGLERR.md

> **Versão:** 2.0  
> **Projeto:** TubeWranglerr  
> **Destino:** Agente autônomo GitHub Copilot  
> **Objetivo:** Refatoração completa para stack FastHTML + SQLite em container standalone  
> **Abordagem:** Container-First Development — nenhum comando Python roda no host  

---

## ⚠️ LEIA ANTES DE QUALQUER AÇÃO

Este documento é a **única fonte de verdade** para o agente. Toda decisão deve ser tomada com base nele. Se houver ambiguidade, o agente deve **parar e registrar a dúvida** no `DECISIONS.md` antes de prosseguir.

**REGRA FUNDAMENTAL:** O host Debian é apenas um sistema de arquivos. Python, pip, pytest e a aplicação rodam **exclusivamente dentro do container Docker**. Nunca instale dependências Python no host.

---

## 📋 ÍNDICE

0. [Etapa 0 — Container de Desenvolvimento](#etapa-0--container-de-desenvolvimento)
1. [Regras Absolutas do Agente](#1-regras-absolutas-do-agente)
2. [Estrutura Final do Projeto](#2-estrutura-final-do-projeto)
3. [Etapa 1 — core/config.py (SQLite)](#3-etapa-1--coreconfigpy-sqlite)
4. [Etapa 2 — Separação de Módulos](#4-etapa-2--separação-de-módulos)
5. [Etapa 3 — Interface FastHTML](#5-etapa-3--interface-fasthtml)
6. [Etapa 4 — Container de Produção](#6-etapa-4--container-de-produção)
7. [Etapa 5 — smart_player.py](#7-etapa-5--smart_playerpy)
8. [Testes entre Etapas](#8-testes-entre-etapas)
9. [Revisão Final de Migração](#9-revisão-final-de-migração)
10. [Protocolo DECISIONS.md](#10-protocolo-decisionsmd)

---

## Etapa 0 — Container de Desenvolvimento

**Esta é a primeira etapa. Nada de código de negócio deve ser escrito antes dela estar completa.**

### Objetivo

Criar um container Docker de desenvolvimento onde todo o código será escrito, testado e executado. O código fica no host (editável pelo VS Code via SSH), mas **executa apenas dentro do container**.

### 0.1 requirements.txt

Criar na raiz do projeto:

```txt
python-fasthtml>=0.12.0
fastlite>=0.0.9
google-api-python-client>=2.0.0
pytz>=2024.1
pytest>=8.0.0
pytest-asyncio>=0.23.0
httpx>=0.27.0
```

**Dependências explicitamente ausentes (não adicionar):**
```txt
# NUNCA adicionar:
# Flask
# python-dotenv
# Werkzeug
```

### 0.2 Dockerfile (desenvolvimento e produção)

```dockerfile
FROM python:3.12-slim

# Dependências do sistema
RUN apt-get update && apt-get install -y \
    ffmpeg \
    fonts-dejavu-core \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependências Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Em produção, o código é copiado aqui
# Em desenvolvimento, é montado como volume
COPY . .

# Diretório de dados (volume externo)
RUN mkdir -p /data/m3us /data/epgs /data/logs

VOLUME ["/data"]
EXPOSE 8888

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8888/')"

CMD ["python3", "-m", "uvicorn", "web.main:app", \
     "--host", "0.0.0.0", "--port", "8888"]
```

### 0.3 docker-compose.yml (produção)

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
      test: ["CMD", "python3", "-c",
             "import urllib.request; urllib.request.urlopen('http://localhost:8888/')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
```

### 0.4 docker-compose.override.yml (desenvolvimento — não vai para produção)

```yaml
# Este arquivo é carregado automaticamente pelo docker compose em desenvolvimento.
# Adicionar ao .gitignore se não quiser versionar, ou manter para padronizar o dev.
services:
  tubewranglerr:
    volumes:
      - .:/app                    # código montado — edições no VS Code refletem imediatamente
      - ./data:/data              # dados persistentes
    command: sleep infinity       # container fica vivo aguardando comandos
    environment:
      - PYTHONUNBUFFERED=1
      - PYTHONDONTWRITEBYTECODE=1
```

### 0.5 .gitignore obrigatório

```
data/
*.db
*.log
.env
__pycache__/
*.pyc
.venv/
*.bak
_archive/
```

### 0.6 Sequência de inicialização do container de desenvolvimento

O agente deve executar estes comandos **no terminal SSH do VS Code** (no host Debian), uma única vez:

```bash
# Build da imagem de desenvolvimento
docker compose build

# Subir o container de desenvolvimento em background
docker compose up -d

# Verificar que o container está rodando
docker compose ps

# Confirmar Python dentro do container
docker compose exec tubewranglerr python3 --version

# Confirmar dependências instaladas
docker compose exec tubewranglerr pip list
```

### 0.7 Padrão de execução a partir daqui

**Todo comando Python, pip ou pytest deve usar este padrão:**

```bash
# Rodar um script
docker compose exec tubewranglerr python3 scripts/migrate_env.py

# Rodar testes de uma etapa específica
docker compose exec tubewranglerr pytest tests/test_config.py -v

# Rodar todos os testes
docker compose exec tubewranglerr pytest tests/ -v

# Shell interativo dentro do container (para debug)
docker compose exec tubewranglerr bash

# Uvicorn com reload automático (desenvolvimento)
docker compose exec tubewranglerr uvicorn web.main:app \
    --host 0.0.0.0 --port 8888 --reload
```

### 0.8 Checklist Etapa 0

```
[ ] requirements.txt criado na raiz
[ ] Dockerfile criado na raiz
[ ] docker-compose.yml criado na raiz
[ ] docker-compose.override.yml criado na raiz
[ ] .gitignore criado na raiz
[ ] docker compose build executa sem erro
[ ] docker compose up -d sobe sem erro
[ ] docker compose exec tubewranglerr python3 --version retorna 3.12.x
[ ] docker compose exec tubewranglerr ffmpeg -version retorna versão
[ ] docker compose exec tubewranglerr pip list mostra python-fasthtml e fastlite
[ ] DECISIONS.md criado e atualizado com esta etapa
```

---

## 1. Regras Absolutas do Agente

### 🚫 PROIBIÇÕES — nunca faça sem aprovação explícita

```
PROIBIDO: Instalar Python, pip ou qualquer pacote diretamente no host Debian
PROIBIDO: Executar python3, pytest ou pip fora de docker compose exec
PROIBIDO: Apagar qualquer arquivo original antes da Etapa 9 (revisão final)
PROIBIDO: Usar os.getenv() ou load_dotenv() em qualquer arquivo novo
PROIBIDO: Importar Flask em qualquer arquivo novo
PROIBIDO: Criar variáveis globais de configuração (ex: API_KEY = os.getenv(...))
PROIBIDO: Usar threading.Thread para o servidor web
PROIBIDO: Misturar lógica de negócio dentro de rotas FastHTML
PROIBIDO: Fazer commit sem que os testes da etapa correspondente passem no container
PROIBIDO: Usar pickle ou shelve para persistência
```

### ✅ OBRIGAÇÕES — sempre faça

```
OBRIGATÓRIO: Criar DECISIONS.md antes de tocar em qualquer arquivo de código
OBRIGATÓRIO: Todo comando de execução usa docker compose exec tubewranglerr
OBRIGATÓRIO: Registrar toda decisão de design em DECISIONS.md antes de implementar
OBRIGATÓRIO: Cada módulo novo tem seu bloco de teste correspondente (ver Seção 8)
OBRIGATÓRIO: AppConfig deve ser passado como parâmetro — nunca importado como global
OBRIGATÓRIO: Manter backward compatibility do state_cache.json
OBRIGATÓRIO: Todo arquivo novo começa com docstring explicando sua responsabilidade
OBRIGATÓRIO: Usar type hints em todas as funções públicas
OBRIGATÓRIO: Rodar os testes de cada etapa dentro do container antes de avançar
OBRIGATÓRIO: Atualizar o checklist em DECISIONS.md ao concluir cada etapa
```

### 📐 Convenções de código

```python
# CORRETO — config como parâmetro injetado
class Scheduler:
    def __init__(self, config: AppConfig, scraper: YouTubeAPI, state: StateManager):
        self.config = config

# ERRADO — config como global
SCHEDULER_INTERVAL = int(os.getenv("SCHEDULER_MAIN_INTERVAL_HOURS", 4))  # PROIBIDO
```

```python
# CORRETO — módulo com docstring de responsabilidade
"""
core/scheduler.py
-----------------
Responsabilidade: Loop assíncrono de agendamento de buscas.
Depende de: AppConfig, YouTubeAPI, StateManager
NÃO depende de: Flask, FastHTML, os.getenv
"""
```

---

## 2. Estrutura Final do Projeto

```
tubewranglerr/
│
├── core/
│   ├── __init__.py
│   ├── config.py                  # AppConfig + SQLite (substitui .env)
│   ├── state_manager.py           # StateManager
│   ├── youtube_api.py             # APIScraper
│   ├── playlist_builder.py        # M3UGenerator + XMLTVGenerator
│   └── scheduler.py               # Scheduler
│
├── web/
│   ├── __init__.py
│   ├── main.py                    # FastHTML app + lifespan
│   └── routes/
│       ├── __init__.py
│       ├── dashboard.py           # GET /
│       ├── config.py              # GET/POST /config
│       ├── channels.py            # GET/POST /channels
│       ├── logs.py                # GET /logs (SSE)
│       └── playlists.py           # M3U + EPG endpoints
│
├── scripts/
│   └── migrate_env.py             # Migração única .env → SQLite
│
├── tests/
│   ├── test_config.py
│   ├── test_state_manager.py
│   ├── test_youtube_api.py
│   ├── test_playlist_builder.py
│   ├── test_scheduler.py
│   └── test_web_routes.py
│
├── _archive/                      # Criado na Etapa 9
│   ├── get_streams.py.bak
│   └── file.env.bak
│
├── data/                          # Volume Docker — NUNCA versionar
│   ├── config.db
│   ├── m3us/
│   ├── epgs/
│   └── logs/
│
├── smart_player.py                # Mantido na raiz
├── Dockerfile
├── docker-compose.yml
├── docker-compose.override.yml
├── requirements.txt
├── .gitignore
├── .github/
│   └── copilot-instructions.md
├── DECISIONS.md
└── REFACTORING_TUBEWRANGLERR.md
```

---

## 3. Etapa 1 — core/config.py (SQLite)

**Pré-requisito:** Etapa 0 completa. Container rodando.

**Esta é a etapa mais crítica de código. Nada mais deve ser tocado antes dela estar completa e testada dentro do container.**

### 3.1 Objetivo

Substituir **todos** os `os.getenv()` do projeto por uma classe `AppConfig` que lê e persiste no SQLite via FastLite. A configuração deve ser **recarregável em runtime** sem reiniciar o processo.

### 3.2 core/config.py completo

```python
"""
core/config.py
--------------
Responsabilidade: Única fonte de verdade para configurações da aplicação.
Substitui completamente o arquivo .env e os.getenv() em todo o projeto.
Depende de: fastlite (SQLite)
NÃO depende de: Flask, FastHTML, os.getenv
"""

from pathlib import Path
from dataclasses import dataclass
from fastlite import database

DB_PATH = Path("/data/config.db")

@dataclass
class ConfigRow:
    key: str
    value: str
    section: str
    description: str
    value_type: str  # "str" | "int" | "bool" | "list" | "mapping"

# Todas as 43 variáveis do file.env original mapeadas
DEFAULTS: dict = {
    # --- Seção 1: Credenciais ---
    "youtube_api_key":               ("", "credentials", "Chave de API do YouTube", "str"),
    "target_channel_handles":        ("", "credentials", "Handles separados por vírgula", "list"),
    "target_channel_ids":            ("", "credentials", "IDs diretos de canais", "list"),

    # --- Seção 2: Agendador ---
    "scheduler_main_interval_hours":         ("4",  "scheduler", "Intervalo principal em horas", "int"),
    "scheduler_pre_event_window_hours":      ("2",  "scheduler", "Janela pré-evento em horas", "int"),
    "scheduler_pre_event_interval_minutes":  ("5",  "scheduler", "Intervalo pré-evento em minutos", "int"),
    "scheduler_post_event_interval_minutes": ("5",  "scheduler", "Intervalo pós-evento em minutos", "int"),
    "enable_scheduler_active_hours":         ("true","scheduler", "Ativar horário de atividade", "bool"),
    "scheduler_active_start_hour":           ("7",  "scheduler", "Hora de início do horário ativo", "int"),
    "scheduler_active_end_hour":             ("22", "scheduler", "Hora de fim do horário ativo", "int"),
    "full_sync_interval_hours":              ("48", "scheduler", "Intervalo de full sync em horas", "int"),
    "resolve_handles_ttl_hours":             ("24", "scheduler", "TTL cache de handles em horas", "int"),
    "initial_sync_days":                     ("2",  "scheduler", "Dias para busca inicial (0=tudo)", "int"),

    # --- Seção 3: Filtros ---
    "max_schedule_hours":            ("72",  "filters", "Limite futuro em horas", "int"),
    "max_upcoming_per_channel":      ("6",   "filters", "Máximo agendamentos por canal", "int"),
    "title_filter_expressions":      ("ao vivo,AO VIVO,cortes,react,JOGO COMPLETO",
                                      "filters", "Expressões a remover dos títulos", "list"),
    "prefix_title_with_channel_name":("true","filters", "Prefixar título com canal", "bool"),
    "prefix_title_with_status":      ("true","filters", "Prefixar título com status", "bool"),
    "category_mappings":             ("Sports|ESPORTES,Gaming|JOGOS,News & Politics|NOTICIAS",
                                      "filters", "Mapeamento de categorias API|Display", "mapping"),
    "channel_name_mappings":         ("Canal GOAT|GOAT,TNT Sports Brasil|TNT Sports",
                                      "filters", "Mapeamento nomes Longo|Curto", "mapping"),
    "epg_description_cleanup":       ("true","filters", "Limpar descrição EPG", "bool"),
    "filter_by_category":            ("true","filters", "Filtrar por categoria da API", "bool"),
    "allowed_category_ids":          ("17",  "filters", "IDs de categoria permitidos", "list"),
    "keep_recorded_streams":         ("true","filters", "Manter streams gravados", "bool"),
    "max_recorded_per_channel":      ("2",   "filters", "Máximo gravações por canal", "int"),
    "recorded_retention_days":       ("2",   "filters", "Dias de retenção de gravações", "int"),

    # --- Seção 4: Saída ---
    "playlist_save_directory":       ("/data/m3us",         "output", "Diretório playlists M3U", "str"),
    "playlist_live_filename":        ("playlist_live.m3u8", "output", "Arquivo live M3U", "str"),
    "playlist_upcoming_filename":    ("playlist_upcoming.m3u8","output","Arquivo upcoming M3U","str"),
    "playlist_vod_filename":         ("playlist_vod.m3u8",  "output", "Arquivo VOD M3U", "str"),
    "xmltv_save_directory":          ("/data/epgs",         "output", "Diretório EPG XML", "str"),
    "xmltv_filename":                ("youtube_epg.xml",    "output", "Arquivo EPG XML", "str"),
    "placeholder_image_url":         ("",                   "output", "URL imagem placeholder", "str"),
    "use_invisible_placeholder":     ("true",               "output", "Placeholder invisível no M3U", "bool"),

    # --- Seção 5: Técnico ---
    "http_port":                     ("8888",             "technical", "Porta HTTP", "int"),
    "state_cache_filename":          ("state_cache.json", "technical", "Arquivo de cache de estado", "str"),
    "stale_hours":                   ("6",                "technical", "Horas para stream stale", "int"),
    "use_playlist_items":            ("true",             "technical", "Usar playlistItems API", "bool"),
    "local_timezone":                ("America/Sao_Paulo","technical", "Fuso horário local", "str"),

    # --- Seção 6: Logs ---
    "log_level":                     ("INFO", "logging", "Nível de log do core", "str"),
    "log_to_file":                   ("true", "logging", "Salvar log em arquivo", "bool"),
    "smart_player_log_level":        ("INFO", "logging", "Nível de log do smart_player", "str"),
    "smart_player_log_to_file":      ("true", "logging", "Salvar log do smart_player", "bool"),
}


class AppConfig:
    """
    Classe de configuração da aplicação.
    Lê e persiste no SQLite via FastLite. Recarregável em runtime via reload().
    NUNCA use os.getenv() fora desta classe.
    """

    def __init__(self, db_path: Path = DB_PATH):
        self._db = database(db_path)
        self._ensure_table()
        self._cache: dict = {}
        self.reload()

    def _ensure_table(self):
        tbl = self._db.t.config
        if "config" not in self._db.t:
            tbl.create(
                key=str, value=str, section=str,
                description=str, value_type=str, pk="key"
            )
        existing = {row.key for row in self._db.t.config.rows}
        for key, (default_val, section, desc, vtype) in DEFAULTS.items():
            if key not in existing:
                self._db.t.config.insert(ConfigRow(
                    key=key, value=default_val,
                    section=section, description=desc, value_type=vtype
                ))

    def reload(self):
        """Recarrega do banco. Chamar após salvar via web."""
        self._cache = {row.key: row for row in self._db.t.config.rows}

    def get_raw(self, key: str) -> str:
        return self._cache[key].value if key in self._cache else DEFAULTS.get(key, ("",))

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
        """Atualiza uma chave no banco E no cache imediatamente."""
        if key not in self._cache:
            raise KeyError(f"Chave desconhecida: {key}")
        self._db.t.config.update({"key": key, "value": str(value)})
        self._cache[key].value = str(value)

    def update_many(self, updates: dict):
        """Atualiza múltiplas chaves atomicamente."""
        for key, value in updates.items():
            self.update(key, str(value))

    def get_all_by_section(self) -> dict:
        """Retorna configurações agrupadas por seção para o formulário web."""
        sections: dict = {}
        for row in self._cache.values():
            sections.setdefault(row.section, []).append(row)
        return sections

    def import_from_env_file(self, env_path: Path):
        """
        Utilitário de migração única: importa valores de um .env existente.
        Mapeia UPPER_SNAKE → lower_snake automaticamente.
        Usar apenas durante migração inicial.
        """
        if not env_path.exists():
            return
        mapping = {k.upper(): k for k in DEFAULTS.keys()}
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                lower_key = mapping.get(k.upper())
                if lower_key:
                    try:
                        self.update(lower_key, v)
                    except KeyError:
                        pass
```

### 3.3 scripts/migrate_env.py

```python
"""
scripts/migrate_env.py
----------------------
Migração única: importa file.env → SQLite config.db.
Executar uma vez após o container subir pela primeira vez.
Comando: docker compose exec tubewranglerr python3 scripts/migrate_env.py
"""
from pathlib import Path
from core.config import AppConfig

if __name__ == "__main__":
    cfg = AppConfig()
    env_path = Path("/app/file.env")
    cfg.import_from_env_file(env_path)
    print("✅ Migração concluída.\n")
    for section, rows in cfg.get_all_by_section().items():
        print(f"[{section}]")
        for row in rows:
            print(f"  {row.key} = {row.value}")
        print()
```

### 3.4 Validação da Etapa 1 (dentro do container)

```bash
# Rodar dentro do container
docker compose exec tubewranglerr python3 scripts/migrate_env.py
docker compose exec tubewranglerr pytest tests/test_config.py -v
```

### 3.5 Checklist Etapa 1

```
[ ] core/__init__.py criado
[ ] core/config.py criado com todas as 43 chaves do DEFAULTS
[ ] scripts/migrate_env.py criado
[ ] docker compose exec tubewranglerr python3 scripts/migrate_env.py → sem erro
[ ] docker compose exec tubewranglerr pytest tests/test_config.py -v → todos passando
[ ] Nenhum os.getenv() em core/config.py
[ ] DECISIONS.md atualizado
```

---

## 4. Etapa 2 — Separação de Módulos

**Pré-requisito:** Etapa 1 testada e passando no container.

### 4.1 Ordem de criação dos módulos

Criar nesta ordem exata (cada um depende do anterior):

1. `core/state_manager.py`
2. `core/youtube_api.py`
3. `core/playlist_builder.py`
4. `core/scheduler.py`

### 4.2 Regras de extração

Cada módulo é extraído de `get_streams.py`. A lógica **não muda** — apenas:
- Remove `os.getenv()` globais → recebe `config: AppConfig` no `__init__`
- Remove imports de Flask
- Adiciona docstring de responsabilidade no topo

**Assinaturas obrigatórias:**

```python
# core/state_manager.py
class StateManager:
    def __init__(self, config: AppConfig, cache_path: Path | None = None):
        # cache_path default = Path(config.get_str("state_cache_filename"))

# core/youtube_api.py
class YouTubeAPI:
    def __init__(self, api_key: str):
        # api_key vem do chamador: config.get_str("youtube_api_key")
        # NÃO lê config internamente

# core/playlist_builder.py
class M3UGenerator:
    def __init__(self, config: AppConfig): ...

class XMLTVGenerator:
    def __init__(self, config: AppConfig): ...

# core/scheduler.py
class Scheduler:
    def __init__(self, config: AppConfig, scraper: YouTubeAPI, state: StateManager): ...

    def reload_config(self, new_config: AppConfig):
        """Atualiza config em runtime sem parar o loop."""
        self.config = new_config

    async def run(self, initial_run_delay: bool = False):
        """Loop principal. Chamado via asyncio.create_task() no lifespan."""
```

### 4.3 Validação da Etapa 2 (dentro do container)

```bash
# Teste de import de cada módulo
docker compose exec tubewranglerr python3 -c "from core.state_manager import StateManager; print('OK')"
docker compose exec tubewranglerr python3 -c "from core.youtube_api import YouTubeAPI; print('OK')"
docker compose exec tubewranglerr python3 -c "from core.playlist_builder import M3UGenerator; print('OK')"
docker compose exec tubewranglerr python3 -c "from core.scheduler import Scheduler; print('OK')"

# Testes completos
docker compose exec tubewranglerr pytest tests/test_state_manager.py -v
docker compose exec tubewranglerr pytest tests/test_youtube_api.py -v
docker compose exec tubewranglerr pytest tests/test_playlist_builder.py -v
docker compose exec tubewranglerr pytest tests/test_scheduler.py -v
```

### 4.4 Checklist Etapa 2

```
[ ] core/state_manager.py — zero os.getenv(), zero Flask
[ ] core/youtube_api.py — zero os.getenv(), zero Flask
[ ] core/playlist_builder.py — zero os.getenv(), zero Flask
[ ] core/scheduler.py — zero os.getenv(), zero Flask, expõe reload_config()
[ ] Todos os imports de módulo retornam OK no container
[ ] Todos os testes da etapa passam no container
[ ] get_streams.py original NÃO foi apagado
[ ] DECISIONS.md atualizado
```

---

## 5. Etapa 3 — Interface FastHTML

**Pré-requisito:** Etapa 2 testada e passando no container.

### 5.1 web/main.py

```python
"""
web/main.py
-----------
Responsabilidade: Entry point da aplicação.
Integra FastHTML com o core via lifespan assíncrono.
NÃO contém lógica de negócio — apenas wiring e rotas de playlists.
"""
from contextlib import asynccontextmanager
import asyncio
from fasthtml.common import *
from core.config import AppConfig
from core.state_manager import StateManager
from core.youtube_api import YouTubeAPI
from core.scheduler import Scheduler
from core.playlist_builder import M3UGenerator, XMLTVGenerator

# Instâncias do core — inicializadas no lifespan
_config: AppConfig | None = None
_state: StateManager | None = None
_scheduler: Scheduler | None = None
_m3u: M3UGenerator | None = None
_xmltv: XMLTVGenerator | None = None

@asynccontextmanager
async def lifespan(app):
    global _config, _state, _scheduler, _m3u, _xmltv

    _config  = AppConfig()
    _state   = StateManager(_config)
    _state.load_from_disk()

    scraper   = YouTubeAPI(_config.get_str("youtube_api_key"))
    _scheduler = Scheduler(_config, scraper, _state)
    _m3u      = M3UGenerator(_config)
    _xmltv    = XMLTVGenerator(_config)

    task = asyncio.create_task(_scheduler.run())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    _state.save_to_disk()

app, rt = fast_app(
    lifespan=lifespan,
    hdrs=[Link(rel="stylesheet",
               href="https://cdn.jsdelivr.net/npm/pico.css@2/css/pico.min.css")]
)
```

### 5.2 Rotas obrigatórias

| Rota | Método | Arquivo | Descrição |
|---|---|---|---|
| `/` | GET | `routes/dashboard.py` | Status geral: live/upcoming/VOD, próxima execução |
| `/config` | GET | `routes/config.py` | Formulário com abas por seção |
| `/config` | POST | `routes/config.py` | Salva, recarrega config, redireciona |
| `/channels` | GET/POST | `routes/channels.py` | Gerenciar handles e IDs |
| `/logs` | GET | `routes/logs.py` | Tail de logs via SSE |
| `/force-sync` | GET | `routes/dashboard.py` | Força execução imediata |
| `/{playlist_live_filename}` | GET | `routes/playlists.py` | M3U live |
| `/{playlist_upcoming_filename}` | GET | `routes/playlists.py` | M3U upcoming |
| `/{playlist_vod_filename}` | GET | `routes/playlists.py` | M3U VOD |
| `/{xmltv_filename}` | GET | `routes/playlists.py` | EPG XML |

### 5.3 Regra de componentes FastHTML

```python
# CORRETO — componentes são funções Python que retornam FT
def config_form(sections: dict) -> FT:
    tabs = [
        Details(Summary(name.title()),
            *[Label(row.description,
                Input(name=row.key, value=row.value,
                      type="number" if row.value_type == "int" else
                           "checkbox" if row.value_type == "bool" else "text"))
              for row in rows])
        for name, rows in sections.items()
    ]
    return Form(*tabs, Button("Salvar", type="submit"), method="post", action="/config")

# ERRADO — HTML como string
def config_form():
    return "<form>...</form>"  # PROIBIDO
```

### 5.4 Validação da Etapa 3 (dentro do container)

```bash
# Subir com reload automático para desenvolvimento
docker compose exec tubewranglerr uvicorn web.main:app \
    --host 0.0.0.0 --port 8888 --reload

# Em outro terminal — testar rotas
docker compose exec tubewranglerr pytest tests/test_web_routes.py -v

# Testar no browser
curl http://localhost:8888/
curl http://localhost:8888/config
```

### 5.5 Checklist Etapa 3

```
[ ] web/main.py com lifespan funcional
[ ] Scheduler sobe como asyncio.Task no lifespan
[ ] GET / retorna 200 com contagem de streams
[ ] GET /config retorna formulário com todas as 6 seções
[ ] POST /config salva e recarrega sem restart do processo
[ ] GET /logs com SSE funcional
[ ] Rotas de playlist retornam M3U com mimetype correto
[ ] Rota EPG retorna XML com mimetype correto
[ ] Todos os testes da etapa passam no container
[ ] DECISIONS.md atualizado
```

---

## 6. Etapa 4 — Container de Produção

**Pré-requisito:** Etapa 3 testada no container de desenvolvimento.

### 6.1 Diferença dev → produção

O `docker-compose.override.yml` (dev) monta o código como volume e usa `sleep infinity`. Em produção, o `docker-compose.yml` copia o código via `COPY . .` no Dockerfile e usa o CMD do uvicorn.

### 6.2 Teste de produção

```bash
# Remover o override temporariamente para simular produção
docker compose -f docker-compose.yml up --build -d

# Verificar health check
docker inspect tubewranglerr --format="{{.State.Health.Status}}"
# Esperado: healthy

# Testar persistência (restart não perde dados)
docker compose restart
curl http://localhost:8888/
```

### 6.3 Checklist Etapa 4

```
[ ] docker compose -f docker-compose.yml build → sem erro
[ ] docker compose -f docker-compose.yml up -d → container healthy
[ ] http://localhost:8888/ acessível externamente
[ ] Volume /data persiste após docker compose restart
[ ] config.db criado em data/ no primeiro boot
[ ] Playlists M3U acessíveis via URL
[ ] Health check retorna healthy
[ ] DECISIONS.md atualizado
```

---

## 7. Etapa 5 — smart_player.py

**Pré-requisito:** Etapas 1-4 completas.

### 7.1 Mudanças necessárias

```python
# smart_player.py

# REMOVER estas linhas:
from dotenv import load_dotenv
load_dotenv(dotenv_path=SCRIPT_DIR / ".env")
PLACEHOLDER_IMAGE_URL = os.getenv("PLACEHOLDER_IMAGE_URL", "")
SMART_PLAYER_LOG_LEVEL_STR = os.getenv("SMART_PLAYER_LOG_LEVEL", "INFO")
SMART_PLAYER_LOG_TO_FILE = os.getenv("SMART_PLAYER_LOG_TO_FILE", "true").lower() == "true"

# SUBSTITUIR por:
from core.config import AppConfig
_cfg = AppConfig()
PLACEHOLDER_IMAGE_URL    = _cfg.get_str("placeholder_image_url")
SMART_PLAYER_LOG_LEVEL_STR = _cfg.get_str("smart_player_log_level")
SMART_PLAYER_LOG_TO_FILE   = _cfg.get_bool("smart_player_log_to_file")
```

O `STATE_CACHE_PATH` deve apontar para `/data/state_cache.json`:

```python
# ANTES:
SCRIPT_DIR = Path(__file__).resolve().parent
STATE_CACHE_PATH = SCRIPT_DIR / "state_cache.json"

# DEPOIS:
STATE_CACHE_PATH = Path("/data") / _cfg.get_str("state_cache_filename")
```

### 7.2 Validação da Etapa 5

```bash
docker compose exec tubewranglerr python3 smart_player.py --help
docker compose exec tubewranglerr python3 smart_player.py \
    -i "https://www.youtube.com/watch?v=TEST"
```

### 7.3 Checklist Etapa 5

```
[ ] import load_dotenv removido
[ ] Todos os os.getenv() substituídos por AppConfig
[ ] STATE_CACHE_PATH aponta para /data/
[ ] python3 smart_player.py --help executa sem erro no container
[ ] DECISIONS.md atualizado
```

---

## 8. Testes entre Etapas

Todos os testes usam `tmp_path` do pytest para criar banco SQLite temporário — nunca afetam `/data/config.db`.

### 8.1 tests/test_config.py

```python
import pytest
from core.config import AppConfig, DEFAULTS

@pytest.fixture
def cfg(tmp_path):
    return AppConfig(db_path=tmp_path / "test.db")

def test_total_de_chaves(cfg):
    assert len(DEFAULTS) == 43

def test_todas_as_chaves_existem_no_banco(cfg):
    for key in DEFAULTS:
        assert cfg.get_raw(key) is not None

def test_get_int(cfg):
    assert cfg.get_int("scheduler_main_interval_hours") == 4

def test_get_bool_true(cfg):
    assert cfg.get_bool("enable_scheduler_active_hours") is True

def test_get_bool_false(cfg):
    cfg.update("enable_scheduler_active_hours", "false")
    assert cfg.get_bool("enable_scheduler_active_hours") is False

def test_get_list(cfg):
    assert "17" in cfg.get_list("allowed_category_ids")

def test_get_mapping(cfg):
    m = cfg.get_mapping("category_mappings")
    assert m.get("Sports") == "ESPORTES"

def test_update_persiste_entre_instancias(cfg, tmp_path):
    cfg.update("http_port", "9999")
    cfg2 = AppConfig(db_path=tmp_path / "test.db")
    assert cfg2.get_int("http_port") == 9999

def test_chave_inexistente_lanca_erro(cfg):
    with pytest.raises(KeyError):
        cfg.update("nao_existe", "valor")

def test_import_env_file(cfg, tmp_path):
    env = tmp_path / "test.env"
    env.write_text('YOUTUBE_API_KEY="chave_teste"\n')
    cfg.import_from_env_file(env)
    assert cfg.get_str("youtube_api_key") == "chave_teste"

def test_secoes_presentes(cfg):
    sections = cfg.get_all_by_section()
    for s in ("credentials","scheduler","filters","output","technical","logging"):
        assert s in sections
```

### 8.2 tests/test_state_manager.py

```python
import pytest
from datetime import datetime, timezone
from core.state_manager import StateManager
from core.config import AppConfig

@pytest.fixture
def cfg(tmp_path):
    return AppConfig(db_path=tmp_path / "cfg.db")

@pytest.fixture
def state(cfg, tmp_path):
    return StateManager(cfg, cache_path=tmp_path / "state.json")

def test_adiciona_stream(state):
    state.update_streams([{"video_id": "abc", "status": "live",
                           "channel_id": "ch1", "title_original": "Test"}])
    assert "abc" in state.streams

def test_save_load(state, tmp_path, cfg):
    state.update_streams([{"video_id": "xyz", "status": "upcoming",
                           "channel_id": "ch2", "title_original": "Test2",
                           "fetch_time": datetime.now(timezone.utc)}])
    state.save_to_disk()
    s2 = StateManager(cfg, cache_path=tmp_path / "state.json")
    assert s2.load_from_disk() is True
    assert "xyz" in s2.streams
```

### 8.3 tests/test_web_routes.py

```python
import pytest
from fasthtml.testclient import TestClient
from web.main import app

@pytest.fixture(scope="module")
def client():
    return TestClient(app)

def test_dashboard(client):
    assert client.get("/").status_code == 200

def test_config_get(client):
    r = client.get("/config")
    assert r.status_code == 200
    assert "credentials" in r.text.lower() or "youtube" in r.text.lower()

def test_config_post(client):
    r = client.post("/config", data={"http_port": "9000"})
    assert r.status_code in (200, 302)

def test_playlist_live(client):
    r = client.get("/playlist_live.m3u8")
    assert r.status_code == 200
    assert "#EXTM3U" in r.text

def test_epg(client):
    r = client.get("/youtube_epg.xml")
    assert r.status_code == 200
    assert "<?xml" in r.text
```

---

## 9. Revisão Final de Migração

**Executar dentro do container:**

```bash
# 1. Verificar eliminação de dependências antigas
docker compose exec tubewranglerr grep -r "os.getenv" core/ web/ --include="*.py"
docker compose exec tubewranglerr grep -r "load_dotenv\|from dotenv" core/ web/ --include="*.py"
docker compose exec tubewranglerr grep -r "from flask\|import Flask" core/ web/ --include="*.py"
# Esperado: nenhuma saída em todos

# 2. Rodar suite completa de testes
docker compose exec tubewranglerr pytest tests/ -v --tb=short

# 3. Verificar cobertura funcional (todos devem importar sem erro)
docker compose exec tubewranglerr python3 -c "
from core.config import AppConfig
from core.state_manager import StateManager
from core.youtube_api import YouTubeAPI
from core.playlist_builder import M3UGenerator, XMLTVGenerator
from core.scheduler import Scheduler
print('✅ Todos os módulos importados com sucesso')
"
```

### 9.1 Checklist funcional completo

```
[ ] resolve_channel_handles_to_ids() → core/youtube_api.py
[ ] fetch_all_streams_for_channels_using_playlists() → core/youtube_api.py
[ ] fetch_streams_by_ids() → core/youtube_api.py
[ ] prune_ended_streams() → core/state_manager.py
[ ] save_to_disk() / load_from_disk() → core/state_manager.py
[ ] M3UGenerator.generate_playlist() para live/upcoming/vod → core/playlist_builder.py
[ ] XMLTVGenerator.generate_xml() → core/playlist_builder.py
[ ] Scheduler.run() com pre/pos-evento e full sync → core/scheduler.py
[ ] Scheduler.reload_config() → core/scheduler.py
[ ] Todas as 43 variáveis editáveis via GET /config
[ ] POST /config aplica sem restart
[ ] Playlists servidas nas URLs corretas
[ ] smart_player.py sem load_dotenv
```

### 9.2 Arquivamento dos originais

```bash
mkdir -p _archive
cp get_streams.py _archive/get_streams.py.bak
cp file.env _archive/file.env.bak
# NÃO apagar — manter como referência histórica
```

Registrar no `DECISIONS.md`:
- Data do arquivamento
- SHA256 dos arquivos: `sha256sum get_streams.py file.env`
- Confirmação de que todos os checklists estão `[x]`

---

## 10. Protocolo DECISIONS.md

### Template inicial

```markdown
# DECISIONS.md — TubeWranglerr Refactoring Log

Gerado por: GitHub Copilot Agent
Início: [DATA_HORA]
Referência: REFACTORING_TUBEWRANGLERR.md v2.0
Ambiente: Container Docker (python:3.12-slim) no Debian via VS Code SSH

***

## Status Geral

| Etapa | Status | Conclusão |
|---|---|---|
| 0 — Container de desenvolvimento | ⬜ | |
| 1 — core/config.py | ⬜ | |
| 2 — Separação de módulos | ⬜ | |
| 3 — Interface FastHTML | ⬜ | |
| 4 — Container de produção | ⬜ | |
| 5 — smart_player.py | ⬜ | |
| Revisão Final | ⬜ | |

Legenda: ⬜ Pendente | ⏳ Em progresso | ✅ Concluído | 🔴 Bloqueado

***

## Decisões

### [ETAPA-0] [DATA] Decisão de arquitetura container-first
**Contexto:** Ambiente é VS Code Windows → SSH → Debian → Docker
**Decisão:** Nenhum Python roda no host. Todo comando via docker compose exec.
**Impacto:** Todos os testes e scripts usam o prefixo docker compose exec tubewranglerr

***

## Dúvidas e Bloqueios

[Preencher quando surgirem]

***

## Revisão Final

[Preenchido pelo agente na Etapa 9]
```

### Regras

```
OBRIGATÓRIO: Criar antes de qualquer arquivo de código
OBRIGATÓRIO: Registrar toda decisão não explícita no documento
OBRIGATÓRIO: Atualizar tabela de Status ao concluir cada etapa
PROIBIDO: Marcar ✅ sem todos os itens do checklist serem [x]
PROIBIDO: Apagar entradas antigas
```

---

## Apêndice — .github/copilot-instructions.md

```markdown
# Copilot Instructions — TubeWranglerr

## Ambiente
- VS Code Windows conectado via SSH a servidor Debian com Docker
- TODO código Python executa DENTRO do container Docker
- Nunca instalar Python ou pip no host Debian
- Comando padrão: docker compose exec tubewranglerr <comando>

## Regras de Código
- Nunca usar os.getenv() — sempre AppConfig de core/config.py
- Nunca importar Flask — projeto usa FastHTML
- Lógica de negócio fica em core/ — zero lógica em web/
- AppConfig é sempre injetado como parâmetro, nunca global
- Type hints em todas as funções públicas
- Docstring de responsabilidade em todo arquivo novo

## Stack
- Web: python-fasthtml (FastHTML + HTMX nativo)
- DB config: fastlite (SQLite)
- Async: asyncio nativo
- YouTube: google-api-python-client
- Testes: pytest + pytest-asyncio

## Estrutura
- core/ → lógica pura, sem dependências web
- web/ → rotas e componentes FastHTML apenas
- data/ → volume Docker, nunca versionar
- tests/ → pytest, usa tmp_path para banco isolado

## Commits
- Apenas após testes da etapa passarem no container
- docker compose exec tubewranglerr pytest tests/ -v deve retornar 0
```

---

*Versão 2.0 — Abordagem container-first. Todo desenvolvimento e teste ocorre exclusivamente dentro do container Docker.*
