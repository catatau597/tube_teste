"""
core/playlist_builder.py
Responsabilidade: Gerar playlists M3U, EPG XML e conteúdos derivados.
Depende de: AppConfig
NÃO depende de: Flask, FastHTML, os.getenv

Exemplo de uso:
    from core.config import AppConfig
    cfg = AppConfig(db_path="/tmp/test.db")
    m3u = M3UGenerator(cfg)
    xml = XMLTVGenerator(cfg)
    cg = ContentGenerator(cfg)
"""
from core.config import AppConfig
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Optional
import logging
import socket

logger = logging.getLogger("TubeWrangler")


def _resolve_proxy_base_url(config) -> str:
    """Resolve PROXY_BASE_URL. Se vazio, auto-detecta IP do host."""
    configured = config.get_str("proxy_base_url").strip()
    if configured:
        return configured.rstrip("/")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        port = config.get_int("http_port")
        return f"http://{ip}:{port}"
    except Exception:
        return f"http://localhost:{config.get_int('http_port')}"


class ContentGenerator:

    def __init__(self, config: AppConfig = None):
        self._config = config
        self.config = config

    @staticmethod
    def is_live(stream: dict) -> bool:
        if stream.get("status") == "live":
            return True
        actual_start = stream.get("actualstarttimeutc")
        actual_end   = stream.get("actualendtimeutc")
        if actual_start and not actual_end:
            return True
        return False

    @staticmethod
    def is_upcoming(stream: dict) -> bool:
        return stream.get("status") == "upcoming"

    @staticmethod
    def is_vod(stream: dict) -> bool:
        return (
            not ContentGenerator.is_live(stream)
            and not ContentGenerator.is_upcoming(stream)
            and stream.get("actualendtimeutc") is not None
        )

    @staticmethod
    def get_sortable_time(stream: dict):
        return (
            stream.get("scheduledstarttimeutc")
            or stream.get("actualstarttimeutc")
            or stream.get("fetchtime")
        )

    def get_display_title(self, stream: dict) -> str:
        title = stream.get("title") or "Sem título"

        # Remover expressões filtradas do título
        for expr in self._config.get_list("title_filter_expressions"):
            title = title.replace(expr, "").strip()

        # Remover dois-pontos e espaços extras no início do título
        title = title.lstrip(": ").strip()

        # Prefixar com status (antes do canal)
        status_prefix = ""
        if self._config.get_bool("prefix_title_with_status"):
            status = stream.get("status", "none")
            if status == "live":
                status_prefix = "🔴 AO VIVO"
            elif status == "upcoming":
                status_prefix = "🕐 AGENDADO"

        # Prefixar com nome do canal
        channel_prefix = ""
        if self._config.get_bool("prefix_title_with_channel_name"):
            mappings = self._config.get_mapping("channel_name_mappings")
            channel  = stream.get("channelname", "")
            channel  = mappings.get(channel, channel)
            if channel:
                channel_prefix = channel

        # Montar título final: [Canal] [Status]: Título
        parts = []
        if channel_prefix:
            parts.append(channel_prefix)
        if status_prefix:
            parts.append(status_prefix)
        if parts:
            title = f"{' | '.join(parts)}: {title}"

        return title

    def get_display_category(self, cat_id: str | None, db: dict) -> str:
        if not cat_id:
            return ""
        mappings = self._config.get_mapping("category_mappings")
        raw = db.get(str(cat_id), str(cat_id))
        return mappings.get(raw, raw)

    def filter_streams(self, streams: list, mode: str) -> list:
        if self._config.get_bool("filter_by_category"):
            allowed = set(self._config.get_list("allowed_category_ids"))
            if allowed:
                streams = [
                    s for s in streams
                    if str(s.get("categoryoriginal", "")) in allowed
                ]
        if mode == "upcoming":
            max_hours  = self._config.get_int("max_schedule_hours")
            max_per_ch = self._config.get_int("max_upcoming_per_channel")
            cutoff     = datetime.now(timezone.utc) + timedelta(hours=max_hours)
            now        = datetime.now(timezone.utc)
            streams = [
                s for s in streams
                if s.get("status") == "upcoming"
                and isinstance(s.get("scheduledstarttimeutc"), datetime)
                and now < s["scheduledstarttimeutc"] <= cutoff
            ]
            per_channel = defaultdict(list)
            for s in streams:
                per_channel[s.get("channelid", "")].append(s)
            result = []
            for ch_streams in per_channel.values():
                ch_streams.sort(
                    key=lambda x: x.get("scheduledstarttimeutc")
                    or datetime.min.replace(tzinfo=timezone.utc)
                )
                result.extend(ch_streams[:max_per_ch])
            return result
        return streams


class M3UGenerator(ContentGenerator):

    def __init__(self, config: AppConfig):
        super().__init__(config)

    def generate_playlist(
        self,
        streams: list,
        categories_db: dict,
        mode: str,
        mode_type: str = "direct",
        thumbnail_manager=None,
        proxy_base_url: str = "",
    ) -> str:
        # LÓGICA DO VOD:
        # Streams VOD não são buscados diretamente.
        # Ciclo: upcoming → live → (encerra) → vod
        # A playlist VOD filtra streams já em memória com actualendtimeutc.
        # NÃO buscar VOD na API — apenas filtrar os existentes.

        if mode == "upcoming" and mode_type == "direct":
            raise ValueError("upcoming nunca pode ser modo direct")

        if mode == "live":
            filtered = [s for s in streams if ContentGenerator.is_live(s)]

        elif mode == "upcoming":
            filtered = self.filter_streams(streams, "upcoming")

        elif mode == "vod":
            keep = self._config.get_bool("keep_recorded_streams")
            if not keep:
                logger.debug("M3U [vod]: keep_recorded_streams=false, pulando")
                return "#EXTM3U\n"
            max_per_ch     = self._config.get_int("max_recorded_per_channel")
            retention_days = self._config.get_int("recorded_retention_days")
            cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
            candidates = [
                s for s in streams
                if ContentGenerator.is_vod(s)
                and isinstance(s.get("fetchtime"), datetime)
                and s["fetchtime"] >= cutoff
            ]
            per_channel = defaultdict(list)
            for s in candidates:
                per_channel[s.get("channelid", "")].append(s)
            filtered = []
            for ch_streams in per_channel.values():
                ch_streams.sort(
                    key=lambda x: x.get("fetchtime")
                    or datetime.min.replace(tzinfo=timezone.utc),
                    reverse=True
                )
                filtered.extend(ch_streams[:max_per_ch])
        else:
            filtered = streams

        lines = ["#EXTM3U"]
        base_url = proxy_base_url.rstrip("/") if proxy_base_url else ""

        for s in filtered:
            vid      = s.get("videoid", "")
            title    = self.get_display_title(s)
            thumb    = s.get("thumbnailurl", "")
            cat_id   = s.get("categoryoriginal", "")
            category = self.get_display_category(cat_id, categories_db)

            if mode_type == "proxy":
                url = f"{base_url}/api/player/{vid}" if base_url else f"/api/player/{vid}"
                logo = (
                    thumbnail_manager.get_url(vid, base_url)
                    if thumbnail_manager and base_url
                    else (f"{base_url}/api/thumbnail/{vid}" if base_url else f"/api/thumbnail/{vid}")
                )
            else:
                url = s.get("watchurl") or f"https://youtube.com/watch?v={vid}"
                logo = thumb

            lines.append(
                f'#EXTINF:-1 tvg-id="{vid}" tvg-name="{title}" '
                f'tvg-logo="{logo}" group-title="{category}",{title}'
            )
            lines.append(url)

        if not filtered and self._config.get_bool("use_invisible_placeholder"):
            placeholder_id = f"PLACEHOLDER_{mode.upper()}"
            placeholder_url = "https://placeholder_url"
            lines.append(
                f'#EXTINF:-1 tvg-id="{placeholder_id}" tvg-name="" tvg-logo="" group-title="",'
            )
            lines.append(placeholder_url)

        logger.debug(f"M3U [{mode}/{mode_type}]: {len(filtered)} entradas geradas")
        return "\n".join(lines)


class XMLTVGenerator(ContentGenerator):

    def __init__(self, config: AppConfig):
        super().__init__(config)

    def generate_xml(
        self, channels: dict, streams: list, categories_db: dict
    ) -> str:
        import xml.etree.ElementTree as ET
        from xml.etree.ElementTree import SubElement

        root = ET.Element("tv", attrib={
            "generator-info-name": "TubeWrangler",
            "source-info-name":    "YouTube"
        })

        for cid, cname in channels.items():
            ch_el = SubElement(root, "channel", id=cid)
            dn = SubElement(ch_el, "display-name")
            dn.text = cname

        for s in streams:
            vid   = s.get("videoid", "")
            cid   = s.get("channelid", "")
            title = self.get_display_title(s)
            desc  = s.get("description", "") or ""
            thumb = s.get("thumbnailurl", "")

            start_dt: Optional[datetime] = (
                s.get("scheduledstarttimeutc")
                or s.get("actualstarttimeutc")
            )
            end_dt: Optional[datetime] = s.get("actualendtimeutc")

            if not start_dt:
                continue

            fmt       = "%Y%m%d%H%M%S %z"
            start_str = start_dt.strftime(fmt)
            end_str   = end_dt.strftime(fmt) if end_dt else start_str

            prog = SubElement(root, "programme", attrib={
                "start":   start_str,
                "stop":    end_str,
                "channel": cid,
            })
            t = SubElement(prog, "title", lang="pt")
            t.text = title

            if desc:
                if self._config.get_bool("epg_description_cleanup"):
                    desc = desc.split("\n")[0][:500]
                d = SubElement(prog, "desc", lang="pt")
                d.text = desc

            if thumb:
                SubElement(prog, "icon", src=thumb)

        xml_str = ET.tostring(root, encoding="unicode", xml_declaration=False)
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str
