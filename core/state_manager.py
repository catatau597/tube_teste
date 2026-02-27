"""
core/state_manager.py
---------------------
Responsabilidade: Gerenciar estado dos streams, canais e cache persistente.
Depende de: AppConfig
NÃO depende de: Flask, FastHTML, os.getenv

Exemplo de uso:
    from core.config import AppConfig
    cfg = AppConfig(db_path="/tmp/test.db")
    sm = StateManager(cfg)
    sm.streams["canal1"] = {"status": "online"}
    print(sm.cache_path)
"""
from pathlib import Path
from core.config import AppConfig

class StateManager:
    def get_all_streams(self) -> list:
        """Retorna todos os streams do estado em memória."""
        if not hasattr(self, 'streams') or not self.streams:
            return []
        return list(self.streams.values())

    def get_all_channels(self) -> dict:
        """Retorna lista de canais monitorados."""
        if not hasattr(self, 'streams') or self.streams is None:
            return {}
        if isinstance(self.streams, dict):
            return self.channels          # dict {id: title} — NÃO lista
        return {}
    def __init__(self, config: AppConfig, cache_path: Path | None = None):
        # Se cache_path for None:
        # cache_path = Path("/data") / config.get_str("state_cache_filename")
        self._config   = config
        self.streams   = {}
        self.channels  = {}          # {channel_id: channel_title}
        self.meta      = {           # metadados do scheduler
            "lastmainrun":      None,
            "lastfullsync":     None,
            "resolvedhandles":  {},
        }
        if cache_path:
            self.cache_path = cache_path
        else:
            self.cache_path = Path("/data") / config.get_str("state_cache_filename")

    # Métodos reais implementados na etapa posterior

    def load_from_disk(self):
        """Carrega estado do arquivo JSON em /data/."""
        import json
        from pathlib import Path
        from datetime import datetime
        DATETIME_FIELDS = {
            "scheduledstarttimeutc",
            "actualstarttimeutc",
            "actualendtimeutc",
            "fetchtime",
        }
        def parse_stream(stream: dict) -> dict:
            for field in DATETIME_FIELDS:
                val = stream.get(field)
                if isinstance(val, str):
                    try:
                        stream[field] = datetime.fromisoformat(val)
                    except (ValueError, TypeError):
                        stream[field] = None
            return stream
        cache_file = Path("/data") / self._config.get_str("state_cache_filename")
        import logging
        logger = logging.getLogger("TubeWrangler")
        try:
            if cache_file.exists():
                with open(cache_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self.streams = {
                    vid: parse_stream(s)
                    for vid, s in raw.items()
                    if isinstance(s, dict)
                }
                logger.info(
                    f"Cache carregado do disco: "
                    f"{cache_file.name} | streams={len(self.streams)}"
                )
            else:
                self.streams = {}
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"StateManager: erro ao carregar cache: {e}")
            self.streams = {}

    def save_to_disk(self):
        """Persiste estado no arquivo JSON em /data/."""
        import json
        from pathlib import Path
        from datetime import datetime
        cache_file = Path("/data") / self._config.get_str("state_cache_filename")
        def default_serializer(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Tipo não serializável: {type(obj)}")
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(
                    self.streams,
                    f,
                    ensure_ascii=False,
                    indent=2,
                    default=default_serializer
                )
        except IOError as e:
            logger.error(f"StateManager: erro ao salvar cache: {e}")

    def update_channels(self, channels_data: dict):
        for cid, title in channels_data.items():
            if cid and title:
                self.channels[cid] = title

    def update_streams(self, new_streams: list):
        added = 0
        updated = 0
        for stream in new_streams:
            vid = stream.get("videoid")
            if not vid:
                continue
            if vid in self.streams:
                self.streams[vid].update(stream)
                updated += 1
            else:
                self.streams[vid] = stream
                added += 1
        import logging
        logger = logging.getLogger("TubeWrangler")
        logger.info(f"Update Streams: Adicionados {added}, Atualizados {updated}")
