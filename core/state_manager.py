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
    def get_all_streams(self) -> list:
        """Retorna todos os streams do estado em memória."""
        if not hasattr(self, "streams") or not self.streams:
            return []
        return list(self.streams.values())

    def get_all_channels(self) -> dict:
        """Retorna lista de canais monitorados."""
        if not hasattr(self, "channels") or self.channels is None:
            return {}
        if isinstance(self.channels, dict):
            return self.channels
        return {}

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
                    self.channels = raw.get("channels", {}) or {}
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

    def update_channels(self, channels_data: dict):
        for cid, title in channels_data.items():
            if cid and title:
                self.channels[cid] = title

    def update_streams(self, new_streams: list):
        """
        Processa lista de streams da API, aplica filtros e atualiza o estado.

        Filtros (em ordem, configurados via /config/filters):
          1. Categoria: descarta se filter_by_category=true e categoria não está em allowed_category_ids.
          2. Shorts por palavras: descarta se título ou tags contêm qualquer palavra de shorts_block_words.
          3. Shorts por duração: descarta se duration_iso <= shorts_max_duration_s (e shorts_max_duration_s > 0).
        """
        now = datetime.now(timezone.utc)
        added = updated = 0
        ign_category = ign_shorts_words = ign_shorts_duration = 0

        # --- Lê configurações de filtro ---
        filter_by_cat   = self._config.get_bool("filter_by_category")
        allowed_cat_ids = set(self._config.get_list("allowed_category_ids"))
        shorts_max_s    = self._config.get_int("shorts_max_duration_s")
        shorts_words    = [w.lower() for w in self._config.get_list("shorts_block_words") if w]

        for stream in new_streams:
            vid = stream.get("videoid")
            if not vid:
                continue

            # ----------------------------------------------------------------
            # Filtro 1: Categoria
            # ----------------------------------------------------------------
            if filter_by_cat and allowed_cat_ids:
                cat_id = str(stream.get("categoryoriginal") or "").strip()
                if cat_id and cat_id not in allowed_cat_ids:
                    logger.debug(f"[filtro:categoria] ignorando {vid} (cat={cat_id})")
                    # Se já estava no estado, remove para não persistir categoria errada
                    self.streams.pop(vid, None)
                    ign_category += 1
                    continue

            # ----------------------------------------------------------------
            # Filtro 2: Shorts por palavras no título ou tags
            # ----------------------------------------------------------------
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

            # ----------------------------------------------------------------
            # Filtro 3: Shorts por duração
            # ----------------------------------------------------------------
            if shorts_max_s > 0:
                duration_s = _parse_duration_seconds(stream.get("durationiso") or "")
                # duration_s == 0 significa que a API ainda não informou duração
                # (upcoming/live em andamento) — não bloqueia nesses casos.
                if 0 < duration_s <= shorts_max_s:
                    logger.debug(
                        f"[filtro:shorts-duracao] ignorando {vid} "
                        f"(dur={duration_s}s <= max={shorts_max_s}s)"
                    )
                    self.streams.pop(vid, None)
                    ign_shorts_duration += 1
                    continue

            # ----------------------------------------------------------------
            # Atualiza estado
            # ----------------------------------------------------------------
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
            f"shorts(dur)={ign_shorts_duration}"
        )
        self.prune_ended_streams()

    def prune_ended_streams(self):
        now = datetime.now(timezone.utc)
        to_delete = set()
        recorded_by_channel = defaultdict(list)

        keep_recorded          = self._config.get_bool("keep_recorded_streams")
        max_recorded_per_channel = self._config.get_int("max_recorded_per_channel")
        retention_days         = self._config.get_int("recorded_retention_days")
        stale_hours            = self._config.get_int("stale_hours")
        main_interval          = self._config.get_int("scheduler_main_interval_hours")

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

            if status == "none":
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
