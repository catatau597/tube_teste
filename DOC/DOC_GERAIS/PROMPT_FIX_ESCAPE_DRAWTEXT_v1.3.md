# PROMPT DE FIX — TubeWrangler RETROFIT v1.3 (escape drawtext ffmpeg)

> **Versão:** RETROFIT v1.3
> **Escopo:** Fix cirúrgico em core/player_router.py — função _escape_ffmpeg_text
> **Problema:** Escape excessivo do ":" no drawtext causa erro de parse no ffmpeg
> **Diagnóstico:**
>   - _escape_ffmpeg_text("28 Fev 19:00") retorna "28 Fev 19\\\\:00" (4 barras)
>   - ffmpeg espera "28 Fev 19\:00" (1 barra)
>   - Erro: "No option name near '00:x=(w-text_w)/2...'"

---

## Causa raiz

A linha 12 de core/player_router.py está assim:

```
for old, new in [("\\", "\\\\"), ("'", "\\'"), (":", "\\:"), ("%", "\\%"), (",", "\\,")]:
```

O escape de ":" está gerando "\\:" (4 barras + dois-pontos) quando o ffmpeg
drawtext espera "\:" (1 barra + dois-pontos).

---

## Fix — core/player_router.py

### Substituir a função _escape_ffmpeg_text inteira (linhas 11-14)

**Remover:**
```python
def _escape_ffmpeg_text(text: str) -> str:
    for old, new in [("\\\\", "\\\\\\\\"), ("\'", "\\\\'"), (":", "\\\\:"), ("%", "\\\\%"), (",", "\\\\,")]:
        text = text.replace(old, new)
    return text
```

**Inserir:**
```python
def _escape_ffmpeg_text(text: str) -> str:
    """Escapa caracteres especiais para uso no filtro drawtext do ffmpeg."""
    text = text.replace("\\", "\\\\")  # \ → \\ (barras primeiro)
    text = text.replace("'", "\\'")         # ' → \'
    text = text.replace(":", "\:")           # : → \:
    text = text.replace("%", "\%")           # % → \%
    text = text.replace(",", "\,")           # , → \,
    return text
```

> ATENÇÃO: Usar replace() individual em sequência, não tuple loop.
> A ordem importa: barras invertidas PRIMEIRO, depois os outros caracteres.

---

## Validação no container

```bash
# 1. Testar escape
docker compose exec tubewranglerr python3 -c "
import importlib, core.player_router as m
importlib.reload(m)
print(repr(m._escape_ffmpeg_text('28 Fev 19:00')))
print(repr(m._escape_ffmpeg_text("ao vivo, hoje")))
print(repr(m._escape_ffmpeg_text("it\'s live")))
"
# Esperado:
# '28 Fev 19\:00'
# 'ao vivo\, hoje'
# "it\'s live"

# 2. Testar ffmpeg com drawtext real
docker compose exec tubewranglerr python3 -c "
import asyncio, importlib
import core.player_router as m
importlib.reload(m)

async def test():
    cmd = m.build_ffmpeg_placeholder_cmd(
        image_url='/data/thumbnails/2zajmVK9DqU.jpg',
        text_line1='Ao vivo em 1d 6h',
        text_line2='28 Fev 19:00',
    )
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=1024*1024,
    )
    try:
        chunk = await asyncio.wait_for(proc.stdout.read(65536), timeout=8.0)
        print(f'bytes: {len(chunk)}')
    except asyncio.TimeoutError:
        print('TIMEOUT')
    err = await asyncio.wait_for(proc.stderr.read(2048), timeout=2.0)
    if err:
        print(f'stderr: {err.decode(errors="replace")}')
    proc.kill()

asyncio.run(test())
"
# Esperado: bytes: 32768 (sem stderr de erro)

# 3. Restartar e testar endpoint
docker compose restart && sleep 8
curl -s --max-time 5 http://localhost:8888/api/player/2zajmVK9DqU | wc -c
# Esperado: > 10000
```

### Resultado esperado final
- `_escape_ffmpeg_text("28 Fev 19:00")` → `"28 Fev 19\:00"`
- `build_ffmpeg_placeholder_cmd` gera stream sem erro de parse
- `curl .../api/player/2zajmVK9DqU | wc -c` > 10000
- VLC abre upcoming e exibe imagem estática

---

## Notas para o agente

- NÃO alterar nenhuma outra função em player_router.py
- NÃO alterar web/main.py (fix v1.2 permanece)
- Commit na branch dev após validação positiva
- Se bytes > 0 mas VLC ainda falhar, reportar — pode ser problema de buffering do player
