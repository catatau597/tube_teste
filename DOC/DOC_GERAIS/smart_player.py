"""
uv pip install python-dotenv
"""

#!/usr/bin/env python3
import argparse
import subprocess
import sys
import json
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta
import os
import logging
from typing import Any, Dict, List, Optional, Set
from dotenv import load_dotenv # Importa load_dotenv

# --- Constantes e Caminhos ---
SCRIPT_DIR = Path(__file__).resolve().parent
STATE_CACHE_PATH = SCRIPT_DIR / "state_cache.json"
TEXTS_CACHE_PATH = SCRIPT_DIR / "textos_epg.json"

# --- Carrega .env ANTES de configurar o log ---
dotenv_path = SCRIPT_DIR / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path=dotenv_path)
else:
    # Log temporário no stderr se .env faltar
    print(f"[{datetime.now()}] WARNING [smart_player] Arquivo .env não encontrado em {dotenv_path}", file=sys.stderr)

# *** NOVO: Função helper para nível de log ***
def get_logging_level(level_str: str) -> int:
    """Converte string de nível de log para o objeto logging correspondente."""
    return getattr(logging, level_str.upper(), logging.INFO)

# *** NOVO: Carrega variáveis de Log ***
SMART_PLAYER_LOG_LEVEL_STR = os.getenv("SMART_PLAYER_LOG_LEVEL", "INFO")
SMART_PLAYER_LOG_TO_FILE = os.getenv("SMART_PLAYER_LOG_TO_FILE", "true").lower() == "true"
SMART_PLAYER_LOG_LEVEL = get_logging_level(SMART_PLAYER_LOG_LEVEL_STR)

# *** MODIFICADO: Configuração de Logging dinâmica ***
log_file_path = SCRIPT_DIR / "smart_player.log"
log_config = {
    "format": "%(asctime)s %(levelname)s [%(name)s] %(message)s",
    "datefmt": "%Y-%m-%d %H:%M:%S",
    "level": SMART_PLAYER_LOG_LEVEL,
    "force": True, # Para sobrescrever config anterior
}

if SMART_PLAYER_LOG_TO_FILE:
    log_config['filename'] = log_file_path
    log_config['filemode'] = 'a'
else:
    # Se não for para arquivo, loga no stderr (padrão do logging, capturado pelo ts_proxy)
    log_config['stream'] = sys.stderr

logging.basicConfig(**log_config) # Aplica a configuração

logger = logging.getLogger("smart_player")
logger.info(f"--- Nova execução. Nível: {SMART_PLAYER_LOG_LEVEL_STR}. Logando para: {'Arquivo' if SMART_PLAYER_LOG_TO_FILE else 'Console (stderr)'} ---")
# --- Fim da Configuração de Log ---


PLACEHOLDER_IMAGE_URL = os.getenv("PLACEHOLDER_IMAGE_URL", "") # Lê do .env já carregado
DEFAULT_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# --- Funções Auxiliares ---

def escape_ffmpeg_text(text: str) -> str:
    text = text.replace('\\', '\\\\')
    text = text.replace("'", r"\'")
    text = text.replace(":", r"\:")
    text = text.replace("%", r"%%")
    text = text.replace(",", r"\,")
    return text

def get_stream_status_from_cache(video_id: str) -> Optional[Dict[str, Any]]:
    try:
        if STATE_CACHE_PATH.exists():
            with open(STATE_CACHE_PATH, "r", encoding="utf-8") as f: cache_data = json.load(f)
            stream_info = cache_data.get("streams", {}).get(video_id)
            if stream_info:
                for key in ('scheduled_start_time_utc', 'actual_start_time_utc', 'actual_end_time_utc', 'fetch_time', 'last_seen'):
                     if key in stream_info and isinstance(stream_info[key], str):
                         try: stream_info[key] = datetime.fromisoformat(stream_info[key].replace('Z', '+00:00'))
                         except (ValueError, TypeError): stream_info[key] = None
                     elif key in stream_info and not isinstance(stream_info[key], datetime): stream_info[key] = None
                return stream_info
            else: logger.debug(f"Video ID {video_id} não encontrado no cache.")
        else: logger.warning(f"Arquivo state_cache.json não encontrado em {STATE_CACHE_PATH}")
    except json.JSONDecodeError as e: logger.error(f"Erro ao decodificar JSON de {STATE_CACHE_PATH}: {e}")
    except Exception as e: logger.error(f"Erro ao ler/processar state_cache.json para {video_id}: {e}")
    return None

def get_texts_from_cache(video_id: str) -> Dict[str, str]:
    texts = {"line1": "", "line2": ""}
    try:
        if TEXTS_CACHE_PATH.exists():
            with open(TEXTS_CACHE_PATH, "r", encoding="utf-8") as f: all_texts = json.load(f)
            stream_texts = all_texts.get(video_id)
            if stream_texts: texts["line1"] = stream_texts.get("line1", ""); texts["line2"] = stream_texts.get("line2", "")
            else: logger.debug(f"Video ID {video_id} não encontrado em {TEXTS_CACHE_PATH}")
        else: logger.warning(f"Arquivo textos_epg.json não encontrado em {TEXTS_CACHE_PATH}")
    except json.JSONDecodeError as e: logger.error(f"Erro ao decodificar JSON de {TEXTS_CACHE_PATH}: {e}")
    except Exception as e: logger.warning(f"Não foi possível obter textos de {TEXTS_CACHE_PATH} para {video_id}: {e}")
    return texts

def run_ffmpeg_placeholder(image_url: str, text_line1: str = "", text_line2: str = "", font_path: str = DEFAULT_FONT_PATH, user_agent: str = "Mozilla/5.0"):
    if not image_url: logger.error("URL da imagem para FFmpeg vazia."); return
    logger.info(f"Iniciando FFmpeg para placeholder/upcoming: {image_url}")
    drawtext_filters = []
    font_file_path = Path(font_path); use_font = font_file_path.is_file()
    if not use_font: logger.warning(f"Arquivo de fonte não encontrado: {font_path}.")
    if use_font:
        if text_line1: escaped_text1 = escape_ffmpeg_text(text_line1); drawtext_filters.append(f"drawtext=fontfile='{font_path}':text='{escaped_text1}':x=(w-text_w)/2:y=h-100:fontsize=48:fontcolor=white:borderw=2:bordercolor=black@0.8")
        if text_line2: escaped_text2 = escape_ffmpeg_text(text_line2); drawtext_filters.append(f"drawtext=fontfile='{font_path}':text='{escaped_text2}':x=(w-text_w)/2:y=h-50:fontsize=36:fontcolor=white:borderw=2:bordercolor=black@0.8")
    filter_chain = ",".join(drawtext_filters) if drawtext_filters else ""
    ffmpeg_cmd = ["ffmpeg", "-loglevel", "error", "-re", "-user_agent", user_agent, "-i", image_url, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-filter_complex", f"[0:v]fps=25,scale=1280:720,loop=-1:1:0{',' if filter_chain else ''}{filter_chain}[v]", "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-shortest", "-tune", "stillimage", "-f", "mpegts", "pipe:1"]
    logger.debug(f"Comando FFmpeg: {' '.join(ffmpeg_cmd)}")
    try:
        process = subprocess.Popen(ffmpeg_cmd, stdout=sys.stdout, stderr=subprocess.PIPE)
        _, stderr_output = process.communicate()
        if process.returncode != 0:
            logger.error(f"FFmpeg falhou com código {process.returncode}")
            if stderr_output: logger.error(f"FFmpeg stderr:\n{stderr_output.decode('utf-8', errors='ignore')}")
    except FileNotFoundError: logger.error("Erro: Comando 'ffmpeg' não encontrado.")
    except Exception as e: logger.error(f"Erro ao executar FFmpeg: {e}")

def run_streamlink(watch_url: str, user_agent: str = "Mozilla/5.0"):
    """Executa o Streamlink para streams ao vivo."""
    logger.info(f"Iniciando Streamlink para live: {watch_url}")
    # *** CORRIGIDO: Adiciona --no-plugin-sideloading ***
    streamlink_cmd = [
        "streamlink",
        "--stdout",
        "--http-header", f"User-Agent={user_agent}",
        "--config", "/dev/null",                  # Ignora arquivo de config
        "--no-plugin-sideloading",              # Ignora plugins customizados
        watch_url,
        "best"
    ]
    logger.debug(f"Comando Streamlink: {' '.join(streamlink_cmd)}")
    try:
        process = subprocess.Popen(streamlink_cmd, stdout=sys.stdout, stderr=subprocess.PIPE)
        _, stderr_output = process.communicate()
        # (Restante como antes...)
        if process.returncode != 0:
            if process.returncode == 1 and stderr_output and "error: No playable streams found" in stderr_output.decode('utf-8', errors='ignore'):
                 logger.warning(f"Streamlink: Nenhum stream jogável encontrado para {watch_url}.")
            else:
                 logger.error(f"Streamlink falhou com código {process.returncode}")
                 if stderr_output: logger.error(f"Streamlink stderr:\n{stderr_output.decode('utf-8', errors='ignore')}")
    except FileNotFoundError: logger.error("Erro: Comando 'streamlink' não encontrado.")
    except Exception as e: logger.error(f"Erro ao executar Streamlink: {e}")

def run_ytdlp(watch_url: str, user_agent: str = "Mozilla/5.0"):
    logger.info(f"Iniciando yt-dlp para VOD: {watch_url}")
    ytdlp_cmd = ["yt-dlp", "-f", "best", "-o", "-", "--user-agent", user_agent, watch_url]
    logger.debug(f"Comando yt-dlp: {' '.join(ytdlp_cmd)}")
    try:
        process = subprocess.Popen(ytdlp_cmd, stdout=sys.stdout, stderr=subprocess.PIPE)
        _, stderr_output = process.communicate()
        if process.returncode != 0:
            logger.error(f"yt-dlp falhou com código {process.returncode}")
            if stderr_output: logger.error(f"yt-dlp stderr:\n{stderr_output.decode('utf-8', errors='ignore')}")
    except FileNotFoundError: logger.error("Erro: Comando 'yt-dlp' não encontrado.")
    except Exception as e: logger.error(f"Erro ao executar yt-dlp: {e}")

# --- Função Principal ---
def main():
    parser = argparse.ArgumentParser(description="Roteador inteligente para streams do YouTube.")
    parser.add_argument("-i", "--input", dest="input_url", required=True, help="URL (imagem ou vídeo YouTube)")
    parser.add_argument("-ua", "--user-agent", dest="user_agent", default="Mozilla/5.0", help="User-Agent HTTP opcional")
    args = parser.parse_args()
    url = args.input_url; user_agent = args.user_agent
    logger.info(f"Recebida requisição para URL: {url} (User-Agent: {user_agent})")

    is_image = False; video_id_from_thumb = None
    if PLACEHOLDER_IMAGE_URL and url == PLACEHOLDER_IMAGE_URL:
        is_image = True; logger.info("URL: placeholder."); run_ffmpeg_placeholder(image_url=url, user_agent=user_agent); return
    elif "ytimg.com/vi/" in url:
        is_image = True; logger.info("URL: thumbnail YT.")
        match = re.search(r'/vi/([^/]+)/', url)
        if match:
            video_id_from_thumb = match.group(1); logger.info(f"Thumb ID: {video_id_from_thumb}")
            texts = get_texts_from_cache(video_id_from_thumb); run_ffmpeg_placeholder(image_url=url, text_line1=texts["line1"], text_line2=texts["line2"], user_agent=user_agent)
        else: logger.warning("Não extraiu ID da thumb. Exibindo sem texto."); run_ffmpeg_placeholder(image_url=url, user_agent=user_agent)
        return

    if not is_image and ("youtube.com/watch?v=" in url or "youtu.be/" in url):
        logger.info("URL: vídeo YT.")
        video_id = None; match_v = re.search(r'v=([a-zA-Z0-9_-]+)', url); match_be = re.search(r'youtu.be/([a-zA-Z0-9_-]+)', url)
        if match_v: video_id = match_v.group(1)
        elif match_be: video_id = match_be.group(1)
        if not video_id: logger.error("Não extraiu ID do vídeo YT."); run_ffmpeg_placeholder(PLACEHOLDER_IMAGE_URL, user_agent=user_agent) if PLACEHOLDER_IMAGE_URL else sys.exit(1); return

        logger.info(f"Video ID: {video_id}")
        stream_info = get_stream_status_from_cache(video_id); status = None; thumbnail_url = None
        if stream_info:
            status = stream_info.get('status'); thumbnail_url = stream_info.get('thumbnail_url')
            logger.info(f"Cache status: '{status}'")
            logger.debug(f"  -> Cache times: start={stream_info.get('actual_start_time_utc')}, end={stream_info.get('actual_end_time_utc')}")
        else:
            logger.warning(f"Video ID {video_id} não encontrado no cache."); thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"

        def is_genuinely_live(stream_dict):
            if not stream_dict: return False
            start = stream_dict.get('actual_start_time_utc'); end = stream_dict.get('actual_end_time_utc')
            return stream_dict.get('status') == 'live' and isinstance(start, datetime) and not isinstance(end, datetime)

        if status == 'live' and is_genuinely_live(stream_info): run_streamlink(url, user_agent=user_agent)
        elif status == 'none' or (status == 'live' and not is_genuinely_live(stream_info)):
            if status == 'live': logger.warning(f"Status '{status}' mas não parece live. Tratando como VOD.")
            run_ytdlp(url, user_agent=user_agent)
        elif status == 'upcoming':
            logger.warning(f"Vídeo {video_id} ('upcoming'). Exibindo thumbnail."); texts = get_texts_from_cache(video_id)
            thumb_to_use = thumbnail_url or PLACEHOLDER_IMAGE_URL
            if thumb_to_use: run_ffmpeg_placeholder(thumb_to_use, texts["line1"], texts["line2"], user_agent=user_agent)
            else: logger.error("Não há thumb/placeholder para vídeo upcoming.")
        else:
            logger.warning(f"Status '{status}' ou vídeo não encontrado/inválido. Fallback para thumb/placeholder.")
            thumb_to_use = thumbnail_url or PLACEHOLDER_IMAGE_URL
            if thumb_to_use: run_ffmpeg_placeholder(thumb_to_use, user_agent=user_agent)
            else: logger.error("Não há thumb/placeholder para fallback.")
    else:
        logger.error(f"URL não reconhecida: {url}")
        if PLACEHOLDER_IMAGE_URL: run_ffmpeg_placeholder(PLACEHOLDER_IMAGE_URL, user_agent=user_agent)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Erro inesperado na função main: {e}", exc_info=True)
        sys.exit(1)