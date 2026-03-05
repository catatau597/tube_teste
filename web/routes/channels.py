"""
web/routes/channels.py
----------------------
Página /canais: gerenciamento de canais YouTube monitorados.
Permite adicionar por @handle ou Channel ID, sincronizar,
congelar (pausar busca global) e deletar canais.
"""

from fasthtml.common import *
from starlette.responses import JSONResponse

from web.layout import _page_shell

_CHANNELS_CSS = Style("""
/* ---------- Channels page ---------- */
.ch-add-bar {
    display: flex;
    gap: 10px;
    margin-bottom: 28px;
    align-items: center;
}
.ch-add-bar input {
    flex: 1;
    margin: 0;
    padding: 8px 14px;
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 6px;
    color: #e6edf3;
    font-size: 0.9rem;
}
.ch-add-bar button {
    white-space: nowrap;
    padding: 8px 18px;
    font-size: 0.9rem;
}

.ch-table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
.ch-table th {
    text-align: left;
    padding: 8px 10px;
    background: #21262d;
    color: #8b949e;
    font-weight: 600;
    border-bottom: 1px solid #30363d;
}
.ch-table td {
    padding: 8px 10px;
    border-bottom: 1px solid #21262d;
    vertical-align: middle;
}
.ch-table tr:hover td { background: #161b22; }

.ch-avatar {
    width: 36px; height: 36px;
    border-radius: 50%;
    object-fit: cover;
    vertical-align: middle;
    margin-right: 8px;
    background: #21262d;
}
.ch-name-cell { display: flex; align-items: center; }

.ch-id { font-family: monospace; font-size: 0.82rem; color: #8b949e; }

.badge-num {
    display: inline-block;
    min-width: 24px;
    text-align: center;
    padding: 2px 6px;
    border-radius: 10px;
    font-size: 0.75rem;
    font-weight: 700;
}
.badge-live-num     { background: #1a7f37; color: #fff; }
.badge-upcoming-num { background: #9a6700; color: #fff; }
.badge-vod-num      { background: #1f6feb; color: #fff; }
.badge-zero         { background: #21262d; color: #8b949e; }

.ch-status-active  { display: inline-flex; align-items:center; gap:5px; color:#3fb950; font-size:0.82rem; }
.ch-status-frozen  { display: inline-flex; align-items:center; gap:5px; color:#d29922; font-size:0.82rem; }
.ch-status-dot     { width:8px; height:8px; border-radius:50%; display:inline-block; }
.dot-active  { background:#3fb950; }
.dot-frozen  { background:#d29922; }

.ch-actions { display: flex; gap: 6px; }
.ch-btn {
    padding: 5px 8px;
    border-radius: 6px;
    border: 1px solid #30363d;
    background: #21262d;
    color: #e6edf3;
    cursor: pointer;
    font-size: 0.82rem;
    transition: background .15s;
    min-width: 32px;
    text-align: center;
}
.ch-btn:hover { background: #30363d; }
.ch-btn-danger { border-color: #da3633; color: #f85149; }
.ch-btn-danger:hover { background: #da363322; }
.ch-btn-sync:hover  { background: #1f6feb22; color: #58a6ff; border-color: #1f6feb; }
.ch-btn-freeze:hover { background: #9a670022; color: #d29922; border-color: #9a6700; }

.ch-feedback {
    font-size: 0.82rem;
    min-height: 20px;
    margin-bottom: 10px;
    color: #3fb950;
}
""")

_CHANNELS_JS = Script("""
function chAddChannel() {
    const val = document.getElementById('ch-input').value.trim();
    if (!val) return;
    const payload = val.startsWith('UC') && !val.startsWith('@')
        ? { id: val, handle: '' }
        : { id: '', handle: val.replace(/^@/, '') };
    fetch('/api/channels/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })
    .then(r => r.json())
    .then(d => {
        if (d.ok) { location.reload(); }
        else { _chFeedback('\u274c ' + (d.error || 'Erro ao adicionar'), true); }
    })
    .catch(() => _chFeedback('\u274c Erro de rede', true));
}

function chSync(cid) {
    _chFeedback('\u23f3 Sincronizando ' + cid + '...');
    fetch('/api/channels/' + encodeURIComponent(cid) + '/sync', { method: 'POST' })
    .then(r => r.json())
    .then(d => {
        if (d.ok) _chFeedback('\u2705 Sync solicitado para ' + cid);
        else _chFeedback('\u274c ' + (d.error || 'Erro'), true);
    })
    .catch(() => _chFeedback('\u274c Erro de rede', true));
}

function chFreeze(cid, btn) {
    fetch('/api/channels/' + encodeURIComponent(cid) + '/freeze', { method: 'POST' })
    .then(r => r.json())
    .then(d => {
        if (d.ok) { location.reload(); }
        else _chFeedback('\u274c ' + (d.error || 'Erro'), true);
    })
    .catch(() => _chFeedback('\u274c Erro de rede', true));
}

function chDelete(cid, name) {
    if (!confirm('Deletar canal "' + name + '"?')) return;
    fetch('/api/channels/' + encodeURIComponent(cid), { method: 'DELETE' })
    .then(r => r.json())
    .then(d => {
        if (d.ok) location.reload();
        else _chFeedback('\u274c ' + (d.error || 'Erro'), true);
    })
    .catch(() => _chFeedback('\u274c Erro de rede', true));
}

function _chFeedback(msg, isErr) {
    const el = document.getElementById('ch-feedback');
    el.textContent = msg;
    el.style.color = isErr ? '#f85149' : '#3fb950';
    if (!isErr) setTimeout(() => { el.textContent = ''; }, 4000);
}

document.addEventListener('keydown', e => {
    if (e.key === 'Enter' && document.activeElement.id === 'ch-input') chAddChannel();
});
""")


def channels_page(state, scheduler):
    """
    Renderiza a página /canais.
    `state`     — StateManager
    `scheduler` — Scheduler (para trigger_now)
    """
    channels      = state.get_all_channels() if state else {}   # {cid: title}
    frozen_set    = getattr(state, 'frozen_channels', set())     # set de cids congelados
    all_streams   = list(state.get_all_streams()) if state else []

    # contadores por canal
    counters = {}  # cid -> {live:0, upcoming:0, vod:0}
    for s in all_streams:
        cid = s.get('channelid', '')
        if not cid:
            continue
        counters.setdefault(cid, {'live': 0, 'upcoming': 0, 'vod': 0})
        st = s.get('status', '')
        if st in ('live', 'upcoming', 'vod'):
            counters[cid][st] += 1

    rows = []
    for cid, title in channels.items():
        is_frozen = cid in frozen_set
        counts    = counters.get(cid, {'live': 0, 'upcoming': 0, 'vod': 0})

        def _num_badge(n, cls):
            c = cls if n > 0 else 'badge-zero'
            return Span(str(n), cls=f'badge-num {c}')

        status_cell = (
            Span(Span(cls='ch-status-dot dot-frozen'), 'Congelado', cls='ch-status-frozen')
            if is_frozen
            else Span(Span(cls='ch-status-dot dot-active'), 'active', cls='ch-status-active')
        )

        freeze_label = '\u25a0 Descongelar' if is_frozen else '\u23f8 Congelar'

        safe_title = (title or cid).replace('"', '&quot;')

        rows.append(Tr(
            Td(
                Div(
                    Img(src=f'/api/thumbnail/channel/{cid}',
                        cls='ch-avatar',
                        onerror="this.style.visibility='hidden'"),
                    Div(
                        Span(title or cid, style='font-weight:600;color:#e6edf3;'),
                        style='display:flex;flex-direction:column;',
                    ),
                    cls='ch-name-cell',
                ),
            ),
            Td(Span(cid[:20] + '\u2026' if len(cid) > 20 else cid, cls='ch-id', title=cid)),
            Td(_num_badge(counts['live'],     'badge-live-num')),
            Td(_num_badge(counts['upcoming'], 'badge-upcoming-num')),
            Td(_num_badge(counts['vod'],      'badge-vod-num')),
            Td(status_cell),
            Td(
                Div(
                    Button('\U0001f503',
                           cls='ch-btn ch-btn-sync',
                           title='Sincronizar este canal',
                           onclick=f"chSync('{cid}')"),
                    Button('\u23f8' if not is_frozen else '\u25b6',
                           cls='ch-btn ch-btn-freeze',
                           title=freeze_label,
                           onclick=f"chFreeze('{cid}', this)"),
                    Button('\U0001f5d1',
                           cls='ch-btn ch-btn-danger',
                           title='Deletar canal',
                           onclick=f"chDelete('{cid}', '{safe_title}')"),
                    cls='ch-actions',
                ),
            ),
        ))

    add_bar = Div(
        Input(
            id='ch-input',
            type='text',
            placeholder='@handle, UC..., ou URL do canal',
        ),
        Button('\u2795 Adicionar', onclick='chAddChannel()',
               style='background:#21262d;border:1px solid #30363d;'),
    cls='ch-add-bar'
    )

    table = Div(
        Table(
            Thead(Tr(
                Th('Canal'),
                Th('Channel ID'),
                Th(Span('\u25cf', style='color:#f85149;'), ' Live',
                   style='white-space:nowrap;'),
                Th(Span('\u25cf', style='color:#d29922;'), ' Up',
                   style='white-space:nowrap;'),
                Th(Span('\u25cf', style='color:#58a6ff;'), ' VOD',
                   style='white-space:nowrap;'),
                Th('Status'),
                Th('A\u00e7\u00f5es'),
            )),
            Tbody(*rows) if rows else Tbody(
                Tr(Td('Nenhum canal cadastrado.', colspan=7,
                      cls='text-muted', style='text-align:center;padding:20px;'))
            ),
            cls='ch-table',
        ),
        cls='card', style='padding:0;overflow:hidden;'
    )

    return _page_shell(
        'Canais', 'channels',
        _CHANNELS_CSS,
        Div('', id='ch-feedback', cls='ch-feedback'),
        add_bar,
        table,
        _CHANNELS_JS,
    )
