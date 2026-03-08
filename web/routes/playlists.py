"""
web/routes/playlists.py
----------------------
Rotas de playlists, EPG e redirecionamentos legados.
"""
from __future__ import annotations

from fasthtml.common import *
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from core.playlist_builder import _resolve_proxy_base_url
from web.app_deps import AppDeps
from web.routes.playlist_dashboard import playlist_dashboard_page


def _get_base_url(request) -> str:
    host = request.headers.get("host", "")
    if host:
        scheme = "https" if request.url.scheme == "https" else "http"
        return f"{scheme}://{host}"
    client_ip = request.headers.get("x-forwarded-for", request.client.host).split(",")[0].strip()
    port = request.url.port or 8888
    return f"http://{client_ip}:{port}"


def register_playlists_routes(app, deps: AppDeps) -> None:
    def _serve_playlist_onthefly(mode: str, mode_type: str, request=None) -> Response:
        if deps.m3u_generator is None or deps.state is None:
            return Response("Servidor ainda inicializando", status_code=503)
        streams = list(deps.state.get_all_streams())
        cats = deps.categories_db if deps.categories_db else {}
        proxy_base = ""
        if mode_type == "proxy":
            if request is not None:
                proxy_base = _get_base_url(request)
            else:
                proxy_base = _resolve_proxy_base_url(deps.config)
        content = deps.m3u_generator.generate_playlist(
            streams,
            cats,
            mode=mode,
            mode_type=mode_type,
            thumbnail_manager=deps.thumbnail_manager,
            proxy_base_url=proxy_base,
        )
        return Response(content, media_type="audio/x-mpegurl")

    for playlist_name, (mode, mode_type) in deps.playlist_routes.items():
        def _make_playlist_route(local_mode=mode, local_mode_type=mode_type):
            async def _playlist_route(request):
                return _serve_playlist_onthefly(local_mode, local_mode_type, request=request)

            app.routes.append(Route(f"/playlist/{playlist_name}", endpoint=_playlist_route))

        _make_playlist_route()

    @app.get("/playlist")
    def playlist_page(request: Request):
        return playlist_dashboard_page(
            request=request,
            playlist_routes=deps.playlist_routes,
            base_url=_get_base_url(request),
        )

    @app.get("/epg.xml")
    def serve_epg_onthefly():
        if deps.xmltv_generator is None or deps.state is None:
            return Response("Servidor ainda inicializando", status_code=503)
        channels = deps.state.get_all_channels()
        streams = list(deps.state.get_all_streams())
        cats = deps.categories_db if deps.categories_db else {}
        content = deps.xmltv_generator.generate_xml(channels, streams, cats)
        return Response(content, media_type="application/xml")

    async def _epg_route(request):
        return serve_epg_onthefly()

    app.router.routes.insert(0, Route("/epg.xml", endpoint=_epg_route))

    for old_path, new_path in deps.legacy_redirects.items():
        def _make_redirect(local_old=old_path, local_new=new_path):
            @app.get(local_old)
            def _redirect():
                return RedirectResponse(url=local_new, status_code=301)

        _make_redirect()

    @app.put("/api/playlists/refresh")
    def api_playlists_refresh():
        if deps.scheduler:
            deps.scheduler.trigger_now()
        return JSONResponse({"ok": True, "message": "sync agendado"})

    @app.get("/api/epg")
    def api_epg():
        if deps.xmltv_generator is None or deps.state is None:
            return JSONResponse({"error": "Servidor ainda inicializando"}, status_code=503)
        channels = deps.state.get_all_channels()
        streams = list(deps.state.get_all_streams())
        cats = deps.categories_db if deps.categories_db else {}
        content = deps.xmltv_generator.generate_xml(channels, streams, cats)
        return Response(content, media_type="application/xml")
