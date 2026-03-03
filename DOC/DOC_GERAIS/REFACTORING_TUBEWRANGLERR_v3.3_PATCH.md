# REFACTORING_TUBEWRANGLERR — Changelog v3.2 → v3.3

## O que muda na v3.3

### Contexto
Na execução real da Etapa 3 ocorreram 3 AttributeErrors em sequência:
  1. StateManager sem load_from_disk()
  2. StateManager sem get_all_streams() / get_all_channels()
  3. Scheduler sem trigger_now()

Esses erros NÃO são novos problemas — são consequência direta de uma
lacuna já conhecida: a Etapa 2 entregou módulos com esqueleto incompleto
(assinaturas definidas, implementação adiada para "etapa posterior").

A v3.2 documenta as assinaturas obrigatórias (seção 4.2) mas não impede
que o agente entregue esqueletos vazios e avance para a Etapa 3.

A v3.3 corrige isso com dois mecanismos:
  A) Checklist mais rigoroso na Etapa 2
  B) docker-compose.override.yml com volume .:/app (descoberta prática)

---

## MUDANÇA 1 — docker-compose.override.yml (seção 0.6)

### v3.2 (incorreto)
```yaml
services:
  tubewranglerr:
    volumes:
      - ./data:/data
    command: sleep infinity
```

### v3.3 (correto)
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

---

## MUDANÇA 2 — Checklist da Etapa 2 (seção 4.3)

### v3.2
```
[ ] Todos os 4 módulos criados sem os.getenv() e sem Flask
[ ] Todos os imports OK no container
[ ] Todos os testes das etapas passam
[ ] grep -r "os.getenv" core/ retorna vazio
[ ] get_streams.py NÃO foi apagado
[ ] DECISIONS.md atualizado
```

### v3.3 — adicionar validação de métodos obrigatórios
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

---

## MUDANÇA 3 — Implementações mínimas obrigatórias (nova seção 4.4)

Adicionar após seção 4.2 (Assinaturas obrigatórias):

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

---

## MUDANÇA 4 — Seção de indentação (nova nota na seção 4.4)

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

## Resumo de mudanças v3.2 → v3.3

| Seção | Mudança |
|---|---|
| 0.6 docker-compose.override.yml | Adicionado volume `.:/app` — obrigatório |
| 4.3 Checklist Etapa 2 | Adicionado script de validação de métodos |
| 4.4 (nova) | Implementações mínimas obrigatórias de StateManager e Scheduler |
| 4.4 (nova) | Nota sobre armadilha de indentação e como detectar |
| Copilot instructions | Adicionada regra: sem volume .:/app não iniciar desenvolvimento |
