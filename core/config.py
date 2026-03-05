"""
core/config.py
--------------
Responsabilidade: Única fonte de verdade para configurações da aplicação.
Substitui completamente o arquivo .env e todos os os.getenv() do projeto.
Depende de: fastlite (SQLite)
NÃO depende de: Flask, FastHTML, os.getenv, python-dotenv

ATENÇÃO: fastlite retorna rows como dicionários.
Sempre acessar como row["key"], NUNCA como row.key
"""

from pathlib import Path
from fastlite import database

DB_PATH = Path("/data/config.db")

_OBSOLETE_KEYS = [
    "playlist_save_directory",
    "playlist_live_filename",
    "playlist_upcoming_filename",
    "playlist_vod_filename",
    "xmltv_save_directory",
    "xmltv_filename",
    "generate_direct_playlists",
    "generate_proxy_playlists",
    "log_to_file",
    "smart_player_log_level",
    "smart_player_log_to_file",
    "local_timezone",
    "youtube_api_key",  # migrado para youtube_api_keys (lista)
    # Migradas para title_format
    "prefix_title_with_channel_name",
    "prefix_title_with_status",
]

# Todas as variáveis do projeto.
# Formato: "chave": ("default", "seção", "descrição", "tipo")
# tipos: "str" | "int" | "bool" | "list" | "mapping"
DEFAULTS: dict = {
    # --- Credenciais ---
    "youtube_api_keys":              ("", "credentials", "Chaves de API do YouTube (vírgula para múltiplas)", "list"),
    "target_channel_handles":        ("", "credentials", "Handles de canais separados por vírgula", "list"),
    "target_channel_ids":            ("", "credentials", "IDs diretos de canais separados por vírgula", "list"),

    # --- Agendador ---
    "scheduler_main_interval_hours":         ("4",  "scheduler", "Intervalo principal em horas", "int"),
    "scheduler_pre_event_window_hours":      ("2",  "scheduler", "Janela pré-evento em horas", "int"),
    "scheduler_pre_event_interval_minutes":  ("5",  "scheduler", "Intervalo pré-evento em minutos", "int"),
    "scheduler_post_event_interval_minutes": ("5",  "scheduler", "Intervalo pós-evento em minutos", "int"),
    "enable_scheduler_active_hours":         ("true", "scheduler", "Ativar horário de atividade", "bool"),
    "scheduler_active_start_hour":           ("7",  "scheduler", "Hora de início (formato 24h)", "int"),
    "scheduler_active_end_hour":             ("22", "scheduler", "Hora de fim (formato 24h)", "int"),
    "full_sync_interval_hours":              ("48", "scheduler", "Intervalo de full sync em horas", "int"),
    "resolve_handles_ttl_hours":             ("24", "scheduler", "TTL cache de handles em horas", "int"),
    "initial_sync_days":                     ("2",  "scheduler", "Dias para busca inicial (0=tudo)", "int"),

    # --- Filtros gerais ---
    "max_schedule_hours":            ("72",  "filters", "Limite futuro em horas para agendamentos", "int"),
    "max_upcoming_per_channel":      ("6",   "filters", "Máximo de agendamentos futuros por canal", "int"),
    "title_filter_expressions":      (
        "ao vivo,AO VIVO,AO VIVO E COM IMAGRENS,ao vivo e com imagens,com imagens,"
        "COM IMAGRENS,cortes,react,ge.globo,#live,!,:,ge tv,JOGO COMPLETO",
        "filters", "Expressões a remover dos títulos (vírgula)", "list"),
    "epg_description_cleanup":        ("true", "filters", "Manter apenas primeiro parágrafo da descrição EPG", "bool"),
    "keep_recorded_streams":          ("true", "filters", "Manter streams gravados (ex-live) no cache", "bool"),
    "max_recorded_per_channel":       ("2",   "filters", "Máximo de gravações mantidas por canal", "int"),
    "recorded_retention_days":        ("2",   "filters", "Dias de retenção de streams gravados", "int"),

    # --- Filtros de categoria ---
    "filter_by_category":            ("true",  "filters", "Filtrar streams por categoria da API YouTube", "bool"),
    "allowed_category_ids":          ("17,22", "filters", "IDs de categoria permitidos (vírgula)", "list"),
    "category_mappings":             (
        "17|ESPORTES,20|JOGOS,22|ESPORTES,25|NOTÍCIAS",
        "filters", "Renomear categorias para exibição: ID|Nome (vírgula) — NÃO filtra", "mapping"),
    "channel_name_mappings":         (
        "FAF TV | @fafalagoas|FAF TV,Canal GOAT|GOAT,"
        "Federação de Futebol de Mato Grosso do Sul|FFMS,"
        "Federação Paranaense de Futebol|FPF TV,"
        "Federação Catarinense de Futebol|FCF TV,"
        "Jovem Pan Esportes|J. Pan Esportes,TNT Sports Brasil|TNT Sports",
        "filters", "Mapeamento nomes canais Longo|Curto (vírgula)", "mapping"),

    # --- Filtros de Shorts ---
    "shorts_max_duration_s":         ("62",       "filters", "Duracão máxima (s) para bloquear Shorts (0=off)", "int"),
    "shorts_block_words":            ("#shorts,#short", "filters", "Palavras no título/tags que identificam Shorts", "list"),

    # --- Formato de Título ---
    # Ordem dos componentes separada por vírgula: channel, status, title
    "title_components_order":        ("channel,status,title", "title_format", "Ordem dos componentes do título (vírgula)", "list"),
    # Quais componentes estão habilitados (vírgula)
    "title_components_enabled":      ("channel,status,title",  "title_format", "Componentes habilitados (vírgula)", "list"),
    # Usar marcadores [ ] ao redor de channel e status
    "title_use_brackets":            ("true", "title_format", "Usar marcadores [ ] nos componentes de prefixo", "bool"),

    # --- Saída ---
    "placeholder_image_url":         (
        "https://i.ibb.co/9kZStw28/placeholder-sports.png",
        "output", "URL da imagem placeholder para streams sem thumb", "str"),
    "use_invisible_placeholder":     ("true", "output", "Usar placeholder invisível no M3U", "bool"),
    "thumbnail_cache_directory":     ("/data/thumbnails", "output", "Diretório de cache de thumbnails", "str"),

    # --- Técnico ---
    "http_port":                     ("8888",             "technical", "Porta HTTP do servidor web", "int"),
    "state_cache_filename":          ("state_cache.json", "technical", "Nome do arquivo JSON de estado", "str"),
    "stale_hours":                   ("6",                "technical", "Horas para considerar stream stale", "int"),
    "use_playlist_items":            ("true",             "technical", "Usar playlistItems API (vs search.list)", "bool"),
    "proxy_base_url":                ("", "technical", "URL base para playlists proxy", "str"),

    # --- Logs ---
    "log_level":                     ("DEBUG", "logging", "Nível de log do core (DEBUG/INFO/WARNING/ERROR)", "str"),
    "hide_access_logs":              ("true",  "logging", "Ocultar logs de acesso HTTP (uvicorn.access GET /)", "bool"),
}


class AppConfig:
    """
    Gerenciador de configuração persistente em SQLite via fastlite.

    IMPORTANTE: fastlite retorna rows como dicionários.
    Sempre usar row["key"], NUNCA row.key
    """

    def __init__(self, db_path: Path = DB_PATH):
        self._db = database(db_path)
        self._ensure_table()
        self._migrate_api_key()
        self._migrate_prefix_keys()
        self._cleanup_obsolete_keys()
        self._cache: dict = {}
        self.reload()

    def _ensure_table(self):
        if "config" not in self._db.t:
            self._db.t.config.create(
                key=str, value=str, section=str,
                description=str, value_type=str,
                pk="key"
            )
        existing = {row["key"] for row in self._db.t.config.rows}
        for key, (default_val, section, desc, vtype) in DEFAULTS.items():
            if key not in existing:
                self._db.t.config.insert({
                    "key": key,
                    "value": default_val,
                    "section": section,
                    "description": desc,
                    "value_type": vtype,
                })

    def _migrate_api_key(self):
        """Migra youtube_api_key (str) para youtube_api_keys (list) se ainda existir no banco."""
        try:
            rows = list(self._db.t.config.rows_where("key = ?", ["youtube_api_key"]))
            if rows:
                old_value = rows[0]["value"]
                if old_value:
                    new_rows = list(self._db.t.config.rows_where("key = ?", ["youtube_api_keys"]))
                    if new_rows and not new_rows[0]["value"]:
                        self._db.t.config.update({"key": "youtube_api_keys", "value": old_value})
        except Exception:
            pass

    def _migrate_prefix_keys(self):
        """
        Migra prefix_title_with_channel_name e prefix_title_with_status
        (seção filters) para os novos campos title_components_enabled/order
        (seção title_format), se ainda existirem no banco.
        """
        try:
            ch_rows = list(self._db.t.config.rows_where("key = ?", ["prefix_title_with_channel_name"]))
            st_rows = list(self._db.t.config.rows_where("key = ?", ["prefix_title_with_status"]))
            if not ch_rows and not st_rows:
                return  # já migrado ou nunca existiu

            ch_on = ch_rows[0]["value"].lower() == "true" if ch_rows else True
            st_on = st_rows[0]["value"].lower() == "true" if st_rows else True

            enabled_components = []
            if ch_on:
                enabled_components.append("channel")
            if st_on:
                enabled_components.append("status")
            enabled_components.append("title")  # título sempre presente

            # Só atualiza title_components_enabled se ainda está no default
            cur_enabled = list(self._db.t.config.rows_where("key = ?", ["title_components_enabled"]))
            if cur_enabled and cur_enabled[0]["value"] == "channel,status,title":
                new_val = ",".join(enabled_components)
                self._db.t.config.update({"key": "title_components_enabled", "value": new_val})
        except Exception:
            pass

    def _cleanup_obsolete_keys(self):
        """Remove configs obsoletos do SQLite se existirem."""
        for key in _OBSOLETE_KEYS:
            try:
                self._db.t.config.delete_where("key = ?", [key])
            except Exception:
                pass

    def reload(self):
        """Recarrega todas as configs do banco. Chamar após POST /config."""
        self._cache = {row["key"]: row for row in self._db.t.config.rows}

    def get_raw(self, key: str) -> str:
        if key in self._cache:
            return self._cache[key]["value"]
        return DEFAULTS.get(key, ("",))[0]

    def get_str(self, key: str) -> str:
        return self.get_raw(key)

    def get_int(self, key: str) -> int:
        try:
            return int(self.get_raw(key))
        except (ValueError, TypeError):
            return 0

    def get_bool(self, key: str) -> bool:
        return self.get_raw(key).lower() == "true"

    def get_list(self, key: str) -> list:
        raw = self.get_raw(key)
        return [x.strip() for x in raw.split(",") if x.strip()]

    def get_mapping(self, key: str) -> dict:
        raw = self.get_raw(key)
        result = {}
        for item in raw.split(","):
            if "|" in item:
                k, v = item.rsplit("|", 1)
                result[k.strip()] = v.strip()
        return result

    def update(self, key: str, value: str):
        """Atualiza chave no banco e no cache. Efeito imediato."""
        if key not in self._cache:
            raise KeyError(f"Chave de configuração desconhecida: '{key}'")
        self._db.t.config.update({"key": key, "value": str(value)})
        self._cache[key]["value"] = str(value)

    def update_many(self, updates: dict):
        """Atualiza múltiplas chaves. Útil para POST /config."""
        for key, value in updates.items():
            try:
                self.update(key, str(value))
            except KeyError:
                pass

    def get_all_by_section(self) -> dict:
        """Retorna configurações agrupadas por seção. Usado pelo formulário web."""
        sections: dict = {}
        for row in self._cache.values():
            sections.setdefault(row["section"], []).append(row)
        return sections

    def get_all(self) -> dict:
        result = {}
        for row in self._db.t.config.rows:
            result[row["key"]] = row["value"]
        return result

    def set(self, key: str, value: str) -> None:
        existing = list(self._db.t.config.rows_where("key = ?", [key]))
        if existing:
            self._db.t.config.update({"key": key, "value": value})
        else:
            self._db.t.config.insert({"key": key, "value": value})
        self.reload()

    def import_from_env_file(self, env_path: Path):
        """
        Migração única: importa valores de um .env para o SQLite.
        Mapeia UPPER_SNAKE_CASE → lower_snake_case automaticamente.
        Usar apenas via scripts/migrate_env.py
        """
        if not env_path.exists():
            print(f"AVISO: {env_path} não encontrado.")
            return
        mapping = {k.upper(): k for k in DEFAULTS.keys()}
        imported = 0
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                lower_key = mapping.get(k.upper())
                if lower_key:
                    try:
                        self.update(lower_key, v)
                        imported += 1
                    except KeyError:
                        pass
        print(f"✅ {imported} valores importados de {env_path}")
