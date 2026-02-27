"""
core/youtube_api.py
-------------------
Responsabilidade: Interface para YouTube Data API v3.
Depende de: api_key passado pelo chamador
NÃO depende de: AppConfig, Flask, FastHTML, os.getenv

Exemplo de uso:
    yt = YouTubeAPI(api_key="dummy")
    print(yt.api_key)
"""
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from datetime import datetime, timezone, timedelta
import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("TubeWrangler")

class YouTubeAPI:
    def __init__(self, api_key: str):
        self.youtube = build("youtube", "v3", developerKey=api_key)
        self.uploads_cache: dict = {}

    def resolve_channel_handles_to_ids(self, handles: List[str], state) -> Dict[str, str]:
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
                        if state.channels.get(cid) != title:
                            state.channels[cid] = title
                        resolved_this_run[cid] = title
                        need_resolve = False
            if not need_resolve:
                continue
            try:
                req = self.youtube.search().list(part="id,snippet", q=handle, type="channel", maxResults=1)
                res = req.execute()
                items = res.get("items", [])
                if items:
                    channel_id = items[0]["id"]["channelId"]
                    channel_title = items[0]["snippet"].get("channelTitle")
                    if channel_id and channel_title is not None:
                        resolved_this_run[channel_id] = channel_title
                        state.meta.setdefault('resolved_handles', {})[handle] = {"channelId": channel_id, "channelTitle": channel_title, "resolved_at": now}
                        state.channels[channel_id] = channel_title
                        logger.info(f"Handle '{handle}' -> {channel_id} ({channel_title})")
                    else:
                        logger.warning(f"API retornou dados incompletos para handle '{handle}'")
                else:
                    logger.warning(f"Nenhum canal encontrado para handle '{handle}'")
            except HttpError as e:
                logger.error(f"Erro de API ao resolver '{handle}': {e}.")
        logger.info(f"Resolvido/cacheado via handle {len(resolved_this_run)} canais nesta execução.")
        return resolved_this_run

    def ensure_channel_titles(self, target_channel_ids: Set[str], state) -> Dict[str, str]:
        ids_without_title = {cid for cid in target_channel_ids if cid not in state.channels or not state.channels[cid]}
        if not ids_without_title:
            logger.debug("Todos os IDs de canal alvo já possuem títulos no estado.")
            return {cid: state.channels.get(cid, "Título não encontrado") for cid in target_channel_ids if cid in state.channels}
        logger.info(f"Buscando títulos para {len(ids_without_title)} IDs de canal faltantes...")
        fetched_titles = {}
        ids_list = list(ids_without_title)
        for i in range(0, len(ids_list), 50):
            batch_ids = ids_list[i:i+50]
            try:
                req = self.youtube.channels().list(part="snippet", id=",".join(batch_ids))
                res = req.execute()
                for item in res.get("items", []):
                    cid = item.get("id")
                    title = item.get("snippet", {}).get("title")
                    if cid and title:
                        fetched_titles[cid] = title
                        state.channels[cid] = title
                        logger.info(f"Título encontrado para ID {cid}: {title}")
            except HttpError as e:
                logger.error(f"Erro ao buscar títulos para o lote de IDs: {e}")
        still_missing = ids_without_title - set(fetched_titles.keys())
        if still_missing:
            logger.warning(f"Não foi possível obter títulos para {len(still_missing)} IDs: {still_missing}")
        final_channels_dict = {}
        for cid in target_channel_ids:
            title = state.channels.get(cid)
            if title:
                final_channels_dict[cid] = title
            else:
                logger.warning(f"ID de canal alvo {cid} sem título associado após busca.")
        return final_channels_dict

    def fetch_streams_by_ids(self, video_ids: List[str], channels_dict: Dict[str, str]) -> List[Dict[str, Any]]:
        if not video_ids:
            return []
        data = []
        logger.info(f"Buscando detalhes para {len(video_ids)} video(s) específicos... (em batches)")
        for i in range(0, len(video_ids), 50):
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
        ids = set()
        logger.info(f"Buscando streams [search.list] para {len(channels_dict)} canais (publishedAfter={published_after})...")
        for cid in channels_dict.keys():
            page_token = None
            page_count = 0
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

    def fetch_all_streams_for_channels_using_playlists(self, channels_dict: Dict[str, str], published_after: Optional[str] = None) -> List[Dict[str, Any]]:
        ids = set()
        logger.info(f"Buscando streams [playlistItems] para {len(channels_dict)} canais (publishedAfter={published_after})...")
        published_after_dt = None
        if published_after:
            try:
                published_after_dt = datetime.fromisoformat(published_after.replace('Z', '+00:00'))
                logger.debug(f"Fetch using playlists: Filtro published_after_dt={published_after_dt}")
            except Exception as e:
                logger.error(f"Erro ao parsear published_after '{published_after}': {e}")
                published_after_dt = None
        for cid in channels_dict.keys():
            playlist_id = self.uploads_cache.get(cid)
            if not playlist_id:
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
            while True:
                page_count += 1
                try:
                    kwargs = {'part': 'snippet', 'playlistId': playlist_id, 'maxResults': 50}
                    if page_token:
                        kwargs['pageToken'] = page_token
                    res = self.youtube.playlistItems().list(**kwargs).execute()
                    items = res.get('items', [])
                    stop_pagination = False
                    for it in items:
                        snip = it.get('snippet', {})
                        resource = snip.get('resourceId', {})
                        vid = resource.get('videoId')
                        publishedAt = snip.get('publishedAt')
                        if published_after_dt and publishedAt:
                            try:
                                pa_dt = datetime.fromisoformat(publishedAt.replace('Z', '+00:00'))
                                if pa_dt <= published_after_dt:
                                    stop_pagination = True
                                    stopped_early = True
                                    logger.debug(f"Playlist {playlist_id} (Canal {cid}): Stop pagination at video {vid} (published: {pa_dt})")
                                    break
                            except Exception as e:
                                logger.warning(f"Erro ao parsear publishedAt '{publishedAt}' para video {vid}: {e}")
                        if vid:
                            ids.add(vid)
                    if stop_pagination:
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
