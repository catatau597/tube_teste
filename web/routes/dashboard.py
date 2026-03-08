"""
web/routes/dashboard.py
----------------------
Dashboard e APIs de status/controle do scheduler e proxy.
"""
from __future__ import annotations

from fasthtml.common import *
from starlette.responses import JSONResponse, RedirectResponse

from core.proxy_manager import _buffers, get_stream_debug_info, is_stream_active, stop_stream, streams_status
from web.app_deps import AppDeps
from web.layout import _page_shell
from web.routes.proxy_dashboard import active_streams_card, dashboard_js, scheduler_cards


def register_dashboard_routes(app, deps: AppDeps) -> None:
    @app.get("/")
    def home(request: Request):
        streams = list(deps.state.get_all_streams()) if deps.state else []
        channels = deps.state.get_all_channels() if deps.state else {}

        n_live = sum(1 for s in streams if s.get("status") == "live")
        n_up = sum(1 for s in streams if s.get("status") == "upcoming")
        n_vod = sum(1 for s in streams if s.get("status") in ("vod", "recorded"))
        channel_count = len(channels)

        return _page_shell(
            "Dashboard",
            "dashboard",
            Div(
                H2("Visão Geral"),
                Div(
                    Div(
                        Span(str(channel_count), style="font-size:2rem;font-weight:700;color:#58a6ff;"),
                        Br(),
                        Span("Canais", cls="text-muted"),
                        Br(),
                        A("Gerenciar →", href="/canais", style="font-size:0.82rem;"),
                        cls="card",
                        style="text-align:center;padding:16px 24px;min-width:120px;",
                    ),
                    Div(
                        Span(str(n_live), style="font-size:2rem;font-weight:700;color:#f85149;"),
                        Br(),
                        Span("🔴 Live", cls="text-muted"),
                        cls="card",
                        style="text-align:center;padding:16px 24px;min-width:120px;",
                    ),
                    Div(
                        Span(str(n_up), style="font-size:2rem;font-weight:700;color:#d29922;"),
                        Br(),
                        Span("🟡 Upcoming", cls="text-muted"),
                        cls="card",
                        style="text-align:center;padding:16px 24px;min-width:120px;",
                    ),
                    Div(
                        Span(str(n_vod), style="font-size:2rem;font-weight:700;color:#8b949e;"),
                        Br(),
                        Span("📼 VOD", cls="text-muted"),
                        cls="card",
                        style="text-align:center;padding:16px 24px;min-width:120px;",
                    ),
                    style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:8px;",
                ),
                P(
                    A("📅 Ver todos os Eventos →", href="/eventos"),
                    "   ",
                    A("📁 Playlists →", href="/playlist"),
                    cls="text-muted",
                    style="margin-top:4px;",
                ),
                cls="card",
                style="margin-bottom:20px;",
            ),
            scheduler_cards(deps.scheduler),
            active_streams_card(),
            dashboard_js(),
        )

    @app.get("/force-sync")
    def force_sync():
        if deps.scheduler:
            deps.scheduler.trigger_now()
            deps.logger.info("Force-sync acionado pelo usuario.")
        return RedirectResponse("/", status_code=303)

    @app.get("/api/scheduler/status")
    def api_scheduler_status():
        if deps.scheduler is None:
            return JSONResponse({"error": "Scheduler não disponível"}, status_code=503)
        paused = getattr(deps.scheduler, "paused", False)
        next_run = getattr(deps.scheduler, "next_run", None)
        return JSONResponse(
            {
                "paused": paused,
                "next_run": next_run.timestamp() if next_run and hasattr(next_run, "timestamp") else None,
            }
        )

    @app.post("/api/scheduler/force")
    def api_scheduler_force():
        if deps.scheduler is None:
            return JSONResponse({"error": "Scheduler não disponível"}, status_code=503)
        deps.scheduler.trigger_now()
        deps.logger.info("Busca global forçada via Dashboard.")
        return JSONResponse({"ok": True})

    @app.post("/api/scheduler/pause")
    def api_scheduler_pause():
        if deps.scheduler is None:
            return JSONResponse({"error": "Scheduler não disponível"}, status_code=503)
        if not hasattr(deps.scheduler, "paused"):
            deps.scheduler.paused = False
        deps.scheduler.paused = not deps.scheduler.paused
        state = deps.scheduler.paused
        deps.logger.info(f"Scheduler {'pausado' if state else 'retomado'} via Dashboard.")
        return JSONResponse({"ok": True, "paused": state})

    @app.get("/api/proxy/status")
    def api_proxy_status():
        return JSONResponse({"streams": streams_status(), "count": len(_buffers)})

    @app.get("/api/proxy/debug/{video_id}")
    def api_proxy_debug(video_id: str):
        info = get_stream_debug_info(video_id)
        if info is None:
            return JSONResponse({"error": "stream não encontrado"}, status_code=404)
        return JSONResponse(info)

    @app.delete("/api/proxy/{video_id}")
    def api_proxy_stop(video_id: str):
        if not is_stream_active(video_id):
            return JSONResponse({"error": "stream nao encontrado ou ja parado"}, status_code=404)
        stop_stream(video_id)
        return JSONResponse({"ok": True, "video_id": video_id})
