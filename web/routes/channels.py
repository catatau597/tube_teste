"""
Página /canais — gerenciamento de canais monitorados.
"""
from fasthtml.common import *
from web.layout import _page_shell


def channels_page(state, scheduler):
    # Usa o novo método que retorna thumbnail junto
    channels_list = state.get_all_channels_with_thumbnail() if state else []
    frozen   = getattr(state, "frozen_channels", set()) if state else set()
    streams  = list(state.get_all_streams()) if state else []

    counts: dict[str, dict] = {}
    for s in streams:
        cid    = s.get("channelid", "")
        status = s.get("status", "")
        if cid not in counts:
            counts[cid] = {"live": 0, "upcoming": 0, "vod": 0}
        if status == "live":
            counts[cid]["live"] += 1
        elif status == "upcoming":
            counts[cid]["upcoming"] += 1
        elif status in ("vod", "recorded"):
            counts[cid]["vod"] += 1

    rows = []
    for ch in channels_list:
        cid   = ch["cid"]
        title = ch["title"]
        thumb = ch["thumbnail_url"]   # URL direta — sem proxy
        c      = counts.get(cid, {"live": 0, "upcoming": 0, "vod": 0})
        is_frz = cid in frozen
        status_label = "Congelado" if is_frz else "Ativo"
        status_cls   = "badge badge-frozen" if is_frz else "badge badge-active"
        short_id     = cid[:18] + "..." if len(cid) > 18 else cid
        initial      = (title[0].upper() if title else "?")

        if thumb:
            avatar = Img(
                src=thumb,
                alt=title,
                onerror="this.style.display='none';this.nextElementSibling.style.display='flex';",
                style="width:32px;height:32px;border-radius:50%;object-fit:cover;flex-shrink:0;",
            )
        else:
            avatar = ""

        fallback_avatar = Div(
            initial,
            style=(
                f"{'display:none' if thumb else 'display:flex'};"
                "width:32px;height:32px;border-radius:50%;"
                "background:#238636;color:#fff;font-weight:700;font-size:0.9rem;"
                "align-items:center;justify-content:center;flex-shrink:0;"
            ),
        )

        rows.append(Tr(
            Td(
                Div(
                    avatar,
                    fallback_avatar,
                    Div(
                        Span(title, style="font-weight:600;line-height:1.2;"),
                        Br(),
                        Span(
                            cid,
                            style="font-size:0.72rem;color:#484f58;font-family:monospace;",
                        ),
                        style="display:flex;flex-direction:column;justify-content:center;",
                    ),
                    style="display:flex;align-items:center;gap:10px;",
                ),
                style="min-width:220px;",
            ),
            Td(Code(short_id, title=cid, style="font-size:0.78rem;cursor:default;")),
            Td(Span(str(c["live"]),    style="display:inline-block;min-width:28px;text-align:center;"), style="text-align:center;"),
            Td(Span(str(c["upcoming"]),style="display:inline-block;min-width:28px;text-align:center;"), style="text-align:center;"),
            Td(Span(str(c["vod"]),     style="display:inline-block;min-width:28px;text-align:center;"), style="text-align:center;"),
            Td(Span(status_label, cls=status_cls), style="text-align:center;"),
            Td(
                Button("\U0001f504", title="Sincronizar agora",  cls="btn-icon btn-sync",   onclick=f"channelAction('sync',   '{cid}', this)"),
                Button(
                    "\u23f8" if not is_frz else "\u25b6",
                    title="Congelar" if not is_frz else "Descongelar",
                    cls="btn-icon btn-freeze" + (" btn-frozen" if is_frz else ""),
                    onclick=f"channelAction('freeze', '{cid}', this)",
                ),
                Button("\U0001f5d1", title="Deletar canal",      cls="btn-icon btn-delete", onclick=f"confirmDelete('{cid}', '{title}')"),
                style="white-space:nowrap;",
            ),
        ))

    table = (
        Table(
            Thead(Tr(
                Th("Canal"),
                Th("Channel ID"),
                Th(Span("\u25cf", style="color:#f85149;"), " Live",    style="text-align:center;"),
                Th(Span("\u25cf", style="color:#d29922;"), " Up",      style="text-align:center;"),
                Th(Span("\u25cf", style="color:#8b949e;"), " VOD",     style="text-align:center;"),
                Th("Status", style="text-align:center;"),
                Th("A\u00e7\u00f5es"),
            )),
            Tbody(*rows),
            id="channels-table",
        )
        if rows else P("Nenhum canal adicionado.", cls="text-muted")
    )

    page_styles = Style("""
        .btn-icon {
            display:inline-flex;align-items:center;justify-content:center;
            width:32px;height:32px;border-radius:6px;
            border:1.5px solid #30363d;background:transparent;
            font-size:1rem;cursor:pointer;margin-right:4px;
            transition:background 0.15s,border-color 0.15s;
        }
        .btn-sync:hover   { background:#1f6feb22; border-color:#388bfd; }
        .btn-freeze       { color:#d29922; }
        .btn-freeze:hover { background:#d2992222; border-color:#d29922; }
        .btn-freeze.btn-frozen { border-color:#388bfd; color:#58a6ff; }
        .btn-delete       { color:#f85149; border-color:#f8514944; }
        .btn-delete:hover { background:#f8514922; border-color:#f85149; }
        #add-channel-form {
            display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px;
        }
        #add-channel-form input { flex:1;min-width:220px; }
        #add-feedback { font-size:0.85rem;min-height:20px;margin-top:4px; }
    """)

    page_js = Script("""
        async function channelAction(action, channelId, btn) {
            btn.disabled = true;
            try {
                const r = await fetch(`/api/channels/${encodeURIComponent(channelId)}/${action}`, { method: 'POST' });
                const d = await r.json();
                if (!d.ok) { alert('Erro: ' + (d.error || 'desconhecido')); return; }
                if (action === 'sync') {
                    showFeedback('\u2705 Sincroniza\u00e7\u00e3o agendada para ' + channelId, 'ok');
                } else { location.reload(); }
            } catch(e) { alert('Erro de comunica\u00e7\u00e3o: ' + e); }
            finally { btn.disabled = false; }
        }

        async function confirmDelete(channelId, title) {
            if (!confirm(`Deletar o canal "${title}"?\nEssa a\u00e7\u00e3o n\u00e3o pode ser desfeita.`)) return;
            const r = await fetch(`/api/channels/${encodeURIComponent(channelId)}`, { method: 'DELETE' });
            const d = await r.json();
            if (d.ok) { showFeedback('\u2705 Canal removido.', 'ok'); location.reload(); }
            else alert('Erro ao deletar: ' + (d.error || ''));
        }

        async function addChannel() {
            const inp = document.getElementById('add-input');
            const val = inp.value.trim();
            if (!val) return;
            const btn = document.getElementById('add-btn');
            btn.disabled = true;
            showFeedback('Adicionando...', 'info');
            const payload = (val.startsWith('UC') || val.startsWith('HC'))
                ? { id: val }
                : { handle: val.replace(/^@/, '') };
            try {
                const r = await fetch('/api/channels/add', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                });
                const d = await r.json();
                if (d.ok) {
                    showFeedback('\u2705 Canal adicionado: ' + d.title, 'ok');
                    inp.value = '';
                    setTimeout(() => location.reload(), 800);
                } else { showFeedback('\u274c ' + (d.error || 'Erro ao adicionar.'), 'error'); }
            } catch(e) { showFeedback('\u274c Erro de comunica\u00e7\u00e3o.', 'error'); }
            finally { btn.disabled = false; }
        }

        function showFeedback(msg, type) {
            const el = document.getElementById('add-feedback');
            el.textContent = msg;
            el.style.color = type==='ok' ? '#3fb950' : type==='error' ? '#f85149' : '#8b949e';
        }

        document.addEventListener('DOMContentLoaded', () => {
            const inp = document.getElementById('add-input');
            if (inp) inp.addEventListener('keydown', e => { if (e.key==='Enter') { e.preventDefault(); addChannel(); } });
        });
    """)

    return _page_shell(
        "Canais", "canais",
        page_styles,
        Div(
            H2("Novo canal"),
            Div(
                Input(id="add-input", type="text", placeholder="@handle, UC..., ou URL do canal",
                      autocomplete="off", style="flex:1;min-width:240px;"),
                Button("+ Adicionar", id="add-btn", type="button", onclick="addChannel()"),
                id="add-channel-form",
            ),
            Div(id="add-feedback"),
            cls="card",
        ),
        Div(table, cls="card"),
        page_js,
    )
