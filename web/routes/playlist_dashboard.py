"""
playlist_dashboard.py — Página /playlist

Exibe:
  1. Tabela de playlists com links + botão Copiar URL.
  2. Info do proxy.
(A tabela de Streams Proxy Ativos foi movida para o Dashboard.)
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

    js = Script("""
        function copyUrl(btn) {
            const url = btn.getAttribute('data-url');
            if (navigator.clipboard && navigator.clipboard.writeText) {
                navigator.clipboard.writeText(url).then(() => {
                    showCopyFeedback('\u2705 Copiado: ' + url);
                }).catch(() => fallbackCopy(url));
            } else {
                fallbackCopy(url);
            }
        }

        function fallbackCopy(url) {
            const ta = document.createElement('textarea');
            ta.value = url;
            ta.style.position = 'fixed';
            ta.style.opacity  = '0';
            document.body.appendChild(ta);
            ta.focus();
            ta.select();
            try {
                document.execCommand('copy');
                showCopyFeedback('\u2705 Copiado: ' + url);
            } catch (e) {
                prompt('Copie manualmente:', url);
            }
            document.body.removeChild(ta);
        }

        function showCopyFeedback(msg) {
            const fb = document.getElementById('copy-feedback');
            if (!fb) return;
            fb.textContent = msg;
            fb.style.color = '#3fb950';
            setTimeout(() => { fb.textContent = ''; }, 3000);
        }
    """)

    return _page_shell(
        "Playlist", "playlist",
        playlist_table,
        proxy_info,
        js,
    )
