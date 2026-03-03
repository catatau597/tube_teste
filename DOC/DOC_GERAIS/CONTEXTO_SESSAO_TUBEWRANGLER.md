# CONTEXTO_SESSAO_TUBEWRANGLER.md

> **Projeto:** TubeWrangler (refatoração do get_streams.py)
> **Status atual:** Em desenvolvimento — branch `dev`
> **Data:** 2026-02-27
> **Objetivo:** Portar get_streams.py (Flask + .env) para stack FastHTML + SQLite em container Docker

---

## O que é o projeto

Script original `get_streams.py` — monitora canais do YouTube, gera playlists M3U e EPG XMLTV.
Está sendo refatorado para rodar como serviço web com interface de configuração via browser.

**Stack nova:**
- `python-fasthtml` + `fastlite` (SQLite) no lugar de Flask + .env
- Container Docker standalone
- Interface web em `localhost:8888`

---

## Estrutura do projeto

```
tubewranglerr/
├── core/
│   ├── config.py          ✅ Completo — AppConfig com SQLite (43 chaves)
│   ├── state_manager.py   ✅ Completo — meta, channels, streams, load/save
│   ├── youtube_api.py     ✅ Completo — resolve_channel_handles_to_ids,
│   │                                    ensure_channel_titles, fetch_streams_by_ids,
│   │                                    fetch_all_streams_for_channels_using_playlists
│   ├── playlist_builder.py ⚠️ Parcial — M3UGenerator e XMLTVGenerator portados,
│   │                                     ContentGenerator.is_live() precisa validação
│   └── scheduler.py       🔧 Em correção — main loop incompleto (ver seção abaixo)
├── web/
│   └── main.py            ✅ Completo — lifespan, logging, rotas, force-sync
├── get_streams.py          📎 Original — fonte de verdade para portar lógica
├── data/
│   ├── config.db           ✅ Populado com credenciais
│   ├── tubewrangler.log    ✅ Logging funcionando
│   └── state_cache.json    (gerado após primeiro sync)
└── REFACTORING_TUBEWRANGLERR_v3.6.md  📋 Documento principal do agente
```

---

## Estado atual confirmado pelos logs

```
2026-02-27 01:59:27 INFO  TubeWrangler  === TubeWrangler iniciando ===
2026-02-27 01:59:27 INFO  TubeWrangler  Cache carregado do disco: None | canais=0 streams=0
2026-02-27 01:59:27 INFO  TubeWrangler  Handles configurados : ['@xsports.brasil', '@cazetv']
2026-02-27 01:59:28 INFO  TubeWrangler  Handles resolvidos: {
                                          'UCH-BU-Os3JSo2L8lBQxE8KA': 'Xsports',
                                          'UCZiYbVptd3PVPf4f6eR6UaQ': 'CazéTV'
                                        }
2026-02-27 01:59:28 INFO  TubeWrangler  Canais prontos: 2 — ['CazéTV', 'Xsports']
2026-02-27 01:59:28 INFO  TubeWrangler  Scheduler iniciado.
```

✅ Logging funcionando
✅ 2 handles resolvidos corretamente via API
✅ 2 canais carregados no estado
❌ Scheduler não busca streams — `run()` é stub, nunca chama fetch

---

## Problema atual: core/scheduler.py incompleto

O `run()` atual tem apenas o loop com `asyncio.Event` mas **não executa busca**.
O arquivo `PROMPT_SCHEDULER_COMPLETO.md` foi gerado com a implementação completa pronta.

### O que o Scheduler completo precisa fazer (portado do get_streams.py):

1. **Main loop** — busca a cada `scheduler_main_interval_hours` (padrão: 4h)
2. **Full sync vs incremental** — `publishedAfter=None` ou data da última run
3. **Pre-event** — refresca streams prestes a ir ao vivo (`scheduler_pre_event_window_hours`)
4. **Post-event** — monitora streams que estão live
5. **Stale check** — refresca streams live/upcoming não atualizados há `stale_hours`
6. **`_save_files()`** — gera e salva M3U + EPG após cada busca
7. **Force-sync** — `asyncio.wait_for` no sleep, reseta `last_main_run`

---

## Bugs já corrigidos nesta sessão

| Bug | Causa | Correção aplicada |
|---|---|---|
| BUG-001 | POST /config usava `query_params` | `async def` + `await req.form()` |
| BUG-002 | lifespan não resolvia handles | Adicionado `resolve_channel_handles_to_ids` + `ensure_channel_titles` |
| BUG-003 | Sem logging | `logging.basicConfig()` com stdout + arquivo `/data/` |
| BUG-004 | `trigger_now()` sem `asyncio.Event` | `set_force_event()` + `asyncio.wait_for` |
| BUG-005 | `youtube_api.py` só tinha `__init__` | Todos os métodos portados do get_streams.py |
| BUG-006 | `state_manager.py` sem `meta` e `channels` | Atributos adicionados com compatibilidade total |

---

## Próximo passo imediato

**Aplicar `PROMPT_SCHEDULER_COMPLETO.md`** ao agente.

O prompt contém o `core/scheduler.py` completo pronto para colar — basta o agente:
1. Inspecionar o arquivo atual (PASSO 1)
2. Substituir pelo conteúdo completo (PASSO 2)
3. Validar métodos (PASSO 3)
4. Reiniciar e verificar logs esperados (PASSO 4):
   ```
   --- Scheduler: verificação principal ---
   Scheduler: full sync. Reason=first_run
   Buscando streams playlistItems para 2 canais...
   Update Streams: Adicionados X, ...
   Arquivos salvos: playlist_live.m3u8, ...
   ```

---

## Pendências após o Scheduler funcionar

| Prioridade | Tarefa |
|---|---|
| 1 | Validar `playlist_builder.py` — `ContentGenerator.is_live()`, `M3UGenerator`, `XMLTVGenerator` |
| 2 | Rota `/logs` — SSE stream de logs em tempo real |
| 3 | Dashboard com contagem real de live/upcoming/vod |
| 4 | Teste de todas as 9 rotas (checklist Etapa 3) |
| 5 | Merge para `main` (checklist Etapa 4) |

---

## Arquivos de referência desta sessão

| Arquivo | Conteúdo |
|---|---|
| `get_streams.py` | Script original — fonte de verdade |
| `REFACTORING_TUBEWRANGLERR_v3.6.md` | Documento principal do agente (v3.6) |
| `PROMPT_SCHEDULER_COMPLETO.md` | Scheduler completo pronto para aplicar |
| `PROMPT_FIX_LOGGING_CANAIS_FORCESYNC.md` | Fix do logging + lifespan (já aplicado) |

---

## Regras que o agente DEVE seguir (resumo)

```
PROIBIDO: os.getenv() em qualquer arquivo novo
PROIBIDO: Flask em qualquer arquivo novo
PROIBIDO: Push direto na main
PROIBIDO: Reescrever arquivos inteiros sem inspecionar primeiro
PROIBIDO: Criar stubs — implementação portada do get_streams.py
OBRIGATÓRIO: Inspecionar código atual antes de qualquer edição
OBRIGATÓRIO: Validar com docker compose exec após cada mudança
OBRIGATÓRIO: Commit após cada passo validado
OBRIGATÓRIO: get_all_channels() retorna dict {id: title}
OBRIGATÓRIO: Handler POST é async + await request.form()
```
