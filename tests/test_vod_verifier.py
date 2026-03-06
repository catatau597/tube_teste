"""
tests/test_vod_verifier.py
--------------------------
Testes unitários para core/vod_verifier.py
"""
import asyncio
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from core.config import AppConfig
from core.state_manager import StateManager
from core.vod_verifier import VodVerifier


@pytest.fixture
def cfg(tmp_path):
    return AppConfig(db_path=tmp_path / "test.db")


@pytest.fixture
def state(cfg):
    sm = StateManager(cfg, cache_path=Path("/tmp/test_state_cache.json"))
    return sm


@pytest.fixture
def scraper():
    mock = MagicMock()
    mock.check_vod_availability_batch.return_value = {}
    return mock


@pytest.fixture
def verifier(scraper, state, cfg):
    return VodVerifier(scraper, state, cfg)


# ── Configuração ────────────────────────────────────────────────────────────

def test_vod_verification_defaults(cfg):
    assert cfg.get_bool("vod_post_live_check_enabled") is True
    assert cfg.get_int("vod_post_live_initial_delay_seconds") == 120
    assert cfg.get_list("vod_post_live_retry_delays") == ["120", "300", "600"]
    assert cfg.get_bool("vod_health_check_enabled") is True
    assert cfg.get_int("vod_health_check_interval_minutes") == 60


def test_vod_verification_section_exists(cfg):
    sections = cfg.get_all_by_section()
    assert "vod_verification" in sections
    keys = {row["key"] for row in sections["vod_verification"]}
    assert "vod_post_live_check_enabled" in keys
    assert "vod_health_check_enabled" in keys


def test_vod_verifier_post_live_enabled(verifier):
    assert verifier.post_live_enabled is True


def test_vod_verifier_health_check_enabled(verifier):
    assert verifier.health_check_enabled is True


def test_vod_verifier_initial_delay(verifier):
    assert verifier.post_live_initial_delay == 120


def test_vod_verifier_retry_delays(verifier):
    assert verifier.post_live_retry_delays == [120, 300, 600]


def test_vod_verifier_health_interval(verifier):
    assert verifier.health_check_interval_seconds == 3600


# ── schedule_post_live_check ─────────────────────────────────────────────────

def test_schedule_post_live_check_disabled(verifier, cfg):
    cfg.update("vod_post_live_check_enabled", "false")
    # Não deve criar task quando desabilitado
    verifier.schedule_post_live_check("abc123")
    assert "abc123" not in verifier._post_live_tasks


def test_schedule_post_live_check_idempotent(verifier):
    """Não agenda o mesmo vídeo duas vezes."""
    verifier._checked_vods.add("abc123")
    verifier.schedule_post_live_check("abc123")
    assert "abc123" not in verifier._post_live_tasks


# ── _check_single_vod ────────────────────────────────────────────────────────

def test_check_single_vod_available(verifier, scraper):
    scraper.check_vod_availability_batch.return_value = {"vid1": True}
    result = verifier._check_single_vod("vid1")
    assert result is True
    scraper.check_vod_availability_batch.assert_called_once_with(["vid1"])


def test_check_single_vod_unavailable(verifier, scraper):
    scraper.check_vod_availability_batch.return_value = {"vid1": False}
    result = verifier._check_single_vod("vid1")
    assert result is False


def test_check_single_vod_not_returned(verifier, scraper):
    """Vídeo não retornado pela API → indisponível."""
    scraper.check_vod_availability_batch.return_value = {}
    result = verifier._check_single_vod("vid1")
    assert result is False


# ── _mark_unavailable / _mark_checked ────────────────────────────────────────

def test_mark_unavailable_sets_flag(verifier, state):
    state.streams["vid1"] = {"videoid": "vid1", "status": "vod"}
    verifier._mark_unavailable("vid1")
    assert state.streams["vid1"]["vod_unavailable"] is True
    assert "vid1" in verifier._checked_vods


def test_mark_checked_clears_flag(verifier, state):
    state.streams["vid1"] = {"videoid": "vid1", "status": "vod", "vod_unavailable": True}
    verifier._mark_checked("vid1")
    assert "vod_unavailable" not in state.streams["vid1"]
    assert "vid1" in verifier._checked_vods


# ── _run_health_check ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_check_marks_unavailable(verifier, state, scraper):
    state.streams["vid1"] = {"videoid": "vid1", "status": "vod"}
    state.streams["vid2"] = {"videoid": "vid2", "status": "vod"}
    scraper.check_vod_availability_batch.return_value = {"vid1": True, "vid2": False}

    await verifier._run_health_check()

    assert "vod_unavailable" not in state.streams["vid1"]
    assert state.streams["vid2"]["vod_unavailable"] is True


@pytest.mark.asyncio
async def test_health_check_recovers_vod(verifier, state, scraper):
    """VOD previamente marcado como indisponível é recuperado."""
    state.streams["vid1"] = {"videoid": "vid1", "status": "vod", "vod_unavailable": True}
    scraper.check_vod_availability_batch.return_value = {"vid1": True}

    await verifier._run_health_check()

    assert "vod_unavailable" not in state.streams["vid1"]


@pytest.mark.asyncio
async def test_health_check_empty_cache(verifier, state, scraper):
    """Health check não chama API se não há VODs no cache."""
    await verifier._run_health_check()
    scraper.check_vod_availability_batch.assert_not_called()


@pytest.mark.asyncio
async def test_health_check_skips_live_and_upcoming(verifier, state, scraper):
    """Health check ignora streams live e upcoming."""
    state.streams["vid_live"]     = {"videoid": "vid_live",     "status": "live"}
    state.streams["vid_upcoming"] = {"videoid": "vid_upcoming", "status": "upcoming"}
    state.streams["vid_vod"]      = {"videoid": "vid_vod",      "status": "vod"}
    scraper.check_vod_availability_batch.return_value = {"vid_vod": True}

    await verifier._run_health_check()

    call_args = scraper.check_vod_availability_batch.call_args[0][0]
    assert "vid_live" not in call_args
    assert "vid_upcoming" not in call_args
    assert "vid_vod" in call_args


# ── _post_live_check_task ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_live_check_success_first_attempt(verifier, state, scraper, cfg):
    """VOD disponível na primeira tentativa."""
    cfg.update("vod_post_live_initial_delay_seconds", "0")
    cfg.update("vod_post_live_retry_delays", "0,0,0")
    state.streams["vid1"] = {"videoid": "vid1", "status": "vod"}
    scraper.check_vod_availability_batch.return_value = {"vid1": True}

    await verifier._post_live_check_task("vid1")

    assert "vid1" in verifier._checked_vods
    assert "vod_unavailable" not in state.streams["vid1"]


@pytest.mark.asyncio
async def test_post_live_check_success_on_retry(verifier, state, scraper, cfg):
    """VOD disponível apenas no segundo retry."""
    cfg.update("vod_post_live_initial_delay_seconds", "0")
    cfg.update("vod_post_live_retry_delays", "0,0")
    state.streams["vid1"] = {"videoid": "vid1", "status": "vod"}
    # Falha nas 2 primeiras chamadas, sucesso na 3ª
    scraper.check_vod_availability_batch.side_effect = [
        {"vid1": False},
        {"vid1": False},
        {"vid1": True},
    ]

    await verifier._post_live_check_task("vid1")

    assert "vid1" in verifier._checked_vods
    assert "vod_unavailable" not in state.streams["vid1"]


@pytest.mark.asyncio
async def test_post_live_check_all_fail(verifier, state, scraper, cfg):
    """VOD indisponível em todas as tentativas → marcado como unavailable."""
    cfg.update("vod_post_live_initial_delay_seconds", "0")
    cfg.update("vod_post_live_retry_delays", "0,0")
    state.streams["vid1"] = {"videoid": "vid1", "status": "vod"}
    scraper.check_vod_availability_batch.return_value = {"vid1": False}

    await verifier._post_live_check_task("vid1")

    assert "vid1" in verifier._checked_vods
    assert state.streams["vid1"].get("vod_unavailable") is True
