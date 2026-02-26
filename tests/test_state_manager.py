import pytest
from core.state_manager import StateManager
from core.config import AppConfig
from pathlib import Path

def test_state_manager_instancia(tmp_path):
    cfg = AppConfig(db_path=tmp_path / "test.db")
    sm = StateManager(cfg)
    assert sm.config is cfg
    assert isinstance(sm.cache_path, Path)
    assert isinstance(sm.streams, dict)
    assert isinstance(sm.channels, dict)
