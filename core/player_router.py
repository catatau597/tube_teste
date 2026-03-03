"""
core/player_router.py
Responsabilidade: Decisao de qual comando ffmpeg/streamlink/yt-dlp executar
dado o status do stream. Retorna (cmd: List[str], temp_files: List[str]).

Regras:
- Nenhum subprocess sincrono que envolva rede (subprocess.run bloquearia o event loop).
- resolve_vod_url_async() e build_vod_cmd() sao a unica fonte de verdade para status="none".
- build_player_command() e build_player_command_async() sao os pontos de entrada publicos.
"""
import asyncio
import json
import logging
import shlex
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("TubeWrangler.player_router")


# ---------------------------------------------------------------------------
# Utilitarios internos
# ---------------------------------------------------------------------------

def _escape_ffmpeg_text(text: str) -> str:
    """Escapa caracteres especiais para uso no filtro drawtext do ffmpeg.

    Ordem importa: barras invertidas primeiro para nao double-escapar.

    Exemplos:
        >>> _escape_ffmpeg_text("50% off: vale 'isso'")
        "50\\% off\\: vale \'isso\'"
    """
    text = text.replace("\\", "\\\\")  # \ -> \\
    text = text.replace("'", "\\'")    # ' -> \'
    text = text.replace(":", "\\:")    # : -> \:
    text = text.replace("%", "\\%")    # % -> \%
    text = text.replace(",", "\\,")    # , -> \,
    return text


def _get_texts_from_cache(video_id: str, texts_cache_path: Path) -> Dict[str, str]:
    """Le textos de overlay do cache JSON. Retorna dict com line1/line2 vazios em falha."""
    try:
        if texts_cache_path.exists():
            data = json.loads(texts_cache_path.read_text(encoding="utf-8"))
            return data.get(video_id, {"line1": "", "line2": ""})
    except Exception:
        pass
    return {"line1": "", "line2": ""}


# ---------------------------------------------------------------------------
# Builders de comandos (sincronos, retornam List[str])
# ---------------------------------------------------------------------------

def build_streamlink_cmd(watch_url: str, user_agent: str = "Mozilla/5.0") -> List[str]:
    """Retorna comando streamlink para stream ao vivo (status=live)."""
    return [
        "streamlink", "--stdout",
        "--http-header", f"User-Agent={user_agent}",
        "--config", "/dev/null",
        "--no-plugin-sideloading",
        watch_url, "best",
    ]


def build_vod_cmd(
    cdn_url: str,
    watch_url: str,
    user_agent: str = "Mozilla/5.0",
) -> List[str]:
    """Retorna comando para reproducao de VOD (status=none).

    Se cdn_url estiver preenchida (resolvida por resolve_vod_url_async),
    usa ffmpeg direto na URL CDN. Caso contrario, usa pipeline bash
    yt-dlp | ffmpeg como fallback garantido.

    Args:
        cdn_url:    URL CDN resolvida pelo yt-dlp --get-url. Pode ser vazio.
        watch_url:  URL original do video (https://youtube.com/watch?v=...).
        user_agent: User-Agent HTTP para requests.

    Returns:
        Lista de strings pronta para asyncio.create_subprocess_exec(*cmd).
    """
    if cdn_url:
        logger.debug(f"VOD via ffmpeg CDN direto: {cdn_url[:80]}...")
        return [
            "ffmpeg", "-loglevel", "error",
            "-headers", f"User-Agent: {user_agent}\r\n",
            "-i", cdn_url,
            "-c", "copy",
            "-f", "mpegts",
            "pipe:1",
        ]

    # Fallback: pipeline bash yt-dlp -> ffmpeg
    q_ua  = shlex.quote(user_agent)
    q_url = shlex.quote(watch_url)
    fallback = (
        "set -o pipefail; "
        f"yt-dlp -f b -o - --no-playlist --user-agent {q_ua} {q_url} "
        "| ffmpeg -loglevel error -i pipe:0 -c copy -f mpegts pipe:1"
    )
    logger.info(f"VOD via fallback yt-dlp|ffmpeg: {watch_url}")
    return ["bash", "-lc", fallback]


def build_ffmpeg_placeholder_cmd(
    image_url: str,
    text_line1: str = "",
    text_line2: str = "",
    font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    user_agent: str = "Mozilla/5.0",
) -> tuple[list[str], list[str]]:
    """Retorna (cmd, temp_files) para placeholder de stream indisponivel.

    Gera um loop de imagem estatica (1280x720, 25fps) com overlay de texto
    opcional via drawtext. Arquivos temporarios de texto sao criados e devem
    ser deletados pelo chamador apos o processo terminar.

    Args:
        image_url:   URL ou path local da imagem de fundo.
        text_line1:  Linha superior do overlay (ex: titulo do evento).
        text_line2:  Linha inferior do overlay (ex: horario previsto).
        font_path:   Path da fonte TrueType dentro do container.
        user_agent:  User-Agent para imagens remotas.

    Returns:
        Tupla (cmd, temp_files) onde temp_files sao paths a deletar.
    """
    temp_files = []
    drawtext_filters = []

    if Path(font_path).is_file():
        for text, y_offset in [(text_line1, "h-100"), (text_line2, "h-50")]:
            if not text:
                continue
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
    input_args = ["-i", image_url] if is_local else ["-headers", f"User-Agent: {user_agent}\r\n", "-i", image_url]

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


# ---------------------------------------------------------------------------
# Resolucao assincrona de URL VOD (unica fonte de verdade)
# ---------------------------------------------------------------------------

async def resolve_vod_url_async(
    watch_url: str,
    user_agent: str = "Mozilla/5.0",
    timeout: int = 20,
) -> str:
    """Resolve a URL CDN real de um VOD via yt-dlp --get-url de forma assincrona.

    Nao bloqueia o event loop. Em timeout ou falha retorna string vazia,
    e o chamador deve usar build_vod_cmd(cdn_url="") para acionar o fallback.

    Args:
        watch_url:  URL do video YouTube.
        user_agent: User-Agent HTTP.
        timeout:    Segundos maximos de espera (default 20).

    Returns:
        URL CDN como string, ou "" em falha/timeout.

    Exemplo:
        cdn = await resolve_vod_url_async("https://youtube.com/watch?v=abc")
        cmd = build_vod_cmd(cdn, "https://youtube.com/watch?v=abc")
    """
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "-f", "b",
            "--get-url",
            "--no-playlist",
            "--user-agent", user_agent,
            watch_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            err_msg = err.decode("utf-8", errors="replace").strip() if err else ""
            logger.warning(
                f"[resolve_vod] yt-dlp falhou rc={proc.returncode} url={watch_url} | {err_msg[:200]}"
            )
            return ""
        text = out.decode("utf-8", errors="replace").strip()
        return text.splitlines()[0] if text else ""

    except asyncio.TimeoutError:
        logger.warning(f"[resolve_vod] timeout ({timeout}s) para {watch_url}")
        if proc is not None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        return ""

    except Exception as exc:
        logger.warning(f"[resolve_vod] erro inesperado para {watch_url}: {exc}")
        return ""


# ---------------------------------------------------------------------------
# Ponto de entrada publico sincronico (status live / placeholder)
# ---------------------------------------------------------------------------

def build_player_command(
    video_id: str,
    status: Optional[str],
    watch_url: str,
    thumbnail_url: str,
    user_agent: str = "Mozilla/5.0",
    font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    texts_cache_path: Optional[Path] = None,
) -> tuple[list[str], list[str]]:
    """Retorna (cmd, temp_files) para status live ou placeholder.

    Para status="none" (VOD), use build_player_command_async() que resolve
    a URL CDN de forma assincrona sem bloquear o event loop.

    Args:
        video_id:         ID do video YouTube.
        status:           "live", "upcoming", None ou qualquer outro valor.
        watch_url:        URL completa do video.
        thumbnail_url:    URL ou path local da thumbnail para placeholder.
        user_agent:       User-Agent HTTP.
        font_path:        Path da fonte TrueType no container.
        texts_cache_path: Path para JSON de textos de overlay.

    Returns:
        Tupla (cmd, temp_files).
    """
    if status == "live":
        logger.debug(f"[{video_id}] modo live -> streamlink")
        return build_streamlink_cmd(watch_url, user_agent), []

    # status "none" nao deve chegar aqui em producao
    # (web/main.py usa build_player_command_async para esse caso)
    # Mas se chamado diretamente (ex: testes/scripts), usa fallback imediato
    if status == "none":
        logger.warning(
            f"[{video_id}] build_player_command() chamado com status=none — "
            "use build_player_command_async() para resolucao assincrona"
        )
        return build_vod_cmd(cdn_url="", watch_url=watch_url, user_agent=user_agent), []

    logger.debug(f"[{video_id}] modo placeholder (status={status!r})")
    texts = _get_texts_from_cache(video_id, texts_cache_path) if texts_cache_path else {}
    return build_ffmpeg_placeholder_cmd(
        image_url=thumbnail_url,
        text_line1=texts.get("line1", ""),
        text_line2=texts.get("line2", ""),
        font_path=font_path,
        user_agent=user_agent,
    )


async def build_player_command_async(
    video_id: str,
    status: Optional[str],
    watch_url: str,
    thumbnail_url: str,
    user_agent: str = "Mozilla/5.0",
    font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    texts_cache_path: Optional[Path] = None,
) -> tuple[list[str], list[str]]:
    """Versao assincrona de build_player_command — obrigatoria para status=none.

    Para status=none resolve a URL CDN via resolve_vod_url_async() sem bloquear
    o event loop. Para os demais status delega ao build_player_command() sincrono.

    Args: idem build_player_command().

    Returns:
        Tupla (cmd, temp_files).

    Exemplo em web/main.py:
        cmd, temp_files = await build_player_command_async(
            video_id=video_id,
            status=status,
            watch_url=watch_url,
            thumbnail_url=thumb_for_ph,
            user_agent=user_agent,
            font_path=FONT_PATH,
            texts_cache_path=TEXTS_CACHE_PATH,
        )
    """
    if status == "none":
        logger.debug(f"[{video_id}] modo VOD -> resolve_vod_url_async")
        cdn_url = await resolve_vod_url_async(watch_url, user_agent)
        if cdn_url:
            logger.debug(f"[{video_id}] CDN resolvida: {cdn_url[:80]}...")
        else:
            logger.info(f"[{video_id}] CDN nao resolvida, usando fallback yt-dlp|ffmpeg")
        return build_vod_cmd(cdn_url=cdn_url, watch_url=watch_url, user_agent=user_agent), []

    return build_player_command(
        video_id=video_id,
        status=status,
        watch_url=watch_url,
        thumbnail_url=thumbnail_url,
        user_agent=user_agent,
        font_path=font_path,
        texts_cache_path=texts_cache_path,
    )
