# Correção de Bug: Persistência POST /config

## Data
26 de fevereiro de 2026

## Contexto
- Branch: dev
- Arquivo: web/main.py
- Container: tubewranglerr

## Erro
O handler POST `/config` não persistia alterações de configuração. Motivos:
1. Usava `req.query_params` para ler dados, que retorna vazio em POST.
2. Não era `async`, impedindo uso de `await req.form()`.

## Sintomas
- POST /config retornava 303, mas não salvava nada no banco.
- Logs mostravam requisições, sem erros, mas sem persistência.

## Correção
- Handler tornado `async`.
- Substituído `req.query_params` por `await req.form()`.
- Lógica de atualização mantida, apenas fonte dos dados corrigida.

## Validação
- Container reiniciado.
- Teste via curl: POST /config retorna 303.
- Valor persistido no banco (verificado via Python).
- Inspeção do código confirma uso de `await req.form()` e `async def`.

## Decisão
Correção aplicada apenas no handler POST `/config` em `web/main.py`. Não foram feitas alterações em outros arquivos ou métodos.

## Status
Bug resolvido. Persistência validada e logs sem erros.

## Bug fix: logging ausente + canais não carregados + force-sync quebrado

### Contexto
Branch: dev
Arquivo principal: web/main.py

### Problemas confirmados

**1. Logging não configurado**
web/main.py não chama logging.basicConfig() — nenhuma mensagem aparece nos docker logs. O original (get_streams.py) configura logging com formato idêntico, handler de arquivo + console.

**2. Canais não carregados (root cause do "Nenhum canal configurado")**
O lifespan cria AppConfig → StateManager → YouTubeAPI → Scheduler, mas NUNCA chama:
  - scraper.resolve_channel_handles_to_ids()
  - scraper.ensure_channel_titles()
Resultado: _state.channels fica vazio → home() exibe "Nenhum canal".

**3. force-sync quebrado**
_scheduler.trigger_now() não existe no Scheduler atual. O Scheduler precisa de um asyncio.Event para aceitar trigger externo.

---

### Correções obrigatórias em web/main.py

#### 1. Adicionar logging no topo do arquivo (após os imports)

```python
import logging
import sys

def _get_log_level(level_str: str) -> int:
    return getattr(logging, level_str.upper(), logging.INFO)

_LOG_LEVEL_STR = "INFO"   # será sobrescrito após AppConfig carregar
_LOG_LEVEL     = _get_log_level(_LOG_LEVEL_STR)
_LOG_FILE      = Path("/data/tubewrangler.log")

logging.basicConfig(
    format  = "%(asctime)s %(levelname)-8s %(name)s %(message)s",
    datefmt = "%Y-%m-%d %H:%M:%S",
    level   = _LOG_LEVEL,
    force   = True,
    handlers= [
        logging.StreamHandler(sys.stdout),           # docker logs
        logging.FileHandler(_LOG_FILE, mode="a"),    # arquivo persistente
    ]
)
logger = logging.getLogger("TubeWrangler")
```

#### Corrigir o lifespan — resolver handles e carregar canais
Substituir o bloco lifespan atual por:

```python
@asynccontextmanager
async def lifespan(app):
    global _config, _state, _scheduler

    _config = AppConfig()

    # Reconfigurar logging com nível do banco
    log_level = _get_log_level(_config.get_str("log_level"))
    logging.getLogger().setLevel(log_level)
    logger.info("=== TubeWrangler iniciando ===")
    logger.info(f"Log level: {_config.get_str('log_level')}")

    _state  = StateManager(_config)
    cache_loaded = _state.load_from_disk()

    api_key  = _config.get_str("youtube_api_key")
    handles  = [h.strip() for h in _config.get_str("target_channel_handles").split(",") if h.strip()]
    chan_ids = [i.strip() for i in _config.get_str("target_channel_ids").split(",") if i.strip()]

    logger.info(f"Canais handles: {handles}")
    logger.info(f"Canais IDs: {chan_ids}")

    scraper = YouTubeAPI(api_key)

    # Resolver handles → IDs (igual ao get_streams.py original)
    all_target_ids = set(chan_ids)
    if handles:
        logger.info(f"Resolvendo {len(handles)} handles...")
        resolved = scraper.resolve_channel_handles_to_ids(handles, _state)
        all_target_ids.update(resolved.keys())
        logger.info(f"Handles resolvidos: {resolved}")

    # Garantir títulos para todos os IDs
    if all_target_ids:
        final_channels = scraper.ensure_channel_titles(all_target_ids, _state)
        logger.info(f"Canais carregados: {len(final_channels)} — {list(final_channels.values())}")
    else:
        logger.warning("Nenhum canal alvo encontrado. Verifique target_channel_handles e target_channel_ids.")

    _scheduler = Scheduler(_config, scraper, _state)
    task = asyncio.create_task(_scheduler.run())
    logger.info("Scheduler iniciado.")

    yield

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    _state.save_to_disk()
    logger.info("=== TubeWrangler encerrado ===")
```

#### Corrigir force-sync
Adicionar _force_sync_event = asyncio.Event() como global e passar ao Scheduler.

No Scheduler (core/scheduler.py), adicionar suporte a trigger externo:

```python
# Em __init__:
self._force_event: asyncio.Event | None = None

def set_force_event(self, event: asyncio.Event):
    self._force_event = event

def trigger_now(self):
    if self._force_event:
        self._force_event.set()
        logger.info("Scheduler: trigger manual solicitado.")
```
No loop run() do Scheduler, substituir o await asyncio.sleep(60) final por:

```python
try:
    await asyncio.wait_for(
        asyncio.shield(self._force_event.wait()) if self._force_event else asyncio.sleep(60),
        timeout=60
    )
    if self._force_event and self._force_event.is_set():
        self._force_event.clear()
        self.last_main_run = datetime.min.replace(tzinfo=timezone.utc)  # força próxima iteração
except asyncio.TimeoutError:
    pass
```

No lifespan, após criar o scheduler:

```python
_force_sync_event = asyncio.Event()
_scheduler.set_force_event(_force_sync_event)
```
No handler force-sync em web/main.py:

```python
@app.get("/force-sync")
def force_sync():
    if _scheduler:
        _scheduler.trigger_now()
        logger.info("Force-sync acionado pelo usuário.")
    from starlette.responses import RedirectResponse
    return RedirectResponse("/", status_code=303)
```

### Validação obrigatória

1. Reiniciar e verificar logs imediatamente
   - docker compose restart
   - sleep 5
   - docker logs tubewranglerr --tail=30
   - Esperado: ver "=== TubeWrangler iniciando ===" e "Canais carregados: N"

2. Verificar canais no estado
   - docker compose exec tubewranglerr python3 -c "from core.config import AppConfig; from core.state_manager import StateManager; cfg = AppConfig(); st = StateManager(cfg); st.load_from_disk(); print(f'Canais no estado: {len(st.get_all_channels())}'); for cid, name in st.get_all_channels().items(): print(f'  {cid}: {name}')"

3. Verificar home após sync
   - curl -s http://localhost:8888/ | grep -i "canal\|stream\|nenhum"

4. Log persistido em arquivo
   - docker compose exec tubewranglerr tail -20 /data/tubewrangler.log

NÃO alterar core/config.py — está correto
NÃO alterar lógica de M3UGenerator / XMLTVGenerator
NÃO alterar rotas de playlist e EPG
