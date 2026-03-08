from __future__ import annotations

import collections
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from core.config import AppConfig
from core.playlist_builder import M3UGenerator, XMLTVGenerator
from core.scheduler import Scheduler
from core.state_manager import StateManager
from core.thumbnail_manager import ThumbnailManager


@dataclass
class AppDeps:
    config: Optional[AppConfig] = None
    state: Optional[StateManager] = None
    scheduler: Optional[Scheduler] = None
    thumbnail_manager: Optional[ThumbnailManager] = None
    m3u_generator: Optional[M3UGenerator] = None
    xmltv_generator: Optional[XMLTVGenerator] = None
    categories_db: dict = field(default_factory=dict)

    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("TubeWrangler"))
    log_buffer: collections.deque[tuple[int, str]] = field(
        default_factory=lambda: collections.deque(maxlen=1000)
    )
    setup_logging: Optional[Callable[[str, bool], None]] = None

    playlist_routes: dict[str, tuple[str, str]] = field(default_factory=dict)
    legacy_redirects: dict[str, str] = field(default_factory=dict)

    get_base_url: Optional[Callable] = None
