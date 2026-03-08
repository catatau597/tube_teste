"""
web/routes/logs.py
-----------------
Página de logs e APIs de logging/SSE.
"""
from __future__ import annotations

import asyncio
import logging

from fasthtml.common import *
from starlette.responses import JSONResponse, StreamingResponse

from core.proxy_manager import set_debug_mode
from web.app_deps import AppDeps
from web.layout import _page_shell


_TOGGLE_STYLE = Style(
    """
    .bool-toggle {
        display: inline-flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 14px;
        cursor: pointer;
        user-select: none;
    }
    .bool-toggle .toggle-pill {
        display: inline-flex;
        align-items: center;
        padding: 4px 14px;
        border-radius: 999px;
        font-size: 0.82rem;
        font-weight: 600;
        border: 1.5px solid transparent;
        transition: background 0.15s, color 0.15s, border-color 0.15s;
        cursor: pointer;
    }
    .bool-toggle .toggle-pill.on { background: #1f6feb; color: #fff; border-color: #388bfd; }
    .bool-toggle .toggle-pill.off { background: transparent; color: #8b949e; border-color: #30363d; }
    .bool-toggle .toggle-label { font-size: 0.9rem; color: #e6edf3; }
"""
)


_TOGGLE_JS = Script(
    """
    function _toggleBool(btn, hiddenId) {
        const hidden = document.getElementById(hiddenId);
        const isOn = hidden.value === 'true';
        hidden.value = isOn ? 'false' : 'true';
        btn.textContent = isOn ? 'Desligado' : 'Ligado';
        btn.className = 'toggle-pill ' + (isOn ? 'off' : 'on');
    }
"""
)


def register_logs_routes(app, deps: AppDeps) -> None:
    @app.get("/api/logs/stream")
    async def api_logs_stream():
        async def event_gen():
            snapshot = list(deps.log_buffer)
            for _, line in snapshot:
                yield f"data: {line}\n\n"
            last_seq = snapshot[-1][0] if snapshot else 0
            while True:
                await asyncio.sleep(1)
                current = list(deps.log_buffer)
                new_entries = [(seq, line) for seq, line in current if seq > last_seq]
                for seq, line in new_entries:
                    yield f"data: {line}\n\n"
                    last_seq = seq

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/logs/level")
    def api_logs_level_get():
        current = logging.getLogger().level
        return JSONResponse({"level": logging.getLevelName(current)})

    @app.post("/api/logs/level")
    async def api_logs_level_set(req):
        body = await req.json()
        level = body.get("level", "INFO").upper()
        if level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            return JSONResponse({"error": "Nível inválido"}, status_code=400)

        hide_access = deps.config.get_bool("hide_access_logs") if deps.config else True
        if deps.setup_logging:
            deps.setup_logging(level, hide_access=hide_access)

        if deps.config:
            try:
                deps.config.update("log_level", level)
                deps.config.reload()
            except Exception:
                pass

        if deps.config:
            streaming_debug = deps.config.get_bool("streaming_debug_enabled")
            set_debug_mode(streaming_debug)

        deps.logger.info(f"Nível de log alterado para {level} via UI.")
        return JSONResponse({"ok": True, "level": level})

    @app.post("/api/logs/hide-access")
    async def api_logs_hide_access_set(req):
        body = await req.json()
        hide_access = bool(body.get("hide_access", True))
        if deps.config:
            deps.config.update("hide_access_logs", "true" if hide_access else "false")
            deps.config.reload()
        current_level = logging.getLevelName(logging.getLogger().level)
        if deps.setup_logging:
            deps.setup_logging(current_level, hide_access=hide_access)
        deps.logger.info(f"Ocultar logs de acesso HTTP: {hide_access}")
        return JSONResponse({"ok": True, "hide_access": hide_access})

    @app.post("/api/logs/streaming-debug")
    async def api_logs_streaming_debug_set(req):
        body = await req.json()
        debug_enabled = bool(body.get("debug_enabled", False))
        if deps.config:
            deps.config.update("streaming_debug_enabled", "true" if debug_enabled else "false")
            deps.config.reload()
        set_debug_mode(debug_enabled)
        deps.logger.info(f"Debug de streaming: {'ATIVADO' if debug_enabled else 'DESATIVADO'}")
        return JSONResponse({"ok": True, "debug_enabled": debug_enabled})

    @app.get("/logs")
    def logs_page():
        current_level = logging.getLevelName(logging.getLogger().level)
        hide_access = deps.config.get_bool("hide_access_logs") if deps.config else True
        streaming_debug = deps.config.get_bool("streaming_debug_enabled") if deps.config else False

        logging_panel = Div(
            Details(
                Summary(
                    "🔧 Configuração de Logging",
                    style="cursor:pointer;font-weight:600;color:#58a6ff;font-size:0.95rem;",
                ),
                Div(
                    P(
                        "Nível atual: ",
                        Strong(current_level, id="current-level-badge", style="color:#58a6ff;"),
                        cls="text-muted",
                        style="margin-bottom:12px;",
                    ),
                    Div(
                        *[
                            Button(
                                lv,
                                type="button",
                                cls="btn-secondary",
                                id=f"btn-level-{lv}",
                                onclick=f"setLogLevel('{lv}')",
                                style=(
                                    "margin-right:8px;font-size:0.82rem;padding:5px 14px;"
                                    + ("border-color:#58a6ff;color:#58a6ff;" if lv == current_level else "")
                                ),
                            )
                            for lv in ("DEBUG", "INFO", "WARNING", "ERROR")
                        ],
                        id="level-buttons",
                        style="margin-bottom:14px;",
                    ),
                    _TOGGLE_STYLE,
                    _TOGGLE_JS,
                    Div(
                        Input(type="hidden", id="hidden_hide_access_logs", value="true" if hide_access else "false"),
                        Button(
                            "Ligado" if hide_access else "Desligado",
                            type="button",
                            cls="toggle-pill " + ("on" if hide_access else "off"),
                            onclick="_toggleBool(this, 'hidden_hide_access_logs'); toggleHideAccess(document.getElementById('hidden_hide_access_logs').value === 'true')",
                        ),
                        Span(
                            "Ocultar logs de acesso HTTP (GET / e /api/logs/stream)",
                            cls="toggle-label",
                            style="font-size:0.85rem;color:#8b949e;",
                        ),
                        cls="bool-toggle",
                        style="margin-bottom:10px;",
                    ),
                    Div(
                        Input(type="hidden", id="hidden_streaming_debug", value="true" if streaming_debug else "false"),
                        Button(
                            "Ligado" if streaming_debug else "Desligado",
                            type="button",
                            cls="toggle-pill " + ("on" if streaming_debug else "off"),
                            onclick="_toggleBool(this, 'hidden_streaming_debug'); toggleStreamingDebug(document.getElementById('hidden_streaming_debug').value === 'true')",
                        ),
                        Span(
                            "Debug detalhado de streaming (ffmpeg verbose + métricas de buffer/clientes)",
                            cls="toggle-label",
                            style="font-size:0.85rem;color:#8b949e;",
                        ),
                        cls="bool-toggle",
                        style="margin-bottom:10px;",
                    ),
                    Div(id="level-feedback", style="font-size:0.82rem;color:#3fb950;min-height:18px;"),
                    style="padding:12px 0 4px;",
                ),
            ),
            cls="card",
            style="margin-bottom:16px;",
        )

        controls = Div(
            Select(
                Option("DEBUG", value="DEBUG"),
                Option("INFO", value="INFO", selected=True),
                Option("WARNING", value="WARNING"),
                Option("ERROR", value="ERROR"),
                id="log-level-filter",
                style="margin-right:8px;width:120px;",
            ),
            Button("Limpar", id="btn-clear", type="button", cls="btn-secondary", style="margin-right:8px;font-size:0.85em;"),
            Label(
                Input(type="checkbox", id="auto-scroll", checked=True, style="margin-right:4px;"),
                "Auto-scroll",
                style="display:inline-flex;align-items:center;font-size:0.85rem;",
            ),
            style="margin-bottom:10px;display:flex;align-items:center;",
        )

        return _page_shell(
            "Logs",
            "logs",
            logging_panel,
            controls,
            Pre(
                id="log-output",
                style=(
                    "height:72vh;overflow-y:auto;"
                    "background:#0d1117;color:#eee;"
                    "font-size:0.76rem;padding:10px;"
                    "border:1px solid #30363d;border-radius:6px;"
                ),
            ),
            Style(
                """
                .log-DEBUG   { color: #484f58; }
                .log-INFO    { color: #e6edf3; }
                .log-WARNING { color: #d29922; }
                .log-ERROR   { color: #f85149; font-weight:bold; }
"""
            ),
            Script(
                """
                const output = document.getElementById('log-output');
                const filter = document.getElementById('log-level-filter');
                const autoScroll = document.getElementById('auto-scroll');
                const btnClear = document.getElementById('btn-clear');
                btnClear.onclick = () => { output.innerHTML = ''; };

                const LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR'];

                function levelOf(line) {
                    for (const lv of LEVELS) {
                        if (line.includes(' ' + lv + ' ') || line.includes(' ' + lv + '\\t')) return lv;
                    }
                    return 'INFO';
                }

                function applyVisibility(span) {
                    const minLevel = filter.value;
                    const lv = span.dataset.level || 'INFO';
                    span.style.display = LEVELS.indexOf(lv) >= LEVELS.indexOf(minLevel) ? '' : 'none';
                }

                function appendLine(line) {
                    const span = document.createElement('span');
                    const lv = levelOf(line);
                    span.className = 'log-' + lv;
                    span.dataset.level = lv;
                    span.textContent = line + '\\n';
                    output.appendChild(span);
                    applyVisibility(span);
                    if (autoScroll.checked) output.scrollTop = output.scrollHeight;
                }

                const es = new EventSource('/api/logs/stream');
                es.onmessage = e => appendLine(e.data);
                es.onerror = () => appendLine('[conexao perdida - reconectando...]');

                filter.onchange = () => {
                    output.querySelectorAll('span').forEach(applyVisibility);
                };

                function setLogLevel(level) {
                    fetch('/api/logs/level', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ level }),
                    })
                    .then(r => r.json())
                    .then(d => {
                        if (d.ok) {
                            document.getElementById('current-level-badge').textContent = d.level;
                            document.getElementById('level-feedback').textContent = '✅ Nível alterado para ' + d.level;
                            document.querySelectorAll('[id^="btn-level-"]').forEach(b => {
                                b.style.borderColor = '';
                                b.style.color = '';
                            });
                            const active = document.getElementById('btn-level-' + d.level);
                            if (active) { active.style.borderColor='#58a6ff'; active.style.color='#58a6ff'; }
                            setTimeout(() => { document.getElementById('level-feedback').textContent=''; }, 3000);
                        }
                    })
                    .catch(() => {
                        document.getElementById('level-feedback').textContent = '❌ Erro ao alterar nível.';
                        document.getElementById('level-feedback').style.color = '#f85149';
                    });
                }

                function toggleHideAccess(hide) {
                    fetch('/api/logs/hide-access', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ hide_access: hide }),
                    })
                    .then(r => r.json())
                    .then(d => {
                        if (d.ok) {
                            const msg = hide ? '✅ Logs de acesso ocultados.' : '✅ Logs de acesso visíveis.';
                            document.getElementById('level-feedback').textContent = msg;
                            setTimeout(() => { document.getElementById('level-feedback').textContent=''; }, 3000);
                        }
                    })
                    .catch(() => {
                        document.getElementById('level-feedback').textContent = '❌ Erro ao salvar preferência.';
                        document.getElementById('level-feedback').style.color = '#f85149';
                    });
                }

                function toggleStreamingDebug(enabled) {
                    fetch('/api/logs/streaming-debug', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ debug_enabled: enabled }),
                    })
                    .then(r => r.json())
                    .then(d => {
                        if (d.ok) {
                            const msg = enabled ? '✅ Debug de streaming ativado.' : '✅ Debug de streaming desativado.';
                            document.getElementById('level-feedback').textContent = msg;
                            setTimeout(() => { document.getElementById('level-feedback').textContent=''; }, 3000);
                        }
                    })
                    .catch(() => {
                        document.getElementById('level-feedback').textContent = '❌ Erro ao salvar configuração.';
                        document.getElementById('level-feedback').style.color = '#f85149';
                    });
                }
"""
            ),
        )
