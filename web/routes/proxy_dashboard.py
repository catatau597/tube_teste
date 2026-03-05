"""
web/routes/proxy_dashboard.py
------------------------------
Fragmentos reutilizáveis do Dashboard:
  - scheduler_cards()   -> card com status do agendador + botões
  - active_streams_card() -> tabela de streams proxy ativos (polling)
"""
from fasthtml.common import *


def scheduler_cards(scheduler=None) -> Div:
    """
    Card de Agendamento: próxima execução, pausado ou não,
    botão Forçar Busca Global e botão Pausar/Retomar.
    """
    is_paused = getattr(scheduler, "paused", False) if scheduler else False

    return Div(
        H2("\U0001f4c5 Agendamento"),
        Div(
            # Status do scheduler
            Div(
                Span(
                    "\u23f8 Pausado" if is_paused else "\u25b6 Rodando",
                    id="sched-status-badge",
                    style=(
                        "display:inline-block;padding:4px 14px;border-radius:999px;"
                        "font-size:0.82rem;font-weight:600;"
                        + ("background:#d2992222;color:#d29922;border:1px solid #d29922;"
                           if is_paused else
                           "background:#23863622;color:#3fb950;border:1px solid #3fb950;")
                    ),
                ),
                Br(),
                Span(
                    "Próxima execução: ",
                    style="font-size:0.82rem;color:#8b949e;margin-top:6px;display:inline-block;",
                ),
                Span(id="sched-next-run", style="font-size:0.82rem;color:#e6edf3;"),
                style="margin-bottom:12px;",
            ),
            # Botões
            Div(
                Button(
                    "\U0001f504 Forçar Busca Global",
                    type="button",
                    id="btn-force-sync",
                    onclick="schedulerAction('force')",
                    style=(
                        "margin-right:10px;padding:7px 18px;"
                        "background:#1f6feb;color:#fff;border:1.5px solid #388bfd;"
                        "border-radius:6px;cursor:pointer;font-size:0.88rem;font-weight:600;"
                        "transition:background .15s;"
                    ),
                ),
                Button(
                    "\u23f8 Pausar" if not is_paused else "\u25b6 Retomar",
                    type="button",
                    id="btn-pause-resume",
                    onclick="schedulerAction('toggle-pause')",
                    style=(
                        "padding:7px 18px;"
                        "background:transparent;"
                        + ("color:#3fb950;border:1.5px solid #3fb950;"
                           if is_paused else
                           "color:#d29922;border:1.5px solid #d29922;")
                        + "border-radius:6px;cursor:pointer;font-size:0.88rem;font-weight:600;"
                    ),
                ),
                Span(id="sched-action-feedback",
                     style="margin-left:12px;font-size:0.82rem;color:#3fb950;min-height:18px;"),
            ),
        ),
        Style("""
            #btn-force-sync:hover { background:#388bfd !important; }
        """),
        cls="card",
        style="margin-bottom:20px;",
    )


def active_streams_card() -> Div:
    """
    Card de Streams Proxy Ativos com polling a cada 2s via JS.
    """
    return Div(
        H2("\U0001f4f9 Streams Proxy Ativos"),
        Div(
            Button("\U0001f504 Atualizar", id="btn-refresh-proxy", type="button",
                   style=(
                       "font-size:0.82rem;padding:4px 12px;margin-right:8px;"
                       "background:transparent;border:1px solid #30363d;"
                       "color:#8b949e;border-radius:6px;cursor:pointer;"
                   )),
            Span(id="proxy-last-update", style="font-size:0.78rem;color:#8b949e;"),
            style="margin-bottom:8px;",
        ),
        Div(
            Table(
                Thead(Tr(
                    Th("Video ID"), Th("Buffer (chunks)"), Th("Buffer (MB)"),
                    Th("Clientes"), Th("PID"), Th("Status"), Th("Ação"),
                )),
                Tbody(id="dash-proxy-tbody"),
                style="width:100%;",
            ),
            P("Nenhum stream proxy ativo.",
              id="dash-no-streams",
              cls="text-muted",
              style="display:none;"),
        ),
        cls="card",
    )


def dashboard_js() -> Script:
    """JS do Dashboard: polling proxy + ações do scheduler."""
    return Script("""
        // -------- Proxy Streams --------
        function _statusBadge(alive) {
            const s = document.createElement('span');
            s.className = alive ? 'badge badge-live' : 'badge badge-none';
            s.textContent = alive ? '\u2705 ativo' : '\u274c parado';
            return s.outerHTML;
        }

        function _renderStreams(data) {
            const tbody = document.getElementById('dash-proxy-tbody');
            const noMsg = document.getElementById('dash-no-streams');
            if (!tbody) return;
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
                    <td>${s.process_pid || '\u2014'}</td>
                    <td>${_statusBadge(s.process_alive)}</td>
                    <td>
                      <button onclick="_stopStream('${s.video_id}')"
                        style="font-size:0.78em;padding:2px 8px;cursor:pointer;
                               border:1px solid #f8514944;color:#f85149;background:transparent;
                               border-radius:4px;">Parar</button>
                    </td>
                `;
                tbody.appendChild(tr);
            });
            const el = document.getElementById('proxy-last-update');
            if (el) el.textContent = 'Atualizado: ' + new Date().toLocaleTimeString();
        }

        function _fetchProxy() {
            fetch('/api/proxy/status')
                .then(r => r.json())
                .then(_renderStreams)
                .catch(e => console.error('Erro proxy status:', e));
        }

        function _stopStream(videoId) {
            if (!confirm('Parar stream ' + videoId + '?')) return;
            fetch('/api/proxy/' + videoId, { method: 'DELETE' })
                .then(r => r.json())
                .then(d => { alert(d.ok ? 'Stream parado.' : 'Erro: ' + (d.error || '?')); _fetchProxy(); })
                .catch(e => alert('Erro: ' + e));
        }

        const _btnRefProxy = document.getElementById('btn-refresh-proxy');
        if (_btnRefProxy) _btnRefProxy.onclick = _fetchProxy;
        _fetchProxy();
        setInterval(_fetchProxy, 2000);

        // -------- Scheduler --------
        function _updateSchedStatus(data) {
            const badge = document.getElementById('sched-status-badge');
            const btn   = document.getElementById('btn-pause-resume');
            const next  = document.getElementById('sched-next-run');
            if (badge) {
                badge.textContent = data.paused ? '\u23f8 Pausado' : '\u25b6 Rodando';
                badge.style.background = data.paused ? '#d2992222' : '#23863622';
                badge.style.color      = data.paused ? '#d29922'   : '#3fb950';
                badge.style.borderColor= data.paused ? '#d29922'   : '#3fb950';
            }
            if (btn) {
                btn.textContent   = data.paused ? '\u25b6 Retomar' : '\u23f8 Pausar';
                btn.style.color   = data.paused ? '#3fb950'  : '#d29922';
                btn.style.borderColor = data.paused ? '#3fb950' : '#d29922';
            }
            if (next && data.next_run) {
                next.textContent = new Date(data.next_run * 1000).toLocaleString();
            } else if (next) {
                next.textContent = '\u2014';
            }
        }

        function _fetchSchedStatus() {
            fetch('/api/scheduler/status')
                .then(r => r.json())
                .then(_updateSchedStatus)
                .catch(() => {});
        }

        async function schedulerAction(action) {
            const fb = document.getElementById('sched-action-feedback');
            if (action === 'force') {
                const r = await fetch('/api/scheduler/force', { method: 'POST' });
                const d = await r.json();
                if (fb) {
                    fb.textContent = d.ok ? '\u2705 Busca global iniciada!' : '\u274c Erro: ' + (d.error || '');
                    fb.style.color = d.ok ? '#3fb950' : '#f85149';
                    setTimeout(() => { fb.textContent = ''; }, 3000);
                }
            } else if (action === 'toggle-pause') {
                const r = await fetch('/api/scheduler/pause', { method: 'POST' });
                const d = await r.json();
                if (d.ok) { _updateSchedStatus(d); }
                if (fb) {
                    fb.textContent = d.ok ? (d.paused ? '\u23f8 Scheduler pausado.' : '\u25b6 Scheduler retomado.') : '\u274c Erro';
                    fb.style.color = d.ok ? '#3fb950' : '#f85149';
                    setTimeout(() => { fb.textContent = ''; }, 3000);
                }
            }
        }

        _fetchSchedStatus();
        setInterval(_fetchSchedStatus, 5000);
    """)
