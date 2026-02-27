# PROMPT DE REFACTOR — TubeWrangler PLAYLIST_URL v1.5

> **Versão:** REFACTOR v1.5
> **Escopo:** Eliminar arquivos M3U/EPG em disco — servir tudo on-the-fly via rotas HTTP
> **Problema:** Playlists salvas em disco ficam defasadas; `playlist_live_proxy.m3u8` só filtra `live` (0 streams agora); arquivos não montados no host
> **Solução:** Rotas on-the-fly que chamam `generate_playlist()` e `generate_xml()` diretamente no request

---

## Rotas novas (substituem arquivos em disco)

| URL                        | mode       | mode_type | Observação                        |
|----------------------------|------------|-----------|-----------------------------------|
| `/playlist/live.m3u`       | live       | direct    |                                   |
| `/playlist/live-proxy.m3u` | live       | proxy     |                                   |
| `/playlist/upcoming-proxy.m3u` | upcoming | proxy   | só proxy (nunca direct)           |
| `/playlist/vod.m3u`        | vod        | direct    |                                   |
| `/playlist/vod-proxy.m3u`  | vod        | proxy     |                                   |
| `/epg.xml`                 | —          | —         | XMLTV on-the-fly (sempre proxy)   |

---

## Fix — web/main.py

### 1. Remover bloco PLAYLIST_FILES e loop de rotas estáticas

Remover completamente:
```python
PLAYLIST_FILES = [...]

for _fname in PLAYLIST_FILES:
    def _make_route(fname):
        @app.get(f"/{fname}")
        ...
    _make_route(_fname)

@app.get("/youtube_epg.xml")
def serve_epg_static():
    ...
```

### 2. Remover _playlist_response (não será mais usada)

```python
# REMOVER:
def _playlist_response(path: Path):
    ...
```

### 3. Adicionar constante de mapeamento e helper on-the-fly

Adicionar após os imports e inicialização de `_state`, `_config`, `_m3u_generator`, `_xmltv_generator`:

```python
# Mapeamento: nome da rota → (mode, mode_type)
_PLAYLIST_ROUTES = {
    "live.m3u":           ("live",     "direct"),
    "live-proxy.m3u":     ("live",     "proxy"),
    "upcoming-proxy.m3u": ("upcoming", "proxy"),
    "vod.m3u":            ("vod",      "direct"),
    "vod-proxy.m3u":      ("vod",      "proxy"),
}


def _serve_playlist_onthefly(mode: str, mode_type: str) -> Response:
    if _m3u_generator is None or _state is None:
        return Response("Servidor ainda inicializando", status_code=503)
    streams = list(_state.get_all_streams())
    cats = _categories_db if _categories_db else {}
    proxy_base = _config.get_str("proxy_base_url") if mode_type == "proxy" else ""
    content = _m3u_generator.generate_playlist(
        streams, cats, mode=mode, mode_type=mode_type, proxy_base_url=proxy_base
    )
    return Response(content, media_type="application/vnd.apple.mpegurl")
```

> **Nota:** `_categories_db` é a variável já existente no lifespan que recebe o dict de categorias.
> Ajustar o nome conforme o real na codebase (pode ser `_cats`, `categories_db`, etc.).

### 4. Adicionar rotas on-the-fly

```python
for _playlist_name, (_mode, _mode_type) in _PLAYLIST_ROUTES.items():
    def _make_playlist_route(mode=_mode, mode_type=_mode_type):
        @app.get(f"/playlist/{_playlist_name}")
        def _playlist_route():
            return _serve_playlist_onthefly(mode, mode_type)
    _make_playlist_route()


@app.get("/epg.xml")
def serve_epg_onthefly():
    if _xmltv_generator is None or _state is None:
        return Response("Servidor ainda inicializando", status_code=503)
    channels = _state.get_all_channels()
    streams = list(_state.get_all_streams())
    cats = _categories_db if _categories_db else {}
    content = _xmltv_generator.generate_xml(channels, streams, cats)
    return Response(content, media_type="application/xml")
```

### 5. (Opcional mas recomendado) Manter compatibilidade com URLs antigas via redirect

```python
# Redirect de URLs antigas para novas — evita quebrar configs existentes no VLC
from starlette.responses import RedirectResponse

_LEGACY_REDIRECTS = {
    "/playlist_live_direct.m3u8":     "/playlist/live.m3u",
    "/playlist_live_proxy.m3u8":      "/playlist/live-proxy.m3u",
    "/playlist_upcoming_proxy.m3u8":  "/playlist/upcoming-proxy.m3u",
    "/playlist_vod_direct.m3u8":      "/playlist/vod.m3u",
    "/playlist_vod_proxy.m3u8":       "/playlist/vod-proxy.m3u",
    "/youtube_epg.xml":               "/epg.xml",
}

for _old, _new in _LEGACY_REDIRECTS.items():
    def _make_redirect(new=_new):
        @app.get(_old)
        def _redirect():
            return RedirectResponse(url=new, status_code=301)
    _make_redirect()
```

---

## Fix — core/scheduler.py

### Simplificar _save_files — remover geração de M3U/EPG, manter só textosepg.json

```python
def _save_files(state, config, m3u_gen, xmltv_gen, categories_db: dict, thumbnail_manager=None):
    """Gera apenas textosepg.json — M3U e EPG agora são servidos on-the-fly via HTTP."""
    all_streams = list(state.get_all_streams())

    # ── Gerar textosepg.json (countdown para smart_player.py) ──
    try:
        texts_cache = {}
        tz = ZoneInfo(config.get_str("local_timezone") or "America/Sao_Paulo")
        for s in all_streams:
            vid = s.get("videoid")
            status = s.get("status")
            sched = s.get("scheduledstarttimeutc")
            if not vid or status != "upcoming" or not sched:
                continue
            if isinstance(sched, str):
                from datetime import datetime
                sched = datetime.fromisoformat(sched.replace("Z", "+00:00"))
            local_dt = sched.astimezone(tz)
            now = datetime.now(tz)
            delta = sched.astimezone(ZoneInfo("UTC")).replace(tzinfo=None) - datetime.utcnow()
            total_seconds = int(delta.total_seconds())
            if total_seconds <= 0:
                line1 = "Ao vivo agora"
            else:
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                if hours >= 24:
                    days = hours // 24
                    line1 = f"Ao vivo em {days}d {hours % 24}h"
                elif hours > 0:
                    line1 = f"Ao vivo em {hours}h {minutes:02d}min"
                else:
                    line1 = f"Ao vivo em {minutes}min"
            line2 = local_dt.strftime("%-d %b %H:%M")
            texts_cache[vid] = {"line1": line1, "line2": line2}

        texts_path = Path("/data/textosepg.json")
        texts_path.write_text(json.dumps(texts_cache, ensure_ascii=False), encoding="utf-8")
        logger.debug(f"textosepg.json: {len(texts_cache)} entradas geradas")
    except Exception as e:
        logger.error(f"Erro ao gerar textosepg.json: {e}")
```

> **Nota:** Manter os imports `json`, `ZoneInfo`, `datetime`, `Path` que já existem no arquivo.
> Remover os imports de `M3UGenerator`/`XMLTVGenerator` do scheduler se não forem mais usados.

### Remover parâmetros m3u_gen e xmltv_gen das chamadas a _save_files

```python
# Todas as chamadas a _save_files no scheduler passam de:
_save_files(state, config, self._m3u_gen, self._xmltv_gen, categories_db, thumbnail_manager)
# Para:
_save_files(state, config, categories_db=categories_db, thumbnail_manager=thumbnail_manager)
```

Atualizar assinatura da função e as ~2 chamadas no corpo do Scheduler.

### Remover set_generators e self._m3u_gen / self._xmltv_gen do Scheduler

```python
# REMOVER do __init__:
self._m3u_gen  = None
self._xmltv_gen = None

# REMOVER método:
def set_generators(self, m3u_gen, xmltv_gen) -> None:
    ...
```

---

## Fix — web/main.py lifespan

### Remover chamadas a set_generators e instâncias de M3UGenerator/XMLTVGenerator do scheduler

```python
# REMOVER do lifespan (ou manter instâncias apenas para as rotas):
_scheduler.set_generators(_m3u_generator, _xmltv_generator)
```

As instâncias `_m3u_generator` e `_xmltv_generator` continuam existindo no módulo `web/main.py`
para uso direto pelas rotas on-the-fly. Apenas param de ser passadas ao scheduler.

---

## Configs a remover do SQLite (opcional — pode manter para não quebrar AppConfig)

```
playlist_save_directory   → não usado após refactor
playlist_live_filename    → não usado
playlist_upcoming_filename → não usado
playlist_vod_filename     → não usado
xmltv_save_directory      → não usado
xmltv_filename            → não usado
generate_direct_playlists → não usado
generate_proxy_playlists  → não usado
```

Recomendação: manter no SQLite por ora (não quebra nada), remover num cleanup posterior.

---

## Validação

```bash
# 1. Restart
docker compose restart && sleep 8

# 2. Testar todas as rotas novas
curl -s http://localhost:8888/playlist/live.m3u | head -5
curl -s http://localhost:8888/playlist/live-proxy.m3u | head -5
curl -s http://localhost:8888/playlist/upcoming-proxy.m3u | head -10
curl -s http://localhost:8888/playlist/vod.m3u | head -5
curl -s http://localhost:8888/playlist/vod-proxy.m3u | head -5
curl -s http://localhost:8888/epg.xml | head -5

# 3. Testar redirect de URLs antigas (deve retornar 301 → seguir redirect)
curl -sv http://localhost:8888/playlist_upcoming_proxy.m3u8 2>&1 | grep -E "Location|HTTP/"

# 4. Contar entradas na upcoming (esperado: 9 streams)
curl -s http://localhost:8888/playlist/upcoming-proxy.m3u | grep -c "^#EXTINF"

# 5. VLC — abrir URL diretamente
# http://100.98.81.67:8888/playlist/upcoming-proxy.m3u
```

---

## Notas para o agente

- `_categories_db` — verificar nome real da variável no lifespan de web/main.py antes de aplicar
- `_m3u_generator` e `_xmltv_generator` — verificar nomes reais no módulo web/main.py
- O loop de rotas com closure (`_make_playlist_route`) é obrigatório para capturar corretamente `mode` e `mode_type` no FastHTML/@app.get
- `generate_playlist` assinatura atual: `(streams, categories_db, mode, mode_type, proxy_base_url="")` — confirmar antes de aplicar
- textosepg.json permanece gerado pelo scheduler (não muda)
- Volume `/data` já está montado no docker-compose — textosepg.json continua funcionando
