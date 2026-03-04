"""
proxy_dashboard.py — Página /proxy

Exibe:
  1. Tabela de playlists com links usando o IP do browser.
  2. Tabela de streams proxy ativos (polling a cada 2s via JS).
  3. Botão para parar um stream manualmente.
"""

from fasthtml.common import *
from starlette.responses import Response


def proxy_dashboard_page(request, playlist_routes: dict, base_url: str) -> Response:
    """
    Renderiza a página do proxy dashboard.

    Args:
        request:          Starlette Request
        playlist_routes:  dict { nome: (mode, mode_type) }
        base_url:         URL base detectada pelo IP do browser (ex: http://192.168.1.10:8000)
    """
    nav = Div(
        A("Dashboard",      href="/",       style="margin-right:12px;"),
        A("Proxy",          href="/proxy",  style="margin-right:12px; font-weight:bold;"),
        A("Force Sync",     href="/force-sync", style="margin-right:12px;"),
        A("Config",         href="/config", style="margin-right:12px;"),
        A("Logs",           href="/logs"),
        style="padding:8px 0 16px 0; border-bottom: 1px solid #ccc; margin-bottom:16px;",
    )

    # -----------------------------------------------------------------------
    # Secão 1: Tabela de playlists
    # -----------------------------------------------------------------------
    playlist_rows = []
    for name, (mode, mode_type) in playlist_routes.items():
        url = f"{base_url}/playlist/{name}"
        proxy_url = f"{base_url}/api/proxy/" + "{video_id}"  # template informativo
        playlist_rows.append(Tr(
            Td(name, style="white-space:nowrap;"),
            Td(mode),
            Td(mode_type),
            Td(
                A(url, href=url, target="_blank",
                  style="font-size:0.82em;word-break:break-all;"),
            ),
        ))

    playlist_table = Div(
        H2("Playlists"),
        P(
            f"IP detectado: ",
            Strong(base_url),
            " — Use esses links no seu player IPTV.",
            style="font-size:0.9em;color:#888;",
        ),
        Table(
            Thead(Tr(
                Th("Arquivo"),
                Th("Modo"),
                Th("Tipo"),
                Th("Link"),
            )),
            Tbody(*playlist_rows),
            style="font-size:0.9em;",
        ),
        style="margin-bottom:32px;",
    )

    # -----------------------------------------------------------------------
    # Secão 2: Link do stream proxy por video_id
    # -----------------------------------------------------------------------
    proxy_info = Div(
        H2("Streaming Proxy"),
        P(
            "Para assistir um stream via proxy use:",
            Br(),
            Code(f"{base_url}/api/proxy/", Strong("VIDEO_ID")),
            style="font-size:0.9em;",
        ),
        P(
            "O proxy inicia o processo automaticamente na primeira conexão "
            "e para o processo 30s após o último cliente sair.",
            style="font-size:0.85em;color:#888;",
        ),
        style="margin-bottom:32px;",
    )

    # -----------------------------------------------------------------------
    # Secão 3: Tabela de streams ativos (polling JS)
    # -----------------------------------------------------------------------
    active_streams = Div(
        H2("Streams Proxy Ativos"),
        Div(
            Button(
                "Atualizar",
                id="btn-refresh",
                type="button",
                style="margin-right:8px;font-size:0.85em;",
            ),
            Span(id="last-update", style="font-size:0.8em;color:#888;"),
            style="margin-bottom:8px;",
        ),
        Div(
            Table(
                Thead(Tr(
                    Th("Video ID"),
                    Th("Buffer (chunks)"),
                    Th("Buffer (MB)"),
                    Th("Clientes"),
                    Th("PID"),
                    Th("Status"),
                    Th("Ação"),
                )),
                Tbody(id="proxy-table-body"),
                style="font-size:0.88em;",
            ),
            P("Nenhum stream proxy ativo.", id="no-streams-msg", style="color:#888;"),
            id="proxy-table-wrapper",
        ),
        style="margin-bottom:32px;",
    )

    # -----------------------------------------------------------------------
    # JavaScript: polling /api/proxy/status a cada 2s
    # -----------------------------------------------------------------------
    js = Script("""
        function statusBadge(alive) {
            const s = document.createElement('span');
            s.textContent = alive ? '\u2705 ativo' : '\u274c parado';
            s.style.color = alive ? '#4c4' : '#f44';
            return s.outerHTML;
        }

        function renderStreams(data) {
            const tbody = document.getElementById('proxy-table-body');
            const noMsg = document.getElementById('no-streams-msg');
            tbody.innerHTML = '';

            if (!data.streams || data.streams.length === 0) {
                noMsg.style.display = '';
                return;
            }
            noMsg.style.display = 'none';

            data.streams.forEach(s => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td><code>${s.video_id}</code></td>
                    <td>${s.buffer_index} (${s.buffer_chunks} no deque)</td>
                    <td>${s.buffer_mb}</td>
                    <td>${s.clients}</td>
                    <td>${s.process_pid || '—'}</td>
                    <td>${statusBadge(s.process_alive)}</td>
                    <td>
                        <button onclick="stopStream('${s.video_id}')"
                                style="font-size:0.8em;padding:2px 8px;cursor:pointer;">
                            Parar
                        </button>
                    </td>
                `;
                tbody.appendChild(tr);
            });

            document.getElementById('last-update').textContent =
                'Atualizado: ' + new Date().toLocaleTimeString();
        }

        function fetchStatus() {
            fetch('/api/proxy/status')
                .then(r => r.json())
                .then(renderStreams)
                .catch(e => console.error('Erro ao buscar status:', e));
        }

        function stopStream(videoId) {
            if (!confirm('Parar stream ' + videoId + '?')) return;
            fetch('/api/proxy/' + videoId, { method: 'DELETE' })
                .then(r => r.json())
                .then(d => {
                    alert(d.ok ? 'Stream parado.' : 'Erro: ' + (d.error || '?'));
                    fetchStatus();
                })
                .catch(e => alert('Erro: ' + e));
        }

        document.getElementById('btn-refresh').onclick = fetchStatus;

        // Polling a cada 2s
        fetchStatus();
        setInterval(fetchStatus, 2000);
    """)

    return Titled(
        "Proxy Dashboard — TubeWrangler",
        nav,
        playlist_table,
        proxy_info,
        active_streams,
        js,
    )
