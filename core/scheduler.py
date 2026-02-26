"""
core/scheduler.py
-----------------
Responsabilidade: Orquestrar agendamento, sincronização e execução periódica.
Depende de: AppConfig, YouTubeAPI, StateManager
NÃO depende de: Flask, FastHTML, os.getenv

Exemplo de uso:
    from core.config import AppConfig
    from core.youtube_api import YouTubeAPI
    from core.state_manager import StateManager
    cfg = AppConfig(db_path="/tmp/test.db")
    yt = YouTubeAPI(api_key="dummy")
    sm = StateManager(cfg)
    sched = Scheduler(cfg, yt, sm)
    sched.reload_config(cfg)
"""
from core.config import AppConfig
from core.youtube_api import YouTubeAPI
from core.state_manager import StateManager

class Scheduler:
    def __init__(self, config: AppConfig, scraper: YouTubeAPI, state: StateManager):
        self.config = config
        self.scraper = scraper
        self.state = state
        import asyncio
        self._trigger_event = asyncio.Event()

    def trigger_now(self):
        """Dispara uma sincronização imediata fora do ciclo agendado."""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(self._trigger_event.set)
        except RuntimeError:
            self._trigger_event = asyncio.Event()
    def reload_config(self, new_config: AppConfig):
        self.config = new_config
    async def run(self, initial_run_delay: bool = False):
        pass  # Implementação real na etapa posterior
