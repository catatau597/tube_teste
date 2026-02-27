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
        if not hasattr(self, 'streams') or self.streams is None:
            return []
        streams = self.streams
        if isinstance(streams, dict):
            result = []
            for channel_id, data in streams.items():
                if isinstance(data, dict) and 'streams' in data:
                    result.extend(data['streams'])
                elif isinstance(data, list):
                    result.extend(data)
            return result
        if isinstance(streams, list):
            return streams
        return []

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
        cache_file = Path("/data") / self._config.get_str("state_cache_filename")
        if cache_file.exists():
            try:
                with open(cache_file, encoding="utf-8") as f:
                    self.streams = json.load(f)
            except (json.JSONDecodeError, OSError):
                self.streams = {}
        else:
            self.streams = {}

    def save_to_disk(self):
        """Persiste estado no arquivo JSON em /data/."""
        import json
        from pathlib import Path
        cache_file = Path("/data") / self._config.get_str("state_cache_filename")
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(self.streams, f, ensure_ascii=False, indent=2)

    def update_channels(self, channels_data: dict):
        for cid, title in channels_data.items():
            if cid and title:
                self.channels[cid] = title
