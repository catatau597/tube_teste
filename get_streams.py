"""
YouTube Stream Aggregator v3.4 - Invert Order & Name Mapping
- Adiciona controles de log (nível, arquivo) via .env.
- Adiciona filtro de categoria (FILTER_BY_CATEGORY) via .env.
- Adiciona mapeamento de nome de canal (CHANNEL_NAME_MAPPINGS) via .env.
- Inverte a ordem dos prefixos de [Canal][Status] para [Status][Canal]..
"""
"""
pip install python-dotenv google-api-python-client pytz Flask
"""

import asyncio
import json
import logging
import os
import re
import threading
import unicodedata
import sys
import pytz
from xml.sax.saxutils import escape
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from dotenv import load_dotenv
from flask import Flask, Response, request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# --- CONFIGURAÇÃO E INICIALIZAÇÃO ---
load_dotenv()

# *** NOVO: Função helper para nível de log ***
def get_logging_level(level_str: str) -> int:
    """Converte string de nível de log para o objeto logging correspondente."""
    level = getattr(logging, level_str.upper(), logging.INFO)
    return level

# *** NOVO: Carrega variáveis de Log ***
LOG_LEVEL_STR = os.getenv("LOG_LEVEL", "INFO")
LOG_TO_FILE = os.getenv("LOG_TO_FILE", "true").lower() == "true"
LOG_LEVEL = get_logging_level(LOG_LEVEL_STR)

# *** MODIFICADO: Configuração de Logging dinâmica ***
SCRIPT_DIR_GET_STREAMS = Path(__file__).resolve().parent
log_file_path_get_streams = SCRIPT_DIR_GET_STREAMS / "get_streams.log"

log_config = {
    "format": "%(asctime)s %(levelname)s [%(name)s] %(message)s",
    "datefmt": "%Y-%m-%d %H:%M:%S",
    "level": LOG_LEVEL,
    "force": True,
}

if LOG_TO_FILE:
    log_config['filename'] = log_file_path_get_streams
    log_config['filemode'] = 'a'
else:
    log_config['stream'] = sys.stdout

logging.basicConfig(**log_config)

logger = logging.getLogger("TubeWranglerr")
# Log inicial vai para o arquivo/console configurado
logger.info(f"--- Nova execução. Nível de log: {LOG_LEVEL_STR}. Logando para: {'Arquivo' if LOG_TO_FILE else 'Console'} ---")

# Adiciona handler de console se logando em arquivo E nível INFO ou acima
if LOG_TO_FILE and LOG_LEVEL <= logging.INFO:
    try:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        console_handler.setFormatter(formatter)
        logging.getLogger().addHandler(console_handler) # Adiciona ao logger raiz
        logger.info("Logging configurado para Arquivo (Nível {LOG_LEVEL_STR}) e Console (Nível INFO+)")
    except Exception as e:
        logger.error(f"Erro ao configurar console handler: {e}")

# Configura fuso horário local
try:
    local_tz_str = os.getenv("LOCAL_TIMEZONE", "America/Sao_Paulo")
    local_tz = pytz.timezone(local_tz_str)
    logger.info(f"Usando fuso horário local: {local_tz_str}")
except pytz.exceptions.UnknownTimeZoneError:
    logger.warning(f"Fuso horário '{local_tz_str}' inválido. Usando UTC.")
    local_tz = pytz.utc


API_KEY = os.getenv("YOUTUBE_API_KEY")
TARGET_CHANNEL_HANDLES = [h.strip() for h in os.getenv("TARGET_CHANNEL_HANDLES", "").split(",") if h.strip()]
TARGET_CHANNEL_IDS = [i.strip() for i in os.getenv("TARGET_CHANNEL_IDS", "").split(",") if i.strip()]
HTTP_PORT = int(os.getenv("HTTP_PORT", "8888"))
PLAYLIST_SAVE_DIRECTORY = os.getenv("PLAYLIST_SAVE_DIRECTORY", ".")
XMLTV_SAVE_DIRECTORY = os.getenv("XMLTV_SAVE_DIRECTORY", ".")
XMLTV_FILENAME = os.getenv("XMLTV_FILENAME", "youtube_epg.xml")
PLAYLIST_LIVE_FILENAME = os.getenv("PLAYLIST_LIVE_FILENAME", "playlist_live.m3u8")
PLAYLIST_UPCOMING_FILENAME = os.getenv("PLAYLIST_UPCOMING_FILENAME", "playlist_upcoming.m3u8")
PLAYLIST_VOD_FILENAME = os.getenv("PLAYLIST_VOD_FILENAME", "playlist_vod.m3u8")
STATE_CACHE_FILENAME = os.getenv("STATE_CACHE_FILENAME", "state_cache.json")
EPG_DESCRIPTION_CLEANUP = os.getenv("EPG_DESCRIPTION_CLEANUP", "false").lower() == "true"
ENABLE_SCHEDULER_ACTIVE_HOURS = os.getenv("ENABLE_SCHEDULER_ACTIVE_HOURS", "false").lower() == "true"
SCHEDULER_ACTIVE_START_HOUR = int(os.getenv("SCHEDULER_ACTIVE_START_HOUR", "7"))
SCHEDULER_ACTIVE_END_HOUR = int(os.getenv("SCHEDULER_ACTIVE_END_HOUR", "22"))
SCHEDULER_MAIN_INTERVAL_HOURS = int(os.getenv("SCHEDULER_MAIN_INTERVAL_HOURS", "4"))
SCHEDULER_PRE_EVENT_WINDOW_HOURS = int(os.getenv("SCHEDULER_PRE_EVENT_WINDOW_HOURS", "2"))
SCHEDULER_PRE_EVENT_INTERVAL_MINUTES = int(os.getenv("SCHEDULER_PRE_EVENT_INTERVAL_MINUTES", "5"))
SCHEDULER_POST_EVENT_INTERVAL_MINUTES = int(os.getenv("SCHEDULER_POST_EVENT_INTERVAL_MINUTES", "5"))
MAX_SCHEDULE_HOURS = int(os.getenv("MAX_SCHEDULE_HOURS", "72"))
MAX_UPCOMING_PER_CHANNEL = int(os.getenv("MAX_UPCOMING_PER_CHANNEL", "6"))
TITLE_FILTER_EXPRESSIONS = [e.strip() for e in os.getenv("TITLE_FILTER_EXPRESSIONS", "").split(",") if e.strip()]
PREFIX_TITLE_WITH_CHANNEL_NAME = os.getenv("PREFIX_TITLE_WITH_CHANNEL_NAME", "true").lower() == "true"
PREFIX_TITLE_WITH_STATUS = os.getenv("PREFIX_TITLE_WITH_STATUS", "true").lower() == "true"
CATEGORY_MAPPINGS_STR = os.getenv("CATEGORY_MAPPINGS", "")
CATEGORY_MAPPINGS = dict(item.split('|', 1) for item in CATEGORY_MAPPINGS_STR.split(',') if '|' in item)

# *** NOVO: Carrega Mapeamento de Nomes de Canais ***
CHANNEL_NAME_MAPPINGS_STR = os.getenv("CHANNEL_NAME_MAPPINGS", "")
CHANNEL_NAME_MAPPINGS = dict(item.rsplit('|', 1) for item in CHANNEL_NAME_MAPPINGS_STR.split(',') if '|' in item)

TEXTS_CACHE_FILENAME = "textos_epg.json"
STALE_HOURS = int(os.getenv("STALE_HOURS", "6"))
FULL_SYNC_INTERVAL_HOURS = int(os.getenv("FULL_SYNC_INTERVAL_HOURS", "48"))
RESOLVE_HANDLES_TTL_HOURS = int(os.getenv("RESOLVE_HANDLES_TTL_HOURS", "24"))
INITIAL_SYNC_DAYS = int(os.getenv("INITIAL_SYNC_DAYS", "0"))
USE_PLAYLIST_ITEMS = os.getenv("USE_PLAYLIST_ITEMS", "true").lower() == "true"
KEEP_RECORDED_STREAMS = os.getenv("KEEP_RECORDED_STREAMS", "true").lower() == "true"
MAX_RECORDED_PER_CHANNEL = int(os.getenv("MAX_RECORDED_PER_CHANNEL", "2"))
RECORDED_RETENTION_DAYS = int(os.getenv("RECORDED_RETENTION_DAYS", "2"))

FILTER_BY_CATEGORY = os.getenv("FILTER_BY_CATEGORY", "false").lower() == "true"
ALLOWED_CATEGORY_IDS_STR = os.getenv("ALLOWED_CATEGORY_IDS", "17")
ALLOWED_CATEGORY_IDS_SET = {cid.strip() for cid in ALLOWED_CATEGORY_IDS_STR.split(',') if cid.strip()}

PLACEHOLDER_IMAGE_URL = os.getenv("PLACEHOLDER_IMAGE_URL", "")
USE_INVISIBLE_PLACEHOLDER = os.getenv("USE_INVISIBLE_PLACEHOLDER", "true").lower() == "true"

PLACEHOLDER_LIVE_ID = "PLACEHOLDER_LIVE"
PLACEHOLDER_LIVE_TITLE = "NO MOMENTO SEM TRANSMISSÃO AO VIVO"
PLACEHOLDER_UPCOMING_ID = "PLACEHOLDER_UPCOMING"
PLACEHOLDER_UPCOMING_TITLE = "NO MOMENTO SEM AGENDAMENTO"
PLACEHOLDER_VOD_ID = "PLACEHOLDER_VOD"
PLACEHOLDER_VOD_TITLE = "NO MOMENTO SEM GRAVAÇÕES"
PLACEHOLDER_CATEGORY = "Esportes"

if not API_KEY: raise ValueError("YOUTUBE_API_KEY é obrigatória no .env")
if not TARGET_CHANNEL_HANDLES and not TARGET_CHANNEL_IDS: raise ValueError("Pelo menos um TARGET_CHANNEL_HANDLES ou TARGET_CHANNEL_IDS deve ser definido no .env")
if not PLACEHOLDER_IMAGE_URL: logger.warning("PLACEHOLDER_IMAGE_URL não definida. Placeholders desativados.")
logger.debug(f"Valor lido para INITIAL_SYNC_DAYS: {INITIAL_SYNC_DAYS}")


class StateManager:
    # *** MODIFICADO: Adiciona filtro de categoria ***
    def __init__(self, cache_path: Path):
        self.streams: Dict[str, Dict[str, Any]] = {}
        self.channels: Dict[str, str] = {}
        self.cache_path = cache_path
        self.meta: Dict[str, Any] = {"last_main_run": None, "last_full_sync": None, "resolved_handles": {}}

    def update_channels(self, channels_data: Dict[str, str]):
        updated_count = 0; new_count = 0
        for cid, title in channels_data.items():
            if cid and title:
                if cid not in self.channels: new_count +=1
                elif self.channels[cid] != title: updated_count += 1
                self.channels[cid] = title
        if new_count > 0 or updated_count > 0: logger.debug(f"Update Channels: Adicionados: {new_count}, Atualizados: {updated_count}")

    def update_streams(self, streams_data: List[Dict[str, Any]]):
        now = datetime.now(timezone.utc)
        added_count = 0; updated_count = 0; ignored_initial_vod_count = 0
        ignored_category_count = 0 # *** NOVO: Contador de filtro de categoria ***

        for stream in streams_data:
            vid = stream.get('video_id')
            if not vid: continue

            existing = self.streams.get(vid)
            current_api_status = stream.get('status', 'none')

            # *** NOVO: LÓGICA DE FILTRO DE CATEGORIA ***
            # Aplica o filtro *antes* de decidir adicionar/atualizar
            if FILTER_BY_CATEGORY:
                category_id = stream.get('category_original') # Ex: "17"
                if category_id not in ALLOWED_CATEGORY_IDS_SET:
                    if existing:
                        logger.debug(f"Ignorando atualização e removendo stream {vid} (categoria {category_id} não permitida).")
                        self.streams.pop(vid, None)
                    else:
                        logger.debug(f"Ignorando stream novo {vid} (categoria {category_id} não permitida).")
                    ignored_category_count += 1
                    continue

            # *** LÓGICA DE FILTRAGEM DE VOD INICIAL (Existente) ***
            if not existing and current_api_status == 'none':
                ignored_initial_vod_count += 1
                logger.debug(f"Ignorando VOD inicial (não estava no cache): {vid}")
                continue

            stream['fetch_time'] = now; stream['last_seen'] = now
            merged = existing.copy() if existing else {}; merged.update(stream); self.streams[vid] = merged
            if existing: updated_count += 1
            else: added_count += 1

        if added_count > 0 or updated_count > 0 or ignored_initial_vod_count > 0 or ignored_category_count > 0:
            logger.info(f"Update Streams: Adicionados: {added_count}, Atualizados: {updated_count}, VODs Iniciais Ign: {ignored_initial_vod_count}, Categorias Ign: {ignored_category_count}")

        self.prune_ended_streams()

    def prune_ended_streams(self):
        # (Lógica sem alterações)
        now = datetime.now(timezone.utc); to_delete = set(); recorded_by_channel = defaultdict(list)
        recorded_cutoff = now - timedelta(days=RECORDED_RETENTION_DAYS)
        stale_cutoff = now - timedelta(hours=max(STALE_HOURS * 2, SCHEDULER_MAIN_INTERVAL_HOURS * 2))
        for vid, s in list(self.streams.items()):
            status = s.get('status'); last_seen = s.get('last_seen') or s.get('fetch_time')
            if isinstance(last_seen, str):
                try: last_seen = datetime.fromisoformat(last_seen.replace('Z', '+00:00'))
                except Exception: last_seen = now
            elif not isinstance(last_seen, datetime): last_seen = now
            channel_id = s.get('channel_id'); aet = s.get('actual_end_time_utc'); aet_dt = None
            if isinstance(aet, str):
                try: aet_dt = datetime.fromisoformat(aet.replace('Z', '+00:00'))
                except Exception: pass
            elif isinstance(aet, datetime): aet_dt = aet
            if aet_dt and aet_dt < recorded_cutoff: to_delete.add(vid); continue
            if status == 'none':
                if not KEEP_RECORDED_STREAMS: to_delete.add(vid); continue
                sort_time = aet_dt or last_seen
                if sort_time < recorded_cutoff: to_delete.add(vid); continue
                recorded_by_channel[channel_id].append((vid, sort_time))
                continue
            if last_seen < stale_cutoff: to_delete.add(vid)
        if KEEP_RECORDED_STREAMS:
            for cid, items in recorded_by_channel.items():
                if len(items) > MAX_RECORDED_PER_CHANNEL:
                    items_sorted = sorted(items, key=lambda x: x[1], reverse=True)
                    for vid_to_del, _ in items_sorted[MAX_RECORDED_PER_CHANNEL:]: to_delete.add(vid_to_del)
        if to_delete:
            logger.info(f"Removendo {len(to_delete)} streams antigas/excedentes/stale do estado.")
            for vid in to_delete: self.streams.pop(vid, None)
    def get_all_streams(self) -> List[Dict[str, Any]]: return list(self.streams.values())
    def get_all_channels(self) -> Dict[str, str]: return self.channels
    def save_to_disk(self):
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_data = {'channels': self.channels, 'streams': self.streams, 'meta': self._meta_serializable()}
            with open(self.cache_path, "w", encoding="utf-8") as f: json.dump(cache_data, f, indent=2, default=self._json_converter)
            logger.info(f"Estado principal salvo com sucesso em '{self.cache_path}'.")
        except Exception as e: logger.error(f"Não foi possível salvar o estado no cache: {e}")
    def load_from_disk(self) -> bool:
        if not self.cache_path.exists(): return False
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f: cache_data = json.load(f)
            self.channels = cache_data.get('channels', {})
            self.streams = cache_data.get('streams', {})
            meta = cache_data.get('meta', {})
            self._load_meta(meta)
            for stream in self.streams.values():
                for key, value in list(stream.items()):
                    if key in ('scheduled_start_time_utc', 'actual_start_time_utc', 'actual_end_time_utc', 'fetch_time', 'last_seen'):
                        if isinstance(value, str):
                            try: stream[key] = datetime.fromisoformat(value.replace('Z', '+00:00'))
                            except (ValueError, TypeError): stream[key] = None
                        elif not isinstance(value, datetime): stream[key] = None
            return True
        except (IOError, json.JSONDecodeError) as e: logger.error(f"Não foi possível carregar o estado do cache: {e}."); return False
    def _json_converter(self, o):
        if isinstance(o, datetime): return o.isoformat()
        return None
    def _meta_serializable(self):
        meta_copy = dict(self.meta)
        for k in ('last_main_run', 'last_full_sync'):
            if meta_copy.get(k) and isinstance(meta_copy[k], datetime): meta_copy[k] = meta_copy[k].isoformat()
            else: meta_copy.pop(k, None)
        rh = {}
        for h, v in self.meta.get('resolved_handles', {}).items():
            if isinstance(v, dict):
                rh[h] = dict(v)
                if 'resolved_at' in rh[h] and isinstance(rh[h]['resolved_at'], datetime): rh[h]['resolved_at'] = rh[h]['resolved_at'].isoformat()
        meta_copy['resolved_handles'] = rh
        return meta_copy
    def _load_meta(self, meta):
        if not isinstance(meta, dict): meta = {}
        self.meta = {}
        for k in ('last_main_run', 'last_full_sync'):
            value_str = meta.get(k)
            if isinstance(value_str, str):
                try: self.meta[k] = datetime.fromisoformat(value_str.replace('Z', '+00:00'))
                except Exception: self.meta[k] = None
            else: self.meta[k] = None
        rh = {}
        for h, v in meta.get('resolved_handles', {}).items():
             if isinstance(v, dict):
                 rh[h] = dict(v)
                 resolved_at_str = rh[h].get('resolved_at')
                 if isinstance(resolved_at_str, str):
                     try: rh[h]['resolved_at'] = datetime.fromisoformat(resolved_at_str.replace('Z', '+00:00'))
                     except Exception: rh[h]['resolved_at'] = None
                 else: rh[h]['resolved_at'] = None
        self.meta['resolved_handles'] = rh


class APIScraper:
    # (Com logs de depuração para playlist)
    def __init__(self, api_key: str):
        self.youtube = build("youtube", "v3", developerKey=api_key)
        self.uploads_cache: Dict[str, str] = {}
    def resolve_channel_handles_to_ids(self, handles: List[str], state: StateManager) -> Dict[str, str]:
        resolved_this_run = {}; logger.info(f"Resolvendo {len(handles)} handles de canais para IDs... (usando cache quando possível)")
        now = datetime.now(timezone.utc)
        for handle in handles:
            rh = state.meta.get('resolved_handles', {}).get(handle); need_resolve = True
            if rh and rh.get('channelId'):
                resolved_at = rh.get('resolved_at')
                if isinstance(resolved_at, datetime) and (now - resolved_at) < timedelta(hours=RESOLVE_HANDLES_TTL_HOURS):
                    cid = rh['channelId']; title = rh.get('channelTitle')
                    if cid and title is not None:
                         if state.channels.get(cid) != title: state.channels[cid] = title
                         resolved_this_run[cid] = title; need_resolve = False
            if not need_resolve: continue
            try:
                req = self.youtube.search().list(part="id,snippet", q=handle, type="channel", maxResults=1); res = req.execute()
                if items := res.get("items", []):
                    channel_id = items[0]["id"]["channelId"]; channel_title = items[0]["snippet"].get("channelTitle")
                    if channel_id and channel_title is not None:
                        resolved_this_run[channel_id] = channel_title
                        state.meta.setdefault('resolved_handles', {})[handle] = {"channelId": channel_id, "channelTitle": channel_title, "resolved_at": now}
                        state.channels[channel_id] = channel_title
                        logger.info(f"Handle '{handle}' -> {channel_id} ({channel_title})")
                    else: logger.warning(f"API retornou dados incompletos para handle '{handle}'")
                else: logger.warning(f"Nenhum canal encontrado para handle '{handle}'")
            except HttpError as e: logger.error(f"Erro de API ao resolver '{handle}': {e}.")
        logger.info(f"Resolvido/cacheado via handle {len(resolved_this_run)} canais nesta execução.")
        return resolved_this_run
    def ensure_channel_titles(self, target_channel_ids: Set[str], state: StateManager) -> Dict[str, str]:
        ids_without_title = {cid for cid in target_channel_ids if cid not in state.channels or not state.channels[cid]}
        if not ids_without_title:
             logger.debug("Todos os IDs de canal alvo já possuem títulos no estado.")
             return {cid: state.channels.get(cid, "Título não encontrado") for cid in target_channel_ids if cid in state.channels}
        logger.info(f"Buscando títulos para {len(ids_without_title)} IDs de canal faltantes...")
        fetched_titles = {}; ids_list = list(ids_without_title)
        for i in range(0, len(ids_list), 50):
            batch_ids = ids_list[i:i+50]
            try:
                req = self.youtube.channels().list(part="snippet", id=",".join(batch_ids)); res = req.execute()
                for item in res.get("items", []):
                    cid = item.get("id"); title = item.get("snippet", {}).get("title")
                    if cid and title: fetched_titles[cid] = title; state.channels[cid] = title; logger.info(f"Título encontrado para ID {cid}: {title}")
            except HttpError as e: logger.error(f"Erro ao buscar títulos para o lote de IDs: {e}")
        still_missing = ids_without_title - set(fetched_titles.keys())
        if still_missing: logger.warning(f"Não foi possível obter títulos para {len(still_missing)} IDs: {still_missing}")
        final_channels_dict = {}
        for cid in target_channel_ids:
             title = state.channels.get(cid)
             if title: final_channels_dict[cid] = title
             else: logger.warning(f"ID de canal alvo {cid} sem título associado após busca.")
        return final_channels_dict
    def fetch_streams_by_ids(self, video_ids: List[str], channels_dict: Dict[str, str]) -> List[Dict[str, Any]]:
        if not video_ids: return []
        data = []; logger.info(f"Buscando detalhes para {len(video_ids)} video(s) específicos... (em batches)")
        for i in range(0, len(video_ids), 50):
            try:
                batch = video_ids[i:i+50]
                req = self.youtube.videos().list(part="snippet,liveStreamingDetails,contentDetails", id=",".join(batch)); res = req.execute()
                for item in res.get("items", []): data.append(self._format_stream_data(item, channels_dict))
            except HttpError as e: logger.error(f"Falha ao buscar detalhes do lote {i//50 + 1}: {e}")
        logger.info(f"Recebidos detalhes de {len(data)} video(s).")
        return data
    def fetch_all_streams_for_channels(self, channels_dict: Dict[str, str], published_after: Optional[str] = None) -> List[Dict[str, Any]]:
        ids: Set[str] = set(); logger.info(f"Buscando streams [search.list] para {len(channels_dict)} canais (publishedAfter={published_after})...")
        for cid in channels_dict.keys():
            page_token = None; page_count = 0
            while True:
                page_count += 1
                try:
                    kwargs = {"part": "id", "channelId": cid, "type": "video", "maxResults": 50}
                    if page_token: kwargs['pageToken'] = page_token
                    if published_after: kwargs['publishedAfter'] = published_after
                    req = self.youtube.search().list(**kwargs); res = req.execute()
                    items = res.get('items', [])
                    if items: ids.update(item['id']['videoId'] for item in items if item.get('id', {}).get('videoId'))
                    page_token = res.get('nextPageToken')
                    if not page_token: break
                    if page_count > 20: logger.warning(f"Atingido limite páginas search.list canal {cid}."); break
                except HttpError as e: logger.error(f"Erro API [search.list] canal {cid} (pág {page_count}): {e}"); break
        logger.info(f"Busca [search.list] encontrou {len(ids)} IDs únicos. Buscando detalhes...")
        return self.fetch_streams_by_ids(list(ids), channels_dict)
    def fetch_all_streams_for_channels_using_playlists(self, channels_dict: Dict[str, str], published_after: Optional[str] = None) -> List[Dict[str, Any]]:
        ids: Set[str] = set(); logger.info(f"Buscando streams [playlistItems] para {len(channels_dict)} canais (publishedAfter={published_after})...")
        published_after_dt = None
        if published_after:
            try: published_after_dt = datetime.fromisoformat(published_after.replace('Z', '+00:00')); logger.debug(f"Fetch using playlists: Filtro published_after_dt={published_after_dt}")
            except Exception as e: logger.error(f"Erro ao parsear published_after '{published_after}': {e}"); published_after_dt = None
        for cid in channels_dict.keys():
            playlist_id = self.uploads_cache.get(cid)
            if not playlist_id:
                try:
                    ch_req = self.youtube.channels().list(part='contentDetails', id=cid, maxResults=1); ch_res = ch_req.execute()
                    items = ch_res.get('items', []);
                    if items: playlist_id = items[0]['contentDetails']['relatedPlaylists'].get('uploads')
                    if playlist_id: self.uploads_cache[cid] = playlist_id
                    else: logger.warning(f"Canal {cid} sem playlist 'uploads'."); continue
                except HttpError as e: logger.error(f"Erro obter uploads playlist {cid}: {e}"); continue
            page_token = None; page_count = 0; stopped_early = False
            while True:
                page_count += 1
                try:
                    kwargs = {'part': 'snippet', 'playlistId': playlist_id, 'maxResults': 50}
                    if page_token: kwargs['pageToken'] = page_token
                    res = self.youtube.playlistItems().list(**kwargs).execute(); items = res.get('items', [])
                    stop_pagination = False
                    for it in items:
                        snip = it.get('snippet', {}); resource = snip.get('resourceId', {}); vid = resource.get('videoId'); publishedAt = snip.get('publishedAt')
                        if published_after_dt and publishedAt:
                            try:
                                pa_dt = datetime.fromisoformat(publishedAt.replace('Z', '+00:00'))
                                if pa_dt <= published_after_dt:
                                    stop_pagination = True; stopped_early = True
                                    logger.debug(f"Playlist {playlist_id} (Canal {cid}): Stop pagination at video {vid} (published: {pa_dt})")
                                    break
                            except Exception as e: logger.warning(f"Erro ao parsear publishedAt '{publishedAt}' para video {vid}: {e}"); pass
                        if vid: ids.add(vid)
                    if stop_pagination: break
                    page_token = res.get('nextPageToken')
                    if not page_token: break
                    if page_count > 40: logger.warning(f"Atingido limite páginas playlistItems {playlist_id}."); break
                except HttpError as e: logger.error(f"Erro [playlistItems] playlist {playlist_id} (pág {page_count}): {e}"); break
            logger.debug(f"Playlist {playlist_id} (Canal {cid}): Paginação {'interrompida' if stopped_early else 'completa'} ({page_count} pág).")
        logger.info(f"Busca [playlistItems] encontrou {len(ids)} IDs únicos. Buscando detalhes...")
        return self.fetch_streams_by_ids(list(ids), channels_dict)
    def _format_stream_data(self, item: Dict, channels_dict: Dict[str, str]) -> Dict:
        snippet = item.get("snippet", {}); vid = item.get("id"); cid = snippet.get("channelId")
        if isinstance(vid, dict): vid = vid.get('videoId') or vid.get('id')
        thumbs = snippet.get("thumbnails", {})
        thumb_url = thumbs.get("maxres", {}).get("url") or thumbs.get("standard", {}).get("url") or thumbs.get("high", {}).get("url") or ""
        live = item.get("liveStreamingDetails", {}); content = item.get("contentDetails", {})
        def parse_time(time_str):
             if not time_str: return None
             try: return datetime.fromisoformat(time_str.replace('Z', '+00:00'))
             except (ValueError, TypeError): return None
        return {
            "video_id": vid, "channel_id": cid, "channel_name": channels_dict.get(cid, snippet.get("channelTitle", "Desconhecido")),
            "title_original": snippet.get("title"), "description": snippet.get("description"), "tags": snippet.get("tags", []),
            "category_original": snippet.get("categoryId"), # *** ID da Categoria é pego aqui ***
            "watch_url": f"https://www.youtube.com/watch?v={vid}", "thumbnail_url": thumb_url,
            "status": snippet.get("liveBroadcastContent", "none"),
            "scheduled_start_time_utc": parse_time(live.get("scheduledStartTime")),
            "actual_start_time_utc": parse_time(live.get("actualStartTime")),
            "actual_end_time_utc": parse_time(live.get("actualEndTime")),
            "duration_iso": content.get("duration"), "content_rating": content.get("contentRating", {})
        }


class ContentGenerator:
    # *** MODIFICADO: _get_display_title usa Mapeamento e Inverte Ordem ***
    def _is_live(self, stream: Dict[str, Any]) -> bool:
        start_time = stream.get('actual_start_time_utc'); is_live_status = stream.get('status') == 'live'
        has_started = isinstance(start_time, datetime); has_not_ended = not stream.get('actual_end_time_utc')
        return is_live_status and has_started and has_not_ended
    @staticmethod
    def _get_sortable_time(stream: Dict[str, Any]):
        time_val = stream.get('actual_start_time_utc') or stream.get('scheduled_start_time_utc')
        if isinstance(time_val, datetime): return time_val
        return datetime.max.replace(tzinfo=timezone.utc)
    def _filter_streams(self, streams: List[Dict[str, Any]], mode: str) -> List[Dict[str, Any]]:
        # (Lógica sem alterações)
        now_utc = datetime.now(timezone.utc)
        upcoming_count = defaultdict(int); recorded_count = defaultdict(int); filtered = []
        sort_key_func = ContentGenerator._get_sortable_time; reverse_sort = False
        if mode == 'vod':
            sort_key_func = lambda s: s.get('last_seen') or s.get('actual_end_time_utc') or datetime.min.replace(tzinfo=timezone.utc)
            reverse_sort = True
        try:
            sorted_streams = sorted(streams, key=lambda s: (not self._is_live(s), sort_key_func(s)), reverse=reverse_sort)
        except TypeError as e:
            logger.error(f"Erro de tipo durante a ordenação dos streams ({mode}): {e}. Verifique tempos."); sorted_streams = streams
        for s in sorted_streams:
            if mode == 'live' and self._is_live(s): filtered.append(s)
            elif mode == 'upcoming' and s.get('status') == 'upcoming' and s.get('scheduled_start_time_utc'):
                stime = ContentGenerator._get_sortable_time(s)
                if isinstance(stime, datetime) and stime > now_utc and (stime - now_utc) <= timedelta(hours=MAX_SCHEDULE_HOURS):
                     channel_id = s.get('channel_id', 'unknown')
                     if upcoming_count[channel_id] < MAX_UPCOMING_PER_CHANNEL:
                         upcoming_count[channel_id] += 1; filtered.append(s)
            elif mode == 'vod' and s.get('status') == 'none' and KEEP_RECORDED_STREAMS:
                 if not s.get('video_id', '').startswith('PLACEHOLDER_'):
                    channel_id = s.get('channel_id', 'unknown')
                    if recorded_count[channel_id] < MAX_RECORDED_PER_CHANNEL:
                        recorded_count[channel_id] += 1; filtered.append(s)
        if mode == 'upcoming': filtered.sort(key=ContentGenerator._get_sortable_time)
        return filtered

    def _get_display_title(self, stream: Dict) -> str:
        title = stream.get('title_original', ''); status_prefix = ""; channel_prefix = ""
        # Limpa título
        if isinstance(title, str):
             title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
             title = title.replace(',', '').replace('"', '').replace('-', '|')
             for exp in TITLE_FILTER_EXPRESSIONS: title = re.sub(exp, "", title, flags=re.IGNORECASE).strip()
             title = re.sub(r'\s+', ' ', title).strip(); title = re.sub(r'[| ]*$', '', title).strip()
        else: title = "Titulo Desconhecido"

        # Prefixo de Status
        if PREFIX_TITLE_WITH_STATUS:
            status = stream.get('status')
            if self._is_live(stream): status_prefix = "[Ao Vivo] "
            elif status == 'upcoming': status_prefix = "[Agendado] "
            elif status == 'none' and KEEP_RECORDED_STREAMS: status_prefix = "[Gravado] "

        # Prefixo de Canal (com mapeamento)
        if PREFIX_TITLE_WITH_CHANNEL_NAME:
            channel_name_raw = stream.get('channel_name', 'Canal Desc.')
            # *** NOVO: Aplica mapeamento de nome ***
            channel_name_display = CHANNEL_NAME_MAPPINGS.get(channel_name_raw, channel_name_raw)
            channel_prefix = f"[{channel_name_display}] "

        # *** NOVO: Ordem Invertida ***
        full_title = f"{status_prefix}{channel_prefix}{title}".strip()
        return re.sub(r'\s+', ' ', full_title)

    def _get_display_category(self, cat_id: Optional[str], db: Dict) -> str:
        orig = db.get(cat_id, "Geral") if cat_id else "Geral"; return CATEGORY_MAPPINGS.get(orig, orig)


class M3UGenerator(ContentGenerator):
    # (Com placeholder invisível e prefixo de status no placeholder)
    def generate_playlist(self, streams: List, db: Dict, mode: str) -> str:
        logger.info(f"Gerando playlist M3U modo '{mode.upper()}'. Avaliando {len(streams)} streams...")
        lines = ["#EXTM3U", f"# Atualizado: {datetime.now(local_tz).strftime('%Y-%m-%d %H:%M:%S %Z')} - MODO: {mode.upper()}"]
        filtered_streams = self._filter_streams(streams, mode)
        if not filtered_streams and PLACEHOLDER_IMAGE_URL:
            placeholder_id, base_placeholder_title = "", ""; placeholder_prefix = ""
            if mode == 'live': placeholder_id, base_placeholder_title = PLACEHOLDER_LIVE_ID, PLACEHOLDER_LIVE_TITLE; placeholder_prefix = "[Ao Vivo] " if PREFIX_TITLE_WITH_STATUS else ""
            elif mode == 'upcoming': placeholder_id, base_placeholder_title = PLACEHOLDER_UPCOMING_ID, PLACEHOLDER_UPCOMING_TITLE; placeholder_prefix = "[Agendado] " if PREFIX_TITLE_WITH_STATUS else ""
            elif mode == 'vod' and KEEP_RECORDED_STREAMS: placeholder_id, base_placeholder_title = PLACEHOLDER_VOD_ID, PLACEHOLDER_VOD_TITLE; placeholder_prefix = "[Gravado] " if PREFIX_TITLE_WITH_STATUS else ""
            if placeholder_id:
                logger.info(f"Playlist M3U '{mode.upper()}' vazia. Placeholder ({'invisível' if USE_INVISIBLE_PLACEHOLDER else 'visível'}).")
                final_placeholder_title = placeholder_prefix + base_placeholder_title
                placeholder_stream = {'video_id': placeholder_id, 'title_original': final_placeholder_title, 'channel_name': '', 'status': 'none', 'thumbnail_url': PLACEHOLDER_IMAGE_URL, 'category_original': None, 'watch_url': None}
                filtered_streams.append(placeholder_stream)
        for s in filtered_streams:
            is_placeholder = s.get('video_id', '').startswith('PLACEHOLDER_')
            title = s['title_original'] if is_placeholder else self._get_display_title(s)
            cat = PLACEHOLDER_CATEGORY if is_placeholder else self._get_display_category(s.get('category_original'), db)
            video_id_raw = s.get('video_id', '')
            tvg_id = re.sub(r'[^a-zA-Z0-9]', '', video_id_raw)
            logo_url = PLACEHOLDER_IMAGE_URL if is_placeholder else s.get("thumbnail_url", "")
            stream_url = ""
            if is_placeholder: stream_url = PLACEHOLDER_IMAGE_URL
            elif mode == 'live': stream_url = s.get('watch_url', '')
            elif mode == 'vod': stream_url = s.get('watch_url', '')
            else: stream_url = s.get("thumbnail_url", "")
            extinf_title = title.replace('\n', ' ').replace('\r', ' ')
            lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{extinf_title}" tvg-logo="{logo_url}" group-title="{cat}",{extinf_title}')
            if stream_url:
                if is_placeholder and USE_INVISIBLE_PLACEHOLDER: lines.append(f"#{stream_url}")
                else: lines.append(stream_url)
            else:
                 if not is_placeholder: logger.warning(f"Stream {tvg_id} ('{title}') sem URL na playlist {mode.upper()}.")
        logger.info(f"Playlist M3U '{mode.upper()}' finalizada com {len(filtered_streams)} items.")
        return "\n".join(lines)


class XMLTVGenerator(ContentGenerator):
    # (Com correção de escaping de categoria)
    CONTROL_CHAR_REMOVER = dict.fromkeys(i for i in range(sys.maxunicode + 1) if unicodedata.category(chr(i)).startswith('C') and chr(i) not in ('\t', '\n', '\r'))
    def _clean_text_for_xml(self, text: Optional[str]) -> str:
        if not isinstance(text, str) or not text.strip(): # Ignora None, não-strings e strings vazias/só com espaços
            return ""
        try:
            normalized_text = unicodedata.normalize('NFKD', text)
            ascii_text = normalized_text.encode('ascii', 'ignore').decode('ascii')
            cleaned_text = ascii_text.translate(self.CONTROL_CHAR_REMOVER)
            cleaned_text = cleaned_text.replace('\n', ' ').replace('\r', ' ')
            cleaned_text = ' '.join(cleaned_text.split())
            return escape(cleaned_text)
        except Exception as e:
            logger.warning(f"Erro ao limpar texto XML: {e}. Texto: '{text[:50]}...'")
            # Fallback seguro em caso de erro inesperado
            safe_text = re.sub(r'[^\x20-\x7E]', '', text) # Remove TUDO exceto ASCII imprimível básico
            safe_text = ' '.join(safe_text.split())
            return escape(safe_text[:200]) # Limita tamanho em caso extremo
    def _parse_iso8601_duration(self, duration_str: Optional[str]) -> Optional[timedelta]:
        if not duration_str or duration_str == 'P0D': return None
        match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
        if not match: return None
        try:
             hours, minutes, seconds = [int(val) if val else 0 for val in match.groups()]; max_seconds_timedelta = 100 * 365 * 24 * 3600; total_seconds = hours * 3600 + minutes * 60 + seconds
             if total_seconds > max_seconds_timedelta: logger.warning(f"Duração ISO '{duration_str}' excede limite. Ignorando."); return None
             return timedelta(hours=hours, minutes=minutes, seconds=seconds)
        except ValueError: return None
    def generate_xml(self, channels: Dict, streams: List, db: Dict) -> str:
        logger.info(f"Gerando EPG XMLTV. Avaliando {len(streams)} streams...")
        lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<tv>']; now_utc = datetime.now(timezone.utc); datetime_max_utc = datetime.max.replace(tzinfo=timezone.utc)
        live_streams = self._filter_streams(streams, 'live'); upcoming_streams = self._filter_streams(streams, 'upcoming'); recorded_streams = self._filter_streams(streams, 'vod') if KEEP_RECORDED_STREAMS else []
        placeholder_streams = []
        if PLACEHOLDER_IMAGE_URL:
            def create_placeholder(ph_id, base_title, status_prefix_text):
                final_title = base_title;
                if PREFIX_TITLE_WITH_STATUS: final_title = status_prefix_text + base_title
                cleaned_escaped_title = self._clean_text_for_xml(final_title)
                return { 'video_id': ph_id, 'title_original': cleaned_escaped_title, 'status': 'none', 'category_original': None, 'thumbnail_url': PLACEHOLDER_IMAGE_URL, 'scheduled_start_time_utc': now_utc, '_end_time_override': now_utc + timedelta(hours=1) }
            if not live_streams: placeholder_streams.append(create_placeholder(PLACEHOLDER_LIVE_ID, PLACEHOLDER_LIVE_TITLE, "[Ao Vivo] "))
            if not upcoming_streams: placeholder_streams.append(create_placeholder(PLACEHOLDER_UPCOMING_ID, PLACEHOLDER_UPCOMING_TITLE, "[Agendado] "))
            if KEEP_RECORDED_STREAMS and not recorded_streams: placeholder_streams.append(create_placeholder(PLACEHOLDER_VOD_ID, PLACEHOLDER_VOD_TITLE, "[Gravado] "))
        all_streams_for_epg = live_streams + upcoming_streams + recorded_streams + placeholder_streams
        all_streams_for_epg.sort(key=ContentGenerator._get_sortable_time); processed_channel_ids = set()
        for s in all_streams_for_epg:
            video_id_raw = s.get('video_id', '')
            video_id = re.sub(r'[^a-zA-Z0-9]', '', video_id_raw)
            if video_id and video_id not in processed_channel_ids:
                is_placeholder = video_id_raw.startswith('PLACEHOLDER_'); channel_title_raw = s['title_original'] if is_placeholder else self._get_display_title(s)
                # Placeholders já vêm limpos/escapados
                channel_title_cleaned_escaped = s['title_original'] if is_placeholder else self._clean_text_for_xml(channel_title_raw)
                lines.append(f'  <channel id="{video_id}"><display-name>{channel_title_cleaned_escaped}</display-name></channel>')
                processed_channel_ids.add(video_id)
        for s in all_streams_for_epg:
            video_id_raw = s.get('video_id', '');
            video_id = re.sub(r'[^a-zA-Z0-9]', '', video_id_raw)
            if not video_id: continue
            is_placeholder = video_id_raw.startswith('PLACEHOLDER_'); start_time_obj = ContentGenerator._get_sortable_time(s); end_time_obj = s.get('_end_time_override')
            if not end_time_obj and not is_placeholder and isinstance(start_time_obj, datetime) and start_time_obj != datetime_max_utc:
                aet = s.get('actual_end_time_utc');
                if isinstance(aet, datetime): end_time_obj = aet
                if not end_time_obj:
                    duration_delta = self._parse_iso8601_duration(s.get('duration_iso'))
                    if duration_delta:
                        try:
                            potential_end_time = start_time_obj + duration_delta
                            if datetime.min.replace(tzinfo=timezone.utc) <= potential_end_time <= datetime_max_utc: end_time_obj = potential_end_time
                            else: logger.warning(f"Tempo final ({potential_end_time}) fora do range {video_id}. Fallback."); end_time_obj = None
                        except OverflowError: logger.warning(f"OverflowError tempo final {video_id}. Fallback."); end_time_obj = None
            if not end_time_obj and isinstance(start_time_obj, datetime) and start_time_obj != datetime_max_utc: end_time_obj = start_time_obj + timedelta(hours=2)
            if not isinstance(start_time_obj, datetime) or not isinstance(end_time_obj, datetime): continue
            if start_time_obj == datetime_max_utc: continue
            if start_time_obj >= end_time_obj - timedelta(minutes=1): continue
            start_str = start_time_obj.strftime('%Y%m%d%H%M%S %z').replace('+0000', '+0000'); end_str = end_time_obj.strftime('%Y%m%d%H%M%S %z').replace('+0000', '+0000')
            main_title_raw = s['title_original'] if is_placeholder else self._get_display_title(s); sub_title_raw = None
            if not is_placeholder:
                title_check_raw = self._get_display_title(s); title_check_cleaned = self._clean_text_for_xml(title_check_raw)
                if PREFIX_TITLE_WITH_CHANNEL_NAME: title_check_cleaned = re.sub(r'\[.*?\]\s*', '', title_check_cleaned, count=1)
                if PREFIX_TITLE_WITH_STATUS: title_check_cleaned = re.sub(r'\[.*?\]\s*', '', title_check_cleaned, count=1)
                if ':' in title_check_cleaned:
                     parts = title_check_cleaned.split(':', 1); original_parts = main_title_raw.split(':', 1)
                     if len(original_parts) > 1: main_title_raw = original_parts[0].strip(); sub_title_raw = original_parts[1].strip()
                     else: sub_title_raw = None
            main_title_cleaned_escaped = s['title_original'] if is_placeholder else self._clean_text_for_xml(main_title_raw)
            sub_title_cleaned_escaped = self._clean_text_for_xml(sub_title_raw) if sub_title_raw else None
            category_raw = PLACEHOLDER_CATEGORY if is_placeholder else self._get_display_category(s.get('category_original'), db)
            category_cleaned_escaped = self._clean_text_for_xml(category_raw) # Limpa/Escapa categoria
            description_raw = main_title_raw if is_placeholder else s.get('description', 'Sem descrição disponível.')
            if not is_placeholder and isinstance(description_raw, str):
                 if EPG_DESCRIPTION_CLEANUP: paragraphs = re.split(r'\n\s*\n', description_raw); description_raw = next((p.strip() for p in paragraphs if p.strip()), "Sem descrição.")
                 else:
                     tags = s.get('tags');
                     if tags and isinstance(tags, list): description_raw += f"\n\nTags: {', '.join(tags)}"
            elif not isinstance(description_raw, str):
                description_raw = "Descrição indisponível."
            if not description_raw.strip() or description_raw in ("Sem descrição disponível.", "Sem descrição.", "Descrição indisponível."):
                if not is_placeholder:
                    logger.debug(f"Descrição vazia/padrão para {video_id}. Usando título como fallback.")
                    description_raw = main_title_raw if main_title_raw else "Título indisponível"
                else:
                    description_raw = main_title_raw
            description_cleaned_escaped = self._clean_text_for_xml(description_raw)
            icon_url = PLACEHOLDER_IMAGE_URL if is_placeholder else s.get("thumbnail_url", ""); rating = "Livre"
            if not is_placeholder and s.get('content_rating', {}).get('ytRating') == 'ytAgeRestricted': rating = "18+"
            programme_lines = [f'  <programme start="{start_str}" stop="{end_str}" channel="{video_id}">', f'    <title lang="pt">{main_title_cleaned_escaped}</title>']
            if sub_title_cleaned_escaped: programme_lines.append(f'    <sub-title lang="pt">{sub_title_cleaned_escaped}</sub-title>')
            programme_lines.extend([f'    <desc lang="pt">{description_cleaned_escaped}</desc>', f'    <icon src="{icon_url}"/>' if icon_url else '', f'    <category lang="pt">{category_cleaned_escaped}</category>', f'    <rating system="BR"><value>{rating}</value></rating>', '  </programme>'])
            lines.extend(line for line in programme_lines if line)
        lines.append('</tv>')
        logger.info(f"EPG XMLTV finalizado {len(all_streams_for_epg)} programas (reais+placeholders).")
        return "\n".join(lines)

class WebServer:
    # (Sem alterações)
    def __init__(self, state_manager: StateManager):
        self.app = Flask(__name__); self.app.logger.disabled = True; log = logging.getLogger('werkzeug'); log.setLevel(logging.ERROR); log.disabled = True
        self.state_manager = state_manager; self.m3u_gen, self.xmltv_gen = M3UGenerator(), XMLTVGenerator(); self.categories_db: Dict = {}
        self._setup_routes()
    def set_categories_db(self, categories: Dict): self.categories_db = categories
    def _setup_routes(self):
        @self.app.before_request
        def log_request_info():
            if request.path != '/favicon.ico': logger.info(f"Req: {request.method} {request.path} de {request.remote_addr}")
        @self.app.route(f"/{PLAYLIST_LIVE_FILENAME}")
        def serve_live_playlist(): content = self.m3u_gen.generate_playlist(self.state_manager.get_all_streams(), self.categories_db, 'live'); return Response(content, mimetype="application/vnd.apple.mpegurl")
        @self.app.route(f"/{PLAYLIST_UPCOMING_FILENAME}")
        def serve_upcoming_playlist(): content = self.m3u_gen.generate_playlist(self.state_manager.get_all_streams(), self.categories_db, 'upcoming'); return Response(content, mimetype="application/vnd.apple.mpegurl")
        @self.app.route(f"/{PLAYLIST_VOD_FILENAME}")
        def serve_vod_playlist(): content = self.m3u_gen.generate_playlist(self.state_manager.get_all_streams(), self.categories_db, 'vod'); return Response(content, mimetype="application/vnd.apple.mpegurl")
        @self.app.route(f"/{XMLTV_FILENAME}")
        def serve_epg(): content = self.xmltv_gen.generate_xml(self.state_manager.get_all_channels(), self.state_manager.get_all_streams(), self.categories_db); return Response(content, mimetype="application/xml")
    def run_in_thread(self, host: str, port: int):
        thread = threading.Thread(target=self.app.run, kwargs={"host": host, "port": port, "debug": False, "use_reloader": False}); thread.daemon = True; thread.start()
        logger.info(f"Servidor HTTP (Flask) rodando em http://{host}:{port}")

class Scheduler:
    # (Sem alterações)
    def __init__(self, api_scraper: APIScraper, state_manager: StateManager):
        self.api_scraper = api_scraper; self.state_manager = state_manager
        datetime_min_utc = datetime.min.replace(tzinfo=timezone.utc)
        loaded_lfs = state_manager.meta.get('last_full_sync')
        self.last_full_sync = loaded_lfs if isinstance(loaded_lfs, datetime) else datetime_min_utc
        if self.last_full_sync == datetime_min_utc and loaded_lfs is not None: logger.warning(f"[Scheduler Init] last_full_sync ('{loaded_lfs}') inválido. Resetado.")
        loaded_lmr = state_manager.meta.get('last_main_run')
        self.last_main_run = loaded_lmr if isinstance(loaded_lmr, datetime) else datetime_min_utc
        if self.last_main_run == datetime_min_utc and loaded_lmr is not None: logger.warning(f"[Scheduler Init] last_main_run ('{loaded_lmr}') inválido. Resetado.")
        self.last_pre_event_run = datetime_min_utc; self.last_post_event_run = datetime_min_utc
        logger.debug(f"[Scheduler Init] last_main_run={self.last_main_run}, last_full_sync={self.last_full_sync}")
    def _log_current_state(self, origin_message: str):
        all_streams = self.state_manager.get_all_streams()
        live_count = len([s for s in all_streams if ContentGenerator._is_live(None, s)])
        upcoming_count = len([s for s in all_streams if s.get('status') == 'upcoming'])
        none_count = len(all_streams) - live_count - upcoming_count
        logger.info(f"-> Status Pós-{origin_message}: {len(all_streams)} streams | {live_count} Live | {upcoming_count} Upcoming | {none_count} VOD/Ended")
    async def run(self, initial_run_delay: bool):
        if initial_run_delay: logger.info("[Scheduler] Aplicando delay inicial."); self.last_main_run = datetime.now(timezone.utc)
        while True:
            now_utc = datetime.now(timezone.utc); datetime_min_utc = datetime.min.replace(tzinfo=timezone.utc)
            time_for_main_run = (now_utc - self.last_main_run) >= timedelta(hours=SCHEDULER_MAIN_INTERVAL_HOURS)
            is_active_time = True
            if ENABLE_SCHEDULER_ACTIVE_HOURS:
                local_hour = datetime.now(local_tz).hour
                if not (SCHEDULER_ACTIVE_START_HOUR <= local_hour < SCHEDULER_ACTIVE_END_HOUR): is_active_time = False
            time_for_full_sync = (now_utc - self.last_full_sync) >= timedelta(hours=FULL_SYNC_INTERVAL_HOURS)
            if time_for_main_run:
                if is_active_time:
                    all_target_channels = self.state_manager.get_all_channels()
                    logger.info(f"--- [Scheduler] Iniciando verificação principal (Intervalo: {SCHEDULER_MAIN_INTERVAL_HOURS}h | Canais: {len(all_target_channels)} | Full Sync?: {time_for_full_sync}) ---")
                    published_after = None
                    if not time_for_full_sync and self.last_main_run != datetime_min_utc:
                        published_after = self.last_main_run.isoformat(); logger.info(f"[Scheduler] Busca incremental (publishedAfter={published_after})")
                    else:
                        reason = "time_for_full_sync=True" if time_for_full_sync else "last_main_run=min"; logger.info(f"[Scheduler] Full Sync (publishedAfter=None). Reason: {reason}")
                    if all_target_channels:
                        try:
                            fetch_method = self.api_scraper.fetch_all_streams_for_channels_using_playlists if USE_PLAYLIST_ITEMS else self.api_scraper.fetch_all_streams_for_channels
                            new_streams_data = fetch_method(all_target_channels, published_after=published_after)
                            self.state_manager.update_streams(new_streams_data)
                        except Exception as e: logger.error(f"Erro busca principal: {e}", exc_info=True)
                    else: logger.warning("[Scheduler] Nenhum canal alvo para buscar streams.")
                    self.last_main_run = now_utc; self.state_manager.meta['last_main_run'] = self.last_main_run
                    if published_after is None: self.last_full_sync = now_utc; self.state_manager.meta['last_full_sync'] = self.last_full_sync
                    self._log_current_state("Verificação Principal")
                else: logger.info(f"--- [Scheduler] Verificação principal pulada (fora do horário ativo {SCHEDULER_ACTIVE_START_HOUR}-{SCHEDULER_ACTIVE_END_HOUR} {local_tz}). ---")
            else:
                 next_run_time = self.last_main_run + timedelta(hours=SCHEDULER_MAIN_INTERVAL_HOURS)
                 logger.debug(f"--- [Scheduler] Próxima verificação principal ~{next_run_time.astimezone(local_tz).strftime('%H:%M:%S %Z')}")
            ids_to_check: Set[str] = set(); streams_in_memory = self.state_manager.get_all_streams(); pre_event_cutoff = now_utc + timedelta(hours=SCHEDULER_PRE_EVENT_WINDOW_HOURS)
            if (now_utc - self.last_pre_event_run) > timedelta(minutes=SCHEDULER_PRE_EVENT_INTERVAL_MINUTES):
                pre_event_ids = {s['video_id'] for s in streams_in_memory if s.get('status') == 'upcoming' and isinstance(s.get('scheduled_start_time_utc'), datetime) and s['scheduled_start_time_utc'] < pre_event_cutoff and s['scheduled_start_time_utc'] > now_utc}
                if pre_event_ids: logger.info(f"--- [Scheduler] {len(pre_event_ids)} na janela PRÉ-EVENTO ---"); ids_to_check.update(pre_event_ids)
                self.last_pre_event_run = now_utc
            if (now_utc - self.last_post_event_run) > timedelta(minutes=SCHEDULER_POST_EVENT_INTERVAL_MINUTES):
                post_event_ids = {s['video_id'] for s in streams_in_memory if ContentGenerator._is_live(None, s)}
                if post_event_ids: logger.info(f"--- [Scheduler] Verificando {len(post_event_ids)} 'live' (PÓS-EVENTO) ---"); ids_to_check.update(post_event_ids)
                self.last_post_event_run = now_utc
            stale_cutoff = now_utc - timedelta(hours=STALE_HOURS)
            stale_ids = {s['video_id'] for s in streams_in_memory if s.get('status') in ('live', 'upcoming') and isinstance(s.get('fetch_time'), datetime) and s['fetch_time'] < stale_cutoff}
            if stale_ids: logger.debug(f"--- [Scheduler] {len(stale_ids)} streams 'stale' precisam de verificação."); ids_to_check.update(stale_ids)
            if ids_to_check:
                logger.info(f"--- [Scheduler] Verificação alta freq. para {len(ids_to_check)} evento(s) ---")
                try:
                    requested_ids_list = list(ids_to_check); current_channels_dict = self.state_manager.get_all_channels()
                    updated_streams_data = self.api_scraper.fetch_streams_by_ids(requested_ids_list, current_channels_dict)
                    if updated_streams_data: self.state_manager.update_streams(updated_streams_data)
                    returned_ids = {s['video_id'] for s in updated_streams_data if 'video_id' in s}; missing_ids = ids_to_check - returned_ids
                    ids_to_mark_missing = {mid for mid in missing_ids if self.state_manager.streams.get(mid, {}).get('status') in ('live', 'upcoming')}
                    if ids_to_mark_missing:
                        logger.warning(f"{len(ids_to_mark_missing)} IDs ativos não retornados API: {ids_to_mark_missing}. Marcando 'none'.")
                        missing_data = [{'video_id': vid, 'status': 'none'} for vid in ids_to_mark_missing]
                        self.state_manager.update_streams(missing_data)
                except Exception as e: logger.error(f"Erro verificação alta freq.: {e}", exc_info=True)
                self._log_current_state("Verificação Alta Frequência")
            elif (now_utc - self.last_pre_event_run).total_seconds() < 70 and (now_utc - self.last_post_event_run).total_seconds() < 70: logger.debug(f"--- [Scheduler] Nenhuma janela ativa. Aguardando...")
            await asyncio.sleep(60)

def save_files(state_manager: StateManager, categories_db: Dict):
    # (Sem alterações)
    logger.info("Iniciando rotina de salvamento de arquivos...")
    m3u_gen, xmltv_gen = M3UGenerator(), XMLTVGenerator()
    all_streams = state_manager.get_all_streams()
    playlist_live = m3u_gen.generate_playlist(all_streams, categories_db, 'live')
    playlist_upcoming = m3u_gen.generate_playlist(all_streams, categories_db, 'upcoming')
    playlist_vod = m3u_gen.generate_playlist(all_streams, categories_db, 'vod')
    epg = xmltv_gen.generate_xml(state_manager.get_all_channels(), all_streams, categories_db)
    live_path = Path(PLAYLIST_SAVE_DIRECTORY) / PLAYLIST_LIVE_FILENAME
    upcoming_path = Path(PLAYLIST_SAVE_DIRECTORY) / PLAYLIST_UPCOMING_FILENAME
    vod_path = Path(PLAYLIST_SAVE_DIRECTORY) / PLAYLIST_VOD_FILENAME
    xmltv_path = Path(XMLTV_SAVE_DIRECTORY) / XMLTV_FILENAME
    try:
        live_path.parent.mkdir(parents=True, exist_ok=True); xmltv_path.parent.mkdir(parents=True, exist_ok=True)
        with open(live_path, "w", encoding="utf-8") as f: f.write(playlist_live)
        with open(upcoming_path, "w", encoding="utf-8") as f: f.write(playlist_upcoming)
        if KEEP_RECORDED_STREAMS and (len(playlist_vod.splitlines()) > 2 or PLACEHOLDER_VOD_ID in playlist_vod):
             with open(vod_path, "w", encoding="utf-8") as f: f.write(playlist_vod)
        elif not KEEP_RECORDED_STREAMS and vod_path.exists():
             try: vod_path.unlink(); logger.info(f"Arquivo VOD {vod_path} removido.")
             except OSError as e: logger.error(f"Erro ao remover {vod_path}: {e}")
        with open(xmltv_path, "w", encoding="utf-8") as f: f.write(epg)
        logger.info(f"Arquivos salvos: {live_path.name}, {upcoming_path.name}{', '+vod_path.name if KEEP_RECORDED_STREAMS else ''}, {xmltv_path.name}")
    except IOError as e: logger.error(f"Erro ao salvar arquivos: {e}")
    texts_cache_data = {}; now_utc_text = datetime.now(timezone.utc); datetime_max_utc = datetime.max.replace(tzinfo=timezone.utc)
    upcoming_streams_text = [s for s in all_streams if s.get('status') == 'upcoming' and not s.get('video_id', '').startswith('PLACEHOLDER_')]
    for s in upcoming_streams_text:
        video_id = s.get('video_id'); text_line1, text_line2 = "", ""
        if not video_id: continue
        start_time = ContentGenerator._get_sortable_time(s)
        if isinstance(start_time, datetime) and start_time != datetime_max_utc:
            try:
                start_time_local = start_time.astimezone(local_tz); delta = start_time - now_utc_text; total_seconds = delta.total_seconds()
                if total_seconds > 0:
                    days, rem = divmod(int(total_seconds), 86400); hours, rem = divmod(rem, 3600); minutes, _ = divmod(rem, 60)
                    if days > 1: text_line1 = f"Ao vivo em {days}d {hours}h"
                    elif days == 1: text_line1 = f"Ao vivo em 1d {hours}h"
                    elif hours > 0: text_line1 = f"Ao vivo em {hours}h {minutes}m"
                    else: text_line1 = f"Ao vivo em {minutes}m" if minutes > 0 else "Ao vivo em instantes"
                meses = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
                text_line2 = f"{start_time_local.day} {meses[start_time_local.month - 1]} às {start_time_local.strftime('%H:%M')}"
                texts_cache_data[video_id] = {"line1": text_line1, "line2": text_line2}
            except Exception as e: logger.warning(f"Erro ao gerar texto para {video_id}: {e}")
    texts_cache_path = Path(state_manager.cache_path.parent) / TEXTS_CACHE_FILENAME
    try:
        with open(texts_cache_path, "w", encoding="utf-8") as f: json.dump(texts_cache_data, f, indent=2)
        logger.info(f"Cache de textos salvo em '{texts_cache_path}'.")
    except IOError as e: logger.error(f"Erro ao salvar cache de textos: {e}")
    state_manager.save_to_disk()

async def run_main_loops(state_manager, scheduler, categories_db, cache_loaded_initially):
    # (Sem alterações)
    async def save_loop():
        while True:
            save_files(state_manager, categories_db)
            sleep_interval = min(SCHEDULER_PRE_EVENT_INTERVAL_MINUTES, SCHEDULER_POST_EVENT_INTERVAL_MINUTES)
            await asyncio.sleep(max(sleep_interval, 1) * 60)
    apply_initial_delay = cache_loaded_initially and not isinstance(state_manager.meta.get('last_main_run'), datetime)
    scheduler_task = asyncio.create_task(scheduler.run(initial_run_delay=apply_initial_delay))
    save_task = asyncio.create_task(save_loop())
    await asyncio.gather(scheduler_task, save_task)

def log_initial_configuration():
    # *** MODIFICADO: Loga novas variáveis ***
    logger.info("========================= CONFIGURAÇÃO INICIAL =========================")
    logger.info(f"Canais Alvo (@Handles): {', '.join(TARGET_CHANNEL_HANDLES) if TARGET_CHANNEL_HANDLES else 'Nenhum'}")
    logger.info(f"Canais Alvo (IDs): {', '.join(TARGET_CHANNEL_IDS) if TARGET_CHANNEL_IDS else 'Nenhum'}")
    logger.info(f"Intervalo Principal do Agendador: {SCHEDULER_MAIN_INTERVAL_HOURS} horas")
    logger.info(f"Janela de Pré-Evento: {SCHEDULER_PRE_EVENT_WINDOW_HOURS} horas")
    logger.info(f"Intervalo de Pré-Evento: {SCHEDULER_PRE_EVENT_INTERVAL_MINUTES} minutos")
    logger.info(f"Intervalo de Pós-Evento: {SCHEDULER_POST_EVENT_INTERVAL_MINUTES} minutos")
    logger.info(f"Máximo de Horas Agendadas: {MAX_SCHEDULE_HOURS}h no futuro")
    logger.info(f"Filtros de Título: {len(TITLE_FILTER_EXPRESSIONS)} expressão(ões)")
    logger.info(f"Prefixo de Status: {'Ativado' if PREFIX_TITLE_WITH_STATUS else 'Desativado'}")
    logger.info(f"Prefixo de Canal: {'Ativado' if PREFIX_TITLE_WITH_CHANNEL_NAME else 'Desativado'}")
    if PREFIX_TITLE_WITH_CHANNEL_NAME: logger.info(f"  -> Mapeamentos de Nomes: {len(CHANNEL_NAME_MAPPINGS)} definidos")
    logger.info(f"Limpeza de Descrição do EPG: {'Ativada' if EPG_DESCRIPTION_CLEANUP else 'Desativada'}")
    logger.info(f"Retenção de VODs (ex-live/upcoming): {'Ativada' if KEEP_RECORDED_STREAMS else 'Desativada'} (Max {MAX_RECORDED_PER_CHANNEL} por {RECORDED_RETENTION_DAYS} dias)")
    if ENABLE_SCHEDULER_ACTIVE_HOURS: logger.info(f"Horário de Atividade da Busca Principal: {SCHEDULER_ACTIVE_START_HOUR}:00 - {SCHEDULER_ACTIVE_END_HOUR}:00 ({local_tz})")
    else: logger.info(f"Horário de Atividade da Busca Principal: Desativado (rodando 24/7)")
    logger.info(f"Estratégia de Busca: {'playlistItems (Baixo Custo)' if USE_PLAYLIST_ITEMS else 'search (Alto Custo)'}")
    logger.info(f"STALE_HOURS: {STALE_HOURS}h | FULL_SYNC_INTERVAL_HOURS: {FULL_SYNC_INTERVAL_HOURS}h | RESOLVE_HANDLES_TTL_HOURS: {RESOLVE_HANDLES_TTL_HOURS}h")
    logger.info(f"Limite de Busca Inicial: {INITIAL_SYNC_DAYS} dias (0 = ilimitado)")
    if FILTER_BY_CATEGORY: logger.info(f"Filtro de Categoria: ATIVADO (Permitidos: {ALLOWED_CATEGORY_IDS_STR})")
    else: logger.info("Filtro de Categoria: DESATIVADO")
    logger.info(f"URL do Placeholder: {'Definida' if PLACEHOLDER_IMAGE_URL else 'NÃO DEFINIDA'}")
    logger.info(f"Usar Placeholder Invisível (URL comentada): {'Sim' if USE_INVISIBLE_PLACEHOLDER else 'Não'}")
    logger.info(f"Nível de Log (get_streams): {LOG_LEVEL_STR} | Salvar em Arquivo: {'Sim' if LOG_TO_FILE else 'Não'}")
    logger.info("======================================================================")


if __name__ == "__main__":
    # (Sem alterações)
    script_dir = Path(__file__).resolve().parent; cache_path = script_dir / STATE_CACHE_FILENAME
    state, scraper = StateManager(cache_path), APIScraper(API_KEY)
    web_server = WebServer(state)
    server_thread = threading.Thread(target=web_server.run_in_thread, args=("0.0.0.0", HTTP_PORT)); server_thread.daemon = True
    categories = {}
    cache_loaded_initially = False
    try:
        cache_loaded_initially = state.load_from_disk()

        all_target_ids = set(TARGET_CHANNEL_IDS)
        resolved_handles_cache = state.meta.get('resolved_handles', {})
        for handle_data in resolved_handles_cache.values():
             if isinstance(handle_data, dict) and handle_data.get('channelId'): all_target_ids.add(handle_data['channelId'])
        resolved_channels_dict = scraper.resolve_channel_handles_to_ids(TARGET_CHANNEL_HANDLES, state)
        all_target_ids.update(resolved_channels_dict.keys())
        final_channels_to_process_dict = scraper.ensure_channel_titles(all_target_ids, state)
        logger.info(f"Total de canais únicos a serem processados: {len(final_channels_to_process_dict)}")

        scheduler = Scheduler(scraper, state)

        if cache_loaded_initially:
            total_streams = len(state.get_all_streams()); upcoming_count = len([s for s in state.get_all_streams() if s.get('status') == 'upcoming'])
            live_count = len([s for s in state.get_all_streams() if ContentGenerator._is_live(None, s)]); none_count = total_streams - upcoming_count - live_count
            logger.info(f"[StateManager] Cache '{STATE_CACHE_FILENAME}' carregado com sucesso.")
            logger.info(f"-> Estado inicial: {len(state.get_all_channels())} canais | {total_streams} streams ({live_count} live, {upcoming_count} upcoming, {none_count} vod/ended)")
            logger.info(f"  -> Meta (do cache): last_main_run={state.meta.get('last_main_run')}, last_full_sync={state.meta.get('last_full_sync')}")
        else: logger.warning(f"[StateManager] Cache '{STATE_CACHE_FILENAME}' não encontrado ou inválido.")

        log_initial_configuration() # Loga a configuração

        logger.info("Buscando categorias do YouTube...");
        try:
            cats = scraper.youtube.videoCategories().list(part="snippet", regionCode="BR").execute()
            categories = {item['id']: item['snippet']['title'] for item in cats.get('items', [])}; web_server.set_categories_db(categories)
            logger.info(f"Categorias do YouTube carregadas ({len(categories)} total).")
        except Exception as e: logger.error(f"Falha ao carregar categorias: {e}")

        if not cache_loaded_initially:
            logger.info("[StateManager] Cache vazio. Executando primeira busca síncrona...")
            if final_channels_to_process_dict:
                initial_published_after = None
                logger.debug(f"Primeira busca: INITIAL_SYNC_DAYS = {INITIAL_SYNC_DAYS}")
                if INITIAL_SYNC_DAYS > 0:
                    initial_published_after_dt = datetime.now(timezone.utc) - timedelta(days=INITIAL_SYNC_DAYS)
                    initial_published_after = initial_published_after_dt.isoformat()
                    logger.debug(f"Primeira busca: Calculado initial_published_after = {initial_published_after}")
                    logger.info(f"[StateManager] Busca inicial limitada aos últimos {INITIAL_SYNC_DAYS} dias.")
                else: logger.info("[StateManager] Busca síncrona completa (INITIAL_SYNC_DAYS=0).")
                fetch_method = scraper.fetch_all_streams_for_channels_using_playlists if USE_PLAYLIST_ITEMS else scraper.fetch_all_streams_for_channels
                all_streams = fetch_method(final_channels_to_process_dict, published_after=initial_published_after)
                state.update_streams(all_streams)
                now_after_sync = datetime.now(timezone.utc)
                state.meta['last_full_sync'] = now_after_sync; state.meta['last_main_run'] = now_after_sync
                scheduler.last_full_sync = now_after_sync; scheduler.last_main_run = now_after_sync
                logger.info("[StateManager] Primeira busca concluída."); scheduler._log_current_state("Busca Inicial")
            else: logger.warning("[StateManager] Nenhum canal alvo definido/encontrado. Busca inicial não executada.")

        server_thread.start()
        asyncio.run(run_main_loops(state_manager=state, scheduler=scheduler, categories_db=categories, cache_loaded_initially=cache_loaded_initially))

    except KeyboardInterrupt: logger.info("Programa interrompido pelo usuário.")
    except Exception as e: logger.error(f"Erro fatal: {e}", exc_info=True)
    finally: logger.info(f"[StateManager] Salvando estado final no cache '{STATE_CACHE_FILENAME}'..."); state.save_to_disk(); logger.info("Finalizado.")