"""
core/playlist_builder.py
------------------------
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

class M3UGenerator:
    def __init__(self, config: AppConfig):
        self.config = config
    # Métodos reais implementados na etapa posterior

class XMLTVGenerator:
    def __init__(self, config: AppConfig):
        self.config = config
    # Métodos reais implementados na etapa posterior

class ContentGenerator:
    def __init__(self, config: AppConfig):
        self.config = config
    # Métodos reais implementados na etapa posterior
