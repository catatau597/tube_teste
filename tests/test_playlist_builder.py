import pytest
from core.playlist_builder import M3UGenerator, XMLTVGenerator, ContentGenerator
from core.config import AppConfig
from pathlib import Path

def test_m3u_generator_instancia(tmp_path):
    cfg = AppConfig(db_path=tmp_path / "test.db")
    m3u = M3UGenerator(cfg)
    assert m3u.config is cfg

def test_xmltv_generator_instancia(tmp_path):
    cfg = AppConfig(db_path=tmp_path / "test.db")
    xml = XMLTVGenerator(cfg)
    assert xml.config is cfg

def test_content_generator_instancia(tmp_path):
    cfg = AppConfig(db_path=tmp_path / "test.db")
    cg = ContentGenerator(cfg)
    assert cg.config is cfg
