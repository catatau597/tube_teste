# PROMPT: Fix logging + canais não carregados + force-sync

## Contexto
- Branch: dev
- Arquivos a modificar: web/main.py e core/scheduler.py
- NÃO modificar: core/config.py, core/state_manager.py, core/youtube_api.py

## Problemas confirmados (não especular, não sugerir outra causa)

1. web/main.py não configura logging — nada aparece no docker logs
2. web/main.py lifespan não resolve handles nem carrega títulos de canais —
   _state.channels fica vazio — home() exibe "Nenhum canal configurado"
3. _scheduler.trigger_now() lança AttributeError — force-sync não funciona

---

## PASSO 1 — Verificar estado atual antes de qualquer edição

```bash
# 1a. Ver o lifespan completo atual
docker compose exec tubewranglerr python3 -c "
import inspect, web.main as m
print(inspect.getsource(m.lifespan))
"

# 1b. Ver o método run() do Scheduler
docker compose exec tubewranglerr python3 -c "
import inspect
from core.scheduler import Scheduler
print(inspect.getsource(Scheduler.run))
"

# 1c. Confirmar que canais estão mesmo vazios
docker compose exec tubewranglerr python3 -c "
from core.state_manager import StateManager
from core.config import AppConfig
st = StateManager(AppConfig())
st.load_from_disk()
print('Canais no disco:', len(st.get_all_channels()))
"
```

---

## PASSO 2 — Editar web/main.py

### 2a. Adicionar imports e configuração de logging no topo do arquivo
Logo após os imports existentes, antes de qualquer outra linha, adicionar:

```python
import logging
import sys
from pathlib import Path

def _get_log_level(s: str) -> int:
    return getattr(logging, s.upper(), logging.INFO)

_LOG_FILE = Path("/data/tubewrangler.log")

logging.basicConfig(
    format   = "%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt  = "%Y-%m-%d %H:%M:%S",
    level    = logging.INFO,
    force    = True,
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_LOG_FILE, mode="a"),
    ]
)
logger = logging.getLogger("TubeWrangler")
```

### 2b. Substituir o lifespan COMPLETO por este:

```python
@asynccontextmanager
async def lifespan(app):
    global _config, _state, _scheduler

    _config = AppConfig()

    # Ajustar nível de log conforme banco
    logging.getLogger().setLevel(_get_log_level(_config.get_str("log_level")))
    logger.info("=== TubeWrangler iniciando ===")

    _state       = StateManager(_config)
    cache_loaded = _state.load_from_disk()
    logger.info(f"Cache carregado do disco: {cache_loaded} | "
                f"canais={len(_state.get_all_channels())} "
                f"streams={len(_state.get_all_streams())}")

    api_key  = _config.get_str("youtube_api_key")
    handles  = [h.strip() for h in _config.get_str("target_channel_handles").split(",") if h.strip()]
    chan_ids = [i.strip() for i in _config.get_str("target_channel_ids").split(",")    if i.strip()]
    logger.info(f"Handles configurados : {handles}")
    logger.info(f"IDs configurados     : {chan_ids}")

    scraper = YouTubeAPI(api_key)

    # Resolver handles → IDs (igual ao get_streams.py original)
    all_target_ids = set(chan_ids)
    if handles:
        logger.info(f"Resolvendo {len(handles)} handle(s) via API...")
        resolved = scraper.resolve_channel_handles_to_ids(handles, _state)
        all_target_ids.update(resolved.keys())
        logger.info(f"Handles resolvidos: {resolved}")

    # Garantir títulos para todos os IDs
    if all_target_ids:
        final_channels = scraper.ensure_channel_titles(all_target_ids, _state)
        logger.info(f"Canais prontos: {len(final_channels)} — {list(final_channels.values())}")
    else:
        logger.warning("Nenhum canal alvo. Verifique target_channel_handles / target_channel_ids no /config.")

    # Criar Scheduler e injetar evento de force-sync
    _force_event = asyncio.Event()
    _scheduler   = Scheduler(_config, scraper, _state)
    _scheduler.set_force_event(_force_event)

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

### 2c. Substituir o handler force-sync:

```python
@app.get("/force-sync")
def force_sync():
    if _scheduler:
        _scheduler.trigger_now()
        logger.info("Force-sync acionado pelo usuário.")
    from starlette.responses import RedirectResponse
    return RedirectResponse("/", status_code=303)
```

---

## PASSO 3 — Editar core/scheduler.py

### 3a. Em __init__, adicionar a linha:
```python
self._force_event: asyncio.Event | None = None
```

### 3b. Adicionar dois métodos novos na classe Scheduler:
```python
def set_force_event(self, event: asyncio.Event) -> None:
    self._force_event = event

def trigger_now(self) -> None:
    if self._force_event:
        self._force_event.set()
        logger.info("Scheduler: trigger manual recebido.")
```

### 3c. No loop run(), localizar o trecho de sleep final:
```python
# ANTES (linha atual):
await asyncio.sleep(60)

# DEPOIS (substituir por):
try:
    await asyncio.wait_for(
        asyncio.shield(self._force_event.wait()) if self._force_event else asyncio.sleep(60),
        timeout=60
    )
    if self._force_event and self._force_event.is_set():
        self._force_event.clear()
        self.last_main_run = datetime.min.replace(tzinfo=timezone.utc)
        logger.info("Scheduler: executando sync forçado.")
except asyncio.TimeoutError:
    pass
```

---

## PASSO 4 — Validação obrigatória (NÃO pular nenhuma etapa)

```bash
# 4a. Reiniciar
docker compose restart
sleep 8

# 4b. Verificar logs — DEVE aparecer os canais carregados
docker logs tubewranglerr --tail=40
# Esperado: ver "=== TubeWrangler iniciando ===" e "Canais prontos: N"

# 4c. Verificar canais no estado em memória via endpoint
curl -s http://localhost:8888/ | python3 -c "
import sys
html = sys.stdin.read()
print('OK' if 'Nenhum canal' not in html else 'FALHA: ainda sem canais')
"

# 4d. Verificar log em arquivo
docker compose exec tubewranglerr tail -20 /data/tubewrangler.log

# 4e. Testar force-sync
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8888/force-sync
# Esperado: 303
sleep 3
docker logs tubewranglerr --tail=10
# Esperado: ver "Force-sync acionado" e "Scheduler: trigger manual recebido"

# 4f. Verificar que não há AttributeError
docker logs tubewranglerr 2>&1 | grep -i "error\|exception\|traceback" | tail -10
# Esperado: nenhuma linha nova de erro
```

---

## PASSO 5 — Commit e merge para main (somente após PASSO 4 completo)

```bash
git add web/main.py core/scheduler.py
git commit -m "fix: logging configurado + canais resolvidos no lifespan + force-sync com asyncio.Event"

git checkout main
git merge dev --no-ff -m "fix: logging + resolve handles no lifespan + force-sync"
git push origin main
git checkout dev

# Rebuild e teste final na main
docker compose -f docker-compose.yml up --build -d
sleep 15
docker logs tubewranglerr --tail=20
```

---

## Regras para o agente

- NÃO reescrever arquivos inteiros — editar apenas os blocos indicados
- NÃO alterar core/config.py, core/state_manager.py, core/youtube_api.py
- NÃO inventar métodos que não existem — usar exatamente os nomes do PASSO 1
- Se o inspect do PASSO 1 mostrar que algum método já existe com nome diferente,
  reportar antes de editar
- Se qualquer etapa do PASSO 4 falhar, parar e reportar o erro completo
