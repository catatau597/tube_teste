# REFACTORING_TUBEWRANGLERR.md

> **Versão:** 3.4
> **Projeto:** TubeWranglerr
> **Destino:** Agente autônomo GitHub Copilot
> **Objetivo:** Refatoração completa para stack FastHTML + SQLite em container standalone
> **Abordagem:** Container-First Development
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
PROIBIDO: Usar @rt sem URL explícita (ex: @rt sem parênteses ou sem argumento de path)
PROIBIDO: Bloquear execução por erros do Pylance sobre fasthtml.common
PROIBIDO: Apagar arquivos originais antes da Etapa 9
PROIBIDO: Duplicar entradas no DECISIONS.md
PROIBIDO: Iniciar desenvolvimento sem o volume .:/app no docker-compose.override.yml
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
│   ├── playlist_builder.py
│   └── scheduler.py
│
├── web/
│   ├── __init__.py
│   └── main.py              ← TODAS as rotas ficam aqui na Etapa 3
│                              web/routes/ só é criado na Etapa 3 APÓS validação
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
├── _archive/                # Criado na Etapa 9
├── data/                    # Volume Docker — NUNCA versionar
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
    "title_filter_expressions":      ("ao vivo,AO VIVO,cortes,react,ge.globo,#live",
                                      "filters", "Expressões a remover dos títulos (vírgula)", "list"),
    "prefix_title_with_channel_name":("true", "filters", "Prefixar título com nome do canal", "bool"),
    "prefix_title_with_status":      ("true", "filters", "Prefixar título com status [Ao Vivo] etc", "bool"),
    "category_mappings":             ("Sports|ESPORTES,Gaming|JOGOS,News & Politics|NOTICIAS",
                                      "filters", "Mapeamento categorias API|Exibição (vírgula)", "mapping"),
    "channel_name_mappings":         ("Canal GOAT|GOAT,TNT Sports Brasil|TNT Sports",
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
                        self.update(lower_key, v.strip().strip('"\'\"').strip("'"))
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
1. core/state_manager.py   — sem deps de outros módulos core
2. core/youtube_api.py     — sem deps de outros módulos core
3. core/playlist_builder.py — depende de config apenas
4. core/scheduler.py       — depende dos 3 anteriores
```

### 4.2 Assinaturas obrigatórias

```python
class StateManager:
    def __init__(self, config: AppConfig, cache_path: Path | None = None): ...

class YouTubeAPI:
    def __init__(self, api_key: str): ...   # api_key vem do CHAMADOR

class M3UGenerator:
    def __init__(self, config: AppConfig): ...

class XMLTVGenerator:
    def __init__(self, config: AppConfig): ...

class Scheduler:
    def __init__(self, config: AppConfig, scraper: YouTubeAPI, state: StateManager): ...
    def reload_config(self, new_config: AppConfig): ...
    def trigger_now(self): ...              # para o /force-sync
    async def run(self, initial_run_delay: bool = False): ...
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

    cfg = AppConfig()
    sm  = StateManager(cfg)
    sch = Scheduler.__new__(Scheduler)

    # StateManager — métodos obrigatórios
    assert hasattr(sm,  'load_from_disk'),   'FALTA: StateManager.load_from_disk()'
    assert hasattr(sm,  'save_to_disk'),     'FALTA: StateManager.save_to_disk()'
    assert hasattr(sm,  'get_all_streams'),  'FALTA: StateManager.get_all_streams()'
    assert hasattr(sm,  'get_all_channels'), 'FALTA: StateManager.get_all_channels()'

    # Scheduler — métodos obrigatórios
    assert hasattr(sch, 'trigger_now'),      'FALTA: Scheduler.trigger_now()'
    assert hasattr(sch, 'reload_config'),    'FALTA: Scheduler.reload_config()'
    assert hasattr(sch, 'run'),              'FALTA: Scheduler.run()'

    print('OK — todos os métodos obrigatórios presentes')
    "

[ ] Script acima retorna "OK — todos os métodos obrigatórios presentes"
[ ] DECISIONS.md atualizado
```

**Por que esse check é necessário:**
A Etapa 3 (main.py) chama métodos específicos de StateManager e Scheduler.
Se esses métodos não existirem, o app sobe mas crasha em runtime ao receber
a primeira requisição. O agente interpreta isso como problema do FastHTML ou
de roteamento, quando a causa real é código incompleto na Etapa 2.

### 4.4 Implementações mínimas — o que NÃO pode ficar como stub

Os módulos da Etapa 2 podem ter implementação simplificada, mas estes
métodos específicos precisam funcionar antes de avançar para a Etapa 3:

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

def get_all_channels(self) -> list:
    if isinstance(self.streams, dict):
        return list(self.streams.keys())
    return []
```

#### Scheduler — implementação mínima obrigatória

```python
def __init__(self, config: AppConfig, scraper, state: StateManager):
    import asyncio
    self._config  = config
    self._scraper = scraper
    self._state   = state
    self._trigger_event = asyncio.Event()   # ← obrigatório para trigger_now()

def trigger_now(self):
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        loop.call_soon_threadsafe(self._trigger_event.set)
    except RuntimeError:
        pass

def reload_config(self, new_config: AppConfig):
    self._config = new_config

async def run(self, initial_run_delay: bool = False):
    import asyncio
    while True:
        await self._trigger_event.wait()
        self._trigger_event.clear()
        # implementação real virá na Etapa 4+
        await asyncio.sleep(1)
```

**Armadilha de indentação em Python:**
Métodos adicionados com indentação errada ficam fora da classe e não
aparecem em `dir(Classe)`. Isso faz o agente pensar que o arquivo
"não sincronizou". Sempre validar com:

```bash
docker compose exec tubewranglerr python3 -c "
import inspect, core.scheduler as m
src = inspect.getsource(m.Scheduler.trigger_now)
print(src[:100])
print('--- indentação OK')
"
```
Se retornar `AttributeError`, o método está fora da classe.

---

## 5. Etapa 3 — Interface FastHTML

**Pré-requisito:** Checklist da Etapa 2 completo (incluindo validação de métodos).

### ⚠️ AVISO CRÍTICO — Leia antes de escrever qualquer código desta etapa

Esta etapa tem **três armadilhas documentadas** que causaram falhas na execução real.
O agente deve ler esta seção completamente antes de criar qualquer arquivo.

---

### 5.1 Armadilha 1 — `@rt` e instâncias locais

**Problema:** O `rt` retornado por `fast_app()` é uma instância **local** do app.
Importar `rt` de `main.py` para usar em outro arquivo **não registra as rotas no app principal**.

```python
# ❌ ERRADO — causa 404 silencioso
# web/routes/config.py
from web.main import rt          # rt de main.py não serve aqui

@rt("/config")
def get(): ...                   # Esta rota NUNCA é registrada no app principal
```

```python
# ✅ CORRETO — todas as rotas em main.py com @app.get/@app.post
# web/main.py
app, rt = fast_app(lifespan=lifespan)

@app.get("/config")              # método HTTP explícito no decorador
def config_page(): ...           # nome da função é livre — não precisa ser "get"

@app.post("/config")
async def save_config(request): ...
```

**Regra:** `@app.get` e `@app.post` são **preferidos** a `@rt` porque o método HTTP
fica explícito no decorador e o nome da função é livre. Use `@rt` apenas se souber
exatamente o que está fazendo.

---

### 5.2 Armadilha 2 — Rota catch-all do FastHTML intercepta extensões

**Problema:** O FastHTML registra internamente uma rota `/{fname:path}.{ext:static}`
para servir arquivos estáticos. Qualquer URL com extensão (`.xml`, `.m3u8`, `.json`,
`.txt`) é **interceptada por essa rota antes de chegar no seu handler**.

```python
# ❌ ERRADO — o @app.get nunca é chamado para .xml e .m3u8
@app.get("/youtube_epg.xml")
def epg_xml(): ...               # 404 — a rota catch-all interceptou antes
```

```python
# ✅ CORRETO — registrar via Starlette Route no TOPO da lista, antes do catch-all
from starlette.routing import Route
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse

async def _epg_xml(req: StarletteRequest):
    return StarletteResponse(
        '<?xml version="1.0" encoding="UTF-8"?><tv></tv>',
        media_type="application/xml"
    )

app, rt = fast_app(lifespan=lifespan)

# insert(0, ...) coloca a rota ANTES do catch-all interno do FastHTML
app.router.routes.insert(0, Route("/youtube_epg.xml", _epg_xml))
```

**Regra:** Toda URL com extensão de arquivo usa `Route` + `insert(0, ...)`.
Toda URL sem extensão (`/`, `/config`, `/channels`, `/logs`) usa `@app.get`/`@app.post`.

---

### 5.3 Armadilha 3 — Erros do Pylance são falsos positivos

**Problema:** `from fasthtml.common import *` usa wildcard import.
O Pylance (analisador estático do VS Code) não consegue resolver wildcard imports
e reporta erros como "import não resolvido", "variável não definida" etc.

**Esses erros NÃO afetam a execução no container.** O container executa com
sucesso mesmo com a IDE reportando erros.

**Regra:** Nunca bloquear ou reverter código por causa de erros do Pylance
sobre `fasthtml.common`. A validação real é sempre no container:
```bash
docker compose exec tubewranglerr python3 -c "from fasthtml.common import *; print('OK')"
```

---

### 5.4 web/main.py — arquivo canônico completo

Este é o arquivo **completo e validado** para a Etapa 3.
O agente deve usar este arquivo exatamente como está — sem criar `web/routes/`.

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
from core.config import AppConfig
from core.state_manager import StateManager
from core.youtube_api import YouTubeAPI
from core.scheduler import Scheduler
from core.playlist_builder import M3UGenerator, XMLTVGenerator

_config: AppConfig | None     = None
_state: StateManager | None   = None
_scheduler: Scheduler | None  = None
_m3u: M3UGenerator | None     = None
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

# ══════════════════════════════════════════════════════════════════
# Rotas COM extensão — registradas via Starlette (bypass do catch-all)
# MOTIVO: FastHTML intercepta /{fname}.{ext:static} antes de @app.get
# ══════════════════════════════════════════════════════════════════

async def _playlist_live(req: StarletteRequest):
    content = _m3u.generate_playlist(_state.get_all_streams(), {}, "live") if _m3u and _state else "#EXTM3U\n"
    return StarletteResponse(content, media_type="application/vnd.apple.mpegurl")

async def _playlist_upcoming(req: StarletteRequest):
    content = _m3u.generate_playlist(_state.get_all_streams(), {}, "upcoming") if _m3u and _state else "#EXTM3U\n"
    return StarletteResponse(content, media_type="application/vnd.apple.mpegurl")

async def _playlist_vod(req: StarletteRequest):
    content = _m3u.generate_playlist(_state.get_all_streams(), {}, "vod") if _m3u and _state else "#EXTM3U\n"
    return StarletteResponse(content, media_type="application/vnd.apple.mpegurl")

async def _epg_xml(req: StarletteRequest):
    if _xmltv and _state:
        content = _xmltv.generate_xml(_state.get_all_channels(), _state.get_all_streams(), {})
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

# Rotas com extensão inseridas no TOPO (antes do catch-all do FastHTML)
app.router.routes.insert(0, Route("/playlist_live.m3u8",     _playlist_live))
app.router.routes.insert(0, Route("/playlist_upcoming.m3u8", _playlist_upcoming))
app.router.routes.insert(0, Route("/playlist_vod.m3u8",      _playlist_vod))
app.router.routes.insert(0, Route("/youtube_epg.xml",        _epg_xml))

# ══════════════════════════════════════════════════════════════════
# Rotas SEM extensão — @app.get / @app.post (funcionam normalmente)
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
                A("📺 Canais", href="/channels"),      " | ",
                A("📋 Logs", href="/logs"),             " | ",
                A("🔄 Forçar sync", href="/force-sync")
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
            Label("Handles (@canal)",
                  Input(name="target_channel_handles", value=handles)),
            Label("IDs diretos",
                  Input(name="target_channel_ids", value=ids)),
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

# Testar TODAS as rotas
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

**Esperado:** `OK` em todas as 9 rotas + `✅ Todas as rotas OK`

### 5.6 Checklist Etapa 3

```
[ ] web/__init__.py criado (vazio)
[ ] web/main.py criado com o conteúdo da seção 5.4
[ ] web/routes/ NÃO existe (ou só tem __init__.py vazio)
[ ] Rotas com extensão usam Route + insert(0,...) — NÃO @app.get
[ ] Rotas sem extensão usam @app.get/@app.post — NÃO @rt
[ ] Nenhum @rt usado em nenhum arquivo desta etapa
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

# Verificar persistência após restart
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

# ══ CAMINHOS ══
STATE_CACHE_PATH = Path("/data") / _cfg.get_str("state_cache_filename")
TEXTS_CACHE_PATH = Path("/data") / "textosepg.json"
```

### 7.2 Checklist Etapa 5

```
[ ] import load_dotenv removido
[ ] Todos os os.getenv() substituídos por AppConfig
[ ] STATE_CACHE_PATH e TEXTS_CACHE_PATH apontam para /data/
[ ] grep load_dotenv smart_player.py retorna vazio
[ ] python3 smart_player.py --help executa sem erro no container
[ ] DECISIONS.md atualizado
```

---

## 8. Testes entre Etapas

**Todos os testes usam `tmp_path` — nunca tocam `/data/config.db`.**

### 8.1 tests/test_config.py

```python
import pytest
from core.config import AppConfig, DEFAULTS

@pytest.fixture
def cfg(tmp_path):
    return AppConfig(db_path=tmp_path / "test.db")

def test_total_43_chaves(cfg):
    assert len(DEFAULTS) == 43

def test_get_int(cfg):
    assert cfg.get_int("scheduler_main_interval_hours") == 4

def test_get_bool_true(cfg):
    assert cfg.get_bool("enable_scheduler_active_hours") is True

def test_get_list(cfg):
    assert "17" in cfg.get_list("allowed_category_ids")

def test_get_mapping(cfg):
    assert cfg.get_mapping("category_mappings").get("Sports") == "ESPORTES"

def test_update_persiste(cfg, tmp_path):
    cfg.update("http_port", "9999")
    assert AppConfig(db_path=tmp_path / "test.db").get_int("http_port") == 9999

def test_chave_desconhecida_keyerror(cfg):
    with pytest.raises(KeyError):
        cfg.update("chave_inexistente", "valor")

def test_rows_sao_dicionarios(cfg):
    for row in cfg._db.t.config.rows:
        assert isinstance(row, dict), f"Row deveria ser dict, é {type(row)}"
        assert "key" in row and "value" in row
```

### 8.2 tests/test_web_routes.py

```python
import pytest
from httpx import AsyncClient, ASGITransport
from web.main import app

@pytest.mark.asyncio
async def test_home():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/")
        assert r.status_code == 200

@pytest.mark.asyncio
async def test_config_get():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/config")
        assert r.status_code == 200

@pytest.mark.asyncio
async def test_channels():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/channels")
        assert r.status_code == 200

@pytest.mark.asyncio
async def test_playlist_live():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/playlist_live.m3u8")
        assert r.status_code == 200
        assert "#EXTM3U" in r.text

@pytest.mark.asyncio
async def test_epg_xml():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/youtube_epg.xml")
        assert r.status_code == 200
        assert "<?xml" in r.text
```

---

## 9. Revisão Final de Migração

```bash
# Suite completa
docker compose exec tubewranglerr pytest tests/ -v --tb=short

# Verificações de limpeza
docker compose exec tubewranglerr grep -rn "os.getenv\|load_dotenv" core/ web/ smart_player.py
docker compose exec tubewranglerr grep -rn "from flask\|import Flask" core/ web/
# Esperado: nenhuma saída

# Import completo
docker compose exec tubewranglerr python3 -c "
from core.config import AppConfig
from core.state_manager import StateManager
from core.youtube_api import YouTubeAPI
from core.playlist_builder import M3UGenerator, XMLTVGenerator
from core.scheduler import Scheduler
print('✅ Todos os módulos OK')
"
```

### 9.1 Arquivamento e commit final

```bash
mkdir -p _archive
cp get_streams.py _archive/get_streams.py.bak
cp file.env _archive/file.env.bak
git add .
git commit -m "refactor: migração completa FastHTML + SQLite + Docker container-first"
```

---

## 10. Protocolo DECISIONS.md

### Template inicial

```markdown
# DECISIONS.md — TubeWranglerr Refactoring Log

Referência: REFACTORING_TUBEWRANGLERR.md v3.3

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

## Decisões

### [ETAPA-0] Decisão de arquitetura container-first
Nenhum Python, ffmpeg, streamlink ou yt-dlp roda no host.
Todo comando via docker compose exec tubewranglerr.

## Dúvidas e Bloqueios

[Uma entrada por dúvida — nunca duplicar]
```

---

## Apêndice — .github/copilot-instructions.md

```markdown
# Copilot Instructions — TubeWranglerr v3.3

## Ambiente
- VS Code Windows → SSH → Debian → Docker (python:3.12-slim)
- TODO Python executa DENTRO do container
- Comando padrão: docker compose exec tubewranglerr <comando>
- NUNCA instalar Python, pip, ffmpeg, streamlink ou yt-dlp no host
- NUNCA iniciar desenvolvimento sem o volume .:/app no docker-compose.override.yml

## Regras críticas

### FastHTML — 3 armadilhas conhecidas

1. **`@rt` é instância local** — nunca importar `rt` de `main.py` em outros arquivos.
   Usar `@app.get`/`@app.post` com URL explícita em `web/main.py`.

2. **Catch-all intercepta extensões** — URLs com `.xml`, `.m3u8`, `.json` etc
   precisam ser registradas via `Route` + `app.router.routes.insert(0, ...)`.
   Usar `@app.get` apenas para URLs sem extensão.

3. **Pylance reporta falsos positivos** — `from fasthtml.common import *` causa
   avisos no Pylance. Ignorar. Validar sempre no container, não na IDE.

### Código
- NUNCA usar os.getenv() — sempre AppConfig de core/config.py
- NUNCA importar Flask
- fastlite retorna rows como DICIONÁRIOS: row["key"], NUNCA row.key
- AppConfig sempre injetado como parâmetro, nunca global
- web/routes/ não existe na Etapa 3

### Etapa 2 — Validação obrigatória antes de avançar
Executar o script de validação de métodos da seção 4.3 antes de avançar para a Etapa 3.
Sem esse check, o app sobe mas crasha em runtime com AttributeError.

### Indentação
Métodos adicionados fora da classe não aparecem em `dir()`.
Sempre validar com `inspect.getsource(Classe.metodo)` — se retornar AttributeError,
o método está fora da classe.

## Stack
- Web: python-fasthtml
- Config/DB: fastlite (SQLite) — rows são dicionários
- Streams ao vivo: streamlink (subprocesso no container)
- VOD: yt-dlp (subprocesso no container)
- Thumbnails: ffmpeg + fonts-dejavu-core (no container)
- Testes: pytest + pytest-asyncio com tmp_path

## Commits
- Apenas após pytest tests/ -v retornar 0 falhas no container
```

---

*Versão 3.4 — Incorpora lições da Etapa 2: volume `.:/app` obrigatório, validação de métodos antes de avançar, implementações mínimas de StateManager e Scheduler, armadilha de indentação.*
