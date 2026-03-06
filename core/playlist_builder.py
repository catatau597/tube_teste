"""
core/playlist_builder.py
Responsabilidade: Gerar playlists M3U, EPG XML e conteudos derivados.
Depende de: AppConfig
NAO depende de: Flask, FastHTML, os.getenv
"""
from core.config import AppConfig
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Optional
import logging
import re
import socket
import unicodedata

logger = logging.getLogger("TubeWrangler")

# Componentes disponíveis para montagem do título
_ALL_COMPONENTS = ("channel", "status", "title")

# Regex que captura emojis e símbolos Unicode especiais
# Cobre: Emoticons, Misc Symbols, Dingbats, Supplemental Symbols,
# Transport & Map, Enclosed alphanumerics, etc.
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # misc symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F700-\U0001F77F"  # alchemical
    "\U0001F780-\U0001F7FF"  # geometric extended
    "\U0001F800-\U0001F8FF"  # supplemental arrows
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols & pictographs extended
    "\U00002702-\U000027B0"  # dingbats
    "\U000024C2-\U0001F251"  # enclosed characters
    "\u200d"                  # zero-width joiner
    "\ufe0f"                  # variation selector-16
    "]+",
    flags=re.UNICODE,
)


def _strip_emojis(text: str) -> str:
    """Remove emojis e símbolos especiais, normaliza espaços."""
    cleaned = _EMOJI_RE.sub("", text)
    # Remove também caracteres de controle e surrogates
    cleaned = "".join(
        ch for ch in cleaned
        if unicodedata.category(ch) not in ("Cc", "Cs")
    )
    return " ".join(cleaned.split())


def _resolve_proxy_base_url(config) -> str:
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
        """VOD = stream encerrado."""
        status = stream.get("status")
        if ContentGenerator.is_live(stream) or ContentGenerator.is_upcoming(stream):
            return False
        return status in ("vod", "none", "completed") or (
            status not in ("live", "upcoming", None)
            and stream.get("actualendtimeutc") is not None
        )

    @staticmethod
    def get_sortable_time(stream: dict):
        return (
            stream.get("scheduledstarttimeutc")
            or stream.get("actualstarttimeutc")
            or stream.get("fetchtime")
        )

    def _get_component_value(self, component: str, stream: dict, use_brackets: bool) -> str:
        """Retorna o valor formatado de um componente individual."""
        if component == "channel":
            mappings = self._config.get_mapping("channel_name_mappings")
            channel  = stream.get("channelname", "")
            channel  = mappings.get(channel, channel)
            if not channel:
                return ""
            return f"[{channel}]" if use_brackets else channel

        if component == "status":
            status = stream.get("status", "")
            if status == "live":
                label = "AO VIVO"
            elif status == "upcoming":
                label = "AGENDADO"
            else:
                return ""  # VOD/none não exibe tag de status
            return f"[{label}]" if use_brackets else label

        if component == "title":
            title = stream.get("title") or "Sem titulo"
            # Remoção case-insensitive de expressões configuradas
            for expr in self._config.get_list("title_filter_expressions"):
                if expr:
                    title = re.sub(re.escape(expr), "", title, flags=re.IGNORECASE)
            title = title.lstrip(": ").strip()
            # Remove espaços múltiplos gerados pelas remoções
            title = " ".join(title.split())
            # Remoção de emojis se toggle ativo
            if self._config.get_bool("title_strip_emojis"):
                title = _strip_emojis(title)
            return title

        return ""

    def get_display_title(self, stream: dict) -> str:
        order   = self._config.get_list("title_components_order")
        enabled = set(self._config.get_list("title_components_enabled"))
        use_brackets = self._config.get_bool("title_use_brackets")

        # Garante que "title" está sempre presente
        if "title" not in order:
            order.append("title")
        if "title" not in enabled:
            enabled.add("title")

        parts = []
        for comp in order:
            if comp not in enabled:
                continue
            val = self._get_component_value(comp, stream, use_brackets)
            if val:
                parts.append(val)

        return " ".join(parts) if parts else (stream.get("title") or "Sem titulo")

    def get_display_category(self, cat_id: str | None, db: dict) -> str:
        if not cat_id:
            return ""
        mappings = self._config.get_mapping("category_mappings")
        raw = db.get(str(cat_id), str(cat_id))
        return mappings.get(raw, raw)

    def filter_streams(self, streams: list, mode: str) -> list:
        if mode == "upcoming":
            max_hours  = self._config.get_int("max_schedule_hours")
            max_per_ch = self._config.get_int("max_upcoming_per_channel")
            cutoff     = datetime.now(timezone.utc) + timedelta(hours=max_hours)
            now        = datetime.now(timezone.utc)
            candidates = [
                s for s in streams
                if s.get("status") == "upcoming"
                and isinstance(s.get("scheduledstarttimeutc"), datetime)
                and now < s["scheduledstarttimeutc"] <= cutoff
            ]
            per_channel = defaultdict(list)
            for s in candidates:
                per_channel[s.get("channelid", "")].append(s)
            result = []
            for ch_streams in per_channel.values():
                ch_streams.sort(
                    key=lambda x: x.get("scheduledstarttimeutc")
                    or datetime.min.replace(tzinfo=timezone.utc)
                )
                result.extend(ch_streams[:max_per_ch])
            logger.debug(f"filter_streams [upcoming]: {len(candidates)} candidatos -> {len(result)} apos max_per_ch")
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
        if mode == "upcoming" and mode_type == "direct":
            raise ValueError("upcoming nunca pode ser modo direct")

        if mode == "live":
            filtered = [s for s in streams if ContentGenerator.is_live(s)]
            filtered = self.filter_streams(filtered, "live")

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

            candidates = []
            for s in streams:
                if not ContentGenerator.is_vod(s):
                    continue
                if s.get("vod_unavailable"):
                    logger.debug(f"M3U [vod]: {s.get('videoid')} excluído (vod_unavailable=True)")
                    continue
                ft = s.get("fetchtime")
                et = s.get("actualendtimeutc")
                ref_time = et if isinstance(et, datetime) else (ft if isinstance(ft, datetime) else None)
                if ref_time is None or ref_time >= cutoff:
                    candidates.append(s)

            per_channel = defaultdict(list)
            for s in candidates:
                per_channel[s.get("channelid", "")].append(s)
            filtered = []
            for ch_streams in per_channel.values():
                ch_streams.sort(
                    key=lambda x: (
                        x.get("actualendtimeutc")
                        or x.get("fetchtime")
                        or datetime.min.replace(tzinfo=timezone.utc)
                    ),
                    reverse=True
                )
                filtered.extend(ch_streams[:max_per_ch])
            logger.debug(f"M3U [vod]: {len(candidates)} candidatos -> {len(filtered)} apos max_per_ch")
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
                url = f"{base_url}/api/proxy/{vid}" if base_url else f"/api/proxy/{vid}"
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
            placeholder_id  = f"PLACEHOLDER_{mode.upper()}"
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
            if not start_dt:
                continue

            actual_end = s.get("actualendtimeutc")
            if actual_end:
                stop_dt = actual_end
            else:
                title_check = (s.get("title") or "").lower()
                sport_keywords = (
                    "jogo", "partida", "futebol", "basquete", "judo", "grand slam",
                    "semifinal", "final", "copa", "campeonato", "ao vivo", "live",
                )
                if any(kw in title_check for kw in sport_keywords):
                    stop_dt = start_dt + timedelta(hours=3)
                else:
                    stop_dt = start_dt + timedelta(hours=2)

            fmt       = "%Y%m%d%H%M%S %z"
            start_str = start_dt.strftime(fmt)
            end_str   = stop_dt.strftime(fmt)

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
