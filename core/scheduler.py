"""
core/scheduler.py
Responsabilidade: Loop assíncrono de busca de streams do YouTube.
Portado do get_streams.py original — classe Scheduler + save_files.
NÃO usa os.getenv() — toda config vem do AppConfig injetado.
"""
import asyncio
import logging
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("TubeWrangler")

def _save_files(state, config, categories_db: dict, thumbnail_manager=None):
    """Gera apenas textosepg.json — M3U/EPG sao servidos on-the-fly via HTTP."""
    from core.playlist_builder import ContentGenerator
    all_streams = list(state.get_all_streams())

    if thumbnail_manager:
        for s in all_streams:
            vid = s.get("videoid")
            thumb = s.get("thumbnailurl")
            if vid and thumb:
                thumbnail_manager.ensure_cached(vid, thumb)

    # ── Gerar textosepg.json (countdown para smartplayer.py) ──
    # Horário de exibição usa UTC-3 (América/São Paulo) fixo.
    # O fuso é fixo pois o sistema não precisa de configuração de timezone.
    try:
        UTC_MINUS_3 = timezone(timedelta(hours=-3))
        now_utc  = datetime.now(timezone.utc)
        MESES    = ["Jan","Fev","Mar","Abr","Mai","Jun",
                    "Jul","Ago","Set","Out","Nov","Dez"]
        texts_cache = {}
        for s in all_streams:
            if s.get("status") != "upcoming":
                continue
            vid   = s.get("videoid", "")
            start = ContentGenerator.get_sortable_time(s)
            if not isinstance(start, datetime):
                continue
            try:
                start_local = start.astimezone(UTC_MINUS_3)
                total_secs  = (start - now_utc).total_seconds()
                if total_secs < 0:
                    line1 = "Ao vivo em instantes"
                else:
                    days, rem  = divmod(int(total_secs), 86400)
                    hours, rem = divmod(rem, 3600)
                    minutes, _ = divmod(rem, 60)
                    if days >= 1:
                        line1 = f"Ao vivo em {days}d {hours}h"
                    elif hours > 0:
                        line1 = f"Ao vivo em {hours}h {minutes}m"
                    else:
                        line1 = f"Ao vivo em {minutes}m" if minutes > 0 else "Ao vivo em instantes"
                line2 = (
                    f"{start_local.day} "
                    f"{MESES[start_local.month - 1]} "
                    f"{start_local.strftime('%H:%M')}"
                )
                texts_cache[vid] = {"line1": line1, "line2": line2}
            except Exception:
                pass

        texts_path = Path("/data/textosepg.json")
        texts_path.write_text(
            json.dumps(texts_cache, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        logger.debug(f"textosepg.json: {len(texts_cache)} entradas geradas")
    except Exception as e:
        logger.error(f"Erro ao gerar textosepg.json: {e}")

class Scheduler:
    """
    Loop assíncrono principal do TubeWrangler.
    Gerencia busca principal (intervalo configurável), pre-event, post-event e stale check.
    """

    def __init__(self, config, scraper, state):
        self._config   = config
        self._scraper  = scraper
        self._state    = state
        self.config = config
        self.scraper = scraper
        self.state = state
        self._categories_db: dict = {}
        self._force_event: Optional[asyncio.Event] = None
        self._thumbnail_manager = None
        self._vod_verifier = None

        dt_min_utc = datetime.min.replace(tzinfo=timezone.utc)

        loaded_lfs = state.meta.get("lastfullsync")
        self.last_full_sync = loaded_lfs if isinstance(loaded_lfs, datetime) else dt_min_utc

        loaded_lmr = state.meta.get("lastmainrun")
        self.last_main_run = loaded_lmr if isinstance(loaded_lmr, datetime) else dt_min_utc

        self.last_pre_event_run  = dt_min_utc
        self.last_post_event_run = dt_min_utc
        logger.debug(f"Scheduler init: last_main_run={self.last_main_run}, last_full_sync={self.last_full_sync}")

    # ─── API pública ────────────────────────────────────────────────

    def set_force_event(self, event: asyncio.Event) -> None:
        self._force_event = event

    def trigger_now(self) -> None:
        if self._force_event:
            self._force_event.set()
            logger.info("Scheduler: trigger manual recebido.")

    def reload_config(self, new_config) -> None:
        self._config = new_config
        self.config = new_config
        logger.info("Scheduler: config recarregada.")

    def set_categories_db(self, categories_db: dict) -> None:
        self._categories_db = categories_db

    def set_thumbnail_manager(self, thumbnail_manager) -> None:
        self._thumbnail_manager = thumbnail_manager

    def set_vod_verifier(self, vod_verifier) -> None:
        self._vod_verifier = vod_verifier

    def log_current_state(self, origin: str = ""):
        from core.playlist_builder import ContentGenerator
        all_streams   = self._state.get_all_streams()
        live_count     = sum(1 for s in all_streams if ContentGenerator.is_live(s))
        upcoming_count = sum(1 for s in all_streams if s.get("status") == "upcoming")
        none_count     = len(all_streams) - live_count - upcoming_count
        logger.info(
            f"Status{' ' + origin if origin else ''}: "
            f"{len(all_streams)} streams — "
            f"{live_count} live, {upcoming_count} upcoming, {none_count} vod/ended"
        )

    # ─── Loop principal ─────────────────────────────────────────────

    async def run(self, initial_run_delay: bool = False):

        if initial_run_delay:
            logger.info("Scheduler: aplicando delay inicial")
            self.last_main_run = datetime.now(timezone.utc)

        # UTC-3 fixo para guard de horário ativo (América/São Paulo)
        UTC_MINUS_3 = timezone(timedelta(hours=-3))

        while True:
            now_utc = datetime.now(timezone.utc)
            dt_min_utc = datetime.min.replace(tzinfo=timezone.utc)

            # FIX #1: Detectar force_triggered no início do loop
            force_triggered = (
                self._force_event and self._force_event.is_set()
            )

            if force_triggered:
                self._force_event.clear()
                self.last_main_run = dt_min_utc
                logger.info(
                    "Scheduler: force-sync detectado — ignorando horário ativo na próxima iteração"
                )

            # Cálculo de intervalos
            main_interval = timedelta(
                hours=self._config.get_int("scheduler_main_interval_hours")
            )
            full_sync_interval = timedelta(
                hours=self._config.get_int("full_sync_interval_hours")
            )
            time_for_main_run = (now_utc - self.last_main_run) >= main_interval
            time_for_full_sync = (
                (now_utc - self.last_full_sync) >= full_sync_interval
            )

            # Guard de horário — ignorado se force_triggered
            is_active_time = True
            if not force_triggered and self._config.get_bool(
                "enable_scheduler_active_hours"
            ):
                try:
                    local_hour = datetime.now(UTC_MINUS_3).hour
                    start_h = self._config.get_int("scheduler_active_start_hour")
                    end_h = self._config.get_int("scheduler_active_end_hour")
                    if not (start_h <= local_hour < end_h):
                        is_active_time = False
                        logger.debug(
                            f"Horário inativo: {local_hour}h "
                            f"(permitido {start_h}h-{end_h}h)"
                        )
                except Exception as e:
                    logger.warning(f"Erro ao verificar horário: {e}")

            # EXECUÇÃO PRINCIPAL
            if time_for_main_run and is_active_time:
                # FIX #2: Usar initial_sync_days apenas na primeira run
                is_first_run = (
                    self.last_main_run == dt_min_utc
                    and self.last_full_sync == dt_min_utc
                )

                if is_first_run:
                    initial_days = self._config.get_int("initial_sync_days")
                    if initial_days > 0:
                        cutoff_date = now_utc - timedelta(days=initial_days)
                        published_after = cutoff_date.isoformat()
                        logger.info(
                            f"Scheduler: PRIMEIRA EXECUÇÃO — "
                            f"Limitando aos últimos {initial_days} dias. "
                            f"publishedAfter={published_after}"
                        )
                    else:
                        published_after = None
                        logger.info(
                            f"Scheduler: PRIMEIRA EXECUÇÃO — "
                            f"Sem limite (initial_sync_days=0). "
                            f"Varrendo histórico completo."
                        )

                elif time_for_full_sync:
                    published_after = None
                    logger.info(
                        f"Scheduler: FULL SYNC PERIÓDICO — "
                        f"48h desde última. publishedAfter=None"
                    )

                else:
                    published_after = self.last_main_run.isoformat()
                    logger.info(
                        f"Scheduler: BUSCA INCREMENTAL — "
                        f"publishedAfter={published_after}"
                    )

                all_target_channels = self._state.get_all_channels()
                logger.info(
                    f"--- Scheduler: Verificação Principal "
                    f"[canais={len(all_target_channels)}, "
                    f"force={force_triggered}] ---"
                )

                if all_target_channels:
                    try:
                        use_playlists = self._config.get_bool("use_playlist_items")
                        if use_playlists:
                            new_streams = await asyncio.to_thread(
                                self._scraper.fetch_all_streams_for_channels_using_playlists,
                                all_target_channels,
                                published_after=published_after,
                                stale_hours=self._config.get_int("stale_hours"),
                                max_schedule_hours=self._config.get_int("max_schedule_hours"),
                            )
                        else:
                            new_streams = await asyncio.to_thread(
                                self._scraper.fetch_all_streams_for_channels,
                                all_target_channels,
                                published_after=published_after,
                            )
                        self._state.update_streams(new_streams)

                    except Exception as e:
                        logger.error(
                            f"Scheduler: Erro na busca principal: {e}",
                            exc_info=True
                        )
                else:
                    logger.warning(
                        "Scheduler: Nenhum canal alvo para buscar streams."
                    )

                self.last_main_run = now_utc
                self._state.meta["lastmainrun"] = now_utc

                if is_first_run or time_for_full_sync:
                    self.last_full_sync = now_utc
                    self._state.meta["lastfullsync"] = now_utc

                self.log_current_state("Verificação Principal")
                await asyncio.to_thread(
                    _save_files,
                    self._state,
                    self._config,
                    self._categories_db,
                    self._thumbnail_manager,
                )
                await asyncio.to_thread(self._state.save_to_disk)

            elif time_for_main_run and not is_active_time:
                start_h = self._config.get_int("scheduler_active_start_hour")
                end_h = self._config.get_int("scheduler_active_end_hour")
                logger.info(
                    f"--- Scheduler: Verificação principal PULADA "
                    f"(fora do horário {start_h}h-{end_h}h) ---"
                )

            # ── 2. Verificações de alta frequência ──
            streams_in_memory = self._state.get_all_streams()
            ids_to_check = set()
            pre_event_interval  = timedelta(minutes=self._config.get_int("scheduler_pre_event_interval_minutes"))
            post_event_interval = timedelta(minutes=self._config.get_int("scheduler_post_event_interval_minutes"))
            pre_event_window    = timedelta(hours=self._config.get_int("scheduler_pre_event_window_hours"))

            # Pre-event
            if (now_utc - self.last_pre_event_run) >= pre_event_interval:
                pre_event_cutoff = now_utc + pre_event_window
                pre_event_ids = {
                    s["videoid"] for s in streams_in_memory
                    if s.get("status") == "upcoming"
                    and isinstance(s.get("scheduledstarttimeutc"), datetime)
                    and s["scheduledstarttimeutc"] <= pre_event_cutoff
                    and s["scheduledstarttimeutc"] > now_utc
                }
                if pre_event_ids:
                    logger.info(f"--- Scheduler: {len(pre_event_ids)} na janela PRÉ-EVENTO ---")
                    ids_to_check.update(pre_event_ids)
                self.last_pre_event_run = now_utc

            # Post-event
            if (now_utc - self.last_post_event_run) >= post_event_interval:
                from core.playlist_builder import ContentGenerator
                post_event_ids = {
                    s["videoid"] for s in streams_in_memory
                    if ContentGenerator.is_live(s)
                }
                if post_event_ids:
                    logger.info(f"--- Scheduler: {len(post_event_ids)} live PÓS-EVENTO ---")
                    ids_to_check.update(post_event_ids)
                self.last_post_event_run = now_utc

            # Stale check
            stale_hours   = self._config.get_int("stale_hours")
            stale_cutoff  = now_utc - timedelta(hours=stale_hours)
            stale_ids = {
                s["videoid"] for s in streams_in_memory
                if s.get("status") in ("live", "upcoming")
                and isinstance(s.get("fetchtime"), datetime)
                and s["fetchtime"] < stale_cutoff
            }
            if stale_ids:
                logger.debug(f"--- Scheduler: {len(stale_ids)} streams stale ---")
                ids_to_check.update(stale_ids)

            if ids_to_check:
                try:
                    current_channels = self._state.get_all_channels()
                    # Captura IDs que estavam como "live" antes do update
                    live_before = {
                        vid for vid, s in self._state.streams.items()
                        if s.get("status") == "live"
                    }
                    updated = await asyncio.to_thread(
                        self._scraper.fetch_streams_by_ids,
                        list(ids_to_check),
                        current_channels,
                    )
                    if updated:
                        self._state.update_streams(updated)
                    returned_ids = {s["videoid"] for s in updated if "videoid" in s}
                    missing_ids  = ids_to_check - returned_ids
                    ids_to_mark  = [
                        mid for mid in missing_ids
                        if self._state.streams.get(mid, {}).get("status") in ("live", "upcoming")
                    ]
                    if ids_to_mark:
                        logger.warning(f"{len(ids_to_mark)} IDs ativos não retornados pela API. Marcando como 'none'.")
                        missing_data = [{"videoid": vid, "status": "none"} for vid in ids_to_mark]
                        self._state.update_streams(missing_data)

                    # Detecta transições live → vod e agenda verificação pós-live
                    if self._vod_verifier:
                        for vid in live_before:
                            stream = self._state.streams.get(vid)
                            if stream and stream.get("status") == "vod":
                                self._vod_verifier.schedule_post_live_check(vid)

                    self.log_current_state("Verificação Alta Frequência")
                    await asyncio.to_thread(
                        _save_files,
                        self._state,
                        self._config,
                        self._categories_db,
                        self._thumbnail_manager,
                    )
                    await asyncio.to_thread(self._state.save_to_disk)
                except Exception as e:
                    logger.error(f"Scheduler: erro na verificação alta frequência: {e}", exc_info=True)

            # ── 3. Sleep com suporte a trigger forçado ──
            try:
                if self._force_event:
                    await asyncio.wait_for(
                        asyncio.shield(self._force_event.wait()),
                        timeout=60
                    )
                else:
                    await asyncio.sleep(60)
            except asyncio.TimeoutError:
                pass
