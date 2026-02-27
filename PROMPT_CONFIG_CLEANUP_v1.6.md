# PROMPT DE CLEANUP — TubeWrangler CONFIG_CLEANUP v1.6

> **Versão:** CLEANUP v1.6
> **Escopo:** Remover configs obsoletos de disco (M3U/EPG), migrar /api/epg para on-the-fly, limpar docker-compose.yml
> **Arquivos:** core/config.py, web/main.py, docker-compose.yml

---

## 1. core/config.py — Remover configs obsoletos dos defaults

Localizar a lista/dict de defaults (provavelmente `DEFAULT_CONFIG`, `DEFAULTS`, ou lista de tuplas
com `(key, value, section)`). Remover as seguintes entradas:

```
playlist_save_directory
playlist_live_filename
playlist_upcoming_filename
playlist_vod_filename
xmltv_save_directory
xmltv_filename
generate_direct_playlists
generate_proxy_playlists
```

Manter todas as demais configs intactas.

> **Importante:** Após remover dos defaults, fazer também DELETE no SQLite existente para
> não deixar resquício nas instâncias já em execução. Adicionar no início do AppConfig.__init__
> ou em um método de migração:

```python
_OBSOLETE_KEYS = [
    "playlist_save_directory",
    "playlist_live_filename",
    "playlist_upcoming_filename",
    "playlist_vod_filename",
    "xmltv_save_directory",
    "xmltv_filename",
    "generate_direct_playlists",
    "generate_proxy_playlists",
]

def _cleanup_obsolete_keys(self):
    """Remove configs obsoletos do SQLite se existirem."""
    for key in _OBSOLETE_KEYS:
        try:
            self._db.execute(f"DELETE FROM config WHERE key = ?", [key])
        except Exception:
            pass
```

Chamar `self._cleanup_obsolete_keys()` no final do `__init__` do AppConfig.

---

## 2. web/main.py — Migrar /api/epg para on-the-fly

### Substituir a rota /api/epg inteira (linha ~349-354):

```python
# ANTES:
@app.get("/api/epg")
def api_epg():
    epg_path = Path(_config.get_str("xmltv_save_directory")) / _config.get_str("xmltv_filename")
    if not epg_path.exists():
        return JSONResponse({"error": "EPG nao gerado ainda"}, status_code=404)
    return Response(epg_path.read_text(encoding="utf-8"), media_type="application/xml")

# DEPOIS:
@app.get("/api/epg")
def api_epg():
    if _xmltv_generator is None or _state is None:
        return JSONResponse({"error": "Servidor ainda inicializando"}, status_code=503)
    channels = _state.get_all_channels()
    streams = list(_state.get_all_streams())
    cats = _categories_db if _categories_db else {}
    content = _xmltv_generator.generate_xml(channels, streams, cats)
    return Response(content, media_type="application/xml")
```

### Remover o redirect legado de upcoming que não pertence mais ao dict (linha ~64):

```python
# VERIFICAR se existe esta linha e remover:
"/playlist_upcoming_proxy.m3u8": "/playlist/upcoming-proxy.m3u",
# (este redirect já deve estar no dict _LEGACY_REDIRECTS — se duplicado, remover)
```

### Remover import de Path se não for mais usado em nenhum outro lugar:

```python
# Verificar se Path ainda é usado após a remoção da linha 351
# Se não for usado em nenhum outro lugar, remover do import:
from pathlib import Path  # ← remover apenas se não houver outro uso
```

---

## 3. docker-compose.yml — Remover volumes obsoletos

Localizar a seção `volumes:` do serviço `tubewranglerr` e remover as linhas de `/data/m3us` e `/data/epgs`:

```yaml
# REMOVER estas linhas do volumes: do serviço:
- ./data/m3us:/data/m3us
- ./data/epgs:/data/epgs

# MANTER:
- ./data:/data
# (ou equivalente que cubra /data/thumbnails e /data/textosepg.json e /data/state_cache.json)
```

> Se o volume for um único `./data:/data` que cobre tudo, não há nada a remover.

---

## Validação

```bash
# 1. Rebuild para aplicar limpeza do SQLite
docker compose down -v && docker compose build --no-cache && docker compose up -d && sleep 10

# 2. Confirmar que configs obsoletos não aparecem mais
curl -s http://localhost:8888/api/config | python3 -m json.tool | grep -E "playlist_save|xmltv_save|xmltv_file|generate_direct|generate_proxy"
# Esperado: nenhuma saída

# 3. /api/epg on-the-fly funcionando
curl -sI http://localhost:8888/api/epg | grep content-type
curl -s http://localhost:8888/api/epg | head -3
# Esperado: content-type: application/xml + <?xml ...

# 4. /epg.xml ainda funciona
curl -s http://localhost:8888/epg.xml | head -3

# 5. Página /config não mostra campos obsoletos
curl -s http://localhost:8888/config | grep -E "playlist_save|xmltv_save|generate_direct"
# Esperado: nenhuma saída

# 6. Playlists on-the-fly ainda funcionam
curl -s http://localhost:8888/playlist/upcoming-proxy.m3u | grep -c "^#EXTINF"
# Esperado: >= 6
```

---

## Notas para o agente

- `Path` pode ainda ser usado em outros lugares de web/main.py (thumbnail_manager, etc.) — verificar antes de remover o import
- O método `_cleanup_obsolete_keys` deve usar a API do fastlite/SQLite disponível no projeto — ajustar sintaxe conforme o padrão já usado em AppConfig (ex: `self._table`, `self._db`, etc.)
- Não alterar a lógica de nenhuma rota além de `/api/epg`
- Não remover `_xmltv_generator` nem `_m3u_generator` — ainda usados pelas rotas on-the-fly
