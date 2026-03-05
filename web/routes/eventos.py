"""
web/routes/eventos.py — Página /eventos

Tabela completa de streams (Live / Upcoming / VOD) monitorados,
equivalente à seção "Streams" que estava no Dashboard.
"""
from fasthtml.common import *
from web.layout import _page_shell

_STATUS_BADGE = {
    "live":     ("badge-live",     "🔴 Live"),
    "upcoming": ("badge-upcoming", "🟡 Upcoming"),
    "vod":      ("badge-vod",      "📼 VOD"),
    "recorded": ("badge-vod",      "📼 VOD"),
}


def eventos_page(state):
    streams = list(state.get_all_streams()) if state else []

    # Contadores para o cabeçalho
    n_live = sum(1 for s in streams if s.get("status") == "live")
    n_up   = sum(1 for s in streams if s.get("status") == "upcoming")
    n_vod  = sum(1 for s in streams if s.get("status") in ("vod", "recorded"))

    rows = []
    for s in streams:
        vid    = s.get("videoid", s.get("video_id", ""))
        title  = s.get("title", "—")
        status = s.get("status", "")
        chan   = s.get("channel_title", s.get("channelid", "—"))
        sched  = s.get("scheduled_start", s.get("start_time", "")) or ""
        yt_url = f"https://www.youtube.com/watch?v={vid}" if vid else "#"

        badge_cls, badge_label = _STATUS_BADGE.get(status, ("badge-none", status or "?"))

        rows.append(Tr(
            Td(Span(badge_label, cls=f"badge {badge_cls}")),
            Td(
                A(title, href=yt_url, target="_blank",
                  style="color:#58a6ff;text-decoration:none;font-weight:500;"),
                style="max-width:360px;",
            ),
            Td(chan, style="font-size:0.85rem;color:#8b949e;"),
            Td(
                Code(vid, style="font-size:0.78rem;") if vid else Span("—"),
            ),
            Td(
                Span(sched[:16].replace("T", " ") if sched else "—",
                     style="font-size:0.82rem;color:#8b949e;"),
            ),
        ))

    header_stats = Div(
        Span(f"\u25cf {n_live} Live",     style="color:#f85149;margin-right:16px;font-weight:600;"),
        Span(f"\u25cf {n_up} Upcoming",  style="color:#d29922;margin-right:16px;font-weight:600;"),
        Span(f"\u25cf {n_vod} VOD",       style="color:#8b949e;font-weight:600;"),
        style="margin-bottom:12px;font-size:0.9rem;",
    )

    table = (
        Table(
            Thead(Tr(
                Th("Status"),
                Th("T\u00edtulo"),
                Th("Canal"),
                Th("Video ID"),
                Th("Agendado"),
            )),
            Tbody(*rows),
            id="eventos-table",
        )
        if rows else P("Nenhum evento encontrado.", cls="text-muted")
    )

    page_styles = Style("""
        #filter-bar {
            display:flex;gap:8px;flex-wrap:wrap;
            align-items:center;margin-bottom:12px;
        }
        .filter-btn {
            padding:4px 14px;border-radius:14px;
            border:1.5px solid #30363d;background:transparent;
            color:#8b949e;font-size:0.82rem;cursor:pointer;
            transition:background .15s,border-color .15s,color .15s;
        }
        .filter-btn:hover, .filter-btn.active {
            background:#21262d;color:#e6edf3;border-color:#58a6ff;
        }
    """)

    page_js = Script("""
        // Filtro client-side por status
        function setFilter(status) {
            document.querySelectorAll('.filter-btn').forEach(b => {
                b.classList.toggle('active', b.dataset.filter === status);
            });
            const rows = document.querySelectorAll('#eventos-table tbody tr');
            rows.forEach(row => {
                const badge = row.querySelector('.badge');
                if (!badge) return;
                const txt = badge.textContent.toLowerCase();
                const show = status === 'all' ||
                             (status === 'live'     && txt.includes('live')) ||
                             (status === 'upcoming' && txt.includes('upcoming')) ||
                             (status === 'vod'      && txt.includes('vod'));
                row.style.display = show ? '' : 'none';
            });
        }
        document.addEventListener('DOMContentLoaded', () => setFilter('all'));
    """)

    return _page_shell(
        "Eventos", "eventos",
        page_styles,
        Div(
            header_stats,
            # Barra de filtro rápido
            Div(
                Button("Todos",    cls="filter-btn active", **{"data-filter": "all"},      onclick="setFilter('all')"),
                Button("🔴 Live",  cls="filter-btn",        **{"data-filter": "live"},     onclick="setFilter('live')"),
                Button("🟡 Up",    cls="filter-btn",        **{"data-filter": "upcoming"}, onclick="setFilter('upcoming')"),
                Button("📼 VOD",   cls="filter-btn",        **{"data-filter": "vod"},      onclick="setFilter('vod')"),
                id="filter-bar",
            ),
            table,
            cls="card",
        ),
        page_js,
    )
