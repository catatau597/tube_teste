# PROMPT DE FIX — TubeWrangler v1.7 (EPG stop + Category B + Pagination + Dashboard)

> **Versão:** v1.7
> **Escopo:** 4 mudanças em paralelo
> 1. EPG `stop` com duração real (2h/3h heurística por título)
> 2. Category Opção B — `allowed_category_ids` removido, `category_mappings` como fonte de IDs permitidos
> 3. Paginação — early-stop por `stale_hours` e `max_schedule_hours`
> 4. Dashboard — coluna "Categoria" na tabela de streams

---

## FIX 1 — core/playlist_builder.py — EPG stop com duração heurística

### Localizar generate_xml, na parte que monta start_str/end_str (linha ~278-293)

```python
# ANTES:
start_dt: Optional[datetime] = (
    s.get("scheduledstarttimeutc")
    ...
)
end_dt: Optional[datetime] = s.get("actualendtimeutc")

if not start_dt:
    continue

start_str = start_dt.strftime(fmt)
# ... monta dict com "stop": end_str

# DEPOIS — substituir bloco de datas:
start_dt: Optional[datetime] = (
    s.get("scheduledstarttimeutc")
    or s.get("actualstarttimeutc")
)
if not start_dt:
    continue

actual_end = s.get("actualendtimeutc")
if actual_end:
    stop_dt = actual_end
else:
    # Heurística por título para upcoming sem end_time
    title_check = (s.get("title") or "").lower()
    sport_keywords = ("jogo", "partida", "ao vivo", "live", "futebol",
                      "basquete", "tênis", "vôlei", "judô", "grand slam",
                      "eliminatória", "semifinal", "final", "copa", "campeonato")
    if any(kw in title_check for kw in sport_keywords):
        stop_dt = start_dt + timedelta(hours=3)
    else:
        stop_dt = start_dt + timedelta(hours=2)

start_str = start_dt.strftime(fmt)
end_str   = stop_dt.strftime(fmt)
```

---

## FIX 2 — core/playlist_builder.py — Category Opção B

### 2a. Atualizar filter_streams — usar keys do category_mappings em vez de allowed_category_ids

```python
# ANTES (linha ~122-127):
if self._config.get_bool("filter_by_category"):
    allowed = set(self._config.get_list("allowed_category_ids"))
    if allowed:
        streams = [
            s for s in streams
            if str(s.get("categoryoriginal", "")) in allowed
        ]

# DEPOIS:
if self._config.get_bool("filter_by_category"):
    mappings = self._config.get_mapping("category_mappings")
    # get_mapping retorna dict {id: nome} — as keys são os IDs permitidos
    allowed = set(mappings.keys())
    if allowed:
        streams = [
            s for s in streams
            if str(s.get("categoryoriginal", "")) in allowed
        ]
```

### 2b. get_display_category — já usa get_mapping, sem alteração necessária

### 2c. core/config.py — remover allowed_category_ids dos DEFAULTS e da lista _OBSOLETE_KEYS (adicionar)

```python
# Adicionar à _OBSOLETE_KEYS existente:
"allowed_category_ids",

# Remover de DEFAULTS:
# ("allowed_category_ids", "17", "filtering"),  ← remover esta linha
```

---

## FIX 3 — core/scheduler.py — Early-stop na paginação de playlistItems

### Localizar o while True do loop de paginação (linha ~146) e a função que faz fetch de playlistItems

Adicionar early-stop após processar cada página de resultados.
O local correto é dentro do scraper/fetch que itera `nextPageToken`.

```python
# Localizar onde items de cada página são processados após o request à API.
# Após receber os items de uma página, antes de seguir para nextPageToken,
# verificar se todos os items da página são muito antigos (early-stop):

# Adicionar imports no topo do arquivo se não existirem:
from datetime import datetime, timezone, timedelta

# Dentro do loop de paginação, após receber items da página:
now_utc = datetime.now(timezone.utc)
stale_cutoff    = now_utc - timedelta(hours=self._config.get_int("stale_hours"))
future_cutoff   = now_utc + timedelta(hours=self._config.get_int("max_schedule_hours"))

all_too_old = True
for item in page_items:
    pub = item.get("publishedAt") or item.get("snippet", {}).get("publishedAt")
    if pub:
        try:
            pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            if pub_dt >= stale_cutoff:
                all_too_old = False
                break
        except Exception:
            all_too_old = False
            break
    else:
        all_too_old = False
        break

if all_too_old:
    logger.debug(f"Early-stop paginação: todos os items da página são anteriores a stale_cutoff")
    break  # parar de paginar — não há mais conteúdo relevante
```

> **Nota para o agente:** identificar o nome exato da variável que contém os items da página
> (pode ser `items`, `page_items`, `results`, etc.) e onde está o `nextPageToken` check.
> O early-stop deve acontecer ANTES de checar nextPageToken.
> Também verificar se o scraper está em `core/scheduler.py` ou em outro módulo (ex: `core/scraper.py`).

---

## FIX 4 — web/main.py — Dashboard: coluna "Categoria" na tabela de streams

### 4a. Atualizar a tabela de streams no home()

```python
# ANTES:
rows = []
for s in streams:
    vid = s.get("videoid", "")
    url = s.get("watchurl") or f"https://www.youtube.com/watch?v={vid}"
    rows.append(
        Tr(
            Td((s.get("title") or "")[:80]),
            Td(s.get("status") or "none"),
            Td(A("Abrir", href=url, target="_blank")),
        )
    )
...
Table(
    Thead(Tr(Th("Evento"), Th("Status"), Th("Assistir"))),
    Tbody(*rows),
)

# DEPOIS:
rows = []
for s in streams:
    vid      = s.get("videoid", "")
    url      = s.get("watchurl") or f"https://www.youtube.com/watch?v={vid}"
    cat_id   = str(s.get("categoryoriginal") or "")
    cat_name = (_categories_db or {}).get(cat_id, "") if cat_id else ""
    cat_cell = f"{cat_id} | {cat_name}" if cat_name else cat_id or "—"
    rows.append(
        Tr(
            Td((s.get("channelname") or "")[:30]),
            Td((s.get("title") or "")[:70]),
            Td(s.get("status") or "none"),
            Td(cat_cell),
            Td(A("▶", href=url, target="_blank")),
        )
    )
...
Table(
    Thead(Tr(Th("Canal"), Th("Evento"), Th("Status"), Th("Categoria"), Th(""))),
    Tbody(*rows),
)
```

### 4b. Atualizar /api/streams para incluir categoria resolvida (opcional mas útil)

```python
# Em /api/streams, ao serializar cada stream, adicionar campo category_display:
# (apenas se já houver serialização customizada — se retorna direto do state, skip)
```

---

## Validação

```bash
# 1. Restart
docker compose restart && sleep 8

# 2. EPG com stop != start
curl -s http://localhost:8888/epg.xml | grep -o 'stop="[^"]*"' | head -5
# Esperado: stop diferente de start (ex: stop="20260228220000..." vs start="20260228190000...")

# 3. filter_by_category usa category_mappings
# Com filter_by_category=true e category_mappings="17|ESPORTES,22|PESSOAS E BLOGS":
curl -s http://localhost:8888/playlist/upcoming-proxy.m3u | grep -c "^#EXTINF"
# Esperado: mesmo número de antes (categorias 17 ainda presentes)

# 4. allowed_category_ids não aparece mais no /config
curl -s http://localhost:8888/api/config | python3 -m json.tool | grep allowed_category
# Esperado: nenhuma saída

# 5. Dashboard com coluna Categoria
curl -s http://localhost:8888/ | grep -o "Categoria"
# Esperado: "Categoria"

# 6. Categoria visível nos dados
curl -s http://localhost:8888/ | grep -o "[0-9]* | [A-Z]*" | head -5
# Esperado: "17 | ESPORTES"

# 7. Testes
docker compose exec tubewranglerr python3 -m pytest tests/ -q
# Esperado: todos passando
```

---

## Notas para o agente

- FIX 3 (paginação): o loop de paginação pode estar em `core/scraper.py` ou diretamente no scheduler
  Verificar com: `grep -rn "nextPageToken\|pageToken" core/`
- FIX 2: `get_mapping` retorna dict com keys como strings — garantir que a comparação é `str(cat_id)`
- FIX 4: `_categories_db` é variável global em web/main.py — verificar se está acessível no escopo do home()
- `allowed_category_ids` pode estar referenciado em testes — atualizar se necessário
- Fazer commit separado para cada fix se preferir, ou um commit único "feat: v1.7 EPG/category/pagination/dashboard"
