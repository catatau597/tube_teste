"""
web/routes/eventos.py — Página /eventos

Tabela completa de streams (Live / Upcoming / VOD) monitorados.
O sistema nunca deve conter streams com status="none" — eles são
promovidos para "vod" ou descartados em state_manager.update_streams.
"""
from fasthtml.common import *
from web.layout import _page_shell

# none aqui só como fallback de segurança (não deve aparecer em produção)
_STATUS_BADGE = {
    "live":     ("badge-live",     "🔴 Live"),
    "upcoming": ("badge-upcoming", "🟡 Upcoming"),
    "vod":      ("badge-vod",      "📼 VOD"),
    "recorded": ("badge-vod",      "📼 VOD"),
    "none":     ("badge-vod",      "📼 VOD"),   # fallback de migração
}

_VOD_STATUSES = {"vod", "recorded", "none"}


def eventos_page(state):
    streams = list(state.get_all_streams()) if state else []

    n_live = sum(1 for s in streams if s.get("status") == "live")
    n_up   = sum(1 for s in streams if s.get("status") == "upcoming")
    n_vod  = sum(1 for s in streams if s.get("status") in _VOD_STATUSES)

    rows = []
    for s in streams:
        vid    = s.get("videoid", s.get("video_id", ""))
        title  = s.get("title", "—")
        status = s.get("status", "")
        chan   = s.get("channelname", s.get("channel_title", s.get("channelid", "—")))
        sched  = s.get("scheduledstarttimeutc") or s.get("scheduled_start") or s.get("start_time") or ""
        if hasattr(sched, "isoformat"):
            sched = sched.isoformat()
        yt_url = f"https://www.youtube.com/watch?v={vid}" if vid else "#"

        badge_cls, badge_label = _STATUS_BADGE.get(status, ("badge-none", status or "?"))

        # data-status para filtro JS
        filter_status = "vod" if status in _VOD_STATUSES else status

        rows.append(Tr(
            Td(Span(badge_label, cls=f"badge {badge_cls}")),
            Td(
                A(title, href=yt_url, target="_blank",
                  style="color:#58a6ff;text-decoration:none;font-weight:500;"),
                style="max-width:360px;",
            ),
            Td(chan, style="font-size:0.85rem;color:#8b949e;"),
            Td(Code(vid, style="font-size:0.78rem;") if vid else Span("—")),
            Td(
                Span(str(sched)[:16].replace("T", " ") if sched else "—",
                     style="font-size:0.82rem;color:#8b949e;"),
            ),
            **{"data-status": filter_status},
        ))

    header_stats = Div(
        Span(f"\u25cf {n_live} Live",    style="color:#f85149;margin-right:16px;font-weight:600;"),
        Span(f"\u25cf {n_up} Upcoming",  style="color:#d29922;margin-right:16px;font-weight:600;"),
        Span(f"\u25cf {n_vod} VOD",      style="color:#8b949e;font-weight:600;"),
        style="margin-bottom:12px;font-size:0.9rem;",
    )

    table = (
        Table(
            Thead(Tr(
                Th("Status"),
                Th("Título"),
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

    # Filtro via data-status no <tr> — mais robusto que inspecionar texto do badge
    page_js = Script("""
        function setFilter(status) {
            document.querySelectorAll('.filter-btn').forEach(b => {
                b.classList.toggle('active', b.dataset.filter === status);
            });
            document.querySelectorAll('#eventos-table tbody tr').forEach(row => {
                const s = row.dataset.status || '';
                row.style.display = (status === 'all' || s === status) ? '' : 'none';
            });
        }
        document.addEventListener('DOMContentLoaded', () => setFilter('all'));
    """)

    return _page_shell(
        "Eventos", "eventos",
        page_styles,
        Div(
            header_stats,
            Div(
                Button("Todos",   cls="filter-btn active", **{"data-filter": "all"},      onclick="setFilter('all')"),
                Button("🔴 Live", cls="filter-btn",        **{"data-filter": "live"},     onclick="setFilter('live')"),
                Button("🟡 Up",   cls="filter-btn",        **{"data-filter": "upcoming"}, onclick="setFilter('upcoming')"),
                Button("📼 VOD",  cls="filter-btn",        **{"data-filter": "vod"},      onclick="setFilter('vod')"),
                id="filter-bar",
            ),
            table,
            cls="card",
        ),
        page_js,
    )
