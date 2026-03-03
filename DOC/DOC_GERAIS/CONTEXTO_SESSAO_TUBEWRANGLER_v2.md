# CONTEXTO DE SESSÃO — TubeWrangler RETROFIT
> Gerado em: 2026-02-27 12:35 BRT
> Continuar em novo chat com este arquivo como referência

---

## Projeto

**TubeWrangler** — servidor FastHTML/Starlette que monitora canais do YouTube,
gera playlists M3U/EPG e serve streams via proxy (streamlink/ffmpeg).

- **Repo local:** `user1@master2:~/projects/tube_teste`
- **Container:** `tubewranglerr` (docker compose)
- **Porta:** `8888`
- **Branch atual:** `dev`
- **Stack:** Python 3.12, FastHTML, Starlette, Uvicorn, fastlite (SQLite), streamlink, ffmpeg 7.1

---

## Arquivos principais

| Arquivo | Responsabilidade |
|---|---|
| `web/main.py` | FastHTML app, lifespan, todas as rotas |
| `core/config.py` | AppConfig — SQLite via fastlite, cache em memória |
| `core/scheduler.py` | Scheduler async — sync YouTube, _save_files |
| `core/player_router.py` | build_player_command, build_ffmpeg_placeholder_cmd |
| `core/state_manager.py` | StateManager — streams em memória |
| `core/playlist_builder.py` | M3UGenerator, XMLTVGenerator |
| `core/thumbnail_manager.py` | Cache local de thumbnails JPG |
| `smart_player.py` | CLI player (usa core/player_router.py) |

---

## Estado atual — o que está funcionando ✅

1. **proxy_base_url persiste:** `http://100.98.81.67:8888` salvo via `AppConfig.update()` no SQLite
2. **Thumbnails cacheadas:** 16 arquivos em `/data/thumbnails/` — lifecycle correto
3. **Playlist M3U proxy:** `playlist_live_proxy.m3u8` com URLs corretas e `group-title="ESPORTES"`
4. **Stream LIVE via VLC:** `http://100.98.81.67:8888/api/player/bdmOJ3P0MBI` → streamlink → OK
5. **category_mappings:** `17|ESPORTES,22|PESSOAS E BLOGS,24|ENTRETENIMENTO`
6. **Rota /api/player via Route Starlette:** sem decorator @app.get (fix v1.2)
7. **build_ffmpeg_placeholder_cmd:** sem -user_agent, com is_local/input_args (fix v1.1)
8. **_escape_ffmpeg_text:** reescrita com replace() sequencial (fix v1.3)

---

## Problema em aberto ❌

**Stream UPCOMING não reproduz no VLC**
- URL: `http://100.98.81.67:8888/api/player/2zajmVK9DqU`
- Servidor retorna `200 OK` mas `curl ... | wc -c = 0`
- Causa: ffmpeg falha no parse do `-filter_complex` por causa do `:` no texto `28 Fev 19:00`
- `text=28 Fev 19\:00` não funciona via subprocess (sem shell, escape não é interpretado)
- **Solução confirmada:** usar `textfile=<arquivo_temporário>` → teste manual retornou `bytes: 32768` ✅

---

## Próximo passo — aplicar PROMPT_FIX_TEXTFILE_v1.4.md

O prompt v1.4 está gerado e pronto para o agente. Deve ser aplicado agora.

### Resumo das mudanças do v1.4

**`core/player_router.py`:**
- `build_ffmpeg_placeholder_cmd` → usa `textfile=` com `tempfile.NamedTemporaryFile`
- Retorna `(cmd: list[str], temp_files: list[str])` em vez de `list[str]`
- `build_player_command` → também retorna `(cmd, temp_files)`
- `build_streamlink_cmd` e `build_ytdlp_cmd` → sem alteração (retornam list)
- Adicionar imports: `import os`, `import tempfile`

**`web/main.py` — rota `api_player_stream`:**
- `cmd, temp_files = build_player_command(...)`
- No `finally` do generator: `for tf in temp_files: os.unlink(tf)`
- Adicionar `import os` se não existir

**`smart_player.py`:**
- `cmd, temp_files = build_player_command(...)`
- Após processo terminar: deletar temp_files

---

## Fixes aplicados (histórico completo)

| Fix | Arquivo | Mudança |
|---|---|---|
| v1.0 | `web/main.py` | lifespan chama `set_generators`, `set_categories_db`; `AppConfig.update()` na rota PUT /api/config |
| v1.0 | `core/config.py` | `set()` chama `self.reload()` — mantido |
| v1.1 | `core/player_router.py` | Remove `-user_agent`; adiciona `is_local`/`input_args`; remove `-shortest` |
| v1.2 | `web/main.py` | Rota `/api/player/{video_id}` via `Route` Starlette direta (sem @app.get) |
| v1.3 | `core/player_router.py` | `_escape_ffmpeg_text` com `replace()` sequencial (não usada após v1.4) |
| v1.4 | `core/player_router.py` + `web/main.py` + `smart_player.py` | `textfile=` para drawtext; retorno tuple (cmd, temp_files) — **PENDENTE** |

---

## Validação esperada após v1.4

```bash
# Bytes do upcoming > 0
curl -s --max-time 5 http://localhost:8888/api/player/2zajmVK9DqU | wc -c
# Esperado: > 10000

# VLC abre upcoming
# http://100.98.81.67:8888/api/player/2zajmVK9DqU
# Esperado: imagem estática com texto "Ao vivo em Xh" e horário
```

---

## Checklist final de validação (antes do merge)

- [x] ProxyBase=http://100.98.81.67:8888 persiste após restart
- [x] Thumbnails em disco (16+ arquivos)
- [x] playlist_live_proxy.m3u8 com URLs corretas
- [x] group-title="ESPORTES" na playlist
- [x] Stream LIVE abre no VLC
- [ ] Stream UPCOMING abre no VLC ← pendente (fix v1.4)
- [ ] Playlist expande canais no VLC ← testar após v1.4
- [ ] Merge dev → main

---

## Comandos úteis

```bash
# Logs em tempo real
docker compose logs tubewranglerr -f | grep -E "ERROR|ProxyBase|player|Arquivo"

# Forçar sync
curl -s -X PUT http://localhost:8888/api/playlists/refresh

# Testar stream
curl -s --max-time 5 http://localhost:8888/api/player/2zajmVK9DqU | wc -c

# Ver config atual
curl -s http://localhost:8888/api/config | python3 -m json.tool | grep -E "proxy|category|thumb"

# Merge quando pronto
git add -A
git commit -m "fix: textfile drawtext, Route Starlette, proxy_base_url, thumbnails"
git checkout main
git merge dev --no-ff -m "feat: RETROFIT v1.0 completo"
git push origin main
```
