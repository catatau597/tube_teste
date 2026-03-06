"""
core/vod_verifier.py
--------------------
Responsabilidade: Verificação de disponibilidade de VODs do YouTube.

Funcionalidade 1 – Verificação Pós-Live:
  Após uma live terminar (status live→vod), aguarda um delay inicial e então
  verifica via YouTube API se o VOD está disponível. Em caso de falha, realiza
  retries com backoff exponencial. Após todas as tentativas, marca o vídeo
  como indisponível no estado.

Funcionalidade 2 – Health Check Periódico:
  Worker em background que verifica periodicamente se os VODs no cache ainda
  estão acessíveis. Verifica streams com status "vod" ou "recorded".
  VODs indisponíveis são marcados e removidos das playlists geradas.

Depende de: YouTubeAPI, StateManager, AppConfig
NÃO depende de: Flask, FastHTML, os.getenv
"""
import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.youtube_api import YouTubeAPI
    from core.state_manager import StateManager
    from core.config import AppConfig

logger = logging.getLogger("TubeWrangler")


class VodVerifier:
    """
    Gerencia verificação de disponibilidade de VODs via YouTube Data API v3.

    Uso:
        verifier = VodVerifier(scraper, state, config)
        # Inicia health check em background:
        asyncio.create_task(verifier.run_health_check_loop())
        # Agenda verificação pós-live:
        verifier.schedule_post_live_check("video_id_xyz")
    """

    def __init__(self, scraper: "YouTubeAPI", state: "StateManager", config: "AppConfig"):
        self._scraper = scraper
        self._state = state
        self._config = config
        # IDs já processados pelo post-live check (não repetir)
        self._checked_vods: set = set()
        # Tasks de post-live em andamento {video_id: Task}
        self._post_live_tasks: dict = {}

    # ── Propriedades de configuração ───────────────────────────────────

    @property
    def post_live_enabled(self) -> bool:
        return self._config.get_bool("vod_post_live_check_enabled")

    @property
    def health_check_enabled(self) -> bool:
        return self._config.get_bool("vod_health_check_enabled")

    @property
    def post_live_initial_delay(self) -> int:
        return self._config.get_int("vod_post_live_initial_delay_seconds")

    @property
    def post_live_retry_delays(self) -> list:
        return [int(x) for x in self._config.get_list("vod_post_live_retry_delays") if x.strip().isdigit()]

    @property
    def health_check_interval_seconds(self) -> int:
        return self._config.get_int("vod_health_check_interval_minutes") * 60

    # ── Verificação Pós-Live ────────────────────────────────────────────

    def schedule_post_live_check(self, video_id: str) -> None:
        """
        Agenda verificação pós-live para um vídeo (não bloqueante).
        Idempotente: ignora se já foi agendado ou verificado.
        Só executa se post_live_enabled=True.
        """
        if not self.post_live_enabled:
            return
        if video_id in self._checked_vods or video_id in self._post_live_tasks:
            return
        task = asyncio.create_task(self._post_live_check_task(video_id))
        self._post_live_tasks[video_id] = task
        logger.info(
            f"[VodVerifier] Verificação pós-live agendada: {video_id} "
            f"(delay inicial={self.post_live_initial_delay}s)"
        )

    async def _post_live_check_task(self, video_id: str) -> None:
        """
        Verifica disponibilidade de um VOD com retry e backoff exponencial.
        Sequência: aguarda delay inicial → tenta → retries com delays configurados.
        """
        try:
            initial_delay = self.post_live_initial_delay
            retry_delays = self.post_live_retry_delays

            logger.debug(
                f"[VodVerifier] Post-live {video_id}: aguardando {initial_delay}s antes da primeira verificação."
            )
            await asyncio.sleep(initial_delay)

            # Primeira tentativa
            available = await asyncio.to_thread(
                self._check_single_vod, video_id
            )
            if available:
                logger.info(f"[VodVerifier] VOD {video_id} disponível após live.")
                self._mark_checked(video_id)
                return

            # Retries com backoff
            for attempt, delay in enumerate(retry_delays, start=1):
                logger.warning(
                    f"[VodVerifier] VOD {video_id} não disponível. "
                    f"Retry {attempt}/{len(retry_delays)} em {delay}s..."
                )
                await asyncio.sleep(delay)
                available = await asyncio.to_thread(
                    self._check_single_vod, video_id
                )
                if available:
                    logger.info(
                        f"[VodVerifier] VOD {video_id} disponível após retry {attempt}."
                    )
                    self._mark_checked(video_id)
                    return

            # Todas as tentativas falharam
            logger.warning(
                f"[VodVerifier] VOD {video_id} indisponível após todas as tentativas "
                f"(1 + {len(retry_delays)} retries). Marcando como indisponível."
            )
            self._mark_unavailable(video_id)

        except asyncio.CancelledError:
            logger.debug(f"[VodVerifier] Task pós-live cancelada: {video_id}")
        except Exception as e:
            logger.error(f"[VodVerifier] Erro inesperado na verificação pós-live de {video_id}: {e}", exc_info=True)
        finally:
            self._post_live_tasks.pop(video_id, None)

    def _check_single_vod(self, video_id: str) -> bool:
        """Verifica disponibilidade de um único VOD (síncrono, para uso com asyncio.to_thread)."""
        result = self._scraper.check_vod_availability_batch([video_id])
        return result.get(video_id, False)

    def _mark_checked(self, video_id: str) -> None:
        self._checked_vods.add(video_id)
        # Garante que o campo vod_unavailable não esteja marcado
        stream = self._state.streams.get(video_id)
        if stream:
            stream.pop("vod_unavailable", None)

    def _mark_unavailable(self, video_id: str) -> None:
        self._checked_vods.add(video_id)
        stream = self._state.streams.get(video_id)
        if stream:
            stream["vod_unavailable"] = True

    # ── Health Check Periódico ──────────────────────────────────────────

    async def run_health_check_loop(self) -> None:
        """
        Worker em background. Verifica periodicamente todos os VODs no cache.
        Aguarda o intervalo configurado antes de cada rodada (primeiro ciclo
        começa após o intervalo, não imediatamente).
        """
        logger.info("[VodVerifier] Health check loop iniciado.")
        while True:
            try:
                interval = self.health_check_interval_seconds
                logger.debug(f"[VodVerifier] Health check: próxima verificação em {interval}s.")
                await asyncio.sleep(interval)

                if not self.health_check_enabled:
                    logger.debug("[VodVerifier] Health check desabilitado. Pulando.")
                    continue

                await self._run_health_check()

            except asyncio.CancelledError:
                logger.info("[VodVerifier] Health check loop cancelado.")
                break
            except Exception as e:
                logger.error(f"[VodVerifier] Erro no health check loop: {e}", exc_info=True)

    async def _run_health_check(self) -> None:
        """Verifica todos os VODs no cache e marca indisponíveis."""
        vod_streams = [
            s for s in self._state.get_all_streams()
            if s.get("status") in ("vod", "recorded")
        ]

        if not vod_streams:
            logger.debug("[VodVerifier] Health check: nenhum VOD no cache.")
            return

        video_ids = [s["videoid"] for s in vod_streams if s.get("videoid")]
        logger.info(f"[VodVerifier] Health check: verificando {len(video_ids)} VODs...")

        try:
            statuses = await asyncio.to_thread(
                self._scraper.check_vod_availability_batch, video_ids
            )
        except Exception as e:
            logger.error(f"[VodVerifier] Erro ao buscar disponibilidade em lote: {e}", exc_info=True)
            return

        unavailable_count = 0
        recovered_count = 0

        for vid_id, available in statuses.items():
            stream = self._state.streams.get(vid_id)
            if stream is None:
                continue
            if not available:
                if not stream.get("vod_unavailable"):
                    stream["vod_unavailable"] = True
                    unavailable_count += 1
                    logger.warning(f"[VodVerifier] Health check: VOD {vid_id} marcado como indisponível.")
            else:
                if stream.get("vod_unavailable"):
                    stream.pop("vod_unavailable", None)
                    recovered_count += 1
                    logger.info(f"[VodVerifier] Health check: VOD {vid_id} recuperado (disponível novamente).")

        logger.info(
            f"[VodVerifier] Health check concluído: "
            f"{unavailable_count} novos indisponíveis, "
            f"{recovered_count} recuperados, "
            f"{len(video_ids)} verificados no total."
        )
