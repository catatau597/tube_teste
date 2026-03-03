# REFACTORING_TUBEWRANGLERR.md

> **Versão:** 3.0  
> **Projeto:** TubeWranglerr  
> **Destino:** Agente autônomo GitHub Copilot  
> **Objetivo:** Refatoração completa para stack FastHTML + SQLite em container standalone  
> **Abordagem:** Container-First Development  
> **Changelog v3.0:** Incorpora lições aprendidas da execução real (DECISIONS.md v1):
> - Corrige acesso a rows do fastlite (dicionário, não atributo)
> - Esclarece ordem correta das etapas (Docker ANTES do código)
> - Elimina ambiguidade sobre ambiente de execução (container vs host)
> - Corrige duplicação de entradas no DECISIONS.md
> - Adiciona pré-requisitos de sistema operacional explícitos

---

## ⚠️ LEIA ANTES DE QUALQUER AÇÃO

Este documento é a **única fonte de verdade** para o agente. Toda decisão deve ser tomada com base nele.

**REGRAS FUNDAMENTAIS:**
1. O host Debian é apenas um sistema de arquivos — Python executa **dentro do container**
2. A Etapa 0 (container) é **obrigatória antes de qualquer código**
3. O fastlite retorna rows como **dicionários** — nunca acessar como `row.key`, sempre `row["key"]`
4. O diretório `/data` no container é um volume — criar via `docker compose` e nunca via `mkdir` no host
5. Em caso de dúvida, registrar em DECISIONS.md e aguardar — nunca assumir silenciosamente

---

## 📋 ÍNDICE

0. [Etapa 0 — Container de Desenvolvimento](#etapa-0--container-de-desenvolvimento)
1. [Regras Absolutas do Agente](#1-regras-absolutas-do-agente)
2. [Estrutura Final do Projeto](#2-estrutura-final-do-projeto)
3. [Etapa 1 — core/config.py](#3-etapa-1--coreconfigpy)
4. [Etapa 2 — Separação de Módulos](#4-etapa-2--separação-de-módulos)
5. [Etapa 3 — Interface FastHTML](#5-etapa-3--interface-fasthtml)
6. [Etapa 4 — Container de Produção](#6-etapa-4--container-de-produção)
7. [Etapa 5 — smart_player.py](#7-etapa-5--smart_playerpy)
8. [Testes entre Etapas](#8-testes-entre-etapas)
9. [Revisão Final de Migração](#9-revisão-final-de-migração)
10. [Protocolo DECISIONS.md](#10-protocolo-decisionsmd)

---

## Etapa 0 — Container de Desenvolvimento

**PRIMEIRA ETAPA. Nada de código de negócio antes desta estar 100% completa.**

### Lição aprendida (v1)
Na primeira execução, o agente tentou rodar código no host Debian, instalou pip e criou
venv diretamente no sistema, e criou `/data` com `sudo mkdir` em vez de usar o volume Docker.
Isso gerou inconsistências de ambiente. A v3.0 previne isso explicitamente.

### 0.1 Pré-requisitos no host Debian (verificar antes de criar qualquer arquivo)

O agente deve rodar estes comandos no terminal SSH do VS Code e confirmar as saídas:

```bash
# Verificar Docker disponível
docker --version
# Esperado: Docker version 24.x ou superior

docker compose version
# Esperado: Docker Compose version v2.x

# Verificar que o usuário tem permissão para Docker sem sudo
docker ps
# Esperado: lista de containers (mesmo vazia), SEM erro de permissão

# Se houver erro de permissão:
sudo usermod -aG docker $USER
# Após isso, fazer logout e login novamente no SSH
```

Se qualquer verificação falhar, registrar em DECISIONS.md e aguardar instrução.
**NÃO instalar Docker no host — ele deve estar pré-instalado.**

### 0.2 requirements.txt

```txt
python-fasthtml>=0.12.0
fastlite>=0.0.9
google-api-python-client>=2.0.0
pytz>=2024.1
pytest>=8.0.0
pytest-asyncio>=0.23.0
httpx>=0.27.0
```

**PROIBIDO adicionar:**
```txt
# Flask        — removido intencionalmente
# python-dotenv — removido intencionalmente
# Werkzeug      — removido intencionalmente
```

### 0.3 Dockerfile

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    fonts-dejavu-core \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código copiado em produção; montado como volume em dev
COPY . .

# Garante diretórios de dados dentro do container
RUN mkdir -p /data/m3us /data/epgs /data/logs

VOLUME ["/data"]
EXPOSE 8888

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8888/')"

CMD ["python3", "-m", "uvicorn", "web.main:app", "--host", "0.0.0.0", "--port", "8888"]
```

### 0.4 docker-compose.yml (produção)

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

### 0.5 docker-compose.override.yml (desenvolvimento)

```yaml
# Carregado automaticamente em desenvolvimento.
# Monta código como volume — edições no VS Code refletem imediatamente.
# NÃO usar em produção.
services:
  tubewranglerr:
    volumes:
      - .:/app        # código montado — sem rebuild a cada edição
      - ./data:/data  # dados persistentes
    command: sleep infinity   # container fica vivo para exec de comandos
    environment:
      - PYTHONUNBUFFERED=1
      - PYTHONDONTWRITEBYTECODE=1
```

### 0.6 .gitignore

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
docker-compose.override.yml
```

### 0.7 Sequência de inicialização (executar UMA ÚNICA VEZ)

```bash
# 1. Build da imagem
docker compose build

# 2. Subir container de desenvolvimento
docker compose up -d

# 3. Confirmar container rodando
docker compose ps
# Esperado: tubewranglerr   running

# 4. Confirmar Python 3.12 dentro do container
docker compose exec tubewranglerr python3 --version
# Esperado: Python 3.12.x

# 5. Confirmar dependências instaladas
docker compose exec tubewranglerr pip list | grep -E "fasthtml|fastlite|pytest"
# Esperado: linhas com python-fasthtml, fastlite, pytest

# 6. Confirmar ffmpeg
docker compose exec tubewranglerr ffmpeg -version
# Esperado: ffmpeg version ...

# 7. Confirmar que /data está montado e com permissão
docker compose exec tubewranglerr ls -la /data
# Esperado: diretórios m3us, epgs, logs
```

### 0.8 Padrão de execução — TODA execução Python usa este formato

```bash
# ✅ CORRETO
docker compose exec tubewranglerr python3 scripts/migrate_env.py
docker compose exec tubewranglerr pytest tests/test_config.py -v
docker compose exec tubewranglerr pytest tests/ -v
docker compose exec tubewranglerr bash   # shell interativo para debug

# ❌ ERRADO — nunca fazer
python3 scripts/migrate_env.py          # executa no host
pip install python-fasthtml             # instala no host
sudo mkdir /data                        # cria fora do volume Docker
```

### 0.9 Checklist Etapa 0

```
[ ] requirements.txt criado (sem Flask, sem python-dotenv)
[ ] Dockerfile criado
[ ] docker-compose.yml criado
[ ] docker-compose.override.yml criado
[ ] .gitignore criado
[ ] docker --version retorna 24.x+
[ ] docker compose build executa sem erro
[ ] docker compose up -d sobe sem erro
[ ] docker compose ps mostra tubewranglerr running
[ ] docker compose exec tubewranglerr python3 --version retorna 3.12.x
[ ] docker compose exec tubewranglerr pip list mostra fasthtml e fastlite
[ ] docker compose exec tubewranglerr ls -la /data mostra diretórios criados
[ ] DECISIONS.md criado e atualizado (tabela de status + decisão de ambiente)
```

---

## 1. Regras Absolutas do Agente

### 🚫 PROIBIÇÕES

```
PROIBIDO: Instalar Python, pip, venv ou qualquer pacote no host Debian
PROIBIDO: Executar python3, pytest ou pip fora de "docker compose exec tubewranglerr"
PROIBIDO: Criar /data com mkdir no host — o volume Docker cria automaticamente
PROIBIDO: Usar os.getenv() ou load_dotenv() em qualquer arquivo novo
PROIBIDO: Importar Flask em qualquer arquivo novo
PROIBIDO: Criar variáveis globais de configuração lidas de ambiente
PROIBIDO: Acessar rows do fastlite como atributos (row.key) — usar sempre row["key"]
PROIBIDO: Usar threading.Thread para o servidor web
PROIBIDO: Misturar lógica de negócio dentro de rotas FastHTML
PROIBIDO: Apagar arquivos originais antes da Etapa 9
PROIBIDO: Fazer commit sem testes passando no container
PROIBIDO: Duplicar entradas no DECISIONS.md — cada decisão tem registro único
```

### ✅ OBRIGAÇÕES

```
OBRIGATÓRIO: Criar DECISIONS.md antes de qualquer arquivo de código
OBRIGATÓRIO: Toda execução Python via docker compose exec tubewranglerr
OBRIGATÓRIO: Registrar toda decisão não explícita neste documento no DECISIONS.md
OBRIGATÓRIO: AppConfig passado como parâmetro — nunca importado como singleton global
OBRIGATÓRIO: Acessar campos de rows fastlite como dicionário: row["key"], row["value"]
OBRIGATÓRIO: Todo arquivo novo começa com docstring de responsabilidade
OBRIGATÓRIO: Type hints em todas as funções públicas
OBRIGATÓRIO: Testes de cada etapa passam no container antes de avançar
OBRIGATÓRIO: Atualizar tabela de Status no DECISIONS.md ao concluir cada etapa
```

### 📐 Acesso correto ao fastlite — regra crítica

```python
# ✅ CORRETO — rows são dicionários
for row in self._db.t.config.rows:
    key   = row["key"]
    value = row["value"]
    section = row["section"]

# ✅ CORRETO — cache usa row["key"] como chave
self._cache = {row["key"]: row for row in self._db.t.config.rows}

# ✅ CORRETO — acessar do cache
value = self._cache["http_port"]["value"]

# ❌ ERRADO — fastlite NÃO retorna objetos com atributos
value = row.value        # AttributeError
key   = row.key          # AttributeError
self._cache[row.key]     # AttributeError
```

### 📐 Injeção de dependência — padrão obrigatório

```python
# ✅ CORRETO
class Scheduler:
    def __init__(self, config: AppConfig, scraper: YouTubeAPI, state: StateManager):
        self.config = config

# ❌ ERRADO
INTERVAL = int(os.getenv("SCHEDULER_MAIN_INTERVAL_HOURS", 4))  # PROIBIDO
```

---

## 2. Estrutura Final do Projeto

```
tubewranglerr/
│
├── core/
│   ├── __init__.py
│   ├── config.py              # AppConfig + SQLite (substitui .env)
│   ├── state_manager.py       # StateManager
│   ├── youtube_api.py         # YouTubeAPI (APIScraper)
│   ├── playlist_builder.py    # M3UGenerator + XMLTVGenerator + ContentGenerator
│   └── scheduler.py           # Scheduler + save_loop
│
├── web/
│   ├── __init__.py
│   ├── main.py                # FastHTML app + lifespan
│   └── routes/
│       ├── __init__.py
│       ├── dashboard.py       # GET / + GET /force-sync
│       ├── config.py          # GET/POST /config
│       ├── channels.py        # GET/POST /channels
│       ├── logs.py            # GET /logs (SSE)
│       └── playlists.py       # M3U + EPG endpoints
│
├── scripts/
│   └── migrate_env.py         # Migração única .env → SQLite
│
├── tests/
│   ├── test_config.py
│   ├── test_state_manager.py
│   ├── test_youtube_api.py
│   ├── test_playlist_builder.py
│   ├── test_scheduler.py
│   └── test_web_routes.py
│
├── _archive/                  # Criado na Etapa 9 — não versionar
│   ├── get_streams.py.bak
│   └── file.env.bak
│
├── data/                      # Volume Docker — NUNCA versionar
│   ├── config.db
│   ├── m3us/
│   ├── epgs/
│   └── logs/
│
├── smart_player.py            # Mantido na raiz
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

## 3. Etapa 1 — core/config.py

**Pré-requisito:** Etapa 0 com checklist 100% completo.

### 3.1 Lição aprendida (v1)

O agente da v1 descobriu que o fastlite retorna rows como dicionários, não objetos com
atributos. O código abaixo já incorpora essa correção. **Não alterar o padrão de acesso.**

### 3.2 core/config.py completo e correto

```python
"""
core/config.py
--------------
Responsabilidade: Única fonte de verdade para configurações da aplicação.
Substitui completamente o arquivo .env e todos os os.getenv() do projeto.
Depende de: fastlite (SQLite)
NÃO depende de: Flask, FastHTML, os.getenv, python-dotenv

ATENÇÃO: fastlite retorna rows como dicionários.
Sempre acessar como row["key"], NUNCA como row.key
"""

from pathlib import Path
from fastlite import database

DB_PATH = Path("/data/config.db")

# Todas as 43 variáveis do file.env original
# Formato: "chave": ("default", "seção", "descrição", "tipo")
# tipos: "str" | "int" | "bool" | "list" | "mapping"
DEFAULTS: dict = {
    # --- Credenciais (3) ---
    "youtube_api_key":               ("", "credentials", "Chave de API do YouTube", "str"),
    "target_channel_handles":        ("", "credentials", "Handles de canais separados por vírgula", "list"),
    "target_channel_ids":            ("", "credentials", "IDs diretos de canais separados por vírgula", "list"),

    # --- Agendador (10) ---
    "scheduler_main_interval_hours":         ("4",  "scheduler", "Intervalo principal em horas", "int"),
    "scheduler_pre_event_window_hours":      ("2",  "scheduler", "Janela pré-evento em horas", "int"),
    "scheduler_pre_event_interval_minutes":  ("5",  "scheduler", "Intervalo pré-evento em minutos", "int"),
    "scheduler_post_event_interval_minutes": ("5",  "scheduler", "Intervalo pós-evento em minutos", "int"),
    "enable_scheduler_active_hours":         ("true","scheduler", "Ativar horário de atividade", "bool"),
    "scheduler_active_start_hour":           ("7",  "scheduler", "Hora de início (formato 24h)", "int"),
    "scheduler_active_end_hour":             ("22", "scheduler", "Hora de fim (formato 24h)", "int"),
    "full_sync_interval_hours":              ("48", "scheduler", "Intervalo de full sync em horas", "int"),
    "resolve_handles_ttl_hours":             ("24", "scheduler", "TTL cache de handles em horas", "int"),
    "initial_sync_days":                     ("2",  "scheduler", "Dias para busca inicial (0=tudo)", "int"),

    # --- Filtros (13) ---
    "max_schedule_hours":            ("72",  "filters", "Limite futuro em horas para agendamentos", "int"),
    "max_upcoming_per_channel":      ("6",   "filters", "Máximo de agendamentos futuros por canal", "int"),
    "title_filter_expressions":      ("ao vivo,AO VIVO,AO VIVO E COM IMAGRENS,ao vivo e com imagens,com imagens,COM IMAGRENS,cortes,react,ge.globo,#live,!,:,ge tv,JOGO COMPLETO",
                                      "filters", "Expressões a remover dos títulos (vírgula)", "list"),
    "prefix_title_with_channel_name":("true","filters", "Prefixar título com nome do canal", "bool"),
    "prefix_title_with_status":      ("true","filters", "Prefixar título com status [Ao Vivo] etc", "bool"),
    "category_mappings":             ("Sports|ESPORTES,Gaming|JOGOS,People & Blogs|ESPORTES,News & Politics|NOTICIAS",
                                      "filters", "Mapeamento categorias API|Exibição (vírgula)", "mapping"),
    "channel_name_mappings":         ("FAF TV | @fafalagoas|FAF TV,Canal GOAT|GOAT,Federação de Futebol de Mato Grosso do Sul|FFMS,Federação Paranaense de Futebol|FPF TV,Federação Catarinense de Futebol|FCF TV,Jovem Pan Esportes|J. Pan Esportes,TNT Sports Brasil|TNT Sports",
                                      "filters", "Mapeamento nomes canais Longo|Curto (vírgula)", "mapping"),
    "epg_description_cleanup":       ("true","filters", "Manter apenas primeiro parágrafo da descrição EPG", "bool"),
    "filter_by_category":            ("true","filters", "Filtrar streams por categoria da API", "bool"),
    "allowed_category_ids":          ("17",  "filters", "IDs de categoria permitidos (vírgula). 17=Sports", "list"),
    "keep_recorded_streams":         ("true","filters", "Manter streams gravados (ex-live) no cache", "bool"),
    "max_recorded_per_channel":      ("2",   "filters", "Máximo de gravações mantidas por canal", "int"),
    "recorded_retention_days":       ("2",   "filters", "Dias de retenção de streams gravados", "int"),

    # --- Saída (8) ---
    "playlist_save_directory":       ("/data/m3us",          "output", "Diretório para salvar playlists M3U", "str"),
    "playlist_live_filename":        ("playlist_live.m3u8",  "output", "Nome do arquivo M3U de lives", "str"),
    "playlist_upcoming_filename":    ("playlist_upcoming.m3u8","output","Nome do arquivo M3U de agendados", "str"),
    "playlist_vod_filename":         ("playlist_vod.m3u8",   "output", "Nome do arquivo M3U de gravados", "str"),
    "xmltv_save_directory":          ("/data/epgs",          "output", "Diretório para salvar EPG XML", "str"),
    "xmltv_filename":                ("youtube_epg.xml",     "output", "Nome do arquivo EPG XMLTV", "str"),
    "placeholder_image_url":         ("https://i.ibb.co/9kZStw28/placeholder-sports.png",
                                      "output", "URL da imagem placeholder para streams sem thumb", "str"),
    "use_invisible_placeholder":     ("true", "output", "Usar placeholder invisível no M3U", "bool"),

    # --- Técnico (5) ---
    "http_port":                     ("8888",             "technical", "Porta HTTP do servidor web", "int"),
    "state_cache_filename":          ("state_cache.json", "technical", "Nome do arquivo JSON de estado", "str"),
    "stale_hours":                   ("6",                "technical", "Horas para considerar stream stale", "int"),
    "use_playlist_items":            ("true",             "technical", "Usar playlistItems API (vs search.list)", "bool"),
    "local_timezone":                ("America/Sao_Paulo","technical", "Fuso horário local (pytz)", "str"),

    # --- Logs (4) ---
    "log_level":                     ("INFO", "logging", "Nível de log do core (DEBUG/INFO/WARNING/ERROR)", "str"),
    "log_to_file":                   ("true", "logging", "Salvar log do core em arquivo", "bool"),
    "smart_player_log_level":        ("INFO", "logging", "Nível de log do smart_player", "str"),
    "smart_player_log_to_file":      ("true", "logging", "Salvar log do smart_player em arquivo", "bool"),
}


class AppConfig:
    """
    Gerenciador de configuração persistente em SQLite via fastlite.

    IMPORTANTE: fastlite retorna rows como dicionários.
    Sempre usar row["key"], NUNCA row.key
    """

    def __init__(self, db_path: Path = DB_PATH):
        self._db = database(db_path)
        self._ensure_table()
        self._cache: dict = {}
        self.reload()

    def _ensure_table(self):
        if "config" not in self._db.t:
            self._db.t.config.create(
                key=str, value=str, section=str,
                description=str, value_type=str,
                pk="key"
            )
        existing = {row["key"] for row in self._db.t.config.rows}
        for key, (default_val, section, desc, vtype) in DEFAULTS.items():
            if key not in existing:
                self._db.t.config.insert({
                    "key": key,
                    "value": default_val,
                    "section": section,
                    "description": desc,
                    "value_type": vtype,
                })

    def reload(self):
        """Recarrega todas as configs do banco. Chamar após POST /config."""
        self._cache = {row["key"]: row for row in self._db.t.config.rows}

    def get_raw(self, key: str) -> str:
        if key in self._cache:
            return self._cache[key]["value"]
        return DEFAULTS.get(key, ("",))[0]

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
        """Atualiza chave no banco e no cache. Efeito imediato."""
        if key not in self._cache:
            raise KeyError(f"Chave de configuração desconhecida: '{key}'")
        self._db.t.config.update({"key": key, "value": str(value)})
        self._cache[key]["value"] = str(value)

    def update_many(self, updates: dict):
        """Atualiza múltiplas chaves. Útil para POST /config."""
        for key, value in updates.items():
            self.update(key, str(value))

    def get_all_by_section(self) -> dict:
        """Retorna configurações agrupadas por seção. Usado pelo formulário web."""
        sections: dict = {}
        for row in self._cache.values():
            sections.setdefault(row["section"], []).append(row)
        return sections

    def import_from_env_file(self, env_path: Path):
        """
        Migração única: importa valores de um .env para o SQLite.
        Mapeia UPPER_SNAKE_CASE → lower_snake_case automaticamente.
        Usar apenas via scripts/migrate_env.py
        """
        if not env_path.exists():
            print(f"AVISO: {env_path} não encontrado. Nenhum valor importado.")
            return
        mapping = {k.upper(): k for k in DEFAULTS.keys()}
        imported = 0
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
                        imported += 1
                    except KeyError:
                        pass
        print(f"✅ {imported} valores importados de {env_path}")
```

### 3.3 scripts/migrate_env.py

```python
"""
scripts/migrate_env.py
----------------------
Migração única: importa file.env → /data/config.db (SQLite).
Executar UMA VEZ após o container subir pela primeira vez.

Comando:
    docker compose exec tubewranglerr python3 scripts/migrate_env.py
"""
from pathlib import Path
import sys
sys.path.insert(0, "/app")

from core.config import AppConfig

if __name__ == "__main__":
    print("Iniciando migração .env → SQLite...")
    cfg = AppConfig()
    env_path = Path("/app/file.env")
    cfg.import_from_env_file(env_path)

    print("\nConfiguração atual por seção:")
    for section, rows in cfg.get_all_by_section().items():
        print(f"\n[{section}]")
        for row in rows:
            # row é dicionário: row["key"], row["value"]
            val = row["value"]
            display = val[:60] + "..." if len(val) > 60 else val
            print(f"  {row[\"key\"]} = {display}")
```

### 3.4 Validação da Etapa 1

```bash
# Migração do .env original
docker compose exec tubewranglerr python3 scripts/migrate_env.py

# Testes unitários
docker compose exec tubewranglerr pytest tests/test_config.py -v

# Verificação rápida — deve imprimir as 6 seções
docker compose exec tubewranglerr python3 -c "
from core.config import AppConfig
cfg = AppConfig()
sections = cfg.get_all_by_section()
print(f'Seções: {list(sections.keys())}')
print(f'Total de chaves: {sum(len(v) for v in sections.values())}')
assert sum(len(v) for v in sections.values()) == 43, 'ERRO: 43 chaves esperadas!'
print('✅ AppConfig OK')
"
```

### 3.5 Checklist Etapa 1

```
[ ] core/__init__.py criado
[ ] core/config.py criado — usa row["key"] em todo acesso ao fastlite
[ ] Todas as 43 chaves presentes no DEFAULTS (conferir seção por seção)
[ ] scripts/migrate_env.py criado
[ ] docker compose exec tubewranglerr python3 scripts/migrate_env.py → 43 valores importados
[ ] docker compose exec tubewranglerr pytest tests/test_config.py -v → 100% passando
[ ] Verificação rápida retorna "43 chaves" e "✅ AppConfig OK"
[ ] Nenhum os.getenv() em core/config.py
[ ] DECISIONS.md atualizado com status ✅ e data
```

---

## 4. Etapa 2 — Separação de Módulos

**Pré-requisito:** Checklist da Etapa 1 completo.

### 4.1 Estratégia de extração

As classes já existem no `get_streams.py`. O trabalho é:
1. Criar arquivo com docstring de responsabilidade
2. Copiar a classe
3. Substituir globals de configuração por `self.config.get_*()`
4. Remover imports de Flask
5. Rodar teste no container

### 4.2 Ordem obrigatória de criação

```
1. core/state_manager.py   (sem dependências de outros módulos core)
2. core/youtube_api.py     (sem dependências de outros módulos core)
3. core/playlist_builder.py (depende de config apenas)
4. core/scheduler.py       (depende dos 3 anteriores)
```

### 4.3 Assinaturas obrigatórias

```python
# core/state_manager.py
class StateManager:
    def __init__(self, config: AppConfig, cache_path: Path | None = None):
        # Se cache_path for None, usar:
        # Path("/data") / config.get_str("state_cache_filename")
        ...

# core/youtube_api.py
class YouTubeAPI:
    def __init__(self, api_key: str):
        # api_key vem do CHAMADOR: config.get_str("youtube_api_key")
        # Este módulo NÃO lê config internamente
        ...

# core/playlist_builder.py
class M3UGenerator:
    def __init__(self, config: AppConfig): ...

class XMLTVGenerator:
    def __init__(self, config: AppConfig): ...

class ContentGenerator:
    def __init__(self, config: AppConfig): ...

# core/scheduler.py
class Scheduler:
    def __init__(self, config: AppConfig, scraper: YouTubeAPI, state: StateManager): ...

    def reload_config(self, new_config: AppConfig):
        """Atualiza config em runtime sem parar o loop."""
        self.config = new_config

    async def run(self, initial_run_delay: bool = False):
        """Loop principal — chamado via asyncio.create_task() no lifespan."""
        ...
```

### 4.4 Globals a eliminar de cada módulo

Ao extrair cada classe, remover estes padrões e substituir por `self.config.get_*()`:

```python
# Remover — são globals de configuração lidos do .env
STALE_HOURS = int(os.getenv(...))
KEEP_RECORDED_STREAMS = os.getenv(...)
MAX_RECORDED_PER_CHANNEL = int(os.getenv(...))
SCHEDULER_MAIN_INTERVAL_HOURS = int(os.getenv(...))
TITLE_FILTER_EXPRESSIONS = [...]
CATEGORY_MAPPINGS = {...}
CHANNEL_NAME_MAPPINGS = {...}
# ... e todos os demais os.getenv() do módulo
```

### 4.5 Validação da Etapa 2

```bash
# Teste de import de cada módulo individualmente
docker compose exec tubewranglerr python3 -c "from core.state_manager import StateManager; print('state_manager ✅')"
docker compose exec tubewranglerr python3 -c "from core.youtube_api import YouTubeAPI; print('youtube_api ✅')"
docker compose exec tubewranglerr python3 -c "from core.playlist_builder import M3UGenerator, XMLTVGenerator; print('playlist_builder ✅')"
docker compose exec tubewranglerr python3 -c "from core.scheduler import Scheduler; print('scheduler ✅')"

# Suite de testes da etapa
docker compose exec tubewranglerr pytest tests/test_state_manager.py tests/test_youtube_api.py tests/test_playlist_builder.py tests/test_scheduler.py -v

# Verificação zero os.getenv nos módulos novos
docker compose exec tubewranglerr grep -r "os.getenv" core/ --include="*.py"
# Esperado: nenhuma saída
```

### 4.6 Checklist Etapa 2

```
[ ] core/state_manager.py — zero os.getenv(), zero Flask
[ ] core/youtube_api.py — zero os.getenv(), zero Flask
[ ] core/playlist_builder.py — zero os.getenv(), zero Flask
[ ] core/scheduler.py — zero os.getenv(), zero Flask, expõe reload_config()
[ ] Todos os imports retornam ✅ no container
[ ] Todos os testes da etapa passam no container
[ ] grep os.getenv core/ retorna vazio
[ ] get_streams.py original NÃO foi apagado
[ ] DECISIONS.md atualizado
```

---

## 5. Etapa 3 — Interface FastHTML

**Pré-requisito:** Checklist da Etapa 2 completo.

### 5.1 web/main.py

```python
"""
web/main.py
-----------
Responsabilidade: Entry point da aplicação FastHTML.
Gerencia lifespan (startup/shutdown) e instâncias do core.
NÃO contém lógica de negócio.
"""
from contextlib import asynccontextmanager
import asyncio
from fasthtml.common import *
from core.config import AppConfig
from core.state_manager import StateManager
from core.youtube_api import YouTubeAPI
from core.scheduler import Scheduler
from core.playlist_builder import M3UGenerator, XMLTVGenerator

# Instâncias do core — acessíveis pelas rotas
_config: AppConfig | None = None
_state: StateManager | None = None
_scheduler: Scheduler | None = None
_m3u: M3UGenerator | None = None
_xmltv: XMLTVGenerator | None = None

@asynccontextmanager
async def lifespan(app):
    global _config, _state, _scheduler, _m3u, _xmltv

    _config    = AppConfig()
    _state     = StateManager(_config)
    _state.load_from_disk()
    scraper    = YouTubeAPI(_config.get_str("youtube_api_key"))
    _scheduler = Scheduler(_config, scraper, _state)
    _m3u       = M3UGenerator(_config)
    _xmltv     = XMLTVGenerator(_config)

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
| `/` | GET | `routes/dashboard.py` | Status: live/upcoming/VOD, próxima execução |
| `/config` | GET | `routes/config.py` | Formulário com abas por seção |
| `/config` | POST | `routes/config.py` | Salva, recarrega, redireciona |
| `/channels` | GET/POST | `routes/channels.py` | Gerenciar handles e IDs |
| `/logs` | GET | `routes/logs.py` | Tail de logs via SSE |
| `/force-sync` | GET | `routes/dashboard.py` | Força execução imediata |
| `/{playlist_live_filename}` | GET | `routes/playlists.py` | M3U live |
| `/{playlist_upcoming_filename}` | GET | `routes/playlists.py` | M3U upcoming |
| `/{playlist_vod_filename}` | GET | `routes/playlists.py` | M3U VOD |
| `/{xmltv_filename}` | GET | `routes/playlists.py` | EPG XML |

### 5.3 Componentes FastHTML — padrão obrigatório

```python
# ✅ CORRETO — componente retorna FT (FastHTML type)
def config_form(sections: dict) -> FT:
    tabs = [
        Details(
            Summary(name.title()),
            *[Label(
                row["description"],
                Input(
                    name=row["key"],
                    value=row["value"],
                    type="number"   if row["value_type"] == "int"  else
                         "checkbox" if row["value_type"] == "bool" else "text"
                )
              )
              for row in rows]
        )
        for name, rows in sections.items()
    ]
    return Form(*tabs, Button("Salvar", type="submit"), method="post", action="/config")

# ❌ ERRADO
def config_form():
    return "<form>...</form>"  # PROIBIDO — HTML como string
```

### 5.4 POST /config — padrão de salvamento

```python
# web/routes/config.py
@rt("/config", methods=["POST"])
async def save_config(request):
    form = await request.form()
    updates = {k: v for k, v in form.items() if k in DEFAULTS}
    _config.update_many(updates)
    _config.reload()
    _scheduler.reload_config(_config)  # aplica sem restart
    return RedirectResponse("/config", status_code=303)
```

### 5.5 SSE para logs

```python
# web/routes/logs.py
import asyncio
from pathlib import Path
from fasthtml.common import *

async def log_generator(log_path: Path):
    """Tail assíncrono de arquivo de log via SSE."""
    with open(log_path, "r", encoding="utf-8") as f:
        f.seek(0, 2)  # vai para o fim
        while True:
            line = f.readline()
            if line:
                yield f"data: {line.strip()}\n\n"
            else:
                await asyncio.sleep(1)
```

### 5.6 Validação da Etapa 3

```bash
# Subir com reload automático (dev)
docker compose exec tubewranglerr uvicorn web.main:app \
    --host 0.0.0.0 --port 8888 --reload &

# Testar rotas
docker compose exec tubewranglerr pytest tests/test_web_routes.py -v

# Teste manual
curl http://localhost:8888/
curl http://localhost:8888/config
curl http://localhost:8888/playlist_live.m3u8
curl http://localhost:8888/youtube_epg.xml
```

### 5.7 Checklist Etapa 3

```
[ ] web/main.py com lifespan funcional
[ ] Scheduler sobe como asyncio.Task no lifespan
[ ] GET / retorna 200 com contagem de streams
[ ] GET /config mostra formulário com todas as 6 seções
[ ] POST /config aplica mudanças sem restart do processo
[ ] GET /channels funcional
[ ] GET /logs SSE funcional
[ ] Playlists M3U retornam mimetype application/vnd.apple.mpegurl
[ ] EPG XML retorna mimetype application/xml
[ ] docker compose exec tubewranglerr pytest tests/test_web_routes.py -v → 100% passando
[ ] DECISIONS.md atualizado
```

---

## 6. Etapa 4 — Container de Produção

**Pré-requisito:** Checklist da Etapa 3 completo.

### 6.1 Diferença dev → produção

| | Desenvolvimento | Produção |
|---|---|---|
| Arquivo | `docker-compose.override.yml` ativo | Apenas `docker-compose.yml` |
| Código | Montado como volume | Copiado via `COPY . .` |
| Comando | `sleep infinity` + exec manual | `uvicorn web.main:app` |
| Reload | `--reload` manual | Sem reload |

### 6.2 Teste de produção

```bash
# Simular produção (sem o override)
docker compose -f docker-compose.yml up --build -d

# Aguardar health check
sleep 30
docker inspect tubewranglerr --format="{{.State.Health.Status}}"
# Esperado: healthy

# Verificar persistência
docker compose restart
sleep 10
curl http://localhost:8888/
# Esperado: 200 OK com dados mantidos
```

### 6.3 Checklist Etapa 4

```
[ ] docker compose -f docker-compose.yml build → sem erro
[ ] docker compose -f docker-compose.yml up -d → container sobe
[ ] docker inspect tubewranglerr → Health: healthy
[ ] http://localhost:8888/ acessível externamente
[ ] Volume /data persiste após docker compose restart
[ ] config.db existe em ./data/ após primeiro boot
[ ] Playlists M3U acessíveis via URL
[ ] DECISIONS.md atualizado
```

---

## 7. Etapa 5 — smart_player.py

**Pré-requisito:** Etapas 1-4 completas.

### 7.1 Mudanças — before/after exato

```python
# ============ REMOVER estas linhas ============
from dotenv import load_dotenv
load_dotenv(dotenv_path=SCRIPT_DIR / ".env")
PLACEHOLDER_IMAGE_URL = os.getenv("PLACEHOLDER_IMAGE_URL", "")
SMART_PLAYER_LOG_LEVEL_STR = os.getenv("SMART_PLAYER_LOG_LEVEL", "INFO")
SMART_PLAYER_LOG_TO_FILE = os.getenv("SMART_PLAYER_LOG_TO_FILE", "true").lower() == "true"

# ============ SUBSTITUIR por ============
from core.config import AppConfig
_cfg = AppConfig()
PLACEHOLDER_IMAGE_URL      = _cfg.get_str("placeholder_image_url")
SMART_PLAYER_LOG_LEVEL_STR = _cfg.get_str("smart_player_log_level")
SMART_PLAYER_LOG_TO_FILE   = _cfg.get_bool("smart_player_log_to_file")

# ============ STATE_CACHE_PATH — atualizar ============
# ANTES:
SCRIPT_DIR = Path(__file__).resolve().parent
STATE_CACHE_PATH = SCRIPT_DIR / "state_cache.json"

# DEPOIS:
STATE_CACHE_PATH = Path("/data") / _cfg.get_str("state_cache_filename")
TEXTS_CACHE_PATH = Path("/data") / "textosepg.json"
```

### 7.2 Validação da Etapa 5

```bash
docker compose exec tubewranglerr python3 smart_player.py --help
# Esperado: usage sem erros

# Verificar que não há mais load_dotenv
docker compose exec tubewranglerr grep -n "load_dotenv\|from dotenv\|os.getenv" smart_player.py
# Esperado: nenhuma saída
```

### 7.3 Checklist Etapa 5

```
[ ] import load_dotenv removido
[ ] Todos os os.getenv() substituídos por AppConfig
[ ] STATE_CACHE_PATH aponta para /data/
[ ] TEXTS_CACHE_PATH aponta para /data/
[ ] python3 smart_player.py --help executa sem erro
[ ] grep load_dotenv smart_player.py retorna vazio
[ ] DECISIONS.md atualizado
```

---

## 8. Testes entre Etapas

**Todos os testes usam `tmp_path` do pytest — nunca tocam em `/data/config.db`.**

### 8.1 tests/test_config.py (Etapa 1)

```python
import pytest
from core.config import AppConfig, DEFAULTS

@pytest.fixture
def cfg(tmp_path):
    return AppConfig(db_path=tmp_path / "test.db")

def test_total_de_chaves_e_43(cfg):
    """Garante cobertura total do file.env original."""
    assert len(DEFAULTS) == 43

def test_todas_as_chaves_no_banco(cfg):
    for key in DEFAULTS:
        assert cfg.get_raw(key) is not None, f"Chave ausente: {key}"

def test_get_int(cfg):
    assert cfg.get_int("scheduler_main_interval_hours") == 4

def test_get_bool_true(cfg):
    assert cfg.get_bool("enable_scheduler_active_hours") is True

def test_get_bool_false(cfg):
    cfg.update("enable_scheduler_active_hours", "false")
    assert cfg.get_bool("enable_scheduler_active_hours") is False

def test_get_list(cfg):
    assert "17" in cfg.get_list("allowed_category_ids")

def test_get_mapping_sports(cfg):
    m = cfg.get_mapping("category_mappings")
    assert m.get("Sports") == "ESPORTES"

def test_update_persiste_entre_instancias(cfg, tmp_path):
    cfg.update("http_port", "9999")
    cfg2 = AppConfig(db_path=tmp_path / "test.db")
    assert cfg2.get_int("http_port") == 9999

def test_chave_desconhecida_lanca_keyerror(cfg):
    with pytest.raises(KeyError):
        cfg.update("chave_inexistente", "valor")

def test_import_env_file(cfg, tmp_path):
    env = tmp_path / "test.env"
    env.write_text('YOUTUBE_API_KEY="minha_chave_teste"\n')
    cfg.import_from_env_file(env)
    assert cfg.get_str("youtube_api_key") == "minha_chave_teste"

def test_secoes_presentes(cfg):
    sections = cfg.get_all_by_section()
    for s in ("credentials", "scheduler", "filters", "output", "technical", "logging"):
        assert s in sections, f"Seção ausente: {s}"

def test_rows_sao_dicionarios(cfg):
    """Garante que fastlite retorna dicionários, não objetos com atributos."""
    for row in cfg._db.t.config.rows:
        assert isinstance(row, dict), f"Row deveria ser dict, é {type(row)}"
        assert "key" in row
        assert "value" in row
```

### 8.2 tests/test_state_manager.py (Etapa 2)

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
    state.update_streams([{
        "video_id": "abc123", "status": "live",
        "channel_id": "ch1", "title_original": "Test Live"
    }])
    assert "abc123" in state.streams

def test_save_e_load_disk(state, tmp_path, cfg):
    state.update_streams([{
        "video_id": "xyz789", "status": "upcoming",
        "channel_id": "ch2", "title_original": "Test Upcoming",
        "fetch_time": datetime.now(timezone.utc)
    }])
    state.save_to_disk()
    s2 = StateManager(cfg, cache_path=tmp_path / "state.json")
    assert s2.load_from_disk() is True
    assert "xyz789" in s2.streams
```

### 8.3 tests/test_playlist_builder.py (Etapa 2)

```python
import pytest
from datetime import datetime, timezone
from core.playlist_builder import M3UGenerator, XMLTVGenerator
from core.config import AppConfig

@pytest.fixture
def cfg(tmp_path):
    return AppConfig(db_path=tmp_path / "cfg.db")

def test_gera_m3u_live(cfg):
    gen = M3UGenerator(cfg)
    streams = [{
        "video_id": "v1", "status": "live",
        "title_original": "Jogo ao vivo", "channel_name": "Canal Teste",
        "watch_url": "https://youtube.com/watch?v=v1",
        "thumbnail_url": "https://img.com/thumb.jpg",
        "category_original": "17",
        "actual_start_time_utc": datetime.now(timezone.utc)
    }]
    result = gen.generate_playlist(streams, {}, "live")
    assert "#EXTM3U" in result
    assert "youtube.com" in result

def test_playlist_vazia_retorna_placeholder(cfg):
    gen = M3UGenerator(cfg)
    result = gen.generate_playlist([], {}, "live")
    assert "#EXTM3U" in result
```

### 8.4 tests/test_scheduler.py (Etapa 2)

```python
import pytest
import asyncio
from unittest.mock import MagicMock
from core.scheduler import Scheduler
from core.config import AppConfig

@pytest.fixture
def cfg(tmp_path):
    return AppConfig(db_path=tmp_path / "cfg.db")

@pytest.fixture
def scheduler(cfg):
    scraper = MagicMock()
    state   = MagicMock()
    state.get_all_streams.return_value  = []
    state.get_all_channels.return_value = {}
    return Scheduler(cfg, scraper, state)

def test_reload_config(scheduler, tmp_path):
    new_cfg = AppConfig(db_path=tmp_path / "new.db")
    new_cfg.update("scheduler_main_interval_hours", "8")
    scheduler.reload_config(new_cfg)
    assert scheduler.config.get_int("scheduler_main_interval_hours") == 8

@pytest.mark.asyncio
async def test_run_e_cancelavel(scheduler):
    task = asyncio.create_task(scheduler.run(initial_run_delay=True))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
```

### 8.5 tests/test_web_routes.py (Etapa 3)

```python
import pytest
from fasthtml.testclient import TestClient
from web.main import app

@pytest.fixture(scope="module")
def client():
    return TestClient(app)

def test_dashboard_ok(client):
    assert client.get("/").status_code == 200

def test_config_get_ok(client):
    r = client.get("/config")
    assert r.status_code == 200
    # Formulário deve ter pelo menos uma seção
    assert any(s in r.text.lower() for s in ("credentials","scheduler","filters"))

def test_config_post_salva(client):
    r = client.post("/config", data={"http_port": "9000"})
    assert r.status_code in (200, 302, 303)

def test_playlist_live_m3u(client):
    r = client.get("/playlist_live.m3u8")
    assert r.status_code == 200
    assert "#EXTM3U" in r.text

def test_epg_xml(client):
    r = client.get("/youtube_epg.xml")
    assert r.status_code == 200
    assert "<?xml" in r.text
```

---

## 9. Revisão Final de Migração

**Executar tudo dentro do container:**

```bash
# 1. Suite completa de testes
docker compose exec tubewranglerr pytest tests/ -v --tb=short
# Esperado: todos passando, 0 falhas

# 2. Verificar eliminação de dependências antigas
docker compose exec tubewranglerr grep -rn "os.getenv" core/ web/ smart_player.py
# Esperado: nenhuma saída

docker compose exec tubewranglerr grep -rn "load_dotenv\|from dotenv" core/ web/ smart_player.py
# Esperado: nenhuma saída

docker compose exec tubewranglerr grep -rn "from flask\|import Flask" core/ web/
# Esperado: nenhuma saída

# 3. Verificar imports completos
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
[ ] M3UGenerator.generate_playlist() live/upcoming/vod → core/playlist_builder.py
[ ] XMLTVGenerator.generate_xml() → core/playlist_builder.py
[ ] Scheduler.run() com pre/pos-evento e full sync → core/scheduler.py
[ ] Scheduler.reload_config() → core/scheduler.py
[ ] 43 variáveis editáveis via GET /config
[ ] POST /config aplica sem restart
[ ] Playlists servidas nas URLs corretas
[ ] smart_player.py sem load_dotenv e sem os.getenv()
[ ] state_cache.json lido de /data/
```

### 9.2 Arquivamento e commit final

```bash
# Arquivar originais
mkdir -p _archive
cp get_streams.py _archive/get_streams.py.bak
cp file.env _archive/file.env.bak

# Registrar SHA256 no DECISIONS.md
sha256sum get_streams.py file.env

# Commit final
git add .
git commit -m "refactor: migração completa para FastHTML + SQLite + Docker container-first"
```

---

## 10. Protocolo DECISIONS.md

### Template inicial obrigatório

```markdown
# DECISIONS.md — TubeWranglerr Refactoring Log

Gerado por: GitHub Copilot Agent
Início: [DATA_HORA]
Referência: REFACTORING_TUBEWRANGLERR.md v3.0
Ambiente: VS Code Windows → SSH → Debian → Docker container (python:3.12-slim)

---

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

---

## Decisões

### [ETAPA-0] [DATA] Decisão de arquitetura container-first
**Contexto:** Ambiente é VS Code Windows → SSH → Debian → Docker
**Decisão:** Nenhum Python roda no host. Todo comando via docker compose exec.
**Impacto:** Todos os comandos de execução usam docker compose exec tubewranglerr

---

## Dúvidas e Bloqueios

[Preencher quando surgirem — uma entrada por dúvida]

---

## Revisão Final

[Preenchido na Etapa 9]
```

### Regras de uso

```
OBRIGATÓRIO: Criar antes de qualquer arquivo de código
OBRIGATÓRIO: Uma entrada por decisão — nunca duplicar
OBRIGATÓRIO: Atualizar Status ao concluir cada etapa
PROIBIDO: Marcar ✅ com checklists incompletos
PROIBIDO: Apagar entradas existentes
```

---

## Apêndice — .github/copilot-instructions.md

```markdown
# Copilot Instructions — TubeWranglerr v3.0

## Ambiente de execução
- VS Code Windows → SSH → Debian → Docker
- TODO Python executa DENTRO do container Docker
- Comando padrão: docker compose exec tubewranglerr <comando>
- NUNCA instalar Python, pip ou pacotes no host Debian

## Regras críticas de código
- NUNCA usar os.getenv() — sempre AppConfig de core/config.py
- NUNCA importar Flask — projeto usa FastHTML
- fastlite retorna rows como DICIONÁRIOS: row["key"], NUNCA row.key
- AppConfig sempre injetado como parâmetro, nunca importado como global
- Lógica de negócio fica em core/ — zero lógica em web/
- Type hints em todas as funções públicas
- Docstring de responsabilidade em todo arquivo novo

## Stack
- Web: python-fasthtml
- Config/DB: fastlite (SQLite) — rows são dicionários
- Async: asyncio nativo (sem Celery, sem Redis)
- YouTube API: google-api-python-client
- Testes: pytest + pytest-asyncio com tmp_path

## Commits
- Apenas após pytest tests/ -v retornar 0 falhas no container
```

---

*Versão 3.0 — Incorpora lições da execução real. Container-first, fastlite como dicionário, sem instalação no host.*
