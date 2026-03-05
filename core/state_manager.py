"""
core/state_manager.py
---------------------
Responsabilidade: Gerenciar estado dos streams, canais e cache persistente.
Depende de: AppConfig
NÃO depende de: Flask, FastHTML, os.getenv

Filtros aplicados em update_streams (em ordem):
  1. Categoria: se filter_by_category=true, apenas IDs em allowed_category_ids passam.
  2. Shorts por palavra: se título ou tags contêm shorts_block_words, descarta.
  3. Shorts por duração: se duration_iso <= shorts_max_duration_s, descarta.
  4. status=none:
     - Se já existia como live/upcoming → promove para vod (evento encerrado).
     - Se nunca foi visto (VOD puro) → descarta.

Estrutura de channels (v2):
  self.channels = {
      "UCxxxx": {"title": "Nome do Canal", "thumbnail_url": "https://..."}
  }
  Para compatibilidade, get_channel_title(cid) / get_channel_thumbnail(cid) são os
  acessores recomendados. get_all_channels() continua retornando {cid: title} para
  compatibilidade com código existente que não usa thumbnail.
"""
import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from core.config import AppConfig

logger = logging.getLogger("TubeWrangler")


def _parse_duration_seconds(duration_iso: str) -> int:
    """Converte duração ISO 8601 (ex: PT1M2S) para segundos. Retorna 0 se inválido."""
    if not duration_iso or duration_iso in ("P0D", "PT0S", ""):
        return 0
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_iso)
    if not m:
        return 0
    h, mi, s = (int(v) if v else 0 for v in m.groups())
    return h * 3600 + mi * 60 + s


class StateManager:
    # ------------------------------------------------------------------
    # Acessores de canais
    # ------------------------------------------------------------------

    def _channel_entry(self, cid: str) -> dict:
        """Retorna o dict interno de um canal, normalizando formato legado."""
        entry = self.channels.get(cid)
        if entry is None:
            return {"title": "", "thumbnail_url": ""}
        if isinstance(entry, str):
            # Formato legado: migra on-the-fly
            return {"title": entry, "thumbnail_url": ""}
        return entry

    def get_channel_title(self, cid: str) -> str:
        return self._channel_entry(cid).get("title", "")

    def get_channel_thumbnail(self, cid: str) -> str:
        return self._channel_entry(cid).get("thumbnail_url", "")

    def get_all_channels(self) -> dict:
        """Retorna {cid: title} — compatibilidade com código existente."""
        if not hasattr(self, "channels") or self.channels is None:
            return {}
        result = {}
        for cid, entry in self.channels.items():
            if isinstance(entry, str):
                result[cid] = entry
            elif isinstance(entry, dict):
                result[cid] = entry.get("title", "")
        return result

    def get_all_channels_with_thumbnail(self) -> list:
        """Retorna lista de dicts com cid, title, thumbnail_url."""
        if not hasattr(self, "channels") or self.channels is None:
            return []
        result = []
        for cid, entry in self.channels.items():
            if isinstance(entry, str):
                result.append({"cid": cid, "title": entry, "thumbnail_url": ""})
            elif isinstance(entry, dict):
                result.append({
                    "cid": cid,
                    "title": entry.get("title", ""),
                    "thumbnail_url": entry.get("thumbnail_url", ""),
                })
        return result

    def get_all_streams(self) -> list:
        """Retorna todos os streams do estado em memória."""
        if not hasattr(self, "streams") or not self.streams:
            return []
        return list(self.streams.values())

    def __init__(self, config: AppConfig, cache_path: Path | None = None):
        self._config = config
        self.config = config
        self.streams = {}
        self.channels = {}
        self.meta = {
            "lastmainrun": None,
            "lastfullsync": None,
            "resolvedhandles": {},
        }
        self._thumbnail_manager = None
        if cache_path:
            self.cache_path = cache_path
        else:
            self.cache_path = Path("/data") / config.get_str("state_cache_filename")

    def set_thumbnail_manager(self, tm) -> None:
        self._thumbnail_manager = tm

    def _parse_dt(self, value):
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return None
        return None

    def load_from_disk(self) -> bool:
        """Carrega estado do arquivo JSON em /data/."""
        DATETIME_FIELDS = {
            "scheduledstarttimeutc",
            "actualstarttimeutc",
            "actualendtimeutc",
            "fetchtime",
            "lastseen",
        }

        def parse_stream(stream: dict) -> dict:
            for field in DATETIME_FIELDS:
                val = stream.get(field)
                stream[field] = self._parse_dt(val)
            return stream

        cache_file = Path("/data") / self._config.get_str("state_cache_filename")
        try:
            if cache_file.exists():
                raw = json.loads(cache_file.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and "streams" in raw:
                    raw_channels = raw.get("channels", {}) or {}
                    # Migra formato legado {cid: title} → {cid: {title, thumbnail_url}}
                    normalized = {}
                    for cid, entry in raw_channels.items():
                        if isinstance(entry, str):
                            normalized[cid] = {"title": entry, "thumbnail_url": ""}
                        elif isinstance(entry, dict):
                            normalized[cid] = entry
                    self.channels = normalized
                    self.meta = raw.get("meta", self.meta) or self.meta
                    source_streams = raw.get("streams", {}) or {}
                else:
                    source_streams = raw if isinstance(raw, dict) else {}
                    self.channels = {}
                    self.meta = self.meta
                self.streams = {
                    vid: parse_stream(s)
                    for vid, s in source_streams.items()
                    if isinstance(s, dict)
                }
                _to_remove = []
                for vid, s in list(self.streams.items()):
                    if s.get("status") == "none":
                        if s.get("actualstarttimeutc") or s.get("actualEndTime"):
                            s["status"] = "vod"
                            logger.debug(f"[migração] {vid} none→vod (tinha actualStart)")
                        else:
                            _to_remove.append(vid)
                for vid in _to_remove:
                    self.streams.pop(vid, None)
                    logger.debug(f"[migração] {vid} removido (none sem histórico live)")
                if _to_remove:
                    logger.info(f"Migração cache: removidos {len(_to_remove)} streams 'none' puros.")
                logger.info(
                    f"Cache carregado do disco: "
                    f"{cache_file.name} | streams={len(self.streams)}"
                )
                return True
            else:
                self.streams = {}
                return False
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"StateManager: erro ao carregar cache: {e}")
            self.streams = {}
            return False

    def save_to_disk(self):
        """Persiste estado no arquivo JSON em /data/."""
        cache_file = Path("/data") / self._config.get_str("state_cache_filename")

        def default_serializer(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Tipo não serializável: {type(obj)}")

        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "channels": self.channels,
                "streams": self.streams,
                "meta": self.meta,
            }
            cache_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=default_serializer),
                encoding="utf-8",
            )
        except IOError as e:
            logger.error(f"StateManager: erro ao salvar cache: {e}")

    def update_channels(self, channels_data):
        """
        Aceita:
          - dict {cid: title}           → formato legado, thumbnail fica vazio
          - dict {cid: {title, thumbnail_url}} → formato novo
          - list [{cid, title, thumbnail_url}] → formato lista
        """
        if isinstance(channels_data, list):
            for item in channels_data:
                cid = item.get("cid") or item.get("channelId") or item.get("channel_id")
                title = item.get("title", "")
                thumb = item.get("thumbnail_url", "")
                if cid and title:
                    existing = self.channels.get(cid, {})
                    if isinstance(existing, str):
                        existing = {"title": existing, "thumbnail_url": ""}
                    self.channels[cid] = {
                        "title": title,
                        "thumbnail_url": thumb or existing.get("thumbnail_url", ""),
                    }
        elif isinstance(channels_data, dict):
            for cid, value in channels_data.items():
                if not cid:
                    continue
                if isinstance(value, str):
                    # legado: preserva thumbnail já salvo
                    existing = self.channels.get(cid, {})
                    if isinstance(existing, str):
                        existing = {"title": existing, "thumbnail_url": ""}
                    self.channels[cid] = {
                        "title": value,
                        "thumbnail_url": existing.get("thumbnail_url", ""),
                    }
                elif isinstance(value, dict) and value.get("title"):
                    existing = self.channels.get(cid, {})
                    if isinstance(existing, str):
                        existing = {"title": existing, "thumbnail_url": ""}
                    self.channels[cid] = {
                        "title": value["title"],
                        "thumbnail_url": value.get("thumbnail_url") or existing.get("thumbnail_url", ""),
                    }

    def update_streams(self, new_streams: list):
        """
        Processa lista de streams da API, aplica filtros e atualiza o estado.
        """
        now = datetime.now(timezone.utc)
        added = updated = 0
        ign_category = ign_shorts_words = ign_shorts_duration = ign_vod_puro = 0

        filter_by_cat   = self._config.get_bool("filter_by_category")
        allowed_cat_ids = set(self._config.get_list("allowed_category_ids"))
        shorts_max_s    = self._config.get_int("shorts_max_duration_s")
        shorts_words    = [w.lower() for w in self._config.get_list("shorts_block_words") if w]

        for stream in new_streams:
            vid = stream.get("videoid")
            if not vid:
                continue

            if filter_by_cat and allowed_cat_ids:
                cat_id = str(stream.get("categoryoriginal") or "").strip()
                if cat_id and cat_id not in allowed_cat_ids:
                    logger.debug(f"[filtro:categoria] ignorando {vid} (cat={cat_id})")
                    self.streams.pop(vid, None)
                    ign_category += 1
                    continue

            if shorts_words:
                title_lower = (stream.get("title") or stream.get("titleoriginal") or "").lower()
                tags_lower  = [t.lower() for t in (stream.get("tags") or [])]
                hit = next(
                    (w for w in shorts_words if w in title_lower or w in tags_lower),
                    None,
                )
                if hit:
                    logger.debug(f"[filtro:shorts-palavra] ignorando {vid} (match='{hit}')")
                    self.streams.pop(vid, None)
                    ign_shorts_words += 1
                    continue

            if shorts_max_s > 0:
                duration_s = _parse_duration_seconds(stream.get("durationiso") or "")
                if 0 < duration_s <= shorts_max_s:
                    logger.debug(
                        f"[filtro:shorts-duracao] ignorando {vid} "
                        f"(dur={duration_s}s <= max={shorts_max_s}s)"
                    )
                    self.streams.pop(vid, None)
                    ign_shorts_duration += 1
                    continue

            if stream.get("status") == "none":
                existing = self.streams.get(vid)
                if existing is None:
                    logger.debug(f"[filtro:vod-puro] descartando {vid} (nunca foi live/upcoming)")
                    ign_vod_puro += 1
                    continue
                prev_status = existing.get("status", "")
                if prev_status in ("live", "upcoming"):
                    stream["status"] = "vod"
                    logger.info(f"[status] {vid}: {prev_status} → vod")
                elif prev_status == "vod":
                    stream["status"] = "vod"
                else:
                    logger.debug(f"[filtro:vod-puro] descartando {vid} (status prev={prev_status!r})")
                    ign_vod_puro += 1
                    continue

            stream["lastseen"] = now
            stream.setdefault("fetchtime", now)
            if vid in self.streams:
                self.streams[vid].update(stream)
                updated += 1
            else:
                self.streams[vid] = stream
                added += 1

        logger.info(
            f"Update Streams: +{added} upd={updated} "
            f"| ign categoria={ign_category} "
            f"shorts(palavra)={ign_shorts_words} "
            f"shorts(dur)={ign_shorts_duration} "
            f"vod-puro={ign_vod_puro}"
        )
        self.prune_ended_streams()

    def prune_ended_streams(self):
        now = datetime.now(timezone.utc)
        to_delete = set()
        recorded_by_channel = defaultdict(list)

        keep_recorded            = self._config.get_bool("keep_recorded_streams")
        max_recorded_per_channel = self._config.get_int("max_recorded_per_channel")
        retention_days           = self._config.get_int("recorded_retention_days")
        stale_hours              = self._config.get_int("stale_hours")
        main_interval            = self._config.get_int("scheduler_main_interval_hours")

        recorded_cutoff = now - timedelta(days=retention_days)
        stale_cutoff    = now - timedelta(hours=max(stale_hours * 2, main_interval * 2))

        for vid, s in list(self.streams.items()):
            status    = s.get("status")
            last_seen = self._parse_dt(s.get("lastseen")) or self._parse_dt(s.get("fetchtime")) or now
            end_time  = self._parse_dt(s.get("actualendtimeutc"))
            channel_id = s.get("channelid")

            if end_time and end_time < recorded_cutoff:
                to_delete.add(vid)
                continue

            if status in ("vod", "recorded", "none"):
                if not keep_recorded:
                    to_delete.add(vid)
                    continue
                sort_time = end_time or last_seen
                if sort_time < recorded_cutoff:
                    to_delete.add(vid)
                    continue
                recorded_by_channel[channel_id].append((vid, sort_time))
                continue

            if last_seen < stale_cutoff:
                to_delete.add(vid)

        if keep_recorded:
            for items in recorded_by_channel.values():
                if len(items) > max_recorded_per_channel:
                    items_sorted = sorted(items, key=lambda x: x[1], reverse=True)
                    for vid_to_del, _ in items_sorted[max_recorded_per_channel:]:
                        to_delete.add(vid_to_del)

        if to_delete:
            logger.info(f"Removendo {len(to_delete)} streams antigas/excedentes/stale do estado.")
            for vid in to_delete:
                self.streams.pop(vid, None)
                if self._thumbnail_manager:
                    self._thumbnail_manager.delete(vid)
