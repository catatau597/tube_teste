# PROMPT DE FIX — TubeWrangler RETROFIT v1.2 (StreamingResponse 0 bytes)

> **Versão:** RETROFIT v1.2
> **Escopo:** Fix cirúrgico em web/main.py — rota /api/player retorna 0 bytes
> **Problema:** asyncio.create_subprocess_exec dentro de async generator no FastHTML
>              retorna 0 bytes no StreamingResponse mesmo com ffmpeg funcionando
> **Diagnóstico confirmado:** ffmpeg asyncio standalone retorna 32768 bytes OK;
>                             curl http://localhost:8888/api/player/{id} | wc -c = 0

---

## Causa raiz

O FastHTML usa Starlette por baixo mas intercepta respostas de rotas `@app.get`.
Quando a rota é `async def` e retorna `StreamingResponse` com um `async generator`
definido **dentro** da própria rota, o Starlette pode consumir o generator antes
de enviar ao cliente em alguns contextos de middleware do FastHTML.

A solução é usar `starlette.routing.Route` direta com `request` como parâmetro,
contornando o wrapper do FastHTML para essa rota específica.

---

## Fix — web/main.py

### Passo 1: Localizar a rota atual

```python
@app.get("/api/player/{video_id}")
async def api_player_stream(video_id: str, user_agent: str = "Mozilla/5.0"):
    ...
    async def stream_from_process():
        proc = await asyncio.create_subprocess_exec(...)
        ...
        yield chunk
    return StreamingResponse(stream_from_process(), ...)
```

### Passo 2: Substituir pela versão com Request explícito

Remover a rota `@app.get("/api/player/{video_id}")` inteira e substituir por:

```python
async def api_player_stream(request):
    video_id = request.path_params["video_id"]
    user_agent = request.query_params.get("user_agent", "Mozilla/5.0")

    stream_info = _state.streams.get(video_id)
    status = stream_info.get("status") if stream_info else None
    thumbnail_url = stream_info.get("thumbnailurl") if stream_info else None
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    placeholder = _config.get_str("placeholder_image_url")
    thumb_for_ph = thumbnail_url or placeholder

    local_thumb = Path(_thumbnail_manager._cache_dir) / f"{video_id}.jpg"
    if local_thumb.exists():
        thumb_for_ph = str(local_thumb)

    cmd = build_player_command(
        video_id=video_id,
        status=status,
        watch_url=watch_url,
        thumbnail_url=thumb_for_ph,
        user_agent=user_agent,
        font_path=FONT_PATH,
        texts_cache_path=TEXTS_CACHE_PATH,
    )

    async def stream_gen():
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=1024 * 1024,
        )
        try:
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                yield chunk
        except asyncio.CancelledError:
            pass
        finally:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass

    return StreamingResponse(
        stream_gen(),
        media_type="video/mp2t",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

### Passo 3: Registrar a rota via Starlette diretamente

Adicionar no final de web/main.py, **após** todas as rotas `@app.get/@app.post`:

```python
# Registrar rota de streaming diretamente no Starlette (contorna wrapper FastHTML)
from starlette.routing import Route
app.routes.append(Route("/api/player/{video_id}", endpoint=api_player_stream))
```

> ATENÇÃO: Verificar se já existe `@app.get("/api/player/{video_id}")` e REMOVER.
> A nova função `api_player_stream` NÃO deve ter decorator `@app.get`.

---

## Validação

```bash
# 1. Confirmar que a rota não tem decorator @app.get
docker compose exec tubewranglerr grep -n "api/player\|api_player" web/main.py

# 2. Restartar
docker compose restart && sleep 8

# 3. Testar bytes recebidos (deve ser > 10000)
curl -s --max-time 5 http://localhost:8888/api/player/2zajmVK9DqU | wc -c

# 4. Se > 0: testar no VLC
# http://100.98.81.67:8888/api/player/2zajmVK9DqU

# 5. Testar stream live também (não deve regredir)
curl -s --max-time 5 http://localhost:8888/api/player/bdmOJ3P0MBI | wc -c
```

### Resultado esperado
- upcoming: wc -c > 10000 em 5s
- live: wc -c > 0 (streamlink demora mais — OK se conectar)
- VLC abre upcoming e exibe imagem estática com áudio

---

## Notas para o agente

- NÃO alterar a rota /api/logs/stream (SSE) — usa padrão diferente
- NÃO alterar nenhuma outra rota
- A função api_player_stream recebe `request` (Starlette Request) — não parâmetros tipados
- Commit na branch dev após validação positiva
