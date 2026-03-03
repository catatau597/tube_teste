# PROMPT DE FIX — TubeWrangler RETROFIT v1.1 (placeholder ffmpeg)

> **Versão:** RETROFIT v1.1
> **Escopo:** Fix cirúrgico em core/player_router.py e web/main.py
> **Problema:** Stream upcoming não reproduz no VLC — ffmpeg falha com "-user_agent" inválido
> **Pré-requisito:** RETROFIT v1.0 aplicado e funcionando

---

## Contexto do problema

A rota `/api/player/{video_id}` para streams com `status=upcoming` invoca o ffmpeg
com o argumento `-user_agent` que **não existe** como opção global no ffmpeg 7.x.
O correto para inputs HTTP é `-headers "User-Agent: ...\r\n"`.
Para inputs locais (path `/data/thumbnails/*.jpg`), nenhum header é necessário.

O servidor retorna `200 OK` mas o ffmpeg falha silenciosamente (stderr=DEVNULL),
o stream é fechado imediatamente e o VLC exibe "entrada não pode ser aberta".

---

## Fix 1 — core/player_router.py

### Localizar

Função `build_ffmpeg_placeholder_cmd`. Trecho atual (linhas ~55-57):

```python
    return [
        "ffmpeg", "-loglevel", "error",
        "-re", "-user_agent", user_agent,
        "-i", image_url,
```

### Substituir por

```python
    # Para path local não usar headers HTTP; para URL remota usar -headers
    is_local = image_url.startswith("/") or image_url.startswith("file://")
    if is_local:
        input_args = ["-i", image_url]
    else:
        input_args = ["-headers", f"User-Agent: {user_agent}\r\n", "-i", image_url]

    return [
        "ffmpeg", "-loglevel", "error",
        "-re",
        *input_args,
```

### Também remover `-shortest` se ainda existir

```python
# Remover esta linha se existir:
        "-shortest",
```

### Resultado esperado da função completa

```python
def build_ffmpeg_placeholder_cmd(
    image_url: str,
    text_line1: str = "",
    text_line2: str = "",
    font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    user_agent: str = "Mozilla/5.0",
) -> List[str]:
    drawtext_filters = []
    if Path(font_path).is_file():
        if text_line1:
            t = _escape_ffmpeg_text(text_line1)
            drawtext_filters.append(
                f"drawtext=fontfile={font_path}:text={t}"
                f":x=(w-text_w)/2:y=h-100:fontsize=48:fontcolor=white:borderw=2:bordercolor=black@0.8"
            )
        if text_line2:
            t = _escape_ffmpeg_text(text_line2)
            drawtext_filters.append(
                f"drawtext=fontfile={font_path}:text={t}"
                f":x=(w-text_w)/2:y=h-50:fontsize=36:fontcolor=white:borderw=2:bordercolor=black@0.8"
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

    return [
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
```

---

## Fix 2 — web/main.py (rota /api/player)

### Localizar

Rota `api_player_stream`. Trecho atual:

```python
    placeholder = _config.get_str("placeholder_image_url")
    thumb_for_ph = thumbnail_url or placeholder
```

### Adicionar logo após (já pode estar presente — verificar antes de duplicar)

```python
    # Preferir thumbnail local cacheada (evita header HTTP no ffmpeg)
    local_thumb = Path(_thumbnail_manager._cache_dir) / f"{video_id}.jpg"
    if local_thumb.exists():
        thumb_for_ph = str(local_thumb)
```

Se já existir essas linhas, não duplicar.

---

## Validação no container

```bash
# 1. Verificar se -user_agent sumiu e input_args foi adicionado
docker compose exec tubewranglerr grep -n "user_agent\|input_args\|is_local\|headers" core/player_router.py

# 2. Testar ffmpeg manualmente com path local
docker compose exec tubewranglerr bash -c '
ffmpeg -loglevel warning \
  -re \
  -i /data/thumbnails/2zajmVK9DqU.jpg \
  -f lavfi -i anullsrc=r=44100:cl=stereo \
  -filter_complex "[0:v]fps=25,scale=1280:720,loop=-1:1:0[v]" \
  -map "[v]" -map "1:a" \
  -c:v libx264 -preset ultrafast -pix_fmt yuv420p \
  -c:a aac -b:a 128k \
  -tune stillimage \
  -f mpegts pipe:1 2>&1 | head -5
'

# 3. Restartar e testar endpoint
docker compose restart
sleep 8
curl -s --max-time 5 http://localhost:8888/api/player/2zajmVK9DqU | wc -c
# Esperado: > 10000 bytes (stream MPEG-TS ativo)

# 4. Se > 0 bytes: abrir no VLC
# http://100.98.81.67:8888/api/player/2zajmVK9DqU
```

### Resultado esperado

- `curl | wc -c` retorna > 10000 bytes em 5 segundos
- VLC abre e exibe imagem estática com áudio silencioso
- `docker compose logs tubewranglerr | grep "player/2zajmVK9DqU"` mostra `200 OK` sem fechar imediatamente

---

## Notas para o agente

- NÃO alterar lógica de `build_streamlink_cmd` nem `build_ytdlp_cmd`
- NÃO alterar `build_player_command` — apenas `build_ffmpeg_placeholder_cmd`
- O `-shortest` deve estar ausente — se encontrar, remover
- `smart_player.py` usa o mesmo `core.player_router` — o fix beneficia CLI também
- Commit na branch `dev` após validação
