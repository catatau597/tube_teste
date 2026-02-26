import pytest
from core.scheduler import Scheduler
from core.config import AppConfig
from core.youtube_api import YouTubeAPI
from core.state_manager import StateManager
from pathlib import Path

def test_scheduler_instancia(tmp_path):
    cfg = AppConfig(db_path=tmp_path / "test.db")
    yt = YouTubeAPI(api_key="dummy")
    sm = StateManager(cfg)
    sched = Scheduler(cfg, yt, sm)
    assert sched.config is cfg
    assert sched.scraper is yt
    assert sched.state is sm
    sched.reload_config(cfg)
    assert sched.config is cfg
