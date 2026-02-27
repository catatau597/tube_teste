"""
core/player_router.py
Responsabilidade: Decisao de qual comando executar dado o status do stream.
Sem subprocess direto, retorna List[str] de comando.
"""
import json
import tempfile
from pathlib import Path
from typing import Dict, List, Optional


def _escape_ffmpeg_text(text: str) -> str:
    """Escapa caracteres especiais para uso no filtro drawtext do ffmpeg."""
    text = text.replace("\\", "\\\\")  # \ -> \\ (barras primeiro)
    text = text.replace("'", "\\'")    # ' -> \'
    text = text.replace(":", "\\:")    # : -> \:
    text = text.replace("%", "\\%")    # % -> \%
    text = text.replace(",", "\\,")    # , -> \,
    return text


def _get_texts_from_cache(video_id: str, texts_cache_path: Path) -> Dict[str, str]:
    try:
        if texts_cache_path.exists():
            data = json.loads(texts_cache_path.read_text(encoding="utf-8"))
            return data.get(video_id, {"line1": "", "line2": ""})
    except Exception:
        pass
    return {"line1": "", "line2": ""}


def build_ffmpeg_placeholder_cmd(
    image_url: str,
    text_line1: str = "",
    text_line2: str = "",
    font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    user_agent: str = "Mozilla/5.0",
) -> tuple[list[str], list[str]]:
    """
    Retorna (cmd, temp_files).
    temp_files: lista de paths de arquivos temporarios que devem ser deletados
    apos o processo terminar.
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
    # Para path local nao usar headers HTTP; para URL remota usar -headers
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


def build_streamlink_cmd(watch_url: str, user_agent: str = "Mozilla/5.0") -> List[str]:
    return [
        "streamlink", "--stdout",
        "--http-header", f"User-Agent={user_agent}",
        "--config", "/dev/null",
        "--no-plugin-sideloading",
        watch_url, "best",
    ]


def build_ytdlp_cmd(watch_url: str, user_agent: str = "Mozilla/5.0") -> List[str]:
    return [
        "yt-dlp", "-f", "best", "-o", "-",
        "--user-agent", user_agent,
        watch_url,
    ]


def build_player_command(
    video_id: str,
    status: Optional[str],
    watch_url: str,
    thumbnail_url: str,
    user_agent: str = "Mozilla/5.0",
    font_path: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    texts_cache_path: Optional[Path] = None,
) -> tuple[list[str], list[str]]:
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
