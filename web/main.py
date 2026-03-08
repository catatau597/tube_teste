from __future__ import annotations

import asyncio
import collections
import logging
import re
from contextlib import asynccontextmanager

from fasthtml.common import *

from core.config import AppConfig
from core.playlist_builder import M3UGenerator, XMLTVGenerator
from core.proxy_manager import _processes, set_debug_mode, stop_stream
from core.scheduler import Scheduler
from core.state_manager import StateManager
from core.thumbnail_manager import ThumbnailManager
from core.vod_verifier import VodVerifier
from core.youtube_api import YouTubeAPI
from web.app_deps import AppDeps
from web.routes.config import register_config_routes
from web.routes.content_api import register_content_api_routes
from web.routes.dashboard import register_dashboard_routes
from web.routes.logs import register_logs_routes
from web.routes.playlists import register_playlists_routes
from web.routes.streaming import register_streaming_routes

_LOG_BUFFER: collections.deque[tuple[int, str]] = collections.deque(maxlen=1000)
_LOG_SEQ = 0


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        global _LOG_SEQ
        try:
            _LOG_SEQ += 1
            _LOG_BUFFER.append((_LOG_SEQ, self.format(record)))
        except Exception:
            pass


_LOG_FMT = logging.Formatter(
    "%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_buffer_handler = _BufferHandler()
_buffer_handler.setFormatter(_LOG_FMT)


class _AccessLogFilter(logging.Filter):
    _SUPPRESS_PATTERNS = [
        re.compile(r'"GET / HTTP'),
        re.compile(r'"GET /api/logs/stream HTTP'),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(p.search(msg) for p in self._SUPPRESS_PATTERNS)


_access_filter = _AccessLogFilter()


def _setup_logging(level_str: str = "INFO", hide_access: bool = True) -> None:
    level = getattr(logging, level_str.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    for h in root.handlers[:]:
        if isinstance(h, (logging.FileHandler, logging.StreamHandler)):
            root.removeHandler(h)
    console = logging.StreamHandler()
    console.setFormatter(_LOG_FMT)
    root.addHandler(_buffer_handler)
    root.addHandler(console)
    access = logging.getLogger("uvicorn.access")
    access.handlers = [_buffer_handler, console]
    access.setLevel(level)
    access.propagate = False
    if hide_access:
        if _access_filter not in access.filters:
            access.addFilter(_access_filter)
    else:
        try:
            access.removeFilter(_access_filter)
        except Exception:
            pass


logger = logging.getLogger("TubeWrangler")

_PLAYLIST_ROUTES = {
    "live.m3u": ("live", "direct"),
    "live-proxy.m3u": ("live", "proxy"),
    "upcoming-proxy.m3u": ("upcoming", "proxy"),
    "vod.m3u": ("vod", "direct"),
    "vod-proxy.m3u": ("vod", "proxy"),
}

_LEGACY_REDIRECTS = {
    "/playlist_live_direct.m3u8": "/playlist/live.m3u",
    "/playlist_live_proxy.m3u8": "/playlist/live-proxy.m3u",
    "/playlist_vod_direct.m3u8": "/playlist/vod.m3u",
    "/playlist_vod_proxy.m3u8": "/playlist/vod-proxy.m3u",
    "/youtube_epg.xml": "/epg.xml",
    "/proxy": "/playlist",
}


deps = AppDeps(
    logger=logger,
    log_buffer=_LOG_BUFFER,
    setup_logging=_setup_logging,
    playlist_routes=_PLAYLIST_ROUTES,
    legacy_redirects=_LEGACY_REDIRECTS,
)


@asynccontextmanager
async def lifespan(app):
    cfg = AppConfig()
    hide_access = cfg.get_bool("hide_access_logs")
    _setup_logging(cfg.get_str("log_level") or "INFO", hide_access=hide_access)
    logger.info("=== TubeWrangler iniciando ===")

    streaming_debug = cfg.get_bool("streaming_debug_enabled")
    set_debug_mode(streaming_debug)

    state = StateManager(cfg)
    cache_loaded = state.load_from_disk()
    logger.info(
        f"Cache disco: {cache_loaded} | "
        f"canais={len(state.get_all_channels())} "
        f"streams={len(state.get_all_streams())}"
    )

    thumb_dir = cfg.get_str("thumbnail_cache_directory")
    thumbnail_manager = ThumbnailManager(thumb_dir)
    state.set_thumbnail_manager(thumbnail_manager)

    api_keys = cfg.get_list("youtube_api_keys")
    handles = [h.strip() for h in cfg.get_str("target_channel_handles").split(",") if h.strip()]
    chan_ids = [i.strip() for i in cfg.get_str("target_channel_ids").split(",") if i.strip()]
    logger.info(f"Handles configurados : {handles}")
    logger.info(f"IDs configurados     : {chan_ids}")

    scraper = YouTubeAPI(api_keys)

    all_target_ids = set(chan_ids)
    if handles:
        logger.info(f"Resolvendo {len(handles)} handle(s) via API...")
        resolved = scraper.resolve_channel_handles_to_ids(handles, state)
        all_target_ids.update(resolved.keys())
        logger.info(f"Handles resolvidos: {resolved}")

    if all_target_ids:
        final_channels = scraper.ensure_channel_titles(all_target_ids, state)
        logger.info(f"Canais prontos: {len(final_channels)}")
    else:
        logger.warning("Nenhum canal alvo. Verifique /canais ou /config/credentials.")

    force_event = asyncio.Event()
    scheduler = Scheduler(cfg, scraper, state)
    scheduler.set_force_event(force_event)
    scheduler.set_thumbnail_manager(thumbnail_manager)
    m3u_generator = M3UGenerator(cfg)
    xmltv_generator = XMLTVGenerator(cfg)
    categories_db: dict = {}
    scheduler.set_categories_db(categories_db)

    vod_verifier = VodVerifier(scraper, state, cfg)
    scheduler.set_vod_verifier(vod_verifier)

    deps.config = cfg
    deps.state = state
    deps.scheduler = scheduler
    deps.thumbnail_manager = thumbnail_manager
    deps.m3u_generator = m3u_generator
    deps.xmltv_generator = xmltv_generator
    deps.categories_db = categories_db

    task = asyncio.create_task(scheduler.run())
    health_check_task = asyncio.create_task(vod_verifier.run_health_check_loop())
    logger.info("Scheduler e VodVerifier iniciados.")

    try:
        yield
    finally:
        task.cancel()
        health_check_task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        try:
            await health_check_task
        except asyncio.CancelledError:
            pass
        for vid in list(_processes.keys()):
            stop_stream(vid)
        if deps.state:
            deps.state.save_to_disk()
        logger.info("=== TubeWrangler encerrado ===")


app, _ = fast_app(
    lifespan=lifespan,
    hdrs=[],
)


register_dashboard_routes(app, deps)
register_config_routes(app, deps)
register_logs_routes(app, deps)
register_playlists_routes(app, deps)
register_content_api_routes(app, deps)
register_streaming_routes(app, deps)
