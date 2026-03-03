# IMPLEMENTAÇÃO COMPLETA: SCHEDULER FIXES #1 E #2
## TubeWrangler Refactoring v3.5.1

**Versão do Documento:** 1.0  
**Data:** 2026-02-26  
**Objetivo:** Corrigir dois problemas críticos no Scheduler (core/scheduler.py) identificados durante testes  
**Escopo:** Force-sync bypass + initial_sync_days limit  

---

## 📋 ÍNDICE

1. [Diagnóstico dos Problemas](#diagnóstico-dos-problemas)
2. [FIX #1: Force-Sync Bypassar Horário](#fix-1-force-sync-bypassar-horário)
3. [FIX #2: Limitar Primeira Busca com initial_sync_days](#fix-2-limitar-primeira-busca)
4. [Implementação Completa do Scheduler](#implementação-completa)
5. [Roteiros de Teste](#roteiros-de-teste)
6. [Validação em Container](#validação-em-container)
7. [Troubleshooting](#troubleshooting)

---

## Diagnóstico dos Problemas

### O que Funcionou Corretamente
✅ Scheduler portado de get_streams.py  
✅ Loop principal com detecção de intervalo  
✅ Paginação de playlists da API  
✅ Logs detalhados  
✅ Salvamento de M3U e EPG  

### O que Não Funcionou
❌ **Fix #1:** Force-sync acionado fora de horário ativo (02:07h) foi ignorado  
❌ **Fix #2:** Primeira execução varreu TODO histórico (2350 streams) em vez de limite de 2 dias  

### Causa Raiz

**Fix #1:**
```
Sequence of events:
1. trigger_now() seta _force_event
2. Loop detecta _force_event.is_set() e reseta last_main_run → datetime.min
3. Calcula: time_for_main_run = (now - datetime.min) >= 4h → TRUE
4. Calcula: is_active_time = False (02:07h, fora de 7-22h)
5. Testa: if time_for_main_run and is_active_time → FALSE and FALSE → NÃO EXECUTA
6. Resultado: Force-sync ignorado porque guard de horário vem DEPOIS da detecção
```

**Fix #2:**
```
Sequence of events:
1. Primeira execução: last_main_run = datetime.min, last_full_sync = datetime.min
2. Calcula: time_for_full_sync = (now - datetime.min) >= 48h → TRUE (sempre!)
3. Testa: if not time_for_full_sync → FALSE
4. Usa: published_after = None (sem limite de dias)
5. Resultado: API retorna histórico completo (41 páginas da CazéTV = 2350 streams)
```

---

## FIX #1: Force-Sync Bypassar Horário

### Lógica Esperada

```
┌─────────────────────────────────────────────────────────────────┐
│ Scheduler Loop                                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│ 1. Detectar force_triggered PRIMEIRO                           │
│    ├─ if _force_event.is_set():                               │
│    │   ├─ clear()                                             │
│    │   └─ reset last_main_run → datetime.min                  │
│    │                                                            │
│ 2. Calcular time_for_main_run                                  │
│    └─ (now - last_main_run) >= main_interval                  │
│                                                                 │
│ 3. Calcular is_active_time                                     │
│    ├─ if NOT force_triggered:  ← BYPASS se força!             │
│    │   └─ Verificar horário (7-22h)                           │
│    └─ else:                                                     │
│        └─ is_active_time = True (força ignora horário)        │
│                                                                 │
│ 4. Decisão final                                               │
│    └─ if time_for_main_run and is_active_time:                │
│        └─ EXECUTA (com ou sem force)                          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Código Corrigido (Trecho 1)

```python
async def run(self, initial_run_delay: bool = False):
    if initial_run_delay:
        logger.info("Scheduler: aplicando delay inicial.")
        self.last_main_run = datetime.now(timezone.utc)

    while True:
        now_utc = datetime.now(timezone.utc)
        dt_min_utc = datetime.min.replace(tzinfo=timezone.utc)
        
        # ═════════════════════════════════════════════════════════════
        # FIX #1: DETECTAR force_triggered NO INÍCIO DO LOOP
        # ═════════════════════════════════════════════════════════════
        force_triggered = (
            self._force_event 
            and self._force_event.is_set()
        )
        
        if force_triggered:
            self._force_event.clear()
            self.last_main_run = dt_min_utc
            logger.info("Scheduler: force-sync detectado — ignorando horário ativo")
        
        # Calcular intervalos
        main_interval = timedelta(
            hours=self._config.get_int("scheduler_main_interval_hours")
        )
        full_sync_interval = timedelta(
            hours=self._config.get_int("full_sync_interval_hours")
        )
        time_for_main_run = (now_utc - self.last_main_run) >= main_interval
        time_for_full_sync = (now_utc - self.last_full_sync) >= full_sync_interval
        
        # Guard de horário — IGNORADO se force_triggered
        is_active_time = True
        if not force_triggered and self._config.get_bool("enable_scheduler_active_hours"):
            try:
                local_tz = pytz.timezone(self._config.get_str("local_timezone"))
                local_hour = datetime.now(local_tz).hour
                start_h = self._config.get_int("scheduler_active_start_hour")
                end_h = self._config.get_int("scheduler_active_end_hour")
                if not (start_h <= local_hour < end_h):
                    is_active_time = False
                    logger.debug(f"Horário inativo {local_hour}h (fora de {start_h}-{end_h}h)")
            except Exception as e:
                logger.warning(f"Erro ao verificar horário: {e}")
                pass
        
        # ─── EXECUÇÃO PRINCIPAL ─────────────────────────────────────
        if time_for_main_run and is_active_time:
            # Aqui entra com force_triggered = True OU horário ativo
            logger.info(f"--- Scheduler: Verificação Principal [force={force_triggered}] ---")
            # ... resto do código de busca ...
```

### Comportamento Após Fix #1

**Cenário A: Force-sync no horário inativo (02:07h)**
```
[02:07:00] POST /force-sync
[02:07:01] trigger_now() → _force_event.set()
[02:07:02] Loop itera: force_triggered = True
[02:07:02] clear() + reset last_main_run
[02:07:02] Não verifica horário (linha "if not force_triggered")
[02:07:02] time_for_main_run = True (porque reset)
[02:07:02] is_active_time = True (bypass)
[02:07:02] EXECUTA sync AGORA ✓
[02:07:03] _force_event.clear() finaliza
```

**Cenário B: Force-sync no horário ativo (14:30h)**
```
[14:30:00] POST /force-sync
[14:30:01] trigger_now() → _force_event.set()
[14:30:02] Loop itera: force_triggered = True
[14:30:02] clear() + reset last_main_run
[14:30:02] Verifica horário? NÃO (bypass)
[14:30:02] time_for_main_run = True
[14:30:02] is_active_time = True
[14:30:02] EXECUTA sync AGORA ✓
```

---

## FIX #2: Limitar Primeira Busca

### Lógica Esperada

```
publishedAfter é calculado como:

┌──────────────────────────────────────────────────────────┐
│ Cenário 1: PRIMEIRA EXECUÇÃO                             │
│ last_main_run = datetime.min                             │
│ last_full_sync = datetime.min                            │
│ ─────────────────────────────────────────────────────────│
│ is_first_run = True                                      │
│ initial_days = 2 (do config)                             │
│ publishedAfter = (now - 2 dias).isoformat()              │
│ Resultado: Busca apenas últimos 2 dias (~100-200 vids)   │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│ Cenário 2: BUSCA NORMAL (4h após primeira)               │
│ last_main_run ≠ datetime.min                             │
│ (now - last_main_run) = 4h < 48h                         │
│ ─────────────────────────────────────────────────────────│
│ time_for_main_run = True                                 │
│ time_for_full_sync = False                               │
│ publishedAfter = last_main_run.isoformat()               │
│ Resultado: Busca incremental desde última execução       │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│ Cenário 3: FULL SYNC PERIÓDICO (48h após primeira)       │
│ last_main_run ≠ datetime.min                             │
│ (now - last_full_sync) >= 48h                            │
│ ─────────────────────────────────────────────────────────│
│ time_for_full_sync = True                                │
│ publishedAfter = None                                    │
│ Resultado: Busca histórico completo (~2350 vids)         │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│ Cenário 4: initial_sync_days = 0 (desabilitar limite)    │
│ is_first_run = True                                      │
│ initial_days = 0                                         │
│ publishedAfter = None                                    │
│ Resultado: Primeira execução busca histórico completo    │
└──────────────────────────────────────────────────────────┘
```

### Código Corrigido (Trecho 2)

```python
        # ─── EXECUÇÃO PRINCIPAL ─────────────────────────────────────
        if time_for_main_run and is_active_time:
            # ═════════════════════════════════════════════════════════════
            # FIX #2: USAR initial_sync_days APENAS PRIMEIRA RUN
            # ═════════════════════════════════════════════════════════════
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
                    f"Razão: 48h desde última (full_sync_interval_reached). "
                    f"publishedAfter=None"
                )
            
            else:
                # Busca incremental normal
                published_after = self.last_main_run.isoformat()
                logger.info(
                    f"Scheduler: BUSCA INCREMENTAL — "
                    f"publishedAfter={published_after}"
                )
            
            # ─────────────────────────────────────────────────────────────
            # EXECUTAR BUSCA
            # ─────────────────────────────────────────────────────────────
            all_target_channels = self._state.get_all_channels()
            
            if all_target_channels:
                logger.info(
                    f"Buscando streams para {len(all_target_channels)} canais "
                    f"(publishedAfter={'None' if published_after is None else 'limite ' + str(initial_days) + 'd'})"
                )
                
                try:
                    use_playlists = self._config.get_bool("use_playlist_items")
                    fetch_method = (
                        self._scraper.fetch_all_streams_for_channels_using_playlists
                        if use_playlists
                        else self._scraper.fetch_all_streams_for_channels
                    )
                    new_streams = fetch_method(
                        all_target_channels, 
                        published_after=published_after
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
            
            # ─────────────────────────────────────────────────────────────
            # ATUALIZAR TIMESTAMPS E SALVAR
            # ─────────────────────────────────────────────────────────────
            self.last_main_run = now_utc
            self._state.meta["lastmainrun"] = now_utc
            
            # Se foi primeira execução OU full sync, atualiza lastfullsync
            if is_first_run or time_for_full_sync:
                self.last_full_sync = now_utc
                self._state.meta["lastfullsync"] = now_utc
            
            self.log_current_state("Verificação Principal")
            _save_files(
                self._state, 
                self._config, 
                self._m3u_gen, 
                self._xmltv_gen, 
                self._categories_db
            )
            self._state.save_to_disk()
```

### Comportamento Após Fix #2

**Primeira Execução (initial_sync_days=2)**
```
[07:00] Scheduler loop
[07:00] is_first_run = (datetime.min == datetime.min AND datetime.min == datetime.min) → TRUE
[07:00] initial_days = 2
[07:00] published_after = (2026-02-24 07:00:00).isoformat()
[07:00] LOG: "PRIMEIRA EXECUÇÃO — Limitando aos últimos 2 dias"
[07:00] API chamada: publishedAfter=2026-02-24T07:00:00+00:00
[07:02] Encontrados: ~100-200 streams (apenas 2 dias)
[07:02] Salvo em /data/m3us/ e /data/epgs/
[07:02] last_main_run = 2026-02-26T07:00:00
[07:02] last_full_sync = 2026-02-26T07:00:00
```

**Segunda Execução (4h depois, mesma primeira run ainda)**
```
[11:00] Scheduler loop
[11:00] is_first_run = (07:00 == datetime.min AND 07:00 == datetime.min) → FALSE
[11:00] time_for_full_sync = (11:00 - 07:00) = 4h >= 48h → FALSE
[11:00] else → Busca incremental
[11:00] published_after = 2026-02-26T07:00:00
[11:00] LOG: "BUSCA INCREMENTAL"
[11:00] API chamada: publishedAfter=2026-02-26T07:00:00+00:00
[11:01] Encontrados: ~10-50 streams (novos desde 07:00)
[11:02] Salvo
```

**Full Sync Periódico (48h depois)**
```
[07:00 dia 2] Scheduler loop
[07:00] is_first_run = FALSE
[07:00] time_for_full_sync = (48h == 48h) >= 48h → TRUE
[07:00] published_after = None
[07:00] LOG: "FULL SYNC PERIÓDICO — full_sync_interval_reached"
[07:00] API chamada: publishedAfter=None (sem limite!)
[07:05] Encontrados: ~2350 streams (histórico completo)
[07:05] Salvo
[07:05] last_full_sync = dia2_07:00
```

---

## Implementação Completa

### core/scheduler.py (Arquivo Completo)

```python
"""
core/scheduler.py
Responsabilidade: Loop assíncrono de busca de streams do YouTube.
Portado do get_streams.py original — classe Scheduler + save_files.

FIXES APLICADOS:
#1: Force-sync ignora horário ativo (enable_scheduler_active_hours)
#2: Primeira execução respeita initial_sync_days
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
    playlist_live = m3u_gen.generate_playlist(all_streams, categories_db, "live")
    playlist_upcoming = m3u_gen.generate_playlist(
        all_streams, categories_db, "upcoming"
    )
    playlist_vod = m3u_gen.generate_playlist(all_streams, categories_db, "vod")
    epg = xmltv_gen.generate_xml(
        state.get_all_channels(), all_streams, categories_db
    )

    playlist_dir = Path(config.get_str("playlist_save_directory"))
    xmltv_dir = Path(config.get_str("xmltv_save_directory"))
    live_path = playlist_dir / config.get_str("playlist_live_filename")
    upcoming_path = playlist_dir / config.get_str("playlist_upcoming_filename")
    vod_path = playlist_dir / config.get_str("playlist_vod_filename")
    xmltv_path = xmltv_dir / config.get_str("xmltv_filename")

    try:
        playlist_dir.mkdir(parents=True, exist_ok=True)
        xmltv_dir.mkdir(parents=True, exist_ok=True)
        live_path.write_text(playlist_live, encoding="utf-8")
        upcoming_path.write_text(playlist_upcoming, encoding="utf-8")
        
        keep_vod = config.get_bool("keep_recorded_streams")
        if keep_vod:
            vod_path.write_text(playlist_vod, encoding="utf-8")
        elif vod_path.exists():
            vod_path.unlink()
        
        xmltv_path.write_text(epg, encoding="utf-8")
        logger.info(
            f"Arquivos salvos: {live_path.name}, {upcoming_path.name}, "
            f"{xmltv_path.name}"
        )
    except IOError as e:
        logger.error(f"Erro ao salvar arquivos: {e}")


class Scheduler:
    """
    Loop assíncrono principal do TubeWrangler.
    Gerencia busca principal (intervalo), pre-event, post-event e stale check.
    
    ATRIBUTOS CRÍTICOS:
    - last_main_run: datetime da última execução principal
    - last_full_sync: datetime do último full sync (sem limite de dias)
    - _force_event: asyncio.Event para sincronização forçada
    """

    def __init__(self, config, scraper, state):
        self._config = config
        self._scraper = scraper
        self._state = state
        self._m3u_gen = None
        self._xmltv_gen = None
        self._categories_db: dict = {}
        self._force_event: Optional[asyncio.Event] = None

        dt_min_utc = datetime.min.replace(tzinfo=timezone.utc)

        # Carregar timestamps salvos
        loaded_lfs = state.meta.get("lastfullsync")
        self.last_full_sync = (
            loaded_lfs if isinstance(loaded_lfs, datetime) else dt_min_utc
        )

        loaded_lmr = state.meta.get("lastmainrun")
        self.last_main_run = (
            loaded_lmr if isinstance(loaded_lmr, datetime) else dt_min_utc
        )

        self.last_pre_event_run = dt_min_utc
        self.last_post_event_run = dt_min_utc
        
        logger.debug(
            f"Scheduler.__init__: "
            f"last_main_run={self.last_main_run}, "
            f"last_full_sync={self.last_full_sync}"
        )

    # ─── API PÚBLICA ────────────────────────────────────────────────────

    def set_force_event(self, event: asyncio.Event) -> None:
        """Conectar asyncio.Event para force-sync."""
        self._force_event = event

    def trigger_now(self) -> None:
        """Sinalizar sync forçado (não-bloqueante)."""
        if self._force_event:
            self._force_event.set()
            logger.info("Scheduler.trigger_now(): force-sync sinalizado")

    def reload_config(self, new_config) -> None:
        """Recarregar configuração em runtime."""
        self._config = new_config
        logger.info("Scheduler: config recarregada")

    def set_generators(self, m3u_gen, xmltv_gen) -> None:
        """Injetar geradores de playlist e EPG."""
        self._m3u_gen = m3u_gen
        self._xmltv_gen = xmltv_gen

    def set_categories_db(self, categories_db: dict) -> None:
        """Injetar banco de categorias."""
        self._categories_db = categories_db

    def log_current_state(self, origin: str = ""):
        """Log do estado atual de streams."""
        from core.playlist_builder import ContentGenerator
        
        all_streams = self._state.get_all_streams()
        live_count = sum(
            1 for s in all_streams if ContentGenerator.is_live(None, s)
        )
        upcoming_count = sum(
            1 for s in all_streams if s.get("status") == "upcoming"
        )
        other_count = len(all_streams) - live_count - upcoming_count
        
        logger.info(
            f"Status{' ' + origin if origin else ''}: "
            f"{len(all_streams)} streams — "
            f"{live_count} live, {upcoming_count} upcoming, {other_count} outro"
        )

    # ─── LOOP PRINCIPAL ────────────────────────────────────────────────

    async def run(self, initial_run_delay: bool = False):
        """Loop assíncrono do Scheduler com suporte a force-sync."""
        if initial_run_delay:
            logger.info("Scheduler: aplicando delay inicial")
            self.last_main_run = datetime.now(timezone.utc)

        while True:
            now_utc = datetime.now(timezone.utc)
            dt_min_utc = datetime.min.replace(tzinfo=timezone.utc)

            # ═════════════════════════════════════════════════════════════
            # FIX #1: DETECTAR force_triggered NO INÍCIO
            # ═════════════════════════════════════════════════════════════
            force_triggered = (
                self._force_event and self._force_event.is_set()
            )

            if force_triggered:
                self._force_event.clear()
                self.last_main_run = dt_min_utc
                logger.info(
                    "Scheduler: force-sync detectado — "
                    "ignorando horário ativo na próxima iteração"
                )

            # ─── Cálculo de intervalos ──────────────────────────────────
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

            # ─── Verificação de horário ativo ───────────────────────────
            is_active_time = True
            if not force_triggered and self._config.get_bool(
                "enable_scheduler_active_hours"
            ):
                try:
                    local_tz = pytz.timezone(
                        self._config.get_str("local_timezone")
                    )
                    local_hour = datetime.now(local_tz).hour
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

            # ─── EXECUÇÃO PRINCIPAL ────────────────────────────────────
            if time_for_main_run and is_active_time:
                # ═════════════════════════════════════════════════════════
                # FIX #2: USAR initial_sync_days APENAS PRIMEIRA RUN
                # ═════════════════════════════════════════════════════════
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

                # ─────────────────────────────────────────────────────────
                all_target_channels = self._state.get_all_channels()
                logger.info(
                    f"--- Scheduler: Verificação Principal "
                    f"[canais={len(all_target_channels)}, "
                    f"force={force_triggered}] ---"
                )

                if all_target_channels:
                    try:
                        use_playlists = self._config.get_bool(
                            "use_playlist_items"
                        )
                        fetch_method = (
                            self._scraper
                            .fetch_all_streams_for_channels_using_playlists
                            if use_playlists
                            else self._scraper.fetch_all_streams_for_channels
                        )
                        new_streams = fetch_method(
                            all_target_channels,
                            published_after=published_after
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

                # ─────────────────────────────────────────────────────────
                self.last_main_run = now_utc
                self._state.meta["lastmainrun"] = now_utc

                if is_first_run or time_for_full_sync:
                    self.last_full_sync = now_utc
                    self._state.meta["lastfullsync"] = now_utc

                self.log_current_state("Verificação Principal")
                _save_files(
                    self._state,
                    self._config,
                    self._m3u_gen,
                    self._xmltv_gen,
                    self._categories_db
                )
                self._state.save_to_disk()

            elif time_for_main_run and not is_active_time:
                start_h = self._config.get_int("scheduler_active_start_hour")
                end_h = self._config.get_int("scheduler_active_end_hour")
                logger.info(
                    f"--- Scheduler: Verificação principal PULADA "
                    f"(fora do horário {start_h}h-{end_h}h) ---"
                )

            # ─── VERIFICAÇÕES DE ALTA FREQUÊNCIA ────────────────────────
            streams_in_memory = self._state.get_all_streams()
            ids_to_check = set()
            
            pre_event_interval = timedelta(
                minutes=self._config.get_int(
                    "scheduler_pre_event_interval_minutes"
                )
            )
            post_event_interval = timedelta(
                minutes=self._config.get_int(
                    "scheduler_post_event_interval_minutes"
                )
            )
            pre_event_window = timedelta(
                hours=self._config.get_int("scheduler_pre_event_window_hours")
            )

            # Pre-event
            if (now_utc - self.last_pre_event_run) >= pre_event_interval:
                pre_event_cutoff = now_utc + pre_event_window
                pre_event_ids = {
                    s["videoid"] for s in streams_in_memory
                    if s.get("status") == "upcoming"
                    and isinstance(
                        s.get("scheduledstarttimeutc"), datetime
                    )
                    and s["scheduledstarttimeutc"] <= pre_event_cutoff
                    and s["scheduledstarttimeutc"] > now_utc
                }
                if pre_event_ids:
                    logger.info(
                        f"--- Scheduler: {len(pre_event_ids)} "
                        f"na janela PRÉ-EVENTO ---"
                    )
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
                    logger.info(
                        f"--- Scheduler: {len(post_event_ids)} "
                        f"live PÓS-EVENTO ---"
                    )
                    ids_to_check.update(post_event_ids)
                self.last_post_event_run = now_utc

            # Stale check
            stale_hours = self._config.get_int("stale_hours")
            stale_cutoff = now_utc - timedelta(hours=stale_hours)
            stale_ids = {
                s["videoid"] for s in streams_in_memory
                if s.get("status") in ("live", "upcoming")
                and isinstance(s.get("fetchtime"), datetime)
                and s["fetchtime"] < stale_cutoff
            }
            if stale_ids:
                logger.debug(
                    f"--- Scheduler: {len(stale_ids)} streams stale ---"
                )
                ids_to_check.update(stale_ids)

            # ─────────────────────────────────────────────────────────────
            if ids_to_check:
                try:
                    current_channels = self._state.get_all_channels()
                    updated = self._scraper.fetch_streams_by_ids(
                        list(ids_to_check), current_channels
                    )
                    if updated:
                        self._state.update_streams(updated)

                    returned_ids = {
                        s["videoid"] for s in updated if "videoid" in s
                    }
                    missing_ids = ids_to_check - returned_ids
                    ids_to_mark = [
                        mid for mid in missing_ids
                        if self._state.streams.get(mid, {}).get("status")
                        in ("live", "upcoming")
                    ]
                    
                    if ids_to_mark:
                        logger.warning(
                            f"{len(ids_to_mark)} IDs ativos não "
                            f"retornados pela API. Marcando como 'none'."
                        )
                        missing_data = [
                            {"videoid": vid, "status": "none"}
                            for vid in ids_to_mark
                        ]
                        self._state.update_streams(missing_data)

                    self.log_current_state("Verificação Alta Frequência")
                    _save_files(
                        self._state,
                        self._config,
                        self._m3u_gen,
                        self._xmltv_gen,
                        self._categories_db
                    )
                    self._state.save_to_disk()

                except Exception as e:
                    logger.error(
                        f"Scheduler: Erro na verificação alta freq: {e}",
                        exc_info=True
                    )

            # ─── SLEEP COM SUPORTE A TRIGGER FORÇADO ────────────────────
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
```

---

## Roteiros de Teste

### Teste 1: Verificar Configuração Base

```bash
# Login no container
docker compose exec tubewranglerr bash

# Verificar variáveis de config
python3 << 'EOF'
from core.config import AppConfig
cfg = AppConfig()
print("=== CONFIGURAÇÃO CRÍTICA ===")
print(f"initial_sync_days = {cfg.get_int('initial_sync_days')}")
print(f"enable_scheduler_active_hours = {cfg.get_bool('enable_scheduler_active_hours')}")
print(f"scheduler_active_start_hour = {cfg.get_int('scheduler_active_start_hour')}")
print(f"scheduler_active_end_hour = {cfg.get_int('scheduler_active_end_hour')}")
print(f"scheduler_main_interval_hours = {cfg.get_int('scheduler_main_interval_hours')}")
print(f"full_sync_interval_hours = {cfg.get_int('full_sync_interval_hours')}")
print(f"local_timezone = {cfg.get_str('local_timezone')}")
EOF
```

**Esperado:**
```
=== CONFIGURAÇÃO CRÍTICA ===
initial_sync_days = 2
enable_scheduler_active_hours = True
scheduler_active_start_hour = 7
scheduler_active_end_hour = 22
scheduler_main_interval_hours = 4
full_sync_interval_hours = 48
local_timezone = America/Sao_Paulo
```

### Teste 2: Force-Sync em Horário Inativo (FIX #1)

```bash
# Terminal 1: Ver logs em real-time
docker logs -f tubewranglerr | grep -i "scheduler\|force\|sync\|horário"

# Terminal 2: Aguardar até horário inativo (ex: 02:00-07:00)
# Ou simular com:
# 1. Limpar state_cache.json para forçar primeira run
# 2. Mudar enable_scheduler_active_hours=false no .env temporariamente

# Ou: Enviar POST /force-sync
curl http://localhost:8888/force-sync

# Esperado nos logs (em até 5 segundos):
# "force-sync detectado — ignorando horário ativo"
# "PRIMEIRA EXECUÇÃO — Limitando aos últimos 2 dias"
# "Buscando streams para X canais"
# "Streams salvos: playlist_live.m3u8, ..."
```

### Teste 3: Primeira Execução Respeita initial_sync_days (FIX #2)

```bash
# Resetar state_cache.json para forçar primeira run
rm -f /data/state_cache.json

# Reiniciar container
docker compose restart

# Aguardar até horário ativo (ex: 07:00)
# Observar logs (last line ~10 segundos após iniciar):
docker logs tubewranglerr --tail=100 | grep -i "primeira\|initial_sync\|limitad\|streams\|update"

# Esperado:
# "PRIMEIRA EXECUÇÃO — Limitando aos últimos 2 dias. publishedAfter=2026-02-24T07:00:00"
# "Update Streams: Adicionados XXX" (onde XXX < 500, não 2350)
# "Status Verificação Principal: 200 streams — 50 live, 100 upcoming, 50 outro"
```

### Teste 4: Busca Incremental após Primeira

```bash
# Deixar rodar ~4h (ou simular modificando last_main_run)
# Próxima execução deve:
# - Log: "BUSCA INCREMENTAL — publishedAfter=2026-02-26T07:00:00"
# - Encontrar apenas novos streams (~10-50)
# - Manter total em ~200-250 (não pular para 2350)

docker logs tubewranglerr --tail=50 | grep -i "incremental\|update"
```

### Teste 5: Full Sync Periódico (48h)

```bash
# Modificar arquivo de teste para simular 48h passados
# OU deixar rodar naturalmente por 48h

# Esperado:
# "FULL SYNC PERIÓDICO — 48h desde última"
# "publishedAfter=None"
# "Update Streams: Adicionados 2000+" (histórico completo)
```

### Teste 6: Verificar enable_scheduler_active_hours=false

```bash
# Editar .env
enable_scheduler_active_hours=false

# Reiniciar
docker compose restart

# Fazer POST /force-sync em horário inativo (ex: 02:00)
curl http://localhost:8888/force-sync

# Esperado: Executa sync SEMPRE, independente da hora
```

---

## Validação em Container

### Checklist de Validação Pós-Implementação

```bash
# 1. Verificar syntax Python
docker compose exec tubewranglerr python3 -m py_compile core/scheduler.py
# Esperado: sem erro

# 2. Verificar que Scheduler foi importado corretamente
docker compose exec tubewranglerr python3 << 'EOF'
from core.scheduler import Scheduler
import inspect
methods = [m for m in dir(Scheduler) if not m.startswith('_')]
print("Métodos públicos encontrados:")
for m in sorted(methods):
    print(f"  - {m}")
EOF

# 3. Verificar que _save_files existe e é callable
docker compose exec tubewranglerr python3 << 'EOF'
from core.scheduler import _save_files
print(f"_save_files: {_save_files}")
print(f"Callable: {callable(_save_files)}")
EOF

# 4. Reiniciar e observar logs
docker compose restart
sleep 5
docker logs tubewranglerr --tail=50 | grep -i "scheduler\|stream\|sync"

# 5. Verificar arquivos gerados
ls -lah /data/m3us/
ls -lah /data/epgs/
cat /data/state_cache.json | head -20

# 6. Fazer teste manual de force-sync
curl http://localhost:8888/force-sync
sleep 2
docker logs tubewranglerr --tail=20 | grep -i "force"
```

---

## Troubleshooting

### Problema: "Force-sync ainda ignora o sync mesmo em horário inativo"

**Causa:** Guard de horário ainda está sendo testado ANTES de force_triggered  
**Solução:**
```python
# ERRADO:
if not force_triggered and self._config.get_bool("enable_scheduler_active_hours"):
    is_active_time = (verificar_horario)

if time_for_main_run and is_active_time:
    # executa

# CORRETO:
if not force_triggered and self._config.get_bool("enable_scheduler_active_hours"):
    # Só verifica se NÃO é force
    is_active_time = (verificar_horario)
else:
    is_active_time = True  # Force ignora horário

if time_for_main_run and is_active_time:
    # executa
```

### Problema: "Primeira execução ainda busca 2350 streams (não 200)"

**Causa:** `is_first_run` não está detectando corretamente  
**Debug:**
```python
# Adicione log:
logger.info(f"DEBUG: is_first_run={is_first_run}")
logger.info(f"  last_main_run={self.last_main_run}")
logger.info(f"  last_full_sync={self.last_full_sync}")
logger.info(f"  dt_min_utc={dt_min_utc}")

# Verificar que datetime.min está com timezone:
dt_min_utc = datetime.min.replace(tzinfo=timezone.utc)
# NÃO usar: datetime.min (sem timezone)
```

### Problema: "Logs não mostram 'PRIMEIRA EXECUÇÃO', mas 'BUSCA INCREMENTAL'"

**Causa:** state_cache.json foi carregado com timestamps antigos  
**Solução:**
```bash
# Limpar cache e reiniciar
rm -f /data/state_cache.json
docker compose restart
```

### Problema: "publishedAfter ainda é None na primeira run"

**Causa:** initial_days está como 0 no .env  
**Solução:**
```env
# Garantir que está definido
INITIAL_SYNC_DAYS=2  # ou outro valor > 0
```

### Problema: "Horário nunca detecta como 'inativo'"

**Causa:** Fuso horário incorreto em config  
**Debug:**
```python
import pytz
from datetime import datetime
local_tz = pytz.timezone("America/Sao_Paulo")  # Verificar este valor
now = datetime.now(local_tz)
print(f"Hora local: {now}")
print(f"Hora UTC: {datetime.now(timezone.utc)}")
# Diferença esperada: ~3h
```

---

## Resumo da Implementação

| Aspecto | Fix #1 | Fix #2 |
|---|---|---|
| **Problema** | Force-sync ignorado fora de horário | Primeira execução varria histórico completo |
| **Causa** | Guard horário testado ANTES de force_triggered | `published_after=None` em primeira run |
| **Solução** | Detectar force_triggered no início do loop | Calcular `published_after = now - initial_sync_days` na primeira |
| **Variável** | `force_triggered` e `is_active_time` | `is_first_run` e `initial_days` |
| **Config** | `enable_scheduler_active_hours` | `initial_sync_days` |
| **Log** | "executando sync forçado na próxima iteração" | "PRIMEIRA EXECUÇÃO — Limitando aos últimos 2 dias" |
| **Teste** | POST /force-sync em 02:00h | Logs mostram < 500 streams (não 2350) |

**Próximas Etapas:**
1. ✅ Implementar core/scheduler.py com os 2 fixes
2. ✅ Validar em container (docker logs)
3. ✅ Executar testes de força
4. ✅ Commit em branch dev
5. ⏳ Preparar para merge em main (quando checklist 100%)
