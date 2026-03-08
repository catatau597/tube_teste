"""
core/player_router.py
Responsabilidade: Decisao de qual comando ffmpeg/streamlink/yt-dlp executar
dado o status do stream. Retorna (cmd: List[str], temp_files: List[str]).

Regras:
- Nenhum subprocess sincrono que envolva rede (subprocess.run bloquearia o event loop).
- resolve_vod_url_async() e build_vod_cmd() sao a unica fonte de verdade para
  status in ("none", "vod", "ended").
- resolve_live_hls_url_async() e build_live_hls_ffmpeg_cmd() sao o caminho primario para
  status="live"; streamlink fica como fallback de compatibilidade.
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


class GeoBlockedError(Exception):
    """Levantada quando yt-dlp detecta bloqueio geografico (retorna HTTP 451).

    Mensagem contém a watch_url do vídeo bloqueado para facilitar logging.
    """


# User-Agent Edge moderno — usado para ffmpeg, yt-dlp e placeholder.
# NAO e passado ao streamlink: o plugin YouTube >=8.1.2 usa useragents.CHROME
# internamente para o POST em youtubei/v1/player (clientName=ANDROID).
# Sobrescrever com --http-header User-Agent quebraria essa autenticacao.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0"
)

# Duracao em segundos de cada segmento do placeholder.
# Deve ser igual ao PLACEHOLDER_SEGMENT_DURATION em proxy_manager.py.
PLACEHOLDER_SEGMENT_DURATION = 30

# Timeout para yt-dlp resolver URL HLS de live stream (fallback).
# Lives retornam rapido (~2-5s); timeout generoso para redes lentas.
LIVE_HLS_RESOLVE_TIMEOUT_S = 15

# Timeout para yt-dlp resolver URL de VOD.
VOD_RESOLVE_TIMEOUT_S = 20

# Conjunto de status que representam conteudo VOD (live encerrada ou status legado).
_VOD_STATUSES = frozenset({"none", "vod", "ended"})


# ---------------------------------------------------------------------------
# Utilitarios internos
# ---------------------------------------------------------------------------

def _escape_ffmpeg_text(text: str) -> str:
    """Escapa caracteres especiais para uso no filtro drawtext do ffmpeg.

    Ordem importa: barras invertidas primeiro para nao double-escapar.

    Exemplos:
        >>> _escape_ffmpeg_text("50% off: vale 'isso'")
        "50\\% off\\: vale \\'isso\\'"
    """
    text = text.replace("\\", "\\\\")  # \ -> \\\\
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

def build_streamlink_cmd(watch_url: str, debug_enabled: bool = False) -> List[str]:
    """Retorna comando streamlink para stream ao vivo (status=live).

    IMPORTANTE — por que nao passamos User-Agent:
    O plugin YouTube do streamlink >=8.1.2 (fix PR #6777) configura
    internamente useragents.CHROME via session.http.headers antes de fazer
    o POST para youtubei/v1/player com clientName=ANDROID). Passar
    --http-header User-Agent=<outro-UA> via CLI sobrescreve esse header e
    pode quebrar a autenticacao da API, causando 400 Bad Request.

    Flags utilizadas:
    - --no-config: ignora qualquer ~/.streamlinkrc ou /etc/streamlink/config
      que possa adicionar flags conflitantes (ex: --hls-segment-threads).
    - --no-plugin-sideloading: ignora plugins de terceiros no filesystem.
    - --http-no-ssl-verify: evita erros SSL em ambientes de container sem
      CA bundle completo.
    - --loglevel: info (padrão) ou debug (modo debug ativado).

    Args:
        watch_url:     URL do vídeo YouTube.
        debug_enabled: Se True, usa --loglevel debug para diagnóstico detalhado.
    """
    loglevel = "debug" if debug_enabled else "info"
    return [
        "streamlink",
        "--no-config",
        "--no-plugin-sideloading",
        "--http-no-ssl-verify",
        "--loglevel", loglevel,
        "--stdout",
        watch_url,
        "best",
    ]


def build_live_hls_ffmpeg_cmd(
    hls_url: str,
    user_agent: str = DEFAULT_USER_AGENT,
    debug_enabled: bool = False,
) -> List[str]:
    """Retorna comando ffmpeg para consumir URL HLS de live stream.

    Usado como fallback quando streamlink falha (fast-fail em web/main.py).
    A URL HLS e resolvida previamente por resolve_live_hls_url_async().

    Confirmado em testes (log novo-28.txt): yt-dlp -g retorna HLS muxado
    de manifest.googlevideo.com com itag/96, 1920x1080, H264+AAC.

    -re: essencial para live streams — lê segmentos HLS a velocidade real
    (1x). Sem -re, ffmpeg baixa todos os segmentos disponíveis no DVR de uma
    vez (velocidade de rede >> velocidade de reprodução), inundando o ring
    buffer antes do cliente conectar e causando "cliente atrasado" em loop.
    Com -re, o buffer cresce a ~1x, o cliente conecta próximo ao live edge
    e não há skips de segmento.

    Flags de reconnect garantem resiliencia durante o live:
    - -reconnect 1: reconecta se a conexao cair (HTTP drops).
    - -reconnect_streamed 1: reconecta em streams HLS/DASH ja iniciados.
    - -reconnect_delay_max 5: espera maximo 5s entre tentativas.

    Args:
        hls_url:       URL HLS do manifest (googlevideo.com ou similar).
        user_agent:    User-Agent para os requests HTTP do ffmpeg.
        debug_enabled: Se True, usa -loglevel info para diagnóstico detalhado.

    Returns:
        Lista de strings pronta para asyncio.create_subprocess_exec(*cmd).
    """
    loglevel = "warning" if debug_enabled else "error"
    return [
        "ffmpeg", "-loglevel", loglevel,
        "-nostats",
        # Gera PTS quando a entrada vier com lacunas de timestamp.
        "-fflags", "+genpts",
        "-re",                           # lê HLS a 1x — impede buffer overflow no live
        "-user_agent", user_agent,
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        "-i", hls_url,
        "-c", "copy",
        # Reduz latência de mux e melhora regularidade do PCR/PTS no TS de saída.
        "-max_interleave_delta", "0",
        "-muxpreload", "0",
        "-muxdelay", "0",
        "-pcr_period", "20",
        "-pat_period", "0.1",
        "-sdt_period", "0.25",
        "-mpegts_flags", "+resend_headers",
        "-flush_packets", "1",
        "-f", "mpegts",
        "pipe:1",
    ]


def build_vod_cmd(
    cdn_url: str,
    watch_url: str,
    user_agent: str = DEFAULT_USER_AGENT,
    debug_enabled: bool = False,
) -> List[str]:
    """Retorna comando para reproducao de VOD (status in _VOD_STATUSES).

    Se cdn_url estiver preenchida (resolvida por resolve_vod_url_async),
    usa ffmpeg direto na URL CDN. Caso contrario, usa pipeline bash
    yt-dlp | ffmpeg como fallback garantido.

    Args:
        cdn_url:       URL CDN resolvida pelo yt-dlp --get-url. Pode ser vazio.
        watch_url:     URL original do video (https://youtube.com/watch?v=...).
        user_agent:    User-Agent HTTP para requests.
        debug_enabled: Se True, usa -loglevel info para diagnóstico detalhado.

    Returns:
        Lista de strings pronta para asyncio.create_subprocess_exec(*cmd).
    """
    loglevel = "warning" if debug_enabled else "error"
    
    if cdn_url:
        logger.debug(f"VOD via ffmpeg CDN direto: {cdn_url[:80]}...")
        return [
            "ffmpeg", "-loglevel", loglevel,
            "-nostats",
            "-re",
            "-headers", f"User-Agent: {user_agent}\r\n",
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-i", cdn_url,
            "-c", "copy",
            "-mpegts_flags", "+resend_headers",
            "-flush_packets", "1",
            "-f", "mpegts",
            "pipe:1",
        ]

    # Fallback: pipeline bash yt-dlp -> ffmpeg
    q_ua  = shlex.quote(user_agent)
    q_url = shlex.quote(watch_url)
    yt_dlp_verbose = "--verbose" if debug_enabled else ""
    fallback = (
        "set -o pipefail; "
        f"yt-dlp {yt_dlp_verbose} -f 'best[ext=mp4][acodec!=none]/best[acodec!=none]/best' "
        f"--js-runtimes node --no-playlist -o - --user-agent {q_ua} {q_url} "
        f"| ffmpeg -loglevel {loglevel} -nostats -re -i pipe:0 -c copy "
        "-mpegts_flags +resend_headers -flush_packets 1 -f mpegts pipe:1"
    )
    logger.info(f"VOD via fallback yt-dlp|ffmpeg: {watch_url}")
    return ["bash", "-lc", fallback]


def build_ffmpeg_placeholder_cmd(
    image_url: str,
    text_line1: str = "",
    text_line2: str = "",
    font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    user_agent: str = DEFAULT_USER_AGENT,
    debug_enabled: bool = False,
) -> tuple[list[str], list[str]]:
    """Retorna (cmd, temp_files) para placeholder de stream indisponivel.

    Mudancas vs. versao anterior:
    - Removido loop=-1 (loop infinito que impede SIGTERM limpo e cria zombie).
    - Adicionado -loop 1 -t PLACEHOLDER_SEGMENT_DURATION: processo encerra
      normalmente apos ~30s; proxy_manager.restart_placeholder_if_needed()
      relanca enquanto houver clientes.
    - fps=1 no filtro + -r 1 -g 1 na saida: reduz CPU de encoding ~96%
      (imagem estatica nao precisa de 25fps).
    - -b:a 32k: audio minimo para placeholder silencioso.
    - Removido -shortest: conflitava com -t explicito.

    Args:
        image_url:     URL ou path local da imagem de fundo.
        text_line1:    Linha superior do overlay (ex: titulo do evento).
        text_line2:    Linha inferior do overlay (ex: horario previsto).
        font_path:     Path da fonte TrueType dentro do container.
        user_agent:    User-Agent para imagens remotas.
        debug_enabled: Se True, usa -loglevel info para diagnóstico detalhado.

    Returns:
        Tupla (cmd, temp_files) onde temp_files sao paths a deletar.
    """
    loglevel = "info" if debug_enabled else "error"
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
    # fps=1: imagem estatica nao precisa de 25fps — reduz CPU ~96%
    # O loop no filtro de vídeo pode desregular PTS e acelerar indevidamente a saída.
    # O -loop 1 no input já é suficiente para placeholder estático.
    video_filter = (
        f"[0:v]fps=1,scale=1280:720"
        f"{(',' + filter_chain) if filter_chain else ''}[v]"
    )

    is_local = image_url.startswith("/") or image_url.startswith("file://")
    input_args = (
        ["-i", image_url]
        if is_local
        else ["-headers", f"User-Agent: {user_agent}\r\n", "-i", image_url]
    )

    cmd = [
        "ffmpeg", "-loglevel", loglevel,
        "-re",
        # -loop 1 + -t: processo encerra naturalmente apos PLACEHOLDER_SEGMENT_DURATION s
        "-loop", "1",
        "-t", str(PLACEHOLDER_SEGMENT_DURATION),
        *input_args,
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-filter_complex", video_filter,
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
        "-maxrate", "800k", "-bufsize", "1200k",
        # 1fps saida + todo frame keyframe -> seek instantaneo no player
        "-r", "1", "-g", "1",
        "-c:a", "aac", "-b:a", "32k",
        "-tune", "stillimage",
        "-mpegts_flags", "+resend_headers",
        "-flush_packets", "1",
        "-f", "mpegts", "pipe:1",
    ]
    return cmd, temp_files


# ---------------------------------------------------------------------------
# Resolucao assincrona de URL de live stream (fallback quando streamlink falha)
# ---------------------------------------------------------------------------

async def resolve_live_hls_url_async(
    watch_url: str,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: int = LIVE_HLS_RESOLVE_TIMEOUT_S,
    debug_enabled: bool = False,
) -> str:
    """Resolve URL HLS de live stream via yt-dlp -g de forma assincrona.

    Usado como fallback quando streamlink falha (fast-fail em web/main.py).

    Comportamento confirmado em testes (log novo-28.txt):
    - yt-dlp -g retorna URL HLS muxada de manifest.googlevideo.com
    - itag/96: video H264 1920x1080 + audio AAC, sem necessidade de muxing
    - Node.js instalado no container resolve o warning de JS runtime

    Args:
        watch_url:     URL do video YouTube (live).
        user_agent:    User-Agent HTTP.
        timeout:       Segundos maximos de espera (default 15).
        debug_enabled: Se True, yt-dlp usa --verbose para diagnóstico.

    Returns:
        URL HLS como string, ou "" em falha/timeout.
    """
    proc = None
    try:
        yt_dlp_args = [
            "yt-dlp",
            "-g",
            "--no-playlist",
            "-f", "best[protocol=m3u8]/best",
            "--js-runtimes", "node",
            "--user-agent", user_agent,
        ]
        if debug_enabled:
            yt_dlp_args.append("--verbose")
        yt_dlp_args.append(watch_url)
        
        proc = await asyncio.create_subprocess_exec(
            *yt_dlp_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            err_msg = err.decode("utf-8", errors="replace").strip() if err else ""
            logger.warning(
                f"[resolve_live_hls] yt-dlp falhou rc={proc.returncode} | {err_msg[:200]}"
            )
            return ""
        text = out.decode("utf-8", errors="replace").strip()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            return ""

        hls_candidates = [
            ln for ln in lines
            if (".m3u8" in ln or "manifest/hls" in ln or "hls_playlist" in ln)
        ]
        if hls_candidates:
            url = hls_candidates[0]
            logger.info(f"[resolve_live_hls] HLS URL resolvida: {url[:80]}...")
            return url

        # Fallback: mantém comportamento anterior, mas reporta que não veio HLS.
        url = lines[0]
        logger.warning(f"[resolve_live_hls] fallback sem HLS explícito: {url[:80]}...")
        return url

    except asyncio.TimeoutError:
        logger.warning(f"[resolve_live_hls] timeout ({timeout}s) para {watch_url}")
        if proc is not None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
        return ""

    except Exception as exc:
        logger.warning(f"[resolve_live_hls] erro inesperado para {watch_url}: {exc}")
        return ""


# ---------------------------------------------------------------------------
# Resolucao assincrona de URL VOD (unica fonte de verdade)
# ---------------------------------------------------------------------------

async def resolve_vod_url_async(
    watch_url: str,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: int = VOD_RESOLVE_TIMEOUT_S,
    debug_enabled: bool = False,
) -> str:
    """Resolve a URL CDN real de um VOD via yt-dlp --get-url de forma assincrona.

    Nao bloqueia o event loop. Em timeout ou falha retorna string vazia,
    e o chamador deve usar build_vod_cmd(cdn_url="") para acionar o fallback.

    Levanta GeoBlockedError se yt-dlp reportar bloqueio geografico.
    O chamador deve capturar GeoBlockedError e retornar HTTP 451 ao cliente,
    sem tentativa de retry (o bloqueio e permanente e determinístico).

    Flags utilizadas:
    - --js-runtimes node: usa Node.js instalado no container para executar
      o JS player do YouTube, evitando o warning de JS runtime e garantindo
      acesso a todos os formatos.
    - -f bestvideo+bestaudio/best: seleciona melhor qualidade disponivel.

    Args:
        watch_url:     URL do video YouTube.
        user_agent:    User-Agent HTTP.
        timeout:       Segundos maximos de espera (default 20).
        debug_enabled: Se True, yt-dlp usa --verbose para diagnóstico.

    Returns:
        URL CDN como string, ou "" em falha/timeout.

    Raises:
        GeoBlockedError: quando yt-dlp detecta bloqueio geografico.
    """
    proc = None
    try:
        yt_dlp_args = [
            "yt-dlp",
            "-f", "best[ext=mp4][acodec!=none]/best[acodec!=none]/best",
            "--get-url",
            "--no-playlist",
            "--js-runtimes", "node",
            "--user-agent", user_agent,
        ]
        if debug_enabled:
            yt_dlp_args.append("--verbose")
        yt_dlp_args.append(watch_url)
        
        proc = await asyncio.create_subprocess_exec(
            *yt_dlp_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            err_msg = err.decode("utf-8", errors="replace").strip() if err else ""
            logger.warning(
                f"[resolve_vod] yt-dlp falhou rc={proc.returncode} url={watch_url} | {err_msg[:200]}"
            )
            # Geo-block: erro permanente e determinístico — não tenta fallback.
            # yt-dlp reporta: "The uploader has not made this video available in your country"
            if "not available in your country" in err_msg.lower():
                logger.warning(
                    f"[resolve_vod] geo-block detectado para {watch_url} — levantando GeoBlockedError"
                )
                raise GeoBlockedError(watch_url)
            return ""
        text = out.decode("utf-8", errors="replace").strip()
        return text.splitlines()[0] if text else ""

    except GeoBlockedError:
        raise  # repropaga sem envolver em outro except

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
    user_agent: str = DEFAULT_USER_AGENT,
    font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    texts_cache_path: Optional[Path] = None,
    debug_enabled: bool = False,
) -> tuple[list[str], list[str]]:
    """Retorna (cmd, temp_files) para status live ou placeholder.

    Para status in ('none', 'vod', 'ended') use build_player_command_async(),
    que resolve a URL CDN de forma assincrona sem bloquear o event loop.
    Esta funcao aceita esses status como fallback de emergencia (yt-dlp|ffmpeg
    sem pre-resolucao), mas NAO deve ser chamada em producao para VOD.

    Para status="live" em producao, o fast-fail deve ser tratado em
    web/main.py: se streamlink encerrar com erro em < 8s, chamar
    resolve_live_hls_url_async() + build_live_hls_ffmpeg_cmd() como fallback.

    Args:
        video_id:         ID do video YouTube.
        status:           "live", "upcoming", "vod", "ended", "none" ou outro.
        watch_url:        URL completa do video.
        thumbnail_url:    URL ou path local da thumbnail para placeholder.
        user_agent:       User-Agent HTTP (nao repassado ao streamlink).
        font_path:        Path da fonte TrueType no container.
        texts_cache_path: Path para JSON de textos de overlay.
        debug_enabled:    Se True, ativa loglevel verbose em todos os processos.

    Returns:
        Tupla (cmd, temp_files).
    """
    if status == "live":
        logger.debug(f"[{video_id}] modo live -> streamlink")
        return build_streamlink_cmd(watch_url, debug_enabled=debug_enabled), []

    # VOD: status 'vod' e 'ended' sao lives encerradas; 'none' e o valor legado.
    # Em producao use build_player_command_async() para pre-resolver a URL CDN.
    if status in _VOD_STATUSES:
        logger.warning(
            f"[{video_id}] build_player_command() chamado com status={status!r} — "
            "use build_player_command_async() para resolucao assincrona"
        )
        return build_vod_cmd(
            cdn_url="", watch_url=watch_url, user_agent=user_agent, debug_enabled=debug_enabled
        ), []

    logger.debug(f"[{video_id}] modo placeholder (status={status!r})")
    texts = _get_texts_from_cache(video_id, texts_cache_path) if texts_cache_path else {}
    return build_ffmpeg_placeholder_cmd(
        image_url=thumbnail_url,
        text_line1=texts.get("line1", ""),
        text_line2=texts.get("line2", ""),
        font_path=font_path,
        user_agent=user_agent,
        debug_enabled=debug_enabled,
    )


async def build_player_command_async(
    video_id: str,
    status: Optional[str],
    watch_url: str,
    thumbnail_url: str,
    user_agent: str = DEFAULT_USER_AGENT,
    font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    texts_cache_path: Optional[Path] = None,
    debug_enabled: bool = False,
) -> tuple[list[str], list[str]]:
    """Versao assincrona de build_player_command — obrigatoria para VOD.

    Para status in ('none', 'vod', 'ended') resolve a URL CDN via
    resolve_vod_url_async() sem bloquear o event loop. Para os demais
    status delega ao build_player_command() sincrono.

    Status VOD reconhecidos:
    - 'vod':   live encerrada, confirmado pelo scheduler YouTube API.
    - 'ended': alias equivalente a 'vod' (compatibilidade futura).
    - 'none':  valor legado (mantido por compatibilidade).

    Raises:
        GeoBlockedError: propagada de resolve_vod_url_async() quando o video
            esta geo-bloqueado. O chamador deve retornar HTTP 451.

    Args: idem build_player_command() + debug_enabled.

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
            debug_enabled=_config.get_bool("streaming_debug_enabled"),
        )
    """
    if status == "live":
        logger.debug(f"[{video_id}] modo LIVE -> resolve_live_hls_url_async")
        hls_url = await resolve_live_hls_url_async(
            watch_url, user_agent=user_agent, debug_enabled=debug_enabled
        )
        if hls_url:
            logger.info(f"[{video_id}] live via HLS (yt-dlp + ffmpeg)")
            return build_live_hls_ffmpeg_cmd(
                hls_url, user_agent=user_agent, debug_enabled=debug_enabled
            ), []
        logger.warning(f"[{video_id}] live HLS indisponivel, fallback streamlink")
        return build_streamlink_cmd(watch_url, debug_enabled=debug_enabled), []

    if status in _VOD_STATUSES:
        logger.debug(f"[{video_id}] modo VOD (status={status!r}) -> resolve_vod_url_async")
        cdn_url = await resolve_vod_url_async(watch_url, user_agent, debug_enabled=debug_enabled)
        if cdn_url:
            logger.debug(f"[{video_id}] CDN resolvida: {cdn_url[:80]}...")
        else:
            logger.info(f"[{video_id}] CDN nao resolvida, usando fallback yt-dlp|ffmpeg")
        return build_vod_cmd(
            cdn_url=cdn_url, watch_url=watch_url, user_agent=user_agent, debug_enabled=debug_enabled
        ), []

    return build_player_command(
        video_id=video_id,
        status=status,
        watch_url=watch_url,
        thumbnail_url=thumbnail_url,
        user_agent=user_agent,
        font_path=font_path,
        texts_cache_path=texts_cache_path,
        debug_enabled=debug_enabled,
    )
