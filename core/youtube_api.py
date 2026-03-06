"""
core/youtube_api.py
-------------------
Responsabilidade: Interface para YouTube Data API v3.
Depende de: lista de api_keys passada pelo chamador
NÃO depende de: AppConfig, Flask, FastHTML, os.getenv

Distribuição de chaves: round-robin por instância.
Cada chamada a next_key() avança o índice, distribuindo uniformemente
as chamadas entre todas as chaves configuradas.

Exemplo de uso:
    yt = YouTubeAPI(api_keys=["key1", "key2"])
    print(yt.api_key)  # chave ativa atual
"""
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from datetime import datetime, timezone, timedelta
import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("TubeWrangler")


class YouTubeAPI:
    def __init__(self, api_keys):
        if isinstance(api_keys, str):
            self._keys = [k.strip() for k in api_keys.split(",") if k.strip()]
        else:
            self._keys = [k.strip() for k in (api_keys or []) if k.strip()]

        self._key_index = 0
        self.youtube = None
        self.api_key = ""
        self.uploads_cache: dict = {}

        if self._keys:
            self._build_client()
            count = len(self._keys)
            if count > 1:
                logger.info(f"YouTubeAPI: {count} chaves configuradas (round-robin ativo).")
            else:
                logger.info("YouTubeAPI: 1 chave configurada.")
        else:
            logger.warning("YouTube API key ausente. API desativada ate configuracao no /config.")

    def _build_client(self):
        self.api_key = self._keys[self._key_index]
        self.youtube = build("youtube", "v3", developerKey=self.api_key)

    def rotate_key(self):
        if len(self._keys) <= 1:
            return
        self._key_index = (self._key_index + 1) % len(self._keys)
        self._build_client()
        logger.debug(f"YouTubeAPI: usando chave [{self._key_index + 1}/{len(self._keys)}]")

    def resolve_channel_handles_to_ids(self, handles: List[str], state) -> Dict[str, str]:
        if not self.youtube:
            logger.warning("resolve_channel_handles_to_ids ignorado: YouTube API desativada (sem key).")
            return {}
        resolved_this_run = {}
        logger.info(f"Resolvendo {len(handles)} handles de canais para IDs... (usando cache quando possível)")
        now = datetime.now(timezone.utc)
        for handle in handles:
            rh = state.meta.get('resolved_handles', {}).get(handle)
            need_resolve = True
            if rh and rh.get('channelId'):
                resolved_at = rh.get('resolved_at')
                if isinstance(resolved_at, datetime) and (now - resolved_at) < timedelta(hours=24):
                    cid = rh['channelId']
                    title = rh.get('channelTitle')
                    if cid and title is not None:
                        state.update_channels({cid: {
                            "title": title,
                            "thumbnail_url": rh.get('thumbnail_url', ''),
                        }})
                        resolved_this_run[cid] = title
                        need_resolve = False
            if not need_resolve:
                continue
            self.rotate_key()
            try:
                req = self.youtube.search().list(part="id,snippet", q=handle, type="channel", maxResults=1)
                res = req.execute()
                items = res.get("items", [])
                if items:
                    channel_id = items[0]["id"]["channelId"]
                    channel_title = items[0]["snippet"].get("channelTitle")
                    # search.list snippet não traz thumbnail do canal; buscamos via channels.list
                    thumb = self._fetch_channel_thumbnail(channel_id)
                    if channel_id and channel_title is not None:
                        resolved_this_run[channel_id] = channel_title
                        state.meta.setdefault('resolved_handles', {})[handle] = {
                            "channelId": channel_id,
                            "channelTitle": channel_title,
                            "thumbnail_url": thumb,
                            "resolved_at": now,
                        }
                        state.update_channels({channel_id: {
                            "title": channel_title,
                            "thumbnail_url": thumb,
                        }})
                        logger.info(f"Handle '{handle}' -> {channel_id} ({channel_title})")
                    else:
                        logger.warning(f"API retornou dados incompletos para handle '{handle}'")
                else:
                    logger.warning(f"Nenhum canal encontrado para handle '{handle}'")
            except HttpError as e:
                logger.error(f"Erro de API ao resolver '{handle}': {e}.")
        logger.info(f"Resolvido/cacheado via handle {len(resolved_this_run)} canais nesta execução.")
        return resolved_this_run

    def _fetch_channel_thumbnail(self, channel_id: str) -> str:
        """Busca thumbnail do canal via channels().list snippet. Retorna URL ou ''."""
        if not self.youtube or not channel_id:
            return ""
        try:
            self.rotate_key()
            req = self.youtube.channels().list(part="snippet", id=channel_id)
            res = req.execute()
            items = res.get("items", [])
            if items:
                thumbs = items[0].get("snippet", {}).get("thumbnails", {})
                return (
                    thumbs.get("default", {}).get("url")
                    or thumbs.get("medium", {}).get("url")
                    or thumbs.get("high", {}).get("url")
                    or ""
                )
        except HttpError as e:
            logger.warning(f"Não foi possível buscar thumbnail para {channel_id}: {e}")
        return ""

    def ensure_channel_titles(self, target_channel_ids: Set[str], state) -> Dict[str, str]:
        if not self.youtube:
            logger.warning("ensure_channel_titles ignorado: YouTube API desativada (sem key).")
            return {cid: state.get_channel_title(cid) for cid in target_channel_ids if state.get_channel_title(cid)}
        ids_without_title = {cid for cid in target_channel_ids if not state.get_channel_title(cid)}
        if not ids_without_title:
            logger.debug("Todos os IDs de canal alvo já possuem títulos no estado.")
            return {cid: state.get_channel_title(cid) for cid in target_channel_ids if state.get_channel_title(cid)}
        logger.info(f"Buscando títulos para {len(ids_without_title)} IDs de canal faltantes...")
        fetched_titles = {}
        ids_list = list(ids_without_title)
        for i in range(0, len(ids_list), 50):
            self.rotate_key()
            batch_ids = ids_list[i:i+50]
            try:
                req = self.youtube.channels().list(part="snippet", id=",".join(batch_ids))
                res = req.execute()
                for item in res.get("items", []):
                    cid = item.get("id")
                    snippet = item.get("snippet", {})
                    title = snippet.get("title")
                    thumbs = snippet.get("thumbnails", {})
                    thumb = (
                        thumbs.get("default", {}).get("url")
                        or thumbs.get("medium", {}).get("url")
                        or thumbs.get("high", {}).get("url")
                        or ""
                    )
                    if cid and title:
                        fetched_titles[cid] = title
                        state.update_channels({cid: {"title": title, "thumbnail_url": thumb}})
                        logger.info(f"Título encontrado para ID {cid}: {title}")
            except HttpError as e:
                logger.error(f"Erro ao buscar títulos para o lote de IDs: {e}")
        still_missing = ids_without_title - set(fetched_titles.keys())
        if still_missing:
            logger.warning(f"Não foi possível obter títulos para {len(still_missing)} IDs: {still_missing}")
        return {cid: state.get_channel_title(cid) for cid in target_channel_ids if state.get_channel_title(cid)}

    def fetch_streams_by_ids(self, video_ids: List[str], channels_dict: Dict[str, str]) -> List[Dict[str, Any]]:
        if not self.youtube:
            logger.warning("fetch_streams_by_ids ignorado: YouTube API desativada (sem key).")
            return []
        if not video_ids:
            return []
        data = []
        logger.info(f"Buscando detalhes para {len(video_ids)} video(s) específicos... (em batches)")
        for i in range(0, len(video_ids), 50):
            self.rotate_key()
            try:
                batch = video_ids[i:i+50]
                req = self.youtube.videos().list(part="snippet,liveStreamingDetails,contentDetails", id=",".join(batch))
                res = req.execute()
                for item in res.get("items", []):
                    data.append(self._format_stream_data(item, channels_dict))
            except HttpError as e:
                logger.error(f"Falha ao buscar detalhes do lote {i//50 + 1}: {e}")
        logger.info(f"Recebidos detalhes de {len(data)} video(s).")
        return data

    def fetch_all_streams_for_channels(self, channels_dict: Dict[str, str], published_after: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self.youtube:
            logger.warning("fetch_all_streams_for_channels ignorado: YouTube API desativada (sem key).")
            return []
        ids = set()
        logger.info(f"Buscando streams [search.list] para {len(channels_dict)} canais (publishedAfter={published_after})...")
        for cid in channels_dict.keys():
            page_token = None
            page_count = 0
            self.rotate_key()
            while True:
                page_count += 1
                try:
                    kwargs = {"part": "id", "channelId": cid, "type": "video", "maxResults": 50}
                    if page_token:
                        kwargs['pageToken'] = page_token
                    if published_after:
                        kwargs['publishedAfter'] = published_after
                    req = self.youtube.search().list(**kwargs)
                    res = req.execute()
                    items = res.get('items', [])
                    if items:
                        ids.update(item['id']['videoId'] for item in items if item.get('id', {}).get('videoId'))
                    page_token = res.get('nextPageToken')
                    if not page_token:
                        break
                    if page_count > 20:
                        logger.warning(f"Atingido limite páginas search.list canal {cid}.")
                        break
                except HttpError as e:
                    logger.error(f"Erro API [search.list] canal {cid} (pág {page_count}): {e}")
                    break
        logger.info(f"Busca [search.list] encontrou {len(ids)} IDs únicos. Buscando detalhes...")
        return self.fetch_streams_by_ids(list(ids), channels_dict)

    def fetch_all_streams_for_channels_using_playlists(
        self,
        channels_dict: Dict[str, str],
        published_after: Optional[str] = None,
        stale_hours: int = 6,
        max_schedule_hours: int = 72,
    ) -> List[Dict[str, Any]]:
        if not self.youtube:
            logger.warning("fetch_all_streams_for_channels_using_playlists ignorado: YouTube API desativada (sem key).")
            return []
        ids = set()
        logger.info(f"Buscando streams [playlistItems] para {len(channels_dict)} canais (publishedAfter={published_after})...")
        published_after_dt = None
        if published_after:
            try:
                published_after_dt = datetime.fromisoformat(published_after.replace('Z', '+00:00'))
            except Exception as e:
                logger.error(f"Erro ao parsear published_after '{published_after}': {e}")
                published_after_dt = None
        for cid in channels_dict.keys():
            playlist_id = self.uploads_cache.get(cid)
            if not playlist_id:
                self.rotate_key()
                try:
                    ch_req = self.youtube.channels().list(part='contentDetails', id=cid, maxResults=1)
                    ch_res = ch_req.execute()
                    items = ch_res.get('items', [])
                    if items:
                        playlist_id = items[0]['contentDetails']['relatedPlaylists'].get('uploads')
                    if playlist_id:
                        self.uploads_cache[cid] = playlist_id
                    else:
                        logger.warning(f"Canal {cid} sem playlist 'uploads'.")
                        continue
                except HttpError as e:
                    logger.error(f"Erro obter uploads playlist {cid}: {e}")
                    continue
            page_token = None
            page_count = 0
            stopped_early = False
            self.rotate_key()
            while True:
                page_count += 1
                try:
                    kwargs = {'part': 'snippet', 'playlistId': playlist_id, 'maxResults': 50}
                    if page_token:
                        kwargs['pageToken'] = page_token
                    res = self.youtube.playlistItems().list(**kwargs).execute()
                    items = res.get('items', [])
                    stop_pagination = False
                    now_utc = datetime.now(timezone.utc)
                    stale_cutoff = now_utc - timedelta(hours=stale_hours)
                    future_cutoff = now_utc + timedelta(hours=max_schedule_hours)
                    all_too_old = True
                    for it in items:
                        snip = it.get('snippet', {})
                        resource = snip.get('resourceId', {})
                        vid = resource.get('videoId')
                        publishedAt = snip.get('publishedAt')
                        if published_after_dt and publishedAt:
                            try:
                                pa_dt = datetime.fromisoformat(publishedAt.replace('Z', '+00:00'))
                                if stale_cutoff <= pa_dt <= future_cutoff:
                                    all_too_old = False
                                if pa_dt <= published_after_dt:
                                    stop_pagination = True
                                    stopped_early = True
                                    break
                            except Exception as e:
                                logger.warning(f"Erro ao parsear publishedAt '{publishedAt}' para video {vid}: {e}")
                                all_too_old = False
                        else:
                            all_too_old = False
                        if vid:
                            ids.add(vid)
                    if stop_pagination:
                        break
                    if items and all_too_old:
                        break
                    page_token = res.get('nextPageToken')
                    if not page_token:
                        break
                    if page_count > 40:
                        logger.warning(f"Atingido limite páginas playlistItems {playlist_id}.")
                        break
                except HttpError as e:
                    logger.error(f"Erro [playlistItems] playlist {playlist_id} (pág {page_count}): {e}")
                    break
            logger.debug(f"Playlist {playlist_id} (Canal {cid}): Paginação {'interrompida' if stopped_early else 'completa'} ({page_count} pág).")
        logger.info(f"Busca [playlistItems] encontrou {len(ids)} IDs únicos. Buscando detalhes...")
        return self.fetch_streams_by_ids(list(ids), channels_dict)

    def _format_stream_data(self, item: dict, channels_dict: dict) -> dict:
        snippet = item.get("snippet", {})
        vid = item.get("id")
        cid = snippet.get("channelId")
        if isinstance(vid, dict):
            vid = vid.get('videoId') or vid.get('id')
        thumbs = snippet.get("thumbnails", {})
        thumb_url = thumbs.get("maxres", {}).get("url") or thumbs.get("standard", {}).get("url") or thumbs.get("high", {}).get("url") or ""
        live = item.get("liveStreamingDetails", {})
        content = item.get("contentDetails", {})
        def parse_time(time_str):
            if not time_str:
                return None
            try:
                return datetime.fromisoformat(time_str.replace('Z', '+00:00'))
            except (ValueError, TypeError):
                return None
        return {
            "videoid": vid,
            "channelid": cid,
            "channelname": channels_dict.get(cid, snippet.get("channelTitle", "Desconhecido")),
            "title": snippet.get("title"),
            "description": snippet.get("description"),
            "tags": snippet.get("tags", []),
            "categoryoriginal": snippet.get("categoryId"),
            "watchurl": f"https://www.youtube.com/watch?v={vid}",
            "thumbnailurl": thumb_url,
            "status": snippet.get("liveBroadcastContent", "none"),
            "scheduledstarttimeutc": parse_time(live.get("scheduledStartTime")),
            "actualstarttimeutc": parse_time(live.get("actualStartTime")),
            "actualendtimeutc": parse_time(live.get("actualEndTime")),
            "durationiso": content.get("duration"),
            "contentrating": content.get("contentRating", {}),
            "fetchtime": datetime.now(timezone.utc),
        }

    def format_stream_data(self, item: dict, channels_dict: dict) -> dict:
        return self._format_stream_data(item, channels_dict)

    def check_vod_availability_batch(self, video_ids: List[str]) -> Dict[str, bool]:
        """
        Verifica disponibilidade de VODs via videos.list (part=status,contentDetails).

        Critério de disponibilidade:
          - status.privacyStatus == "public" ou "unlisted"
          - status.uploadStatus == "processed"

        Vídeos não retornados pela API (deletados, geo-bloqueados, etc.)
        são marcados como indisponíveis (False).

        Em caso de quota excedida, assume disponível para evitar falsos negativos.

        Retorna {video_id: True/False}.
        """
        if not self.youtube:
            logger.warning("check_vod_availability_batch ignorado: YouTube API desativada (sem key).")
            return {vid: True for vid in video_ids}

        result: Dict[str, bool] = {}
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i + 50]
            try:
                self.rotate_key()
                req = self.youtube.videos().list(
                    part="status,contentDetails",
                    id=",".join(batch),
                )
                res = req.execute()
                returned_ids: set = set()
                for item in res.get("items", []):
                    vid = item.get("id")
                    if not vid:
                        continue
                    returned_ids.add(vid)
                    status = item.get("status", {})
                    privacy = status.get("privacyStatus", "")
                    upload_status = status.get("uploadStatus", "")
                    available = privacy in ("public", "unlisted") and upload_status == "processed"
                    result[vid] = available
                    logger.debug(
                        f"[check_vod] {vid}: privacy={privacy} upload={upload_status} "
                        f"-> {'disponível' if available else 'indisponível'}"
                    )
                for vid in batch:
                    if vid not in returned_ids:
                        result[vid] = False
                        logger.debug(f"[check_vod] {vid}: não retornado pela API -> indisponível")
            except HttpError as e:
                if "quotaExceeded" in str(e):
                    logger.warning(f"[YouTubeAPI] Quota excedida durante verificação de VODs: {e}")
                    for vid in batch:
                        result.setdefault(vid, True)
                else:
                    logger.error(f"[YouTubeAPI] Erro ao verificar disponibilidade de VODs: {e}")
        return result
