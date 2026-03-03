# REFACTORING_TUBEWRANGLERR.md

> **Versão:** 3.5
> **Projeto:** TubeWranglerr
> **Destino:** Agente autônomo (GitHub Copilot Workspace, Claude Sonnet, GPT-4o)
> **Objetivo:** Refatoração completa para stack FastHTML + SQLite em container standalone
> **Abordagem:** Container-First Development
>
> **Changelog v3.5:** Validação de cobertura funcional completa contra scripts originais:
> - Nova seção 4.5: `ContentGenerator` como classe base obrigatória em `core/playlist_builder.py`
> - Nova seção 4.6: `texts_cache` (textosepg.json) — onde gerar e onde consumir
> - Nova seção 4.7: `categories_db` — busca no `lifespan` e passagem para geradores
> - Nova seção 7.3: Cadeia completa `smart_player.py` documentada com dependências
> - Seção 5.4 (`web/main.py`): adicionado `categories_db` no `lifespan`
>
> **Changelog v3.4 (incorporado):** Renomeação de versão. Conteúdo idêntico ao v3.3.
>
> **Changelog v3.3:** Incorpora lições da execução real da Etapa 2 — AttributeErrors em cadeia:
> - `docker-compose.override.yml` com volume `.:/app` é **obrigatório** no desenvolvimento
> - Checklist da Etapa 2 agora exige validação de métodos antes de avançar para a Etapa 3
> - Nova seção 4.4: implementações mínimas obrigatórias de `StateManager` e `Scheduler`
> - Nova nota sobre armadilha de indentação em Python e como detectar
>
> **Changelog v3.2:** Incorpora todas as lições aprendidas na execução real da Etapa 3:
> - Documenta o bug catch-all do FastHTML para rotas com extensão (.xml, .m3u8)
> - Padrão correto: `@app.get`/`@app.post` em vez de `@rt` com nome de função
> - Proíbe explicitamente criação de `web/routes/` antes da Etapa 3 estar completa
> - `web/main.py` completo e funcional fornecido como referência canônica
> - Esclarece que erros do Pylance são falsos positivos — não bloquear execução
>
> **Changelog v3.1:** Dependências de sistema e Python completas:
> - Adiciona streamlink, yt-dlp e DejaVu fonts ao Dockerfile
> - Tabela completa de dependências de sistema vs Python
> - Verificações de binários no checklist da Etapa 0
>
> **Changelog v3.0:** Incorpora lições aprendidas da execução real (DECISIONS.md v1):
> - Corrige acesso a rows do fastlite (dicionário, não atributo)
> - Esclarece ordem correta das etapas (Docker ANTES do código)
> - Elimina ambiguidade sobre ambiente de execução (container vs host)

---

## ⚠️ LEIA ANTES DE QUALQUER AÇÃO

Este documento é a **única fonte de verdade** para o agente. Toda decisão deve ser tomada com base nele.

**REGRAS FUNDAMENTAIS:**
1. O host Debian é apenas um sistema de arquivos — Python executa **dentro do container**
2. A Etapa 0 (container) é **obrigatória antes de qualquer código**
3. O fastlite retorna rows como **dicionários** — nunca acessar como `row.key`, sempre `row["key"]`
4. O diretório `/data` no container é um volume — criado via `docker compose`, nunca via `mkdir` no host
5. `streamlink`, `yt-dlp` e `ffmpeg` são instalados **dentro do container** — nunca no host
6. `web/routes/` **não existe na Etapa 3** — todas as rotas ficam em `web/main.py`
7. Erros do Pylance sobre `fasthtml.common` são **falsos positivos** — não bloquear execução por causa deles
8. Em caso de dúvida, registrar em DECISIONS.md e aguardar — nunca assumir silenciosamente
9. **Sem volume `.:/app` no `docker-compose.override.yml`, NÃO iniciar desenvolvimento**
10. `textosepg.json` é gerado pelo `Scheduler` e consumido pelo `smart_player.py` — nunca hardcodar caminhos

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
Na primeira execução, o agente instalou pip/venv no host e criou `/data` com `sudo mkdir`.
A v3.x previne isso explicitamente. Todo binário e pacote Python vive no container.

### 0.1 Pré-requisitos no host Debian

```bash
docker --version        # Esperado: Docker version 24.x+
docker compose version  # Esperado: Docker Compose version v2.x
docker ps               # Esperado: lista sem erro de permissão
```

Se qualquer verificação falhar, registrar em DECISIONS.md e aguardar.
**NÃO instalar Docker — deve estar pré-instalado no servidor.**

### 0.2 Mapa completo de dependências

#### Dependências Python — `requirements.txt`

| Pacote | Versão mín. | Motivo |
|---|---|---|
| `python-fasthtml` | 0.12.0 | Framework web (substitui Flask) |
| `fastlite` | 0.0.9 | SQLite ORM (substitui .env) |
| `google-api-python-client` | 2.0.0 | YouTube Data API v3 |
| `google-auth` | 2.0.0 | Autenticação Google API |
| `google-auth-httplib2` | 0.2.0 | Transport para google-auth |
| `pytz` | 2024.1 | Fuso horário (America/Sao_Paulo) |
| `streamlink` | latest | Extração de streams ao vivo (smart_player.py) |
| `yt-dlp` | latest | Download/stream de VODs (smart_player.py) |
| `pytest` | 8.0.0 | Testes unitários |
| `pytest-asyncio` | 0.23.0 | Testes assíncronos |
| `httpx` | 0.27.0 | HTTP client para testes de rotas |

#### Dependências de sistema — `Dockerfile` via `apt`

| Pacote | Motivo | Binário |
|---|---|---|
| `ffmpeg` | Renderizar placeholder/thumbnail com texto overlay | `/usr/bin/ffmpeg` |
| `fonts-dejavu-core` | Fonte `DejaVuSans-Bold.ttf` para overlay no ffmpeg | `/usr/share/fonts/truetype/dejavu/` |
| `curl` | Health check e utilitários | `/usr/bin/curl` |

#### Pacotes REMOVIDOS intencionalmente

| Pacote | Motivo |
|---|---|
| `Flask` | Substituído por `python-fasthtml` |
| `python-dotenv` | Substituído por `AppConfig` + SQLite |
| `Werkzeug` | Dependência do Flask removido |

### 0.3 requirements.txt

```txt
python-fasthtml>=0.12.0
fastlite>=0.0.9
google-api-python-client>=2.0.0
google-auth>=2.0.0
google-auth-httplib2>=0.2.0
pytz>=2024.1
streamlink
yt-dlp
pytest>=8.0.0
pytest-asyncio>=0.23.0
httpx>=0.27.0
# NUNCA adicionar: Flask, python-dotenv, Werkzeug
```

### 0.4 Dockerfile

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

COPY . .

RUN mkdir -p /data/m3us /data/epgs /data/logs

VOLUME ["/data"]
EXPOSE 8888

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8888/')"

CMD ["python3", "-m", "uvicorn", "web.main:app", "--host", "0.0.0.0", "--port", "8888"]
```

### 0.5 docker-compose.yml

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

### 0.6 docker-compose.override.yml (desenvolvimento)

```yaml
services:
  tubewranglerr:
    volumes:
      - .:/app          # ← CRÍTICO: código-fonte montado como volume
      - ./data:/data    # ← dados persistentes
    command: sleep infinity
    environment:
      - PYTHONUNBUFFERED=1
      - PYTHONDONTWRITEBYTECODE=1
```

**Por que é crítico:**
Sem `.:/app`, cada alteração de código exige `docker compose build --no-cache`
(~90 segundos). Com `.:/app`, basta `docker compose restart` (~5 segundos).
O agente fica confuso quando edita um arquivo e o container não reflete a
mudança — inventa diagnósticos de "contexto corrompido", "arquivo não sincronizado".
Este volume elimina completamente esse problema durante o desenvolvimento.

**Atenção:** o `docker-compose.override.yml` é usado APENAS em desenvolvimento.
O `docker-compose.yml` principal (produção) NÃO monta `.:/app`.

### 0.7 Sequência de inicialização

```bash
docker compose build
docker compose up -d
docker compose ps

# Verificações obrigatórias
docker compose exec tubewranglerr python3 --version          # Python 3.12.x
docker compose exec tubewranglerr pip list | grep -E "fasthtml|fastlite|streamlink|yt-dlp"
docker compose exec tubewranglerr ffmpeg -version
docker compose exec tubewranglerr streamlink --version
docker compose exec tubewranglerr yt-dlp --version
docker compose exec tubewranglerr ls /usr/share/fonts/truetype/dejavu/ | grep Bold
docker compose exec tubewranglerr ls -la /data
```

### 0.8 Checklist Etapa 0

```
[ ] requirements.txt criado (streamlink, yt-dlp incluídos — sem Flask, sem python-dotenv)
[ ] Dockerfile criado (ffmpeg + fonts-dejavu-core + curl)
[ ] docker-compose.yml criado
[ ] docker-compose.override.yml criado COM volume .:/app
[ ] docker compose build → sem erro
[ ] docker compose up -d → container running
[ ] python3 --version → 3.12.x
[ ] pip list → fasthtml, fastlite, streamlink, yt-dlp presentes
[ ] ffmpeg -version → OK
[ ] streamlink --version → OK
[ ] yt-dlp --version → OK
[ ] DejaVuSans-Bold.ttf presente em /usr/share/fonts/truetype/dejavu/
[ ] ls /data → m3us/ epgs/ logs/ presentes
[ ] DECISIONS.md criado
```

---

### 0.9 Git — Branches e fluxo

- Branch ativa de desenvolvimento: `dev`
- Branch de produção: `main` — recebe merge APENAS com checklist 100% completo
- Testes são obrigatórios na `dev` antes de qualquer merge
- NUNCA fazer push direto na `main`

Fluxo obrigatório:
  dev (desenvolve + testa) → merge → main → deploy
  
---

## 1. Regras Absolutas do Agente

### 🚫 PROIBIÇÕES

```
PROIBIDO: Instalar Python, pip, venv, ffmpeg, streamlink ou yt-dlp no host Debian
PROIBIDO: Executar python3, pytest ou pip fora de "docker compose exec tubewranglerr"
PROIBIDO: Criar /data com mkdir no host
PROIBIDO: Usar os.getenv() ou load_dotenv() em qualquer arquivo novo
PROIBIDO: Importar Flask em qualquer arquivo novo
PROIBIDO: Acessar rows do fastlite como atributos (row.key) — usar sempre row["key"]
PROIBIDO: Criar web/routes/ antes da Etapa 3 estar 100% validada
PROIBIDO: Importar rt de main.py em outros arquivos
PROIBIDO: Usar @rt sem URL explícita
PROIBIDO: Bloquear execução por erros do Pylance sobre fasthtml.common
PROIBIDO: Apagar arquivos originais antes da Etapa 9
PROIBIDO: Duplicar entradas no DECISIONS.md
PROIBIDO: Iniciar desenvolvimento sem o volume .:/app no docker-compose.override.yml
PROIBIDO: Hardcodar caminhos de /data — sempre usar AppConfig
```

### ✅ OBRIGAÇÕES

```
OBRIGATÓRIO: Criar DECISIONS.md antes de qualquer arquivo de código
OBRIGATÓRIO: Toda execução Python via docker compose exec tubewranglerr
OBRIGATÓRIO: AppConfig passado como parâmetro — nunca importado como singleton global
OBRIGATÓRIO: Acessar campos de rows fastlite como dicionário: row["key"]
OBRIGATÓRIO: Todo arquivo novo começa com docstring de responsabilidade
OBRIGATÓRIO: Type hints em todas as funções públicas
OBRIGATÓRIO: Testes de cada etapa passam antes de avançar
OBRIGATÓRIO: Atualizar DECISIONS.md ao concluir cada etapa
OBRIGATÓRIO: Validar métodos obrigatórios de StateManager e Scheduler antes de avançar para Etapa 3
OBRIGATÓRIO: ContentGenerator como classe base de M3UGenerator e XMLTVGenerator
OBRIGATÓRIO: textosepg.json gerado pelo Scheduler.save_files() em /data/
OBRIGATÓRIO: categories_db carregado no lifespan e passado aos geradores
```

### 📐 Acesso correto ao fastlite — regra crítica

```python
# ✅ CORRETO — rows são dicionários
for row in self._db.t.config.rows:
    key   = row["key"]
    value = row["value"]

# ❌ ERRADO — fastlite NÃO retorna objetos com atributos
value = row.value   # AttributeError
```

---

## 2. Estrutura Final do Projeto

```
tubewranglerr/
├── core/
│   ├── __init__.py
│   ├── config.py
│   ├── state_manager.py
│   ├── youtube_api.py
│   ├── playlist_builder.py      ← ContentGenerator + M3UGenerator + XMLTVGenerator
│   └── scheduler.py             ← salva playlists + textosepg.json
│
├── web/
│   ├── __init__.py
│   └── main.py                  ← TODAS as rotas + lifespan com categories_db
│
├── scripts/
│   └── migrate_env.py
│
├── tests/
│   ├── test_config.py
│   ├── test_state_manager.py
│   ├── test_youtube_api.py
│   ├── test_playlist_builder.py
│   ├── test_scheduler.py
│   └── test_web_routes.py
│
├── _archive/                    # Criado na Etapa 9
├── data/                        # Volume Docker — NUNCA versionar
├── smart_player.py
├── Dockerfile
├── docker-compose.yml
├── docker-compose.override.yml
├── requirements.txt
├── .gitignore
├── .github/copilot-instructions.md
├── DECISIONS.md
└── REFACTORING_TUBEWRANGLERR.md
```

---

## 3. Etapa 1 — core/config.py

**Pré-requisito:** Etapa 0 com checklist 100% completo.

### 3.1 core/config.py

```python
"""
core/config.py
Responsabilidade: Única fonte de verdade para configurações.
Substitui completamente o arquivo .env e todos os os.getenv().
ATENÇÃO: fastlite retorna rows como dicionários. Sempre row["key"], NUNCA row.key
"""
from pathlib import Path
from fastlite import database

DB_PATH = Path("/data/config.db")

DEFAULTS: dict = {
    # --- Credenciais (3) ---
    "youtube_api_key":               ("", "credentials", "Chave de API do YouTube", "str"),
    "target_channel_handles":        ("", "credentials", "Handles de canais separados por vírgula", "list"),
    "target_channel_ids":            ("", "credentials", "IDs diretos de canais separados por vírgula", "list"),
    # --- Agendador (10) ---
    "scheduler_main_interval_hours":         ("4",    "scheduler", "Intervalo principal em horas", "int"),
    "scheduler_pre_event_window_hours":      ("2",    "scheduler", "Janela pré-evento em horas", "int"),
    "scheduler_pre_event_interval_minutes":  ("5",    "scheduler", "Intervalo pré-evento em minutos", "int"),
    "scheduler_post_event_interval_minutes": ("5",    "scheduler", "Intervalo pós-evento em minutos", "int"),
    "enable_scheduler_active_hours":         ("true", "scheduler", "Ativar horário de atividade", "bool"),
    "scheduler_active_start_hour":           ("7",    "scheduler", "Hora de início (formato 24h)", "int"),
    "scheduler_active_end_hour":             ("22",   "scheduler", "Hora de fim (formato 24h)", "int"),
    "full_sync_interval_hours":              ("48",   "scheduler", "Intervalo de full sync em horas", "int"),
    "resolve_handles_ttl_hours":             ("24",   "scheduler", "TTL cache de handles em horas", "int"),
    "initial_sync_days":                     ("2",    "scheduler", "Dias para busca inicial (0=tudo)", "int"),
    # --- Filtros (13) ---
    "max_schedule_hours":            ("72",   "filters", "Limite futuro em horas para agendamentos", "int"),
    "max_upcoming_per_channel":      ("6",    "filters", "Máximo de agendamentos futuros por canal", "int"),
    "title_filter_expressions":      ("ao vivo,AO VIVO,AO VIVO E COM IMAGRENS,com imagens,cortes,react,ge.globo,#live,!,:,ge tv,JOGO COMPLETO",
                                      "filters", "Expressões a remover dos títulos (vírgula)", "list"),
    "prefix_title_with_channel_name":("true", "filters", "Prefixar título com nome do canal", "bool"),
    "prefix_title_with_status":      ("true", "filters", "Prefixar título com status [Ao Vivo] etc", "bool"),
    "category_mappings":             ("Sports|ESPORTES,Gaming|JOGOS,People & Blogs|ESPORTES,News & Politics|NOTICIAS",
                                      "filters", "Mapeamento categorias API|Exibição (vírgula)", "mapping"),
    "channel_name_mappings":         ("FAF TV | @fafalagoas|FAF TV,Canal GOAT|GOAT,Federação de Futebol de Mato Grosso do Sul|FFMS,Federação Paranaense de Futebol|FPF TV,Federação Catarinense de Futebol|FCF TV,Jovem Pan Esportes|J. Pan Esportes,TNT Sports Brasil|TNT Sports",
                                      "filters", "Mapeamento nomes canais Longo|Curto (vírgula)", "mapping"),
    "epg_description_cleanup":       ("true", "filters", "Manter apenas 1º parágrafo da descrição EPG", "bool"),
    "filter_by_category":            ("true", "filters", "Filtrar streams por categoria da API", "bool"),
    "allowed_category_ids":          ("17",   "filters", "IDs de categoria permitidos. 17=Sports", "list"),
    "keep_recorded_streams":         ("true", "filters", "Manter streams gravados no cache", "bool"),
    "max_recorded_per_channel":      ("2",    "filters", "Máximo de gravações por canal", "int"),
    "recorded_retention_days":       ("2",    "filters", "Dias de retenção de streams gravados", "int"),
    # --- Saída (8) ---
    "playlist_save_directory":       ("/data/m3us",           "output", "Diretório para playlists M3U", "str"),
    "playlist_live_filename":        ("playlist_live.m3u8",   "output", "Nome do M3U de lives", "str"),
    "playlist_upcoming_filename":    ("playlist_upcoming.m3u8","output","Nome do M3U de agendados", "str"),
    "playlist_vod_filename":         ("playlist_vod.m3u8",    "output", "Nome do M3U de gravados", "str"),
    "xmltv_save_directory":          ("/data/epgs",           "output", "Diretório para EPG XML", "str"),
    "xmltv_filename":                ("youtube_epg.xml",      "output", "Nome do arquivo EPG XMLTV", "str"),
    "placeholder_image_url":         ("https://i.ibb.co/9kZStw28/placeholder-sports.png",
                                      "output", "URL da imagem placeholder", "str"),
    "use_invisible_placeholder":     ("true", "output", "Usar placeholder invisível no M3U", "bool"),
    # --- Técnico (5) ---
    "http_port":                     ("8888",             "technical", "Porta HTTP do servidor", "int"),
    "state_cache_filename":          ("state_cache.json", "technical", "Nome do arquivo JSON de estado", "str"),
    "stale_hours":                   ("6",                "technical", "Horas para considerar stream stale", "int"),
    "use_playlist_items":            ("true",             "technical", "Usar playlistItems API", "bool"),
    "local_timezone":                ("America/Sao_Paulo","technical", "Fuso horário local (pytz)", "str"),
    # --- Logs (4) ---
    "log_level":                     ("INFO", "logging", "Nível de log do core", "str"),
    "log_to_file":                   ("true", "logging", "Salvar log do core em arquivo", "bool"),
    "smart_player_log_level":        ("INFO", "logging", "Nível de log do smart_player", "str"),
    "smart_player_log_to_file":      ("true", "logging", "Salvar log do smart_player em arquivo", "bool"),
}

class AppConfig:
    """
    Gerenciador de configuração persistente em SQLite via fastlite.
    IMPORTANTE: fastlite retorna rows como dicionários. row["key"], NUNCA row.key
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
                description=str, value_type=str, pk="key"
            )
        existing = {row["key"] for row in self._db.t.config.rows}
        for key, (default_val, section, desc, vtype) in DEFAULTS.items():
            if key not in existing:
                self._db.t.config.insert({
                    "key": key, "value": default_val,
                    "section": section, "description": desc, "value_type": vtype,
                })

    def reload(self):
        self._cache = {row["key"]: row for row in self._db.t.config.rows}

    def get_raw(self, key: str) -> str:
        return self._cache[key]["value"] if key in self._cache else DEFAULTS.get(key, ("",))[0]

    def get_str(self, key: str) -> str:     return self.get_raw(key)
    def get_int(self, key: str) -> int:     return int(self.get_raw(key))
    def get_bool(self, key: str) -> bool:   return self.get_raw(key).lower() == "true"
    def get_list(self, key: str) -> list:   return [x.strip() for x in self.get_raw(key).split(",") if x.strip()]
    def get_mapping(self, key: str) -> dict:
        return {k.strip(): v.strip() for item in self.get_raw(key).split(",")
                if "|" in item for k, v in [item.rsplit("|", 1)]}

    def update(self, key: str, value: str):
        if key not in self._cache:
            raise KeyError(f"Chave desconhecida: '{key}'")
        self._db.t.config.update({"key": key, "value": str(value)})
        self._cache[key]["value"] = str(value)

    def update_many(self, updates: dict):
        for key, value in updates.items():
            self.update(key, str(value))

    def get_all_by_section(self) -> dict:
        sections: dict = {}
        for row in self._cache.values():
            sections.setdefault(row["section"], []).append(row)
        return sections

    def import_from_env_file(self, env_path: Path):
        if not env_path.exists():
            print(f"AVISO: {env_path} não encontrado.")
            return
        mapping = {k.upper(): k for k in DEFAULTS.keys()}
        imported = 0
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                lower_key = mapping.get(k.strip().upper())
                if lower_key:
                    try:
                        self.update(lower_key, v.strip().strip('"\'\"').strip("\'"))
                        imported += 1
                    except KeyError:
                        pass
        print(f"✅ {imported} valores importados de {env_path}")
```

### 3.2 Checklist Etapa 1

```
[ ] core/__init__.py criado (vazio)
[ ] core/config.py criado — usa row["key"] em TODO acesso ao fastlite
[ ] 43 chaves presentes no DEFAULTS
[ ] title_filter_expressions default inclui: ao vivo,AO VIVO,AO VIVO E COM IMAGRENS,com imagens,cortes,react,ge.globo,#live,!,:,ge tv,JOGO COMPLETO
[ ] scripts/migrate_env.py criado
[ ] docker compose exec → migrate_env.py → 43 valores importados
[ ] docker compose exec → pytest tests/test_config.py -v → 100% passando
[ ] Nenhum os.getenv() em core/config.py
[ ] DECISIONS.md atualizado
```

---

## 4. Etapa 2 — Separação de Módulos

**Pré-requisito:** Checklist da Etapa 1 completo.

### 4.1 Ordem de criação

```
1. core/state_manager.py    — sem deps de outros módulos core
2. core/youtube_api.py      — sem deps de outros módulos core
3. core/playlist_builder.py — ContentGenerator + M3UGenerator + XMLTVGenerator
4. core/scheduler.py        — depende dos 3 anteriores + salva arquivos + texts_cache
```

### 4.2 Assinaturas obrigatórias

```python
class StateManager:
    def __init__(self, config: AppConfig, cache_path: Path | None = None): ...

class YouTubeAPI:
    def __init__(self, api_key: str): ...   # api_key vem do CHAMADOR

class ContentGenerator:                      # ← CLASSE BASE — ver seção 4.5
    def is_live(self, stream: dict) -> bool: ...
    def filter_streams(self, streams: list, mode: str) -> list: ...
    def get_display_title(self, stream: dict) -> str: ...
    def get_display_category(self, cat_id: str | None, db: dict) -> str: ...

class M3UGenerator(ContentGenerator):
    def __init__(self, config: AppConfig): ...
    def generate_playlist(self, streams: list, db: dict, mode: str) -> str: ...

class XMLTVGenerator(ContentGenerator):
    def __init__(self, config: AppConfig): ...
    def generate_xml(self, channels: dict, streams: list, db: dict) -> str: ...

class Scheduler:
    def __init__(self, config: AppConfig, scraper: YouTubeAPI, state: StateManager): ...
    def reload_config(self, new_config: AppConfig): ...
    def trigger_now(self): ...
    async def run(self, initial_run_delay: bool = False): ...
    def save_files(self, categories_db: dict): ...   # ← gera playlists + textosepg.json
```

### 4.3 Checklist Etapa 2

```
[ ] Todos os 4 módulos criados sem os.getenv() e sem Flask
[ ] Todos os imports OK no container
[ ] Todos os testes das etapas passam
[ ] grep -r "os.getenv" core/ retorna vazio
[ ] get_streams.py NÃO foi apagado

[ ] VALIDAÇÃO DE MÉTODOS — executar antes de avançar para Etapa 3:
    docker compose exec tubewranglerr python3 -c "
    from core.config import AppConfig
    from core.state_manager import StateManager
    from core.scheduler import Scheduler
    from core.playlist_builder import ContentGenerator, M3UGenerator, XMLTVGenerator

    cfg = AppConfig()
    sm  = StateManager(cfg)
    sch = Scheduler.__new__(Scheduler)
    m3u = M3UGenerator(cfg)
    xml = XMLTVGenerator(cfg)

    # StateManager
    assert hasattr(sm,  'load_from_disk'),   'FALTA: StateManager.load_from_disk()'
    assert hasattr(sm,  'save_to_disk'),     'FALTA: StateManager.save_to_disk()'
    assert hasattr(sm,  'get_all_streams'),  'FALTA: StateManager.get_all_streams()'
    assert hasattr(sm,  'get_all_channels'), 'FALTA: StateManager.get_all_channels()'

    # Scheduler
    assert hasattr(sch, 'trigger_now'),      'FALTA: Scheduler.trigger_now()'
    assert hasattr(sch, 'reload_config'),    'FALTA: Scheduler.reload_config()'
    assert hasattr(sch, 'run'),              'FALTA: Scheduler.run()'
    assert hasattr(sch, 'save_files'),       'FALTA: Scheduler.save_files()'

    # ContentGenerator (herança)
    assert hasattr(m3u, 'is_live'),          'FALTA: M3UGenerator herda ContentGenerator.is_live()'
    assert hasattr(m3u, 'filter_streams'),   'FALTA: M3UGenerator herda ContentGenerator.filter_streams()'
    assert hasattr(xml, 'clean_text_for_xml'),'FALTA: XMLTVGenerator.clean_text_for_xml()'

    print('OK — todos os métodos obrigatórios presentes')
    "

[ ] Script acima retorna "OK — todos os métodos obrigatórios presentes"
[ ] DECISIONS.md atualizado
```

### 4.4 Implementações mínimas — o que NÃO pode ficar como stub

#### StateManager — implementação mínima obrigatória

```python
def load_from_disk(self):
    import json
    cache_file = self.cache_path
    if cache_file.exists():
        try:
            with open(cache_file, encoding="utf-8") as f:
                self.streams = json.load(f)
        except (json.JSONDecodeError, OSError):
            self.streams = {}
    else:
        self.streams = {}

def save_to_disk(self):
    import json
    self.cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(self.cache_path, "w", encoding="utf-8") as f:
        json.dump(self.streams, f, ensure_ascii=False, indent=2)

def get_all_streams(self) -> list:
    if not self.streams:
        return []
    if isinstance(self.streams, dict):
        result = []
        for data in self.streams.values():
            if isinstance(data, dict) and 'streams' in data:
                result.extend(data['streams'])
            elif isinstance(data, list):
                result.extend(data)
        return result
    return list(self.streams) if isinstance(self.streams, list) else []

def get_all_channels(self) -> dict:
    # Retorna dict {channel_id: title} — igual ao original
    if isinstance(self.streams, dict):
        return self._channels if hasattr(self, '_channels') else {}
    return {}
```

#### Scheduler — implementação mínima obrigatória

```python
def __init__(self, config: AppConfig, scraper, state: StateManager):
    import asyncio
    self._config  = config
    self._scraper = scraper
    self._state   = state
    self._trigger_event = asyncio.Event()

def trigger_now(self):
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        loop.call_soon_threadsafe(self._trigger_event.set)
    except RuntimeError:
        pass

def reload_config(self, new_config: AppConfig):
    self._config = new_config

def save_files(self, categories_db: dict):
    # Implementação real: gera M3U + XML + textosepg.json
    # Ver seção 4.6 para textosepg.json e seção 4.7 para categories_db
    pass

async def run(self, initial_run_delay: bool = False):
    import asyncio
    while True:
        await self._trigger_event.wait()
        self._trigger_event.clear()
        await asyncio.sleep(1)
```

**Armadilha de indentação em Python:**
Métodos adicionados com indentação errada ficam fora da classe.
Sempre validar com:

```bash
docker compose exec tubewranglerr python3 -c "
import inspect, core.scheduler as m
src = inspect.getsource(m.Scheduler.trigger_now)
print(src[:100])
print('--- indentação OK')
"
```

---

### 4.5 ContentGenerator — classe base obrigatória

**Por que é necessário:**
No `get_streams.py` original, `M3UGenerator` e `XMLTVGenerator` herdam de `ContentGenerator`.
Métodos como `is_live()`, `filter_streams()`, `get_display_title()` e `get_display_category()`
são compartilhados entre os dois geradores. Se o agente não implementar a herança,
vai duplicar código ou causar inconsistências entre playlist e EPG.

```python
class ContentGenerator:
    """
    Classe base para M3UGenerator e XMLTVGenerator.
    Contém lógica compartilhada de filtragem, ordenação e formatação de títulos.
    Todos os parâmetros de configuração vêm de self._config (AppConfig).
    """

    def is_live(self, stream: dict) -> bool:
        start  = stream.get("actual_start_time_utc")
        status = stream.get("status") == "live"
        started = isinstance(start, datetime)
        not_ended = not stream.get("actual_end_time_utc")
        return status and started and not_ended

    @staticmethod
    def get_sortable_time(stream: dict):
        from datetime import timezone
        timeval = stream.get("actual_start_time_utc") or stream.get("scheduled_start_time_utc")
        if isinstance(timeval, datetime):
            return timeval
        return datetime.max.replace(tzinfo=timezone.utc)

    def filter_streams(self, streams: list, mode: str) -> list:
        # Filtra por mode: "live", "upcoming", "vod"
        # Respeita max_upcoming_per_channel e max_recorded_per_channel do config
        ...

    def get_display_title(self, stream: dict) -> str:
        # Aplica title_filter_expressions, channel_name_mappings,
        # prefix_title_with_status e prefix_title_with_channel_name
        ...

    def get_display_category(self, cat_id: str | None, db: dict) -> str:
        # Usa category_mappings do config
        ...

class M3UGenerator(ContentGenerator):
    def __init__(self, config: AppConfig): ...

class XMLTVGenerator(ContentGenerator):
    def __init__(self, config: AppConfig): ...
    def clean_text_for_xml(self, text: str | None) -> str: ...
    def parse_iso8601_duration(self, duration_str: str | None): ...
```

**Regra:** O agente NÃO pode implementar `filter_streams` ou `get_display_title`
separadamente em `M3UGenerator` e `XMLTVGenerator`. Ambos herdam de `ContentGenerator`.

---

### 4.6 textosepg.json — geração e consumo

**O que é:**
Arquivo JSON em `/data/textosepg.json` com textos de countdown para streams `upcoming`.
Gerado pelo `Scheduler.save_files()` e consumido pelo `smart_player.py` para overlay via FFmpeg.

**Formato:**
```json
{
  "VIDEO_ID_1": {"line1": "Ao vivo em 2h30m", "line2": "26 Fev 21:00"},
  "VIDEO_ID_2": {"line1": "Ao vivo em instantes", "line2": "26 Fev 20:45"}
}
```

**Onde gerar — `Scheduler.save_files()`:**
```python
def save_files(self, categories_db: dict):
    import json, pytz, re
    from datetime import datetime, timezone, timedelta
    from pathlib import Path

    config      = self._config
    state       = self._state
    m3u         = M3UGenerator(config)
    xmltv       = XMLTVGenerator(config)
    all_streams = state.get_all_streams()
    local_tz    = pytz.timezone(config.get_str("local_timezone"))
    now_utc     = datetime.now(timezone.utc)

    # 1. Salvar playlists M3U
    for mode, filename_key, directory_key in [
        ("live",     "playlist_live_filename",     "playlist_save_directory"),
        ("upcoming", "playlist_upcoming_filename",  "playlist_save_directory"),
        ("vod",      "playlist_vod_filename",       "playlist_save_directory"),
    ]:
        content = m3u.generate_playlist(all_streams, categories_db, mode)
        path = Path(config.get_str(directory_key)) / config.get_str(filename_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    # 2. Salvar EPG XML
    xml_content = xmltv.generate_xml(state.get_all_channels(), all_streams, categories_db)
    xml_path = Path(config.get_str("xmltv_save_directory")) / config.get_str("xmltv_filename")
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    xml_path.write_text(xml_content, encoding="utf-8")

    # 3. Salvar textosepg.json — countdown para upcoming
    texts_cache = {}
    meses = ["Jan","Fev","Mar","Abr","Mai","Jun","Jul","Ago","Set","Out","Nov","Dez"]
    upcoming = [s for s in all_streams
                if s.get("status") == "upcoming"
                and not s.get("video_id","").startswith("PLACEHOLDER")]
    for s in upcoming:
        video_id  = s.get("video_id")
        start     = ContentGenerator.get_sortable_time(s)
        if not isinstance(start, datetime) or start == datetime.max.replace(tzinfo=timezone.utc):
            continue
        try:
            start_local = start.astimezone(local_tz)
            delta       = start - now_utc
            total_secs  = delta.total_seconds()
            if total_secs > 0:
                days, rem  = divmod(int(total_secs), 86400)
                hours, rem = divmod(rem, 3600)
                minutes, _ = divmod(rem, 60)
                if days > 1:
                    line1 = f"Ao vivo em {days}d {hours}h"
                elif days == 1:
                    line1 = f"Ao vivo em 1d {hours}h"
                elif hours > 0:
                    line1 = f"Ao vivo em {hours}h {minutes}m"
                else:
                    line1 = f"Ao vivo em {minutes}m" if minutes > 0 else "Ao vivo em instantes"
                line2 = f"{start_local.day} {meses[start_local.month-1]} {start_local.strftime('%H:%M')}"
                texts_cache[video_id] = {"line1": line1, "line2": line2}
        except Exception:
            pass

    texts_path = Path(config.get_str("state_cache_filename")).parent / "textosepg.json"
    # Caminho correto: mesmo diretório do state_cache
    texts_path = Path("/data") / "textosepg.json"
    texts_path.write_text(json.dumps(texts_cache, ensure_ascii=False, indent=2), encoding="utf-8")
```

**Onde consumir — `smart_player.py`:**
```python
# smart_player.py lê /data/textosepg.json para overlay FFmpeg em streams upcoming
TEXTS_CACHE_PATH = Path("/data") / "textosepg.json"

def get_texts_from_cache(video_id: str) -> dict:
    # Retorna {"line1": "...", "line2": "..."} ou {"line1": "", "line2": ""}
    ...
```

**Cadeia completa:**
```
Scheduler.save_files()
    └── gera /data/textosepg.json
            └── smart_player.py lê ao receber URL de thumbnail upcoming
                    └── run_ffmpeg_placeholder(url, line1, line2)
                            └── FFmpeg overlay texto na imagem → mpegts stdout
```

---

### 4.7 categories_db — busca e passagem

**O que é:**
Dicionário `{category_id: category_name}` obtido via `videoCategories.list` da API YouTube.
Usado por `M3UGenerator` e `XMLTVGenerator` para traduzir IDs numéricos em nomes legíveis.

**Onde buscar — `lifespan` em `web/main.py`:**
```python
@asynccontextmanager
async def lifespan(app):
    global _config, _state, _scheduler, _m3u, _xmltv, _categories_db

    _config    = AppConfig()
    _state     = StateManager(_config)
    _state.load_from_disk()
    scraper    = YouTubeAPI(_config.get_str("youtube_api_key"))

    # Carrega categorias do YouTube — necessário para M3U e EPG
    _categories_db = {}
    try:
        cats = scraper.youtube.videoCategories().list(
            part="snippet", regionCode="BR"
        ).execute()
        _categories_db = {
            item["id"]: item["snippet"]["title"]
            for item in cats.get("items", [])
        }
    except Exception as e:
        logger.warning(f"Falha ao carregar categorias: {e}. Usando dict vazio.")

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
```

**Como passar aos geradores:**
```python
# Nas rotas de playlist e EPG, passar _categories_db:
async def _playlist_live(req):
    content = _m3u.generate_playlist(_state.get_all_streams(), _categories_db, "live")
    return StarletteResponse(content, media_type="application/vnd.apple.mpegurl")

async def _epg_xml(req):
    content = _xmltv.generate_xml(_state.get_all_channels(), _state.get_all_streams(), _categories_db)
    return StarletteResponse(content, media_type="application/xml")

# No scheduler, ao salvar arquivos:
_scheduler.save_files(_categories_db)
```

**Fallback obrigatório:**
Se `videoCategories.list` falhar (quota, sem API key), usar `{}` — os geradores
devem funcionar com dict vazio, usando `category_mappings` do config como fallback.

---

## 5. Etapa 3 — Interface FastHTML

**Pré-requisito:** Checklist da Etapa 2 completo (incluindo validação de métodos).

### ⚠️ AVISO CRÍTICO — Leia antes de escrever qualquer código desta etapa

Esta etapa tem **três armadilhas documentadas** que causaram falhas na execução real.

---

### 5.1 Armadilha 1 — `@rt` e instâncias locais

```python
# ❌ ERRADO — causa 404 silencioso
from web.main import rt
@rt("/config")
def get(): ...

# ✅ CORRETO
@app.get("/config")
def config_page(): ...
```

---

### 5.2 Armadilha 2 — Rota catch-all do FastHTML intercepta extensões

```python
# ❌ ERRADO — interceptado pelo catch-all /{fname}.{ext:static}
@app.get("/youtube_epg.xml")
def epg_xml(): ...

# ✅ CORRETO — Starlette Route inserida no topo
from starlette.routing import Route
app.router.routes.insert(0, Route("/youtube_epg.xml", _epg_xml))
```

**Regra:** Toda URL com extensão → `Route` + `insert(0, ...)`. Toda URL sem extensão → `@app.get`/`@app.post`.

---

### 5.3 Armadilha 3 — Erros do Pylance são falsos positivos

`from fasthtml.common import *` usa wildcard import. Pylance não resolve — ignora.
Validação real sempre no container:
```bash
docker compose exec tubewranglerr python3 -c "from fasthtml.common import *; print('OK')"
```

---

### 5.4 web/main.py — arquivo canônico completo (v3.5)

```python
"""
web/main.py
Responsabilidade: Entry point da aplicação FastHTML.
Contém TODAS as rotas da Etapa 3.
web/routes/ NÃO existe nesta etapa.
"""
from contextlib import asynccontextmanager
from fasthtml.common import *
from starlette.routing import Route
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse
import asyncio
import logging
from core.config import AppConfig
from core.state_manager import StateManager
from core.youtube_api import YouTubeAPI
from core.scheduler import Scheduler
from core.playlist_builder import M3UGenerator, XMLTVGenerator

logger = logging.getLogger("tubewranglerr.web")

_config:        AppConfig | None    = None
_state:         StateManager | None = None
_scheduler:     Scheduler | None    = None
_m3u:           M3UGenerator | None = None
_xmltv:         XMLTVGenerator | None = None
_categories_db: dict                = {}

@asynccontextmanager
async def lifespan(app):
    global _config, _state, _scheduler, _m3u, _xmltv, _categories_db
    _config    = AppConfig()
    _state     = StateManager(_config)
    _state.load_from_disk()
    scraper    = YouTubeAPI(_config.get_str("youtube_api_key"))

    # Carrega categorias — necessário para M3U e EPG corretos
    _categories_db = {}
    try:
        cats = scraper.youtube.videoCategories().list(part="snippet", regionCode="BR").execute()
        _categories_db = {item["id"]: item["snippet"]["title"] for item in cats.get("items", [])}
        logger.info(f"Categorias carregadas: {len(_categories_db)} total.")
    except Exception as e:
        logger.warning(f"Falha ao carregar categorias: {e}. Usando dict vazio.")

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

# ══════════════════════════════════════════════════════════════════
# Rotas COM extensão — Starlette Route (bypass do catch-all FastHTML)
# ══════════════════════════════════════════════════════════════════

async def _playlist_live(req: StarletteRequest):
    content = _m3u.generate_playlist(_state.get_all_streams(), _categories_db, "live") \
              if _m3u and _state else "#EXTM3U\n"
    return StarletteResponse(content, media_type="application/vnd.apple.mpegurl")

async def _playlist_upcoming(req: StarletteRequest):
    content = _m3u.generate_playlist(_state.get_all_streams(), _categories_db, "upcoming") \
              if _m3u and _state else "#EXTM3U\n"
    return StarletteResponse(content, media_type="application/vnd.apple.mpegurl")

async def _playlist_vod(req: StarletteRequest):
    content = _m3u.generate_playlist(_state.get_all_streams(), _categories_db, "vod") \
              if _m3u and _state else "#EXTM3U\n"
    return StarletteResponse(content, media_type="application/vnd.apple.mpegurl")

async def _epg_xml(req: StarletteRequest):
    if _xmltv and _state:
        content = _xmltv.generate_xml(_state.get_all_channels(), _state.get_all_streams(), _categories_db)
    else:
        content = '<?xml version="1.0" encoding="UTF-8"?><tv></tv>'
    return StarletteResponse(content, media_type="application/xml")

# ══════════════════════════════════════════════════════════════════
# App principal
# ══════════════════════════════════════════════════════════════════

app, rt = fast_app(
    lifespan=lifespan,
    hdrs=[Link(rel="stylesheet",
               href="https://cdn.jsdelivr.net/npm/pico.css@2/css/pico.min.css")]
)

app.router.routes.insert(0, Route("/playlist_live.m3u8",     _playlist_live))
app.router.routes.insert(0, Route("/playlist_upcoming.m3u8", _playlist_upcoming))
app.router.routes.insert(0, Route("/playlist_vod.m3u8",      _playlist_vod))
app.router.routes.insert(0, Route("/youtube_epg.xml",        _epg_xml))

# ══════════════════════════════════════════════════════════════════
# Rotas SEM extensão — @app.get / @app.post
# ══════════════════════════════════════════════════════════════════

@app.get("/")
def home():
    streams  = _state.get_all_streams() if _state else []
    live     = [s for s in streams if s.get("status") == "live"]
    upcoming = [s for s in streams if s.get("status") == "upcoming"]
    vod      = [s for s in streams if s.get("status") == "none"]
    return Titled("TubeWranglerr",
        Article(
            Header(H2("Dashboard")),
            Ul(
                Li(f"🔴 Ao vivo: {len(live)}"),
                Li(f"📅 Agendados: {len(upcoming)}"),
                Li(f"📼 Gravados: {len(vod)}"),
            ),
            Footer(
                A("⚙️ Configurações", href="/config"), " | ",
                A("📺 Canais",        href="/channels"), " | ",
                A("📋 Logs",          href="/logs"),     " | ",
                A("🔄 Forçar sync",   href="/force-sync")
            )
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
    return RedirectResponse("/", status_code=303)
```

---

### 5.5 Validação obrigatória da Etapa 3

```bash
docker compose build --no-cache
docker compose up -d
sleep 10

docker compose exec tubewranglerr python3 -c "
import urllib.request, urllib.error
rotas = [
    '/', '/config', '/channels', '/logs', '/force-sync',
    '/playlist_live.m3u8', '/playlist_upcoming.m3u8',
    '/playlist_vod.m3u8', '/youtube_epg.xml'
]
ok = True
for rota in rotas:
    try:
        r = urllib.request.urlopen(f'http://localhost:8888{rota}')
        print(f'OK  {rota} → {r.status}')
    except urllib.error.HTTPError as e:
        print(f'ERR {rota} → HTTP {e.code}')
        ok = False
    except Exception as e:
        print(f'ERR {rota} → {e}')
        ok = False
print()
print('✅ Todas as rotas OK' if ok else '❌ Há falhas — NÃO avançar')
"
```

### 5.6 Checklist Etapa 3

```
[ ] web/__init__.py criado (vazio)
[ ] web/main.py criado com o conteúdo da seção 5.4 (incluindo _categories_db no lifespan)
[ ] web/routes/ NÃO existe
[ ] Rotas com extensão usam Route + insert(0,...) — NÃO @app.get
[ ] Rotas sem extensão usam @app.get/@app.post — NÃO @rt
[ ] _categories_db global declarado e carregado no lifespan
[ ] Rotas de playlist e EPG passam _categories_db aos geradores
[ ] docker compose build --no-cache → sem erro
[ ] Script de validação retorna OK nas 9 rotas
[ ] docker inspect tubewranglerr → Health: healthy
[ ] pytest tests/test_web_routes.py -v → 100% passando
[ ] DECISIONS.md atualizado
```

---

## 6. Etapa 4 — Container de Produção

**Pré-requisito:** Checklist da Etapa 3 completo.

```bash
docker compose -f docker-compose.yml up --build -d
sleep 30
docker inspect tubewranglerr --format="{{.State.Health.Status}}"
# Esperado: healthy

docker compose restart
sleep 15
curl http://localhost:8888/
```

### 6.1 Checklist Etapa 4

```
[ ] Build de produção (sem override) sem erro
[ ] Health: healthy
[ ] http://localhost:8888/ acessível
[ ] /data persiste após restart
[ ] config.db em ./data/ após primeiro boot
[ ] streamlink e yt-dlp disponíveis no container de produção
[ ] DECISIONS.md atualizado
```

---

## 7. Etapa 5 — smart_player.py

**Pré-requisito:** Etapas 1-4 completas.

### 7.1 Mudanças — before/after exato

```python
# ══ REMOVER ══
from dotenv import load_dotenv
load_dotenv(dotenv_path=SCRIPT_DIR / ".env")
PLACEHOLDER_IMAGE_URL      = os.getenv("PLACEHOLDER_IMAGE_URL", "")
SMART_PLAYER_LOG_LEVEL_STR = os.getenv("SMART_PLAYER_LOG_LEVEL", "INFO")
SMART_PLAYER_LOG_TO_FILE   = os.getenv("SMART_PLAYER_LOG_TO_FILE", "true").lower() == "true"

# ══ SUBSTITUIR POR ══
from core.config import AppConfig
_cfg = AppConfig()
PLACEHOLDER_IMAGE_URL      = _cfg.get_str("placeholder_image_url")
SMART_PLAYER_LOG_LEVEL_STR = _cfg.get_str("smart_player_log_level")
SMART_PLAYER_LOG_TO_FILE   = _cfg.get_bool("smart_player_log_to_file")

# ══ CAMINHOS — sempre de /data via config ══
STATE_CACHE_PATH = Path("/data") / _cfg.get_str("state_cache_filename")
TEXTS_CACHE_PATH = Path("/data") / "textosepg.json"
```

### 7.2 Checklist Etapa 5

```
[ ] import load_dotenv removido
[ ] Todos os os.getenv() substituídos por AppConfig
[ ] STATE_CACHE_PATH aponta para /data/state_cache.json
[ ] TEXTS_CACHE_PATH aponta para /data/textosepg.json
[ ] DEFAULT_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
[ ] docker compose exec tubewranglerr python3 smart_player.py --help → sem erro
[ ] DECISIONS.md atualizado
```

### 7.3 Cadeia completa de dependências do smart_player.py

Esta é a cadeia que o agente DEVE garantir funcionando end-to-end:

```
[Scheduler.save_files(categories_db)]
    ├── M3UGenerator.generate_playlist() → /data/m3us/playlist_live.m3u8
    │                                    → /data/m3us/playlist_upcoming.m3u8
    │                                    → /data/m3us/playlist_vod.m3u8
    │
    ├── XMLTVGenerator.generate_xml()    → /data/epgs/youtube_epg.xml
    │
    └── texts_cache loop (upcoming)      → /data/textosepg.json
            {"VIDEO_ID": {"line1": "Ao vivo em 2h", "line2": "26 Fev 21:00"}}

[IPTV Player → smart_player.py -i <URL>]
    ├── URL = PLACEHOLDER_IMAGE_URL
    │       → run_ffmpeg_placeholder(url) — sem texto
    │
    ├── URL = ytimg.com/vi/<ID>/...  (thumbnail upcoming)
    │       → get_texts_from_cache(video_id)  ← lê /data/textosepg.json
    │       → run_ffmpeg_placeholder(url, line1, line2)
    │               └── FFmpeg drawtext overlay → mpegts stdout
    │
    ├── URL = youtube.com/watch?v=<ID>  + status "live"
    │       → run_streamlink(url) → mpegts stdout
    │
    ├── URL = youtube.com/watch?v=<ID>  + status "none" (VOD/gravado)
    │       → run_yt_dlp(url) → mpegts stdout
    │
    └── URL = youtube.com/watch?v=<ID>  + status "upcoming"
            → get_texts_from_cache(video_id)
            → run_ffmpeg_placeholder(thumbnail_url, line1, line2)
```

**Validação end-to-end obrigatória antes de fechar Etapa 5:**
```bash
# 1. Verificar que /data/textosepg.json existe após sync
docker compose exec tubewranglerr ls -la /data/textosepg.json

# 2. Verificar que smart_player lê o arquivo sem erro
docker compose exec tubewranglerr python3 -c "
import json
from pathlib import Path
path = Path('/data/textosepg.json')
if path.exists():
    data = json.loads(path.read_text())
    print(f'textosepg.json OK — {len(data)} entradas')
else:
    print('AVISO: textosepg.json ainda não existe — aguardar primeiro sync')
"

# 3. Verificar que FFmpeg e fonte estão disponíveis
docker compose exec tubewranglerr python3 -c "
from pathlib import Path
font = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')
assert font.exists(), f'Fonte não encontrada: {font}'
print('FFmpeg font OK')
"
```

---

## 8. Testes entre Etapas

Cada etapa deve ter seus testes passando antes de avançar.

```bash
# Etapa 1
docker compose exec tubewranglerr pytest tests/test_config.py -v

# Etapa 2
docker compose exec tubewranglerr pytest tests/test_state_manager.py tests/test_youtube_api.py tests/test_playlist_builder.py tests/test_scheduler.py -v

# Etapa 3
docker compose exec tubewranglerr pytest tests/test_web_routes.py -v

# Todos
docker compose exec tubewranglerr pytest -v
```

---

## 9. Revisão Final de Migração

**Pré-requisito:** Etapas 0-5 completas, todos os testes passando.

```
[ ] get_streams.py movido para _archive/
[ ] get_streams.py NÃO é mais importado por nenhum módulo
[ ] grep -r "get_streams" . --include="*.py" retorna vazio (exceto _archive/)
[ ] grep -r "os.getenv" . --include="*.py" retorna vazio (exceto _archive/)
[ ] grep -r "load_dotenv" . --include="*.py" retorna vazio (exceto _archive/)
[ ] grep -r "from flask" . --include="*.py" retorna vazio (exceto _archive/)
[ ] Playlist live/upcoming/vod funcionais no VLC
[ ] EPG carregado corretamente no cliente IPTV
[ ] smart_player.py funciona para live, upcoming e VOD
[ ] DECISIONS.md com todas as etapas documentadas
```

---

## 10. Protocolo DECISIONS.md

Atualizar ao concluir cada etapa. Formato obrigatório:

```markdown
## ETAPA X — <Nome> — <DATA>

**Status:** ✅ Completa

**Decisões tomadas:**
- <decisão 1>
- <decisão 2>

**Problemas encontrados e soluções:**
- <problema> → <solução>

**Checklist:** Todos os itens marcados ✅
```

**Regras:**
- Uma entrada por etapa — nunca duplicar
- Registrar ANTES de avançar para a próxima etapa
- Em caso de dúvida durante execução, registrar e aguardar — nunca assumir
