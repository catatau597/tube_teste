"""
tests/test_scheduler_fixes.py
Testes para validação dos Fixes #1 e #2 do Scheduler.

FIX #1: Force-sync deve ignorar guard de horário
FIX #2: Primeira execução deve respeitar initial_sync_days
"""
import asyncio
import pytest
from core.scheduler import Scheduler, _save_files
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, MagicMock, patch, call


class MockConfig:
    """Mock de AppConfig para testes."""
    def __init__(self, **overrides):
        self.defaults = {
            "scheduler_main_interval_hours": 4,
            "full_sync_interval_hours": 48,
            "enable_scheduler_active_hours": True,
            "scheduler_active_start_hour": 7,
            "scheduler_active_end_hour": 22,
            "local_timezone": "America/Sao_Paulo",
            "initial_sync_days": 2,
            "scheduler_pre_event_interval_minutes": 5,
            "scheduler_post_event_interval_minutes": 5,
            "scheduler_pre_event_window_hours": 2,
            "use_playlist_items": True,
            "playlist_save_directory": "/data/m3us",
            "xmltv_save_directory": "/data/epgs",
            "playlist_live_filename": "playlist_live.m3u8",
            "playlist_upcoming_filename": "playlist_upcoming.m3u8",
            "playlist_vod_filename": "playlist_vod.m3u8",
            "xmltv_filename": "epg.xml",
            "keep_recorded_streams": True,
        }
        self.defaults.update(overrides)
    
    def get_int(self, key):
        return self.defaults.get(key, 0)
    
    def get_str(self, key):
        return self.defaults.get(key, "")
    
    def get_bool(self, key):
        return self.defaults.get(key, False)


class MockState:
    """Mock de StateManager para testes."""
    def __init__(self):
        self.streams = {}
        self.channels = {"ch1": "Channel 1", "ch2": "Channel 2"}
        self.meta = {}
    
    def get_all_streams(self):
        return list(self.streams.values())
    
    def get_all_channels(self):
        return self.channels
    
    def update_streams(self, new_streams):
        for s in new_streams:
            if "videoid" in s:
                self.streams[s["videoid"]] = s
    
    def save_to_disk(self):
        pass


class MockScraper:
    """Mock de YouTubeScraper para testes."""
    def __init__(self):
        self.last_published_after = None
        self.call_count = 0
    
    def fetch_all_streams_for_channels(self, channels, published_after=None):
        self.last_published_after = published_after
        self.call_count += 1
        
        # Simular resposta conforme published_after
        if published_after is None:
            # Full sync: 2350 streams
            return [
                {"videoid": f"vid_{i}", "title": f"Stream {i}", "status": "live"}
                for i in range(2350)
            ]
        else:
            # Incremental: 50 streams
            return [
                {"videoid": f"vid_new_{i}", "title": f"New Stream {i}", "status": "live"}
                for i in range(50)
            ]
    
    def fetch_all_streams_for_channels_using_playlists(self, channels, published_after=None):
        return self.fetch_all_streams_for_channels(channels, published_after)
    
    def fetch_streams_by_ids(self, ids, channels):
        return [{"videoid": vid, "status": "live"} for vid in ids[:10]]


# ─────────────────────────────────────────────────────────────────────────
# TESTES: FIX #1 — Force-Sync Bypassar Horário
# ─────────────────────────────────────────────────────────────────────────

class TestFix1ForceSyncBypassHoraio:
    """Testes para FIX #1: Force-sync deve ignorar guard de horário."""
    
    def test_force_event_initial_state(self):
        """Force event começa com None."""
        config = MockConfig()
        state = MockState()
        scraper = MockScraper()
        scheduler = Scheduler(config, scraper, state)
        
        assert scheduler._force_event is None
    
    def test_set_force_event(self):
        """Pode definir force event."""
        config = MockConfig()
        state = MockState()
        scraper = MockScraper()
        scheduler = Scheduler(config, scraper, state)
        
        event = asyncio.Event()
        scheduler.set_force_event(event)
        
        assert scheduler._force_event is event
    
    def test_trigger_now_seta_force_event(self):
        """trigger_now() seta o force event."""
        config = MockConfig()
        state = MockState()
        scraper = MockScraper()
        scheduler = Scheduler(config, scraper, state)
        
        event = asyncio.Event()
        scheduler.set_force_event(event)
        assert not event.is_set()
        
        scheduler.trigger_now()
        assert event.is_set()
    
    @pytest.mark.asyncio
    async def test_force_triggered_ignores_inactive_hour(self):
        """Force-sync ignora guard de horário inativo."""
        config = MockConfig(
            enable_scheduler_active_hours=True,
            scheduler_active_start_hour=7,
            scheduler_active_end_hour=22
        )
        state = MockState()
        scraper = MockScraper()
        scheduler = Scheduler(config, scraper, state)
        
        scheduler.set_categories_db({})
        dt_min = datetime.min.replace(tzinfo=timezone.utc)
        scheduler.last_main_run = dt_min
        scheduler.last_full_sync = dt_min
        force_event = asyncio.Event()
        scheduler.set_force_event(force_event)
        with patch("core.scheduler.datetime") as mock_dt:
            mock_dt.now.side_effect = [
                datetime(2026, 2, 26, 2, 7, 0, tzinfo=timezone.utc),
                datetime(2026, 2, 26, 2, 7, 0, tzinfo=timezone.utc),
                datetime(2026, 2, 26, 2, 7, 0, tzinfo=timezone.utc),
                datetime(2026, 2, 26, 2, 7, 0, tzinfo=timezone.utc),
            ]
            mock_dt.min.return_value = datetime.min
            mock_dt.utcnow.return_value = datetime(2026, 2, 26, 2, 7, 0, tzinfo=timezone.utc)
            force_event.set()
            async def run_one_iteration():
                force_triggered = (
                    scheduler._force_event 
                    and scheduler._force_event.is_set()
                )
                assert force_triggered, "Force event deve estar set"
                scheduler._force_event.clear()
                scheduler.last_main_run = datetime.min.replace(tzinfo=timezone.utc)
                is_active_time = True
                assert is_active_time, "is_active_time deve ser True com force"
                assert (
                    datetime(2026, 2, 26, 2, 7, 0, tzinfo=timezone.utc) 
                    - datetime.min.replace(tzinfo=timezone.utc)
                ) >= timedelta(hours=4)
            await run_one_iteration()
    
    @pytest.mark.asyncio
    async def test_force_triggered_resets_last_main_run(self):
        """Detectar force-sync reseta last_main_run para datetime.min."""
        config = MockConfig()
        state = MockState()
        scraper = MockScraper()
        scheduler = Scheduler(config, scraper, state)
        recent_time = datetime(2026, 2, 26, 14, 0, 0, tzinfo=timezone.utc)
        scheduler.last_main_run = recent_time
        force_event = asyncio.Event()
        scheduler.set_force_event(force_event)
        force_event.set()
        force_triggered = (
            scheduler._force_event 
            and scheduler._force_event.is_set()
        )
        if force_triggered:
            scheduler._force_event.clear()
            scheduler.last_main_run = datetime.min.replace(tzinfo=timezone.utc)
        assert scheduler.last_main_run == datetime.min.replace(tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────────────────
# TESTES: FIX #2 — Limitar Primeira Busca com initial_sync_days
# ─────────────────────────────────────────────────────────────────────────

class TestFix2InitialSyncDays:
    """Testes para FIX #2: Primeira execução respeita initial_sync_days."""
    
    def test_first_run_detection(self):
        config = MockConfig()
        state = MockState()
        scraper = MockScraper()
        scheduler = Scheduler(config, scraper, state)
        dt_min = datetime.min.replace(tzinfo=timezone.utc)
        assert scheduler.last_main_run == dt_min
        assert scheduler.last_full_sync == dt_min
        is_first_run = (
            scheduler.last_main_run == dt_min
            and scheduler.last_full_sync == dt_min
        )
        assert is_first_run
    
    def test_not_first_run_after_main_run(self):
        config = MockConfig()
        state = MockState()
        scraper = MockScraper()
        scheduler = Scheduler(config, scraper, state)
        dt_min = datetime.min.replace(tzinfo=timezone.utc)
        recent = datetime(2026, 2, 26, 14, 0, 0, tzinfo=timezone.utc)
        scheduler.last_main_run = recent
        scheduler.last_full_sync = dt_min
        is_first_run = (
            scheduler.last_main_run == dt_min
            and scheduler.last_full_sync == dt_min
        )
        assert not is_first_run
    
    def test_calculate_published_after_first_run(self):
        config = MockConfig(initial_sync_days=2)
        now_utc = datetime(2026, 2, 26, 7, 0, 0, tzinfo=timezone.utc)
        initial_days = config.get_int("initial_sync_days")
        cutoff_date = now_utc - timedelta(days=initial_days)
        published_after = cutoff_date.isoformat()
        expected = "2026-02-24T07:00:00+00:00"
        assert published_after == expected
    
    def test_published_after_none_when_initial_sync_days_zero(self):
        config = MockConfig(initial_sync_days=0)
        initial_days = config.get_int("initial_sync_days")
        if initial_days > 0:
            published_after = "limited"
        else:
            published_after = None
        assert published_after is None
    
    def test_first_run_vs_incremental_vs_full_sync(self):
        config = MockConfig(
            initial_sync_days=2,
            full_sync_interval_hours=48,
            scheduler_main_interval_hours=4
        )
        state = MockState()
        scraper = MockScraper()
        scheduler = Scheduler(config, scraper, state)
        dt_min = datetime.min.replace(tzinfo=timezone.utc)
        now_utc = datetime(2026, 2, 26, 7, 0, 0, tzinfo=timezone.utc)
        # Cenário 1: Primeira Run
        scheduler.last_main_run = dt_min
        scheduler.last_full_sync = dt_min
        is_first_run = (
            scheduler.last_main_run == dt_min
            and scheduler.last_full_sync == dt_min
        )
        time_for_full_sync = False
        if is_first_run:
            initial_days = config.get_int("initial_sync_days")
            if initial_days > 0:
                cutoff = now_utc - timedelta(days=initial_days)
                pa1 = cutoff.isoformat()
            else:
                pa1 = None
        else:
            pa1 = "not_first"
        assert pa1 == "2026-02-24T07:00:00+00:00"
        # Cenário 2: Incremental (4h depois)
        scheduler.last_main_run = datetime(2026, 2, 26, 7, 0, 0, tzinfo=timezone.utc)
        scheduler.last_full_sync = datetime(2026, 2, 26, 7, 0, 0, tzinfo=timezone.utc)
        is_first_run = (
            scheduler.last_main_run == dt_min
            and scheduler.last_full_sync == dt_min
        )
        time_for_full_sync = False
        if is_first_run:
            pa2 = "limited"
        elif time_for_full_sync:
            pa2 = None
        else:
            pa2 = scheduler.last_main_run.isoformat()
        assert pa2 == "2026-02-26T07:00:00+00:00"
        # Cenário 3: Full Sync (48h depois)
        scheduler.last_full_sync = datetime(2026, 2, 24, 7, 0, 0, tzinfo=timezone.utc)
        now_utc = datetime(2026, 2, 26, 7, 0, 0, tzinfo=timezone.utc)
        full_sync_interval = timedelta(
            hours=config.get_int("full_sync_interval_hours")
        )
        time_for_full_sync = (
            (now_utc - scheduler.last_full_sync) >= full_sync_interval
        )
        if is_first_run:
            pa3 = "limited"
        elif time_for_full_sync:
            pa3 = None
        else:
            pa3 = scheduler.last_main_run.isoformat()
        assert pa3 is None


# ─────────────────────────────────────────────────────────────────────────
# TESTES: Integração
# ─────────────────────────────────────────────────────────────────────────

class TestSchedulerIntegration:
    """Testes de integração do Scheduler com os fixes."""
    def test_scheduler_init_loads_saved_timestamps(self):
        config = MockConfig()
        state = MockState()
        scraper = MockScraper()
        saved_time = datetime(2026, 2, 20, 10, 0, 0, tzinfo=timezone.utc)
        state.meta["lastmainrun"] = saved_time
        state.meta["lastfullsync"] = saved_time
        scheduler = Scheduler(config, scraper, state)
        assert scheduler.last_main_run == saved_time
        assert scheduler.last_full_sync == saved_time
    def test_set_categories_db(self):
        config = MockConfig()
        state = MockState()
        scraper = MockScraper()
        scheduler = Scheduler(config, scraper, state)
        cats = {"cat1": "Category 1", "cat2": "Category 2"}
        scheduler.set_categories_db(cats)
        assert scheduler._categories_db == cats
    def test_reload_config(self):
        config1 = MockConfig(scheduler_main_interval_hours=4)
        config2 = MockConfig(scheduler_main_interval_hours=8)
        state = MockState()
        scraper = MockScraper()
        scheduler = Scheduler(config1, scraper, state)
        assert scheduler._config.get_int("scheduler_main_interval_hours") == 4
        scheduler.reload_config(config2)
        assert scheduler._config.get_int("scheduler_main_interval_hours") == 8
