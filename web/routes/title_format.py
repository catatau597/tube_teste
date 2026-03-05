"""
web/routes/title_format.py — Página /config/title-format

Permite configurar a ordem e ativação dos componentes do título
da playlist via drag-and-drop + toggles.

Componentes disponíveis:
  channel  → Nome do Canal (ex: CazéTV)
  status   → Status do evento (ex: AO VIVO / AGENDADO)
  title    → Título do vídeo (sempre presente)

Exemplo de saída com ordem [channel, status, title] e brackets=true:
  [CazéTV] [AO VIVO] Final da Copa
"""
from fasthtml.common import *
from web.layout import _page_shell

# Metadados fixos de cada componente
_COMPONENT_META = {
    "channel": {"label": "[NOME DO CANAL]",  "emoji": "📺", "removable": True},
    "status":  {"label": "[STATUS]",          "emoji": "🔴", "removable": True},
    "title":   {"label": "[NOME DO EVENTO]",  "emoji": "📝", "removable": False},
}

_ALL_COMPONENTS = ["channel", "status", "title"]


def _build_preview(order: list[str], enabled: set[str], use_brackets: bool) -> str:
    """Gera exemplo estático de como ficará o título."""
    parts = []
    for comp in order:
        if comp not in enabled:
            continue
        if comp == "channel":
            val = "[CazéTV]" if use_brackets else "CazéTV"
        elif comp == "status":
            val = "[AO VIVO]" if use_brackets else "AO VIVO"
        elif comp == "title":
            val = "Final da Copa"
        else:
            continue
        parts.append(val)
    return " ".join(parts) if parts else "Final da Copa"


def title_format_page(config, saved: bool = False):
    order   = config.get_list("title_components_order")
    enabled = set(config.get_list("title_components_enabled"))
    use_brackets = config.get_bool("title_use_brackets")

    # Garante que todos os componentes conhecidos estão na ordem
    # (adiciona ao final os que estiverem faltando)
    for c in _ALL_COMPONENTS:
        if c not in order:
            order.append(c)

    preview_text = _build_preview(order, enabled, use_brackets)

    alert = Div("✅ Formato de título salvo com sucesso.",
                cls="alert alert-success") if saved else ""

    # ── Componentes drag-and-drop ──────────────────────────────────
    component_items = []
    for comp in order:
        meta    = _COMPONENT_META.get(comp, {"label": comp, "emoji": "❓", "removable": True})
        is_on   = comp in enabled
        pill_cls   = "toggle-pill on" if is_on else "toggle-pill off"
        pill_label = "Ligado" if is_on else "Desligado"
        hidden_id  = f"toggle_hidden_{comp}"

        component_items.append(
            Div(
                # Handle de drag
                Span("≡", style="cursor:grab;color:#484f58;font-size:1.2rem;margin-right:12px;user-select:none;",
                     cls="drag-handle"),
                # Toggle ligado/desligado (desabilitado para title)
                Input(type="hidden", name=f"comp_enabled_{comp}",
                      value="true" if is_on else "false", id=hidden_id),
                Button(
                    pill_label,
                    type="button",
                    cls=pill_cls,
                    **(  {"onclick": f"_toggleBool(this, '{hidden_id}'); updatePreview()"}
                          if meta["removable"]
                          else {"disabled": True, "title": "O título do evento é sempre obrigatório"}
                    ),
                    style="min-width:80px;" + ("opacity:0.5;cursor:not-allowed;" if not meta["removable"] else ""),
                ),
                # Label
                Span(
                    f"{meta['emoji']} {meta['label']}",
                    style="margin-left:12px;font-size:0.95rem;color:#e6edf3;",
                ),
                # data-comp para leitura pelo JS
                cls="component-row",
                **{"data-comp": comp},
                style=(
                    "display:flex;align-items:center;"
                    "padding:12px 16px;"
                    "background:#0d1117;"
                    "border:1px solid #30363d;"
                    "border-radius:8px;"
                    "margin-bottom:8px;"
                ),
            )
        )

    # Campo hidden que guarda a ordem final (atualizado pelo JS)
    order_hidden = Input(
        type="hidden",
        name="title_components_order",
        id="title_components_order",
        value=",".join(order),
    )

    # Campo hidden que guarda os enabled (atualizado pelo JS no submit)
    # (já são enviados via comp_enabled_* individuais)

    # Toggle de brackets
    brackets_hidden_id = "hidden_title_use_brackets"
    brackets_pill_cls   = "toggle-pill on" if use_brackets else "toggle-pill off"
    brackets_pill_label = "Ligado" if use_brackets else "Desligado"
    brackets_toggle = Div(
        Input(type="hidden", name="title_use_brackets",
              value="true" if use_brackets else "false", id=brackets_hidden_id),
        Button(
            brackets_pill_label,
            type="button",
            cls=brackets_pill_cls,
            onclick=f"_toggleBool(this, '{brackets_hidden_id}'); updatePreview()",
        ),
        Span("Usar marcadores [ ] nos componentes de prefixo",
             cls="toggle-label"),
        cls="bool-toggle",
    )

    page_style = Style("""
        #components-list { list-style: none; padding: 0; margin: 0; }
        .component-row.drag-over { border-color: #58a6ff !important; background: #161b22 !important; }
        .component-row.dragging  { opacity: 0.4; }
        .toggle-pill {
            display: inline-flex; align-items: center;
            padding: 4px 14px; border-radius: 999px;
            font-size: 0.82rem; font-weight: 600;
            border: 1.5px solid transparent;
            transition: background 0.15s, color 0.15s;
            cursor: pointer;
        }
        .toggle-pill.on  { background: #1f6feb; color: #fff; border-color: #388bfd; }
        .toggle-pill.off { background: transparent; color: #8b949e; border-color: #30363d; }
        .bool-toggle { display: inline-flex; align-items: center; gap: 10px; margin-bottom: 14px; cursor: pointer; }
        .toggle-label { font-size: 0.9rem; color: #e6edf3; }
        #preview-box {
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 20px;
            text-align: center;
            font-size: 1.05rem;
            color: #e6edf3;
            letter-spacing: 0.01em;
            margin-top: 4px;
        }
    """)

    page_js = Script("""
        // --- Toggle ligado/desligado (reusado do layout global) ---
        function _toggleBool(btn, hiddenId) {
            const hidden = document.getElementById(hiddenId);
            const isOn   = hidden.value === 'true';
            hidden.value = isOn ? 'false' : 'true';
            btn.textContent = isOn ? 'Desligado' : 'Ligado';
            btn.className   = 'toggle-pill ' + (isOn ? 'off' : 'on');
        }

        // --- Drag and Drop ---
        let dragSrc = null;

        function initDrag() {
            document.querySelectorAll('.component-row').forEach(row => {
                row.setAttribute('draggable', 'true');
                row.addEventListener('dragstart', e => {
                    dragSrc = row;
                    row.classList.add('dragging');
                    e.dataTransfer.effectAllowed = 'move';
                });
                row.addEventListener('dragend', e => {
                    row.classList.remove('dragging');
                    document.querySelectorAll('.component-row').forEach(r => r.classList.remove('drag-over'));
                    syncOrder();
                    updatePreview();
                });
                row.addEventListener('dragover', e => {
                    e.preventDefault();
                    e.dataTransfer.dropEffect = 'move';
                    if (row !== dragSrc) row.classList.add('drag-over');
                });
                row.addEventListener('dragleave', e => { row.classList.remove('drag-over'); });
                row.addEventListener('drop', e => {
                    e.preventDefault();
                    row.classList.remove('drag-over');
                    if (dragSrc && dragSrc !== row) {
                        const list = row.parentNode;
                        const allRows = [...list.querySelectorAll('.component-row')];
                        const srcIdx = allRows.indexOf(dragSrc);
                        const dstIdx = allRows.indexOf(row);
                        if (srcIdx < dstIdx) {
                            list.insertBefore(dragSrc, row.nextSibling);
                        } else {
                            list.insertBefore(dragSrc, row);
                        }
                    }
                });
            });
        }

        function syncOrder() {
            const rows  = document.querySelectorAll('.component-row');
            const order = [...rows].map(r => r.dataset.comp).filter(Boolean);
            document.getElementById('title_components_order').value = order.join(',');
        }

        function updatePreview() {
            const rows  = [...document.querySelectorAll('.component-row')];
            const order = rows.map(r => r.dataset.comp).filter(Boolean);
            const useBrackets = document.getElementById('hidden_title_use_brackets').value === 'true';

            const labels = {
                channel: useBrackets ? '[CazéTV]'   : 'CazéTV',
                status:  useBrackets ? '[AO VIVO]'  : 'AO VIVO',
                title:   'Final da Copa'
            };

            const parts = [];
            for (const comp of order) {
                const hiddenId = 'toggle_hidden_' + comp;
                const hidden   = document.getElementById(hiddenId);
                const isOn     = !hidden || hidden.value === 'true';
                if (isOn && labels[comp]) parts.push(labels[comp]);
            }
            document.getElementById('preview-text').textContent =
                parts.length ? parts.join(' ') : 'Final da Copa';
        }

        document.addEventListener('DOMContentLoaded', () => {
            initDrag();
            updatePreview();
        });
    """)

    return _page_shell(
        "Formato de Título", "config_title_format",
        page_style,
        alert,
        Form(
            Div(
                H2("Componentes e Ordem"),
                P("Arraste os itens para reordenar. Ative ou desative os componentes que aparecerão no título final.",
                  cls="text-muted"),
                Div(*component_items, id="components-list"),
                order_hidden,
                style="margin-bottom: 4px;",
                cls="card",
            ),
            Div(
                brackets_toggle,
                cls="card",
                style="padding: 16px 24px;",
            ),
            Div(
                H2("Pré-visualização"),
                P("Como o título aparecerá na playlist:", cls="text-muted"),
                Div(
                    Span(preview_text, id="preview-text"),
                    id="preview-box",
                ),
                P(
                    "Nota: A playlist do YouTube só aparecerá se a informação estiver disponível na API para o evento específico.",
                    cls="text-muted",
                    style="font-size:0.78rem;margin-top:8px;",
                ),
                cls="card",
            ),
            Div(
                Button("Salvar Alterações", type="submit"),
                style="margin-top: 8px;",
            ),
            method="post",
            action="/config/title-format",
        ),
        page_js,
    )
