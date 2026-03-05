"""
web/routes/title_format.py
--------------------------
Página /config/title-format

Permite ao usuário configurar:
  - Quais componentes aparecem no título (toggles Ligado/Desligado)
  - A ordem de exibição via drag-and-drop
  - Se componentes opcionais usam colchetes

Chaves de config usadas:
  title_components_order   -> "channel,status,datetime,title"  (csv, ordem)
  title_components_enabled -> "channel,status,datetime,title"  (csv, ativos)
  title_use_brackets       -> "true" / "false"
"""
from fasthtml.common import *
from web.layout import _page_shell

_ALL_COMPONENTS = [
    ("channel",  "Nome do Canal",        "Prefixar com o nome do canal — ex: ESPN Brasil"),
    ("status",   "Status",               "Prefixar com status — ex: [Ao Vivo] ou [Agendado]"),
    ("datetime", "Data e Hora (início)", "Prefixar com data/hora de início — ex: [05/03 22:00]"),
    ("title",    "Título do Evento",     "Texto original do vídeo — sempre presente, não pode ser desativado"),
]

_TOGGLE_STYLE = Style("""
    .bool-toggle {
        display: inline-flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 14px;
        cursor: pointer;
        user-select: none;
    }
    .bool-toggle .toggle-pill {
        display: inline-flex;
        align-items: center;
        padding: 4px 14px;
        border-radius: 999px;
        font-size: 0.82rem;
        font-weight: 600;
        border: 1.5px solid transparent;
        transition: background 0.15s, color 0.15s, border-color 0.15s;
        cursor: pointer;
    }
    .bool-toggle .toggle-pill.on  { background:#1f6feb; color:#fff; border-color:#388bfd; }
    .bool-toggle .toggle-pill.off { background:transparent; color:#8b949e; border-color:#30363d; }
    .bool-toggle .toggle-label    { font-size:0.9rem; color:#e6edf3; }

    /* drag-and-drop */
    .dnd-list {
        list-style: none;
        padding: 0;
        margin: 0 0 16px 0;
        display: flex;
        flex-direction: column;
        gap: 8px;
    }
    .dnd-item {
        display: flex;
        align-items: center;
        gap: 14px;
        padding: 12px 16px;
        background: #0d1117;
        border: 1px solid #30363d;
        border-radius: 8px;
        cursor: grab;
        user-select: none;
        transition: border-color .15s, background .15s;
    }
    .dnd-item.dragging  { opacity:.45; border-color:#58a6ff; cursor:grabbing; }
    .dnd-item.drag-over { border-color:#58a6ff; background:#1c2a3a; }
    .dnd-handle { color:#484f58; font-size:1.1rem; flex-shrink:0; }
    .dnd-info   { flex:1; }
    .dnd-info strong { font-size:0.9rem; color:#e6edf3; }
    .dnd-info small  { display:block; color:#8b949e; font-size:0.78rem; margin-top:2px; }
    .dnd-required {
        font-size:0.72rem;
        color:#3fb950;
        border:1px solid #238636;
        border-radius:10px;
        padding:2px 10px;
        margin-left:auto;
        flex-shrink:0;
    }

    .preview-box {
        background:#0d1117;
        border:1px solid #30363d;
        border-radius:6px;
        padding:10px 16px;
        font-size:0.88rem;
        color:#e6edf3;
        margin-top:14px;
        min-height:36px;
        font-family:monospace;
    }
""")

_TOGGLE_JS = Script("""
    function _toggleBool(btn, hiddenId) {
        const hidden = document.getElementById(hiddenId);
        const isOn   = hidden.value === 'true';
        hidden.value = isOn ? 'false' : 'true';
        btn.textContent = isOn ? 'Desligado' : 'Ligado';
        btn.className   = 'toggle-pill ' + (isOn ? 'off' : 'on');
        _updatePreview();
    }

    function _toggleComp(btn, hiddenId) {
        const hidden = document.getElementById(hiddenId);
        const isOn   = hidden.value === 'true';
        hidden.value = isOn ? 'false' : 'true';
        btn.textContent = isOn ? 'Desligado' : 'Ligado';
        btn.className   = 'toggle-pill ' + (isOn ? 'off' : 'on');
        _updatePreview();
    }
""")

_DND_JS = Script("""
    let _dragged = null;

    function _initDnd() {
        const list = document.getElementById('dnd-list');
        list.querySelectorAll('.dnd-item').forEach(item => {
            item.addEventListener('dragstart', e => {
                _dragged = item;
                setTimeout(() => item.classList.add('dragging'), 0);
            });
            item.addEventListener('dragend', () => {
                item.classList.remove('dragging');
                list.querySelectorAll('.dnd-item').forEach(i => i.classList.remove('drag-over'));
                _syncOrder();
                _updatePreview();
            });
            item.addEventListener('dragover', e => {
                e.preventDefault();
                if (item !== _dragged) {
                    list.querySelectorAll('.dnd-item').forEach(i => i.classList.remove('drag-over'));
                    item.classList.add('drag-over');
                    const rect = item.getBoundingClientRect();
                    if (e.clientY < rect.top + rect.height / 2) {
                        list.insertBefore(_dragged, item);
                    } else {
                        list.insertBefore(_dragged, item.nextSibling);
                    }
                }
            });
        });
    }

    function _syncOrder() {
        const items = document.getElementById('dnd-list').querySelectorAll('.dnd-item');
        document.getElementById('title_components_order').value =
            Array.from(items).map(i => i.dataset.comp).join(',');
    }

    function _updatePreview() {
        const list     = document.getElementById('dnd-list');
        const items    = list.querySelectorAll('.dnd-item');
        const brackets = document.getElementById('hidden_title_use_brackets').value === 'true';
        const parts    = [];
        const samples  = {
            channel:  'ESPN Brasil',
            status:   'Ao Vivo',
            datetime: '05/03 22:00',
            title:    'Título do Evento'
        };
        items.forEach(item => {
            const comp    = item.dataset.comp;
            const enabled = document.getElementById('hidden_comp_enabled_' + comp).value === 'true';
            if (!enabled) return;
            const lbl = samples[comp] || comp;
            if (comp === 'title') {
                parts.push(lbl);
            } else {
                parts.push(brackets ? '[' + lbl + ']' : lbl);
            }
        });
        document.getElementById('preview-output').textContent =
            parts.length ? parts.join(' \u2014 ') : '(vazio)';
    }

    document.addEventListener('DOMContentLoaded', () => {
        _initDnd();
        _updatePreview();
    });
""")


def _bool_toggle_comp(comp: str, enabled: bool, label: str, required: bool = False) -> Div:
    """Toggle pill para habilitar/desabilitar componente (não pode desativar o obrigatório)."""
    hidden_id  = f"hidden_comp_enabled_{comp}"
    pill_cls   = "toggle-pill on" if enabled else "toggle-pill off"
    pill_label = "Ligado" if enabled else "Desligado"
    if required:
        # Sempre ligado, não clicável
        return Div(
            Input(type="hidden", name=f"comp_enabled_{comp}", value="true", id=hidden_id),
            Button("Ligado", type="button", cls="toggle-pill on",
                   style="cursor:not-allowed;opacity:.7;", disabled=True),
            Span(label, cls="toggle-label"),
            cls="bool-toggle",
        )
    return Div(
        Input(type="hidden", name=f"comp_enabled_{comp}",
              value="true" if enabled else "false", id=hidden_id),
        Button(
            pill_label,
            type="button",
            cls=pill_cls,
            onclick=f"_toggleComp(this, '{hidden_id}')",
        ),
        Span(label, cls="toggle-label"),
        cls="bool-toggle",
    )


def title_format_page(config, saved: bool = False):
    order_raw   = config.get_raw("title_components_order")   if hasattr(config, "get_raw") else "channel,status,datetime,title"
    enabled_raw = config.get_raw("title_components_enabled") if hasattr(config, "get_raw") else "channel,status,title"
    use_brackets = config.get_bool("title_use_brackets")     if hasattr(config, "get_bool") else False

    if not order_raw.strip():   order_raw   = "channel,status,datetime,title"
    if not enabled_raw.strip(): enabled_raw = "channel,status,title"

    order_list  = [c.strip() for c in order_raw.split(",")   if c.strip()]
    enabled_set = {c.strip() for c in enabled_raw.split(",") if c.strip()}

    all_comp_keys = [k for k, _, _ in _ALL_COMPONENTS]
    for key in all_comp_keys:
        if key not in order_list:
            order_list.append(key)

    _info = {k: (lbl, desc) for k, lbl, desc in _ALL_COMPONENTS}

    # --- Card 1: Componentes e Ordem ---
    dnd_items = []
    for comp in order_list:
        lbl, desc    = _info.get(comp, (comp, ""))
        is_required  = (comp == "title")
        is_enabled   = comp in enabled_set or is_required
        dnd_items.append(
            Li(
                Span("\u2630", cls="dnd-handle"),
                Div(
                    Strong(lbl),
                    Small(desc),
                    cls="dnd-info",
                ),
                _bool_toggle_comp(comp, is_enabled, "", required=is_required),
                *([ Span("obrigatório", cls="dnd-required") ] if is_required else []),
                draggable="true",
                data_comp=comp,
                cls="dnd-item",
            )
        )

    # --- Card 2: Opções de Formatação ---
    brackets_section = Div(
        H2("Opções de Formatação"),
        P(
            "Quando ativo, os componentes opcionais (Canal, Status, Data/Hora) são envolvidos em "
            "colchetes — ex: ",
            Code("[ESPN Brasil] — [Ao Vivo] — Título do Evento"),
            ".",
            cls="text-muted",
        ),
        Input(type="hidden", id="hidden_title_use_brackets",
              name="title_use_brackets",
              value="true" if use_brackets else "false"),
        Div(
            Button(
                "Ligado" if use_brackets else "Desligado",
                type="button",
                cls="toggle-pill " + ("on" if use_brackets else "off"),
                onclick="_toggleBool(this, 'hidden_title_use_brackets')",
            ),
            Span("Envolver componentes opcionais em colchetes [ ]", cls="toggle-label"),
            cls="bool-toggle",
        ),
        H3("Prévia"),
        P("Como o título aparecerá na playlist:", cls="text-muted"),
        Div(id="preview-output", cls="preview-box"),
        cls="card",
    )

    alert = Div("\u2705 Formato de título salvo com sucesso.",
                cls="alert alert-success") if saved else ""

    return _page_shell(
        "Formato de Título", "config_title_format",
        alert,
        _TOGGLE_STYLE,
        _TOGGLE_JS,
        _DND_JS,
        P(
            "Defina quais componentes aparecem no título e arraste \u2630 para mudar a ordem. "
            "O componente ",
            Strong("Título do Evento"),
            " é sempre exibido.",
            cls="text-muted",
            style="margin-bottom:20px;",
        ),
        Form(
            Input(type="hidden", name="title_components_order",
                  id="title_components_order",
                  value=",".join(order_list)),
            Div(
                H2("Componentes e Ordem"),
                P("Arraste para reordenar. Use os botões para ativar/desativar.",
                  cls="text-muted"),
                Ul(*dnd_items, id="dnd-list", cls="dnd-list"),
                cls="card",
            ),
            brackets_section,
            Div(Button("Salvar", type="submit"), style="margin-top:8px;"),
            method="post",
            action="/config/title-format",
        ),
    )
