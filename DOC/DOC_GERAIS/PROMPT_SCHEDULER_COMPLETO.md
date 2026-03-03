## Tarefa: implementar core/scheduler.py completo — portado do get_streams.py

### Contexto
O scheduler atual em core/scheduler.py tem apenas __init__ stub e trigger_now com asyncio.Event.
A lógica real de busca (main loop, pre-event, post-event, stale check, save_files)
existe no get_streams.py original — deve ser PORTADA, não reinventada.

Os canais já estão sendo resolvidos corretamente no lifespan (log confirma).
O problema é que o Scheduler nunca executa a busca de streams.

---

### PASSO 1 — Inspecionar o scheduler atual antes de qualquer edição

```bash
docker compose exec tubewranglerr python3 -c "
import inspect
from core.scheduler import Scheduler
print(inspect.getsource(Scheduler))
"
```

---

### PASSO 2 — Substituir core/scheduler.py pelo conteúdo abaixo (COMPLETO)

```python
"""
core/scheduler.py
Responsabilidade: Loop assíncrono de busca de streams do YouTube.
Portado do get_streams.py original — classe Scheduler + save_files.
NÃO usa os.getenv() — toda config vem do AppConfig injetado.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import pytz

logger = logging.getLogger("TubeWrangler")


def _save_files(state, config, m3u_gen, xmltv_gen, categories_db: dict):
    """Gera e salva playlists M3U e EPG XML em disco."""
    from core.playlist_builder import M3UGenerator, XMLTVGenerator
    if m3u_gen is None:
        m3u_gen = M3UGenerator(config)
    if xmltv_gen is None:
        xmltv_gen = XMLTVGenerator(config)

    all_streams = state.get_all_streams()
    playlist_live     = m3u_gen.generate_playlist(all_streams, categories_db, "live")
    playlist_upcoming = m3u_gen.generate_playlist(all_streams, categories_db, "upcoming")
    playlist_vod      = m3u_gen.generate_playlist(all_streams, categories_db, "vod")
    epg               = xmltv_gen.generate_xml(state.get_all_channels(), all_streams, categories_db)

    playlist_dir = Path(config.get_str("playlist_save_directory"))
    xmltv_dir    = Path(config.get_str("xmltv_save_directory"))
    live_path     = playlist_dir / config.get_str("playlist_live_filename")
    upcoming_path = playlist_dir / config.get_str("playlist_upcoming_filename")
    vod_path      = playlist_dir / config.get_str("playlist_vod_filename")
    xmltv_path    = xmltv_dir    / config.get_str("xmltv_filename")

    try:
        playlist_dir.mkdir(parents=True, exist_ok=True)
        xmltv_dir.mkdir(parents=True, exist_ok=True)
        live_path.write_text(playlist_live,     encoding="utf-8")
        upcoming_path.write_text(playlist_upcoming, encoding="utf-8")
        keep_vod = config.get_bool("keep_recorded_streams")
        if keep_vod:
            vod_path.write_text(playlist_vod, encoding="utf-8")
        elif vod_path.exists():
            vod_path.unlink()
        xmltv_path.write_text(epg, encoding="utf-8")
        logger.info(f"Arquivos salvos: {live_path.name}, {upcoming_path.name}, {xmltv_path.name}")
    except IOError as e:
        logger.error(f"Erro ao salvar arquivos: {e}")


class Scheduler:
    """
    Loop assíncrono principal do TubeWrangler.
    Gerencia busca principal (intervalo configurável), pre-event, post-event e stale check.
    """

    def __init__(self, config, scraper, state):
        self._config   = config
        self._scraper  = scraper
        self._state    = state
        self._m3u_gen  = None
        self._xmltv_gen = None
        self._categories_db: dict = {}
        self._force_event: Optional[asyncio.Event] = None

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
        logger.info("Scheduler: config recarregada.")

    def set_generators(self, m3u_gen, xmltv_gen) -> None:
        self._m3u_gen   = m3u_gen
        self._xmltv_gen = xmltv_gen

    def set_categories_db(self, categories_db: dict) -> None:
        self._categories_db = categories_db

    def log_current_state(self, origin: str = ""):
        from core.playlist_builder import ContentGenerator
        all_streams   = self._state.get_all_streams()
        live_count     = sum(1 for s in all_streams if ContentGenerator.is_live(None, s))
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
            logger.info("Scheduler: aplicando delay inicial.")
            self.last_main_run = datetime.now(timezone.utc)

        while True:
            now_utc    = datetime.now(timezone.utc)
            dt_min_utc = datetime.min.replace(tzinfo=timezone.utc)

            # ── 1. Verificação principal (intervalo configurável) ──
            main_interval = timedelta(hours=self._config.get_int("scheduler_main_interval_hours"))
            full_sync_interval = timedelta(hours=self._config.get_int("full_sync_interval_hours"))
            time_for_main_run  = (now_utc - self.last_main_run) >= main_interval
            time_for_full_sync = (now_utc - self.last_full_sync) >= full_sync_interval

            # Verificar horário ativo
            is_active_time = True
            if self._config.get_bool("enable_scheduler_active_hours"):
                try:
                    local_tz   = pytz.timezone(self._config.get_str("local_timezone"))
                    local_hour = datetime.now(local_tz).hour
                    start_h    = self._config.get_int("scheduler_active_start_hour")
                    end_h      = self._config.get_int("scheduler_active_end_hour")
                    if not (start_h <= local_hour < end_h):
                        is_active_time = False
                except Exception:
                    pass

            if time_for_main_run and is_active_time:
                all_target_channels = self._state.get_all_channels()
                logger.info(
                    f"--- Scheduler: verificação principal | "
                    f"intervalo={self._config.get_int('scheduler_main_interval_hours')}h | "
                    f"canais={len(all_target_channels)} | "
                    f"full_sync={time_for_full_sync} ---"
                )

                published_after = None
                if not time_for_full_sync and self.last_main_run != dt_min_utc:
                    published_after = self.last_main_run.isoformat()
                    logger.info(f"Scheduler: busca incremental publishedAfter={published_after}")
                else:
                    reason = "full_sync_due" if time_for_full_sync else "first_run"
                    logger.info(f"Scheduler: full sync. Reason={reason}")

                if all_target_channels:
                    try:
                        use_playlists = self._config.get_bool("use_playlist_items")
                        fetch_method = (
                            self._scraper.fetch_all_streams_for_channels_using_playlists
                            if use_playlists
                            else self._scraper.fetch_all_streams_for_channels
                        )
                        new_streams = fetch_method(all_target_channels, published_after=published_after)
                        self._state.update_streams(new_streams)
                    except Exception as e:
                        logger.error(f"Scheduler: erro na busca principal: {e}", exc_info=True)
                else:
                    logger.warning("Scheduler: nenhum canal alvo para buscar streams.")

                self.last_main_run = now_utc
                self._state.meta["lastmainrun"] = now_utc
                if published_after is None:
                    self.last_full_sync = now_utc
                    self._state.meta["lastfullsync"] = now_utc

                self.log_current_state("Verificação Principal")
                _save_files(self._state, self._config, self._m3u_gen, self._xmltv_gen, self._categories_db)
                self._state.save_to_disk()

            elif time_for_main_run and not is_active_time:
                start_h = self._config.get_int("scheduler_active_start_hour")
                end_h   = self._config.get_int("scheduler_active_end_hour")
                logger.info(f"--- Scheduler: verificação principal pulada (fora do horário {start_h}-{end_h}h) ---")

            else:
                next_run = self.last_main_run + main_interval
                logger.debug(f"Scheduler: próxima verificação principal em {next_run}")

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
                    if ContentGenerator.is_live(None, s)
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
                    updated = self._scraper.fetch_streams_by_ids(list(ids_to_check), current_channels)
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
                    self.log_current_state("Verificação Alta Frequência")
                    _save_files(self._state, self._config, self._m3u_gen, self._xmltv_gen, self._categories_db)
                    self._state.save_to_disk()
                except Exception as e:
                    logger.error(f"Scheduler: erro na verificação alta frequência: {e}", exc_info=True)

            # ── 3. Sleep com suporte a trigger forçado ──
            try:
                if self._force_event:
                    await asyncio.wait_for(
                        asyncio.shield(self._force_event.wait()),
                        timeout=60
                    )
                    if self._force_event.is_set():
                        self._force_event.clear()
                        self.last_main_run = datetime.min.replace(tzinfo=timezone.utc)
                        logger.info("Scheduler: executando sync forçado na próxima iteração.")
                else:
                    await asyncio.sleep(60)
            except asyncio.TimeoutError:
                pass
```

---

### PASSO 3 — Validar o scheduler

```bash
docker compose exec tubewranglerr python3 -c "
import inspect
from core.scheduler import Scheduler
metodos = [m for m in dir(Scheduler) if not m.startswith('_')]
obrigatorios = ['run', 'trigger_now', 'reload_config', 'set_force_event',
                'set_generators', 'log_current_state']
for m in obrigatorios:
    print(f'OK  {m}' if m in metodos else f'FALTA  {m}')
"
```

---

### PASSO 4 — Reiniciar e verificar logs

```bash
docker compose restart
sleep 10
docker logs tubewranglerr --tail=40
```

**Esperado nos logs (em ~30 segundos):**
```
=== TubeWrangler iniciando ===
Canais prontos: 2
Scheduler iniciado.
--- Scheduler: verificação principal | ...
Scheduler: full sync. Reason=first_run
Buscando streams playlistItems para 2 canais...
Update Streams: Adicionados X, ...
Arquivos salvos: playlist_live.m3u8, ...
```

---

### PASSO 5 — Verificar home() com streams

```bash
curl -s http://localhost:8888/ | grep -i "live\|upcoming\|vod\|stream\|nenhum"
```

---

### PASSO 6 — Commit após validação

```bash
git add core/scheduler.py
git commit -m "feat: Scheduler completo portado do get_streams.py — main loop + pre/post-event + stale + save_files"
```

---

### Regras
- NÃO criar _save_files como stub — implementação completa está acima
- NÃO usar os.getenv() — toda config via self._config.get_*()
- NÃO alterar core/config.py, core/state_manager.py, core/youtube_api.py
- Se playlist_builder.py não tiver ContentGenerator.is_live() ou generate_playlist(),
  reportar antes de prosseguir
