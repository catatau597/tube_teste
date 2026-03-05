"""
playlist_dashboard.py — Página /playlist

Exibe:
  1. Tabela de playlists com links + botão Copiar URL.
  2. Info do proxy.
  3. Tabela de streams proxy ativos (polling a cada 2s via JS).
"""
from fasthtml.common import *
from starlette.responses import Response
from web.layout import _page_shell


def playlist_dashboard_page(request, playlist_routes: dict, base_url: str) -> Response:
    playlist_rows = []
    for name, (mode, mode_type) in playlist_routes.items():
        url = f"{base_url}/playlist/{name}"
        playlist_rows.append(Tr(
            Td(name, style="white-space:nowrap;"),
            Td(mode),
            Td(mode_type),
            Td(
                A(url, href=url, target="_blank",
                  style="font-size:0.82em;word-break:break-all;"),
            ),
            # Coluna Copiar
            Td(
                Button(
                    "\U0001f4cb Copiar",
                    type="button",
                    cls="btn-copy btn-secondary",
                    style="font-size:0.8em;padding:4px 10px;white-space:nowrap;",
                    **{"data-url": url},
                    onclick="copyUrl(this)",
                ),
                style="white-space:nowrap;",
            ),
        ))

    playlist_table = Div(
        H2("Playlists"),
        P(
            "IP detectado: ", Strong(base_url),
            " \u2014 Use esses links no seu player IPTV.",
            cls="text-muted",
        ),
        Div(id="copy-feedback", style="font-size:0.82rem;min-height:18px;margin-bottom:6px;"),
        Table(
            Thead(Tr(
                Th("Arquivo"),
                Th("Modo"),
                Th("Tipo"),
                Th("Link"),
                Th("Copiar"),
            )),
            Tbody(*playlist_rows),
        ),
        style="margin-bottom:32px;",
    )

    proxy_info = Div(
        H2("Streaming Proxy"),
        P(
            "Para assistir um stream via proxy use:",
            Br(),
            Code(f"{base_url}/api/proxy/"), Strong("VIDEO_ID"),
        ),
        P(
            "O proxy inicia o processo automaticamente na primeira conex\u00e3o "
            "e para o processo 30s ap\u00f3s o \u00faltimo cliente sair.",
            cls="text-muted",
        ),
        style="margin-bottom:32px;",
    )

    active_streams = Div(
        H2("Streams Proxy Ativos"),
        Div(
            Button("Atualizar", id="btn-refresh", type="button", cls="btn-secondary",
                   style="margin-right:8px;font-size:0.85em;"),
            Span(id="last-update", style="font-size:0.8em;color:#8b949e;"),
            style="margin-bottom:8px;",
        ),
        Div(
            Table(
                Thead(Tr(
                    Th("Video ID"), Th("Buffer (chunks)"), Th("Buffer (MB)"),
                    Th("Clientes"), Th("PID"), Th("Status"), Th("A\u00e7\u00e3o"),
                )),
                Tbody(id="proxy-table-body"),
            ),
            P("Nenhum stream proxy ativo.", id="no-streams-msg", cls="text-muted"),
        ),
        style="margin-bottom:32px;",
    )

    js = Script("""
        function copyUrl(btn) {
            const url = btn.getAttribute('data-url');
            navigator.clipboard.writeText(url).then(() => {
                const fb = document.getElementById('copy-feedback');
                fb.textContent = '\u2705 Copiado: ' + url;
                fb.style.color = '#3fb950';
                setTimeout(() => { fb.textContent = ''; }, 3000);
            }).catch(() => {
                prompt('Copie manualmente:', url);
            });
        }

        function statusBadge(alive) {
            const s = document.createElement('span');
            s.className = alive ? 'badge badge-live' : 'badge badge-none';
            s.textContent = alive ? '\u2705 ativo' : '\u274c parado';
            return s.outerHTML;
        }

        function renderStreams(data) {
            const tbody = document.getElementById('proxy-table-body');
            const noMsg = document.getElementById('no-streams-msg');
            tbody.innerHTML = '';
            if (!data.streams || data.streams.length === 0) {
                noMsg.style.display = ''; return;
            }
            noMsg.style.display = 'none';
            data.streams.forEach(s => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td><code>${s.video_id}</code></td>
                    <td>${s.buffer_index} (${s.buffer_chunks} no deque)</td>
                    <td>${s.buffer_mb}</td>
                    <td>${s.clients}</td>
                    <td>${s.process_pid || '\u2014'}</td>
                    <td>${statusBadge(s.process_alive)}</td>
                    <td><button onclick="stopStream('${s.video_id}')"
                        style="font-size:0.8em;padding:2px 8px;cursor:pointer;">Parar</button></td>
                `;
                tbody.appendChild(tr);
            });
            document.getElementById('last-update').textContent =
                'Atualizado: ' + new Date().toLocaleTimeString();
        }

        function fetchStatus() {
            fetch('/api/proxy/status').then(r => r.json()).then(renderStreams)
                .catch(e => console.error('Erro ao buscar status:', e));
        }

        function stopStream(videoId) {
            if (!confirm('Parar stream ' + videoId + '?')) return;
            fetch('/api/proxy/' + videoId, { method: 'DELETE' })
                .then(r => r.json())
                .then(d => { alert(d.ok ? 'Stream parado.' : 'Erro: ' + (d.error||'?')); fetchStatus(); })
                .catch(e => alert('Erro: ' + e));
        }

        document.getElementById('btn-refresh').onclick = fetchStatus;
        fetchStatus();
        setInterval(fetchStatus, 2000);
    """)

    return _page_shell(
        "Playlist", "playlist",
        playlist_table,
        proxy_info,
        active_streams,
        js,
    )
