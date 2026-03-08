"""
web/routes/content_api.py
-------------------------
Rotas de páginas de conteúdo e APIs de channels/streams/config.
"""
from __future__ import annotations

from fasthtml.common import *
from starlette.responses import JSONResponse

from core.youtube_api import YouTubeAPI
from web.app_deps import AppDeps
from web.routes.channels import channels_page as _channels_page
from web.routes.eventos import eventos_page as _eventos_page


def _serialize_stream(deps: AppDeps, s: dict) -> dict:
    data = {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in s.items()}
    cat_id = str(s.get("categoryoriginal") or "")
    cat_name = (deps.categories_db or {}).get(cat_id, "") if cat_id else ""
    if not cat_name and deps.config is not None and cat_id:
        cat_name = deps.config.get_mapping("category_mappings").get(cat_id, "")
    data["category_display"] = f"{cat_id} | {cat_name}" if cat_name else (cat_id or "—")
    return data


def register_content_api_routes(app, deps: AppDeps) -> None:
    @app.get("/canais")
    def canais_page():
        return _channels_page(deps.state, deps.scheduler, config=deps.config)

    @app.get("/eventos")
    def eventos_page_route():
        return _eventos_page(deps.state)

    @app.get("/api/channels")
    def api_channels_list():
        if deps.state is None:
            return JSONResponse([])
        return JSONResponse([{"id": k, "title": v} for k, v in deps.state.get_all_channels().items()])

    @app.post("/api/channels")
    async def api_channels_create(req):
        if deps.state is None:
            return JSONResponse({"error": "Servidor ainda inicializando"}, status_code=503)
        body = await req.json()
        cid = body.get("id", "").strip()
        title = body.get("title", "").strip()
        if not cid or not title:
            return JSONResponse({"error": "id e title obrigatorios"}, status_code=400)
        deps.state.channels[cid] = title
        deps.state.save_to_disk()
        return JSONResponse({"ok": True, "id": cid})

    @app.post("/api/channels/add")
    async def api_channels_add(req):
        if deps.state is None or deps.config is None:
            return JSONResponse({"error": "Servidor ainda inicializando"}, status_code=503)

        body = await req.json()
        cid = body.get("id", "").strip()
        handle = body.get("handle", "").strip().lstrip("@")
        if not cid and not handle:
            return JSONResponse({"error": "Forneça id ou handle"}, status_code=400)

        api_keys = deps.config.get_list("youtube_api_keys")
        scraper = YouTubeAPI(api_keys)
        if handle and not cid:
            resolved = scraper.resolve_channel_handles_to_ids([handle], deps.state)
            if not resolved:
                return JSONResponse({"error": f"Handle @{handle} não encontrado"}, status_code=404)
            cid = list(resolved.keys())[0]
        titles = scraper.ensure_channel_titles({cid}, deps.state)
        title = titles.get(cid) or cid
        deps.state.channels[cid] = title
        deps.state.save_to_disk()
        deps.logger.info(f"Canal adicionado via UI: {cid} ({title})")
        return JSONResponse({"ok": True, "id": cid, "title": title})

    @app.delete("/api/channels/{channel_id}")
    def api_channels_delete(channel_id: str):
        if deps.state is None or channel_id not in deps.state.channels:
            return JSONResponse({"error": "nao encontrado"}, status_code=404)
        del deps.state.channels[channel_id]
        frozen = getattr(deps.state, "frozen_channels", set())
        frozen.discard(channel_id)
        deps.state.save_to_disk()
        deps.logger.info(f"Canal deletado via UI: {channel_id}")
        return JSONResponse({"ok": True})

    @app.post("/api/channels/{channel_id}/sync")
    def api_channels_sync(channel_id: str):
        if deps.scheduler is None:
            return JSONResponse({"error": "Scheduler não disponível"}, status_code=503)
        deps.scheduler.trigger_now()
        deps.logger.info(f"Sync forçado via UI para canal: {channel_id}")
        return JSONResponse({"ok": True, "channel_id": channel_id})

    @app.post("/api/channels/{channel_id}/freeze")
    def api_channels_freeze(channel_id: str):
        if deps.state is None:
            return JSONResponse({"error": "State não disponível"}, status_code=503)
        if not hasattr(deps.state, "frozen_channels"):
            deps.state.frozen_channels = set()
        if channel_id in deps.state.frozen_channels:
            deps.state.frozen_channels.discard(channel_id)
            frozen = False
        else:
            deps.state.frozen_channels.add(channel_id)
            frozen = True
        deps.state.save_to_disk()
        deps.logger.info(f"Canal {'congelado' if frozen else 'descongelado'} via UI: {channel_id}")
        return JSONResponse({"ok": True, "channel_id": channel_id, "frozen": frozen})

    @app.get("/api/streams")
    def api_streams_list(status: str = ""):
        if deps.state is None:
            return JSONResponse([])
        streams = deps.state.get_all_streams()
        if status:
            streams = [s for s in streams if s.get("status") == status]
        return JSONResponse([_serialize_stream(deps, s) for s in streams])

    @app.get("/api/streams/{video_id}")
    def api_streams_detail(video_id: str):
        if deps.state is None:
            return JSONResponse({"error": "Servidor ainda inicializando"}, status_code=503)
        stream = deps.state.streams.get(video_id)
        if not stream:
            return JSONResponse({"error": "nao encontrado"}, status_code=404)
        return JSONResponse(_serialize_stream(deps, stream))

    @app.get("/api/config")
    def api_config_get():
        if deps.config is None:
            return JSONResponse({})
        return JSONResponse(deps.config.get_all())

    @app.put("/api/config")
    async def api_config_put(req):
        if deps.config is None:
            return JSONResponse({"error": "Servidor ainda inicializando"}, status_code=503)
        body = await req.json()
        key = body.get("key", "").strip()
        value = str(body.get("value", "")).strip()
        if not key:
            return JSONResponse({"error": "key obrigatorio"}, status_code=400)
        deps.config.update(key, value)
        deps.config.reload()
        if deps.scheduler:
            deps.scheduler.reload_config(deps.config)
        return JSONResponse({"ok": True, "key": key, "value": value})
