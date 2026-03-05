"""
Página /canais — gerenciamento de canais monitorados.

Funcionalidades:
- Adicionar canal por @handle ou Channel ID (UC...)
- Listar canais com contadores Live / Up / VOD e status (active / frozen)
- Ações por canal: Sincronizar, Congelar/Descongelar, Deletar
"""
from fasthtml.common import *
from web.layout import _page_shell


def channels_page(state, scheduler):
    channels = state.get_all_channels() if state else {}
    frozen   = getattr(state, "frozen_channels", set()) if state else set()
    streams  = list(state.get_all_streams()) if state else []

    # Contar streams por canal e status
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
    for cid, title in channels.items():
        c      = counts.get(cid, {"live": 0, "upcoming": 0, "vod": 0})
        is_frz = cid in frozen
        status_label = "Congelado" if is_frz else "active"
        status_cls   = "badge badge-frozen" if is_frz else "badge badge-active"

        short_id = cid[:18] + "..." if len(cid) > 18 else cid

        rows.append(Tr(
            # Canal
            Td(
                Span(title, style="font-weight:600;"),
                Br(),
                Span(f"@{title.lower().replace(' ', '')}",
                     style="font-size:0.78rem;color:#8b949e;"),
                style="min-width:160px;",
            ),
            # Channel ID
            Td(
                Code(short_id, title=cid,
                     style="font-size:0.78rem;cursor:default;"),
            ),
            # Live
            Td(
                Span(str(c["live"]),
                     style="display:inline-block;min-width:28px;text-align:center;"),
                style="text-align:center;",
            ),
            # Up
            Td(
                Span(str(c["upcoming"]),
                     style="display:inline-block;min-width:28px;text-align:center;"),
                style="text-align:center;",
            ),
            # VOD
            Td(
                Span(str(c["vod"]),
                     style="display:inline-block;min-width:28px;text-align:center;"),
                style="text-align:center;",
            ),
            # Status
            Td(Span(status_label, cls=status_cls), style="text-align:center;"),
            # Ações
            Td(
                # Sincronizar
                Button(
                    "\U0001f504",
                    title="Sincronizar agora",
                    cls="btn-icon btn-sync",
                    onclick=f"channelAction('sync', '{cid}', this)",
                ),
                # Congelar / Descongelar
                Button(
                    "\u23f8" if not is_frz else "\u25b6",
                    title="Congelar" if not is_frz else "Descongelar",
                    cls="btn-icon btn-freeze" + (" btn-frozen" if is_frz else ""),
                    onclick=f"channelAction('freeze', '{cid}', this)",
                ),
                # Deletar
                Button(
                    "\U0001f5d1",
                    title="Deletar canal",
                    cls="btn-icon btn-delete",
                    onclick=f"confirmDelete('{cid}', '{title}')",
                ),
                style="white-space:nowrap;",
            ),
        ))

    table = (
        Table(
            Thead(
                Tr(
                    Th("Canal"),
                    Th("Channel ID"),
                    Th(
                        Span("\u25cf", style="color:#f85149;"),
                        " Live",
                        style="text-align:center;",
                    ),
                    Th(
                        Span("\u25cf", style="color:#d29922;"),
                        " Up",
                        style="text-align:center;",
                    ),
                    Th(
                        Span("\u25cf", style="color:#8b949e;"),
                        " VOD",
                        style="text-align:center;",
                    ),
                    Th("Status", style="text-align:center;"),
                    Th("A\u00e7\u00f5es"),
                )
            ),
            Tbody(*rows),
            id="channels-table",
        )
        if rows
        else P("Nenhum canal adicionado.", cls="text-muted")
    )

    page_styles = Style("""
        /* ---- botões de ação ---- */
        .btn-icon {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 32px;
            height: 32px;
            border-radius: 6px;
            border: 1.5px solid #30363d;
            background: transparent;
            font-size: 1rem;
            cursor: pointer;
            margin-right: 4px;
            transition: background 0.15s, border-color 0.15s;
        }
        .btn-sync:hover  { background: #1f6feb22; border-color: #388bfd; }
        .btn-freeze      { color: #d29922; }
        .btn-freeze:hover{ background: #d2992222; border-color: #d29922; }
        .btn-freeze.btn-frozen { border-color: #388bfd; color: #58a6ff; }
        .btn-delete      { color: #f85149; border-color: #f8514944; }
        .btn-delete:hover{ background: #f8514922; border-color: #f85149; }

        /* ---- badges ---- */
        .badge-active {
            background: #1a3a1a;
            color: #3fb950;
            border: 1px solid #238636;
        }
        .badge-frozen {
            background: #1c2a3a;
            color: #58a6ff;
            border: 1px solid #1f6feb;
        }

        /* ---- add-form ---- */
        #add-channel-form {
            display: flex;
            gap: 8px;
            align-items: center;
            flex-wrap: wrap;
            margin-bottom: 8px;
        }
        #add-channel-form input {
            flex: 1;
            min-width: 220px;
        }
        #add-feedback {
            font-size: 0.85rem;
            min-height: 20px;
            margin-top: 4px;
        }
    """)

    page_js = Script("""
        async function channelAction(action, channelId, btn) {
            const url = `/api/channels/${encodeURIComponent(channelId)}/${action}`;
            btn.disabled = true;
            try {
                const r = await fetch(url, { method: 'POST' });
                const d = await r.json();
                if (!d.ok) { alert('Erro: ' + (d.error || 'desconhecido')); return; }
                if (action === 'sync') {
                    showFeedback('\u2705 Sincroniza\u00e7\u00e3o agendada para ' + channelId, 'ok');
                } else if (action === 'freeze') {
                    // Recarrega para refletir novo estado
                    location.reload();
                }
            } catch(e) {
                alert('Erro de comunica\u00e7\u00e3o: ' + e);
            } finally {
                btn.disabled = false;
            }
        }

        async function confirmDelete(channelId, title) {
            if (!confirm(`Deletar o canal "${title}" (${channelId})?\nEssa a\u00e7\u00e3o n\u00e3o pode ser desfeita.`)) return;
            try {
                const r = await fetch(`/api/channels/${encodeURIComponent(channelId)}`, { method: 'DELETE' });
                const d = await r.json();
                if (d.ok) {
                    const row = document.querySelector(`[data-cid="${channelId}"]`);
                    if (row) row.remove();
                    showFeedback('\u2705 Canal removido.', 'ok');
                    location.reload();
                } else {
                    alert('Erro ao deletar: ' + (d.error || ''));
                }
            } catch(e) {
                alert('Erro: ' + e);
            }
        }

        async function addChannel() {
            const inp = document.getElementById('add-input');
            const val = inp.value.trim();
            if (!val) return;

            const btn = document.getElementById('add-btn');
            btn.disabled = true;
            showFeedback('Adicionando...', 'info');

            // Detecta se é handle (@...) ou Channel ID (UC...)
            let payload = {};
            if (val.startsWith('UC') || val.startsWith('HC')) {
                payload = { id: val };
            } else {
                payload = { handle: val.replace(/^@/, '') };
            }

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
                } else {
                    showFeedback('\u274c ' + (d.error || 'Erro ao adicionar.'), 'error');
                }
            } catch(e) {
                showFeedback('\u274c Erro de comunica\u00e7\u00e3o.', 'error');
            } finally {
                btn.disabled = false;
            }
        }

        function showFeedback(msg, type) {
            const el = document.getElementById('add-feedback');
            el.textContent = msg;
            el.style.color = type === 'ok' ? '#3fb950' : type === 'error' ? '#f85149' : '#8b949e';
        }

        // Enter no input
        document.addEventListener('DOMContentLoaded', () => {
            const inp = document.getElementById('add-input');
            if (inp) inp.addEventListener('keydown', e => {
                if (e.key === 'Enter') { e.preventDefault(); addChannel(); }
            });
        });
    """)

    return _page_shell(
        "Canais", "canais",
        page_styles,
        # Formulário de adição
        Div(
            H2("Novo canal"),
            Div(
                Input(
                    id="add-input",
                    type="text",
                    placeholder="@handle, UC..., ou URL do canal",
                    autocomplete="off",
                    style="flex:1;min-width:240px;",
                ),
                Button(
                    "+ Adicionar",
                    id="add-btn",
                    type="button",
                    onclick="addChannel()",
                ),
                id="add-channel-form",
            ),
            Div(id="add-feedback"),
            cls="card",
        ),
        # Tabela de canais
        Div(
            table,
            cls="card",
        ),
        page_js,
    )
