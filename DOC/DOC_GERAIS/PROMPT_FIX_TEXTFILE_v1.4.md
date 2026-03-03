# PROMPT DE FIX — TubeWrangler RETROFIT v1.4 (drawtext textfile)

> **Versão:** RETROFIT v1.4
> **Escopo:** Fix em core/player_router.py — usar textfile= no drawtext
> **Problema:** text= no drawtext não aceita ":" mesmo com escape via subprocess
> **Solução confirmada:** textfile= com arquivo temporário → bytes: 32768 ✅

---

## Causa raiz

O ffmpeg drawtext passado via `subprocess` (sem shell) não interpreta `\:` como
escape de `:` — ele trata o valor inteiro de `-filter_complex` como string literal.
O `:` no texto `28 Fev 19:00` quebra o parser do filtro.

A solução é usar `textfile=<path>` em vez de `text=<valor>`, escrevendo o texto
em um arquivo temporário antes de invocar o ffmpeg.

---

## Fix — core/player_router.py

### Adicionar import no topo do arquivo

```python
import os
import tempfile
```

### Substituir a função build_ffmpeg_placeholder_cmd inteira

```python
def build_ffmpeg_placeholder_cmd(
    image_url: str,
    text_line1: str = "",
    text_line2: str = "",
    font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    user_agent: str = "Mozilla/5.0",
) -> tuple[list[str], list[str]]:
    """
    Retorna (cmd, temp_files).
    temp_files: lista de paths de arquivos temporários que devem ser deletados
    após o processo terminar.
    """
    temp_files = []
    drawtext_filters = []

    if Path(font_path).is_file():
        for text, y_offset in [(text_line1, "h-100"), (text_line2, "h-50")]:
            if not text:
                continue
            # Escrever texto em arquivo temporário (evita problemas de escape com ":")
            tf = tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8"
            )
            tf.write(text)
            tf.close()
            temp_files.append(tf.name)
            fontsize = 48 if y_offset == "h-100" else 36
            drawtext_filters.append(
                f"drawtext=fontfile={font_path}:textfile={tf.name}"
                f":x=(w-text_w)/2:y={y_offset}:fontsize={fontsize}"
                f":fontcolor=white:borderw=2:bordercolor=black@0.8"
            )

    filter_chain = ",".join(drawtext_filters) if drawtext_filters else ""
    video_filter = (
        f"[0:v]fps=25,scale=1280:720,loop=-1:1:0"
        f"{(',' + filter_chain) if filter_chain else ''}[v]"
    )

    is_local = image_url.startswith("/") or image_url.startswith("file://")
    if is_local:
        input_args = ["-i", image_url]
    else:
        input_args = ["-headers", f"User-Agent: {user_agent}\r\n", "-i", image_url]

    cmd = [
        "ffmpeg", "-loglevel", "error",
        "-re",
        *input_args,
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-filter_complex", video_filter,
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-tune", "stillimage",
        "-f", "mpegts", "pipe:1",
    ]
    return cmd, temp_files
```

### Atualizar build_player_command para receber temp_files

```python
def build_player_command(
    video_id: str,
    status,
    watch_url: str,
    thumbnail_url: str,
    user_agent: str = "Mozilla/5.0",
    font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    texts_cache_path=None,
) -> tuple[list[str], list[str]]:
    """Retorna (cmd, temp_files). temp_files vazio para live/vod."""
    if status == "live":
        return build_streamlink_cmd(watch_url, user_agent), []
    if status == "none":
        return build_ytdlp_cmd(watch_url, user_agent), []
    texts = _get_texts_from_cache(video_id, texts_cache_path) if texts_cache_path else {}
    return build_ffmpeg_placeholder_cmd(
        image_url=thumbnail_url,
        text_line1=texts.get("line1", ""),
        text_line2=texts.get("line2", ""),
        font_path=font_path,
        user_agent=user_agent,
    )
```

### Atualizar build_streamlink_cmd e build_ytdlp_cmd — retornar tuple

```python
def build_streamlink_cmd(watch_url: str, user_agent: str = "Mozilla/5.0") -> list[str]:
    # SEM ALTERAÇÃO — apenas garantir que retorna list (não tuple)
    return [
        "streamlink", "--stdout",
        "--http-header", f"User-Agent={user_agent}",
        "--config", "/dev/null",
        "--no-plugin-sideloading",
        watch_url, "best",
    ]

def build_ytdlp_cmd(watch_url: str, user_agent: str = "Mozilla/5.0") -> list[str]:
    # SEM ALTERAÇÃO
    return [
        "yt-dlp", "-f", "best", "-o", "-",
        "--user-agent", user_agent,
        watch_url,
    ]
```

---

## Fix — web/main.py (rota api_player_stream)

### Atualizar para receber e limpar temp_files

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

    cmd, temp_files = build_player_command(
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
            # Limpar arquivos temporários de texto
            for tf in temp_files:
                try:
                    os.unlink(tf)
                except Exception:
                    pass

    return StreamingResponse(
        stream_gen(),
        media_type="video/mp2t",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

Adicionar import no topo de web/main.py se não existir:
```python
import os
```

---

## Fix — smart_player.py

O `smart_player.py` chama `build_player_command` e agora recebe tuple.
Atualizar a chamada:

```python
# Localizar linha com build_player_command em smart_player.py e atualizar:
cmd, temp_files = build_player_command(
    video_id=video_id,
    status=status,
    watch_url=url,
    thumbnail_url=thumb_to_use,
    user_agent=user_agent,
    texts_cache_path=TEXTS_CACHE_PATH,
)
process = subprocess.Popen(cmd, stdout=sys.stdout, stderr=subprocess.PIPE)
_, stderr_output = process.communicate()
# Limpar temp files
for tf in temp_files:
    try:
        os.unlink(tf)
    except Exception:
        pass
if process.returncode != 0 and stderr_output:
    logger.error(f"Stderr: {stderr_output.decode('utf-8', errors='ignore')}")
```

---

## Validação

```bash
# 1. Testar build_player_command retorna tuple
docker compose exec tubewranglerr python3 -c "
import importlib, core.player_router as m
importlib.reload(m)
from pathlib import Path
cmd, tmp = m.build_player_command(
    video_id='2zajmVK9DqU',
    status='upcoming',
    watch_url='https://youtube.com/watch?v=2zajmVK9DqU',
    thumbnail_url='/data/thumbnails/2zajmVK9DqU.jpg',
    texts_cache_path=Path('/data/textosepg.json'),
)
print('cmd ok:', cmd[0])
print('temp_files:', tmp)
import os
for tf in tmp: os.unlink(tf)
"

# 2. Testar asyncio com temp_files
docker compose exec tubewranglerr python3 -c "
import asyncio, os
from pathlib import Path
import importlib, core.player_router as m
importlib.reload(m)

async def test():
    cmd, tmp = m.build_player_command(
        video_id='2zajmVK9DqU', status='upcoming',
        watch_url='x', thumbnail_url='/data/thumbnails/2zajmVK9DqU.jpg',
        texts_cache_path=Path('/data/textosepg.json'),
    )
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=1024*1024,
    )
    try:
        chunk = await asyncio.wait_for(proc.stdout.read(65536), timeout=8.0)
        print(f'bytes: {len(chunk)}')
    except asyncio.TimeoutError:
        print('TIMEOUT')
    proc.kill()
    for tf in tmp: os.unlink(tf)

asyncio.run(test())
"
# Esperado: bytes: 32768

# 3. Restartar e testar endpoint
docker compose restart && sleep 8
curl -s --max-time 5 http://localhost:8888/api/player/2zajmVK9DqU | wc -c
# Esperado: > 10000
```

---

## Notas para o agente

- _escape_ffmpeg_text pode ser mantida ou removida — não é mais usada
- build_streamlink_cmd e build_ytdlp_cmd NÃO mudam de assinatura (retornam list)
- Apenas build_player_command e build_ffmpeg_placeholder_cmd retornam tuple (cmd, temp_files)
- Commit na branch dev após validação positiva
