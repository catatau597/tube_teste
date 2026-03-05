"""
web/routes/title_format.py
--------------------------
Página /config/title-format

Permite ao usuário configurar:
  - Quais componentes aparecem no título (toggles Ligado/Desligado)
  - A ordem de exibição via drag-and-drop
  - Se cada componente usa colchetes (por item, não global)
  - Expressões a remover dos títulos (case-insensitive)
  - Toggle para remover emojis

Chaves de config usadas:
  title_components_order    -> "channel,status,datetime,title"  (csv, ordem)
  title_components_enabled  -> "channel,status,datetime,title"  (csv, ativos)
  title_components_brackets -> "channel,status"                 (csv, com colchetes)
  title_filter_expressions  -> "ao vivo,cortes,..."             (csv, expressões)
  title_strip_emojis        -> bool
"""
from fasthtml.common import *
from web.layout import _page_shell

_ALL_COMPONENTS = [
    ("channel",  "Nome do Canal",        "Prefixar com o nome do canal — ex: ESPN Brasil"),
    ("status",   "Status",               "Prefixar com status — ex: [Ao Vivo] ou [Agendado]"),
    ("datetime", "Data e Hora (início)", "Prefixar com data/hora de início — ex: 05/03 22:00"),
    ("title",    "Título do Evento",     "Texto original do vídeo"),
]

_STYLES = Style("""
    /* Toggle pill */
    .toggle-pill {
        display: inline-flex;
        align-items: center;
        padding: 3px 11px;
        border-radius: 999px;
        font-size: 0.79rem;
        font-weight: 600;
        border: 1.5px solid transparent;
        transition: background 0.15s, color 0.15s, border-color 0.15s;
        cursor: pointer;
        white-space: nowrap;
    }
    .toggle-pill.on  { background:#1f6feb; color:#fff; border-color:#388bfd; }
    .toggle-pill.off { background:transparent; color:#8b949e; border-color:#30363d; }
    .toggle-pill[disabled] { cursor:not-allowed; opacity:.55; }

    /* Bracket pill */
    .bracket-pill {
        display: inline-flex;
        align-items: center;
        padding: 3px 10px;
        border-radius: 6px;
        font-size: 0.79rem;
        font-weight: 700;
        font-family: monospace;
        border: 1.5px solid transparent;
        transition: background 0.15s, color 0.15s, border-color 0.15s;
        cursor: pointer;
        white-space: nowrap;
    }
    .bracket-pill.on  { background:#2d333b; color:#e6edf3; border-color:#58a6ff; }
    .bracket-pill.off { background:transparent; color:#484f58; border-color:#30363d; }

    /* Bool toggle (reutiliza mesmos estilos do main.py) */
    .bool-toggle {
        display: inline-flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 14px;
        cursor: pointer;
        user-select: none;
    }
    .bool-toggle .toggle-label {
        font-size: 0.9rem;
        color: #e6edf3;
    }

    /* DnD list */
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
        gap: 12px;
        padding: 11px 14px;
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
    .dnd-info   { flex:1; min-width:0; }
    .dnd-info strong { font-size:0.88rem; color:#e6edf3; }
    .dnd-info small  { display:block; color:#8b949e; font-size:0.76rem; margin-top:1px; }
    .dnd-controls { display:flex; gap:6px; align-items:center; flex-shrink:0; }

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
        word-break:break-all;
    }

    /* Tag chips */
    .tag-list { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:8px; }
    .tag {
        display:inline-flex; align-items:center; gap:4px;
        background:#21262d; border:1px solid #30363d;
        border-radius:999px; padding:3px 10px;
        font-size:0.8rem; color:#e6edf3;
    }
    .remove-tag {
        background:none; border:none; color:#8b949e;
        cursor:pointer; font-size:0.85rem; padding:0 2px;
        line-height:1;
    }
    .remove-tag:hover { color:#f85149; }
""")

_JS = Script("""
    /* ---- Componentes DnD ---- */
    function _enabledComps() {
        return Array.from(
            document.querySelectorAll('[id^="hidden_comp_enabled_"]')
        ).filter(h => h.value === 'true').map(h => h.id.replace('hidden_comp_enabled_', ''));
    }

    function _refreshLocks() {
        const active = _enabledComps();
        document.querySelectorAll('.dnd-item').forEach(item => {
            const comp = item.dataset.comp;
            const btn  = item.querySelector('.comp-toggle');
            if (!btn) return;
            const isOn = document.getElementById('hidden_comp_enabled_' + comp).value === 'true';
            if (isOn && active.length === 1) {
                btn.disabled = true;
                btn.title = 'Pelo menos um componente deve estar ativo';
            } else {
                btn.disabled = false;
                btn.title = '';
            }
        });
    }

    function _toggleComp(btn, hiddenId) {
        if (btn.disabled) return;
        const hidden = document.getElementById(hiddenId);
        const isOn   = hidden.value === 'true';
        hidden.value = isOn ? 'false' : 'true';
        btn.textContent = isOn ? 'Desligado' : 'Ligado';
        btn.className   = 'toggle-pill comp-toggle ' + (isOn ? 'off' : 'on');
        _refreshLocks();
        _updatePreview();
    }

    function _toggleBracket(btn, hiddenId) {
        const hidden = document.getElementById(hiddenId);
        const isOn   = hidden.value === 'true';
        hidden.value = isOn ? 'false' : 'true';
        btn.className   = 'bracket-pill ' + (isOn ? 'off' : 'on');
        _updatePreview();
    }

    function _toggleBool(btn, hiddenId) {
        const hidden = document.getElementById(hiddenId);
        const isOn   = hidden.value === 'true';
        hidden.value = isOn ? 'false' : 'true';
        btn.textContent = isOn ? 'Desligado' : 'Ligado';
        btn.className   = 'toggle-pill ' + (isOn ? 'off' : 'on');
    }

    /* ---- DnD ---- */
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

    /* ---- Preview ---- */
    function _updatePreview() {
        const list  = document.getElementById('dnd-list');
        const items = list.querySelectorAll('.dnd-item');
        const parts = [];
        const samples = { channel:'ESPN Brasil', status:'Ao Vivo', datetime:'05/03 22:00', title:'Final da Copa' };
        items.forEach(item => {
            const comp    = item.dataset.comp;
            const enabled = document.getElementById('hidden_comp_enabled_' + comp).value === 'true';
            if (!enabled) return;
            const brackets = document.getElementById('hidden_comp_brackets_' + comp);
            const useBrackets = brackets && brackets.value === 'true';
            const lbl = samples[comp] || comp;
            parts.push(useBrackets ? '[' + lbl + ']' : lbl);
        });
        document.getElementById('preview-output').textContent =
            parts.length ? parts.join(' \u2014 ') : '(vazio)';
    }

    /* ---- Tags (expressões) ---- */
    function _syncHidden(hiddenName) {
        const container = document.getElementById('tags_' + hiddenName);
        const hidden    = document.getElementById('hidden_' + hiddenName);
        const texts = Array.from(container.querySelectorAll('.tag-text'))
                           .map(el => el.textContent.trim())
                           .filter(Boolean);
        hidden.value = texts.join(',');
    }

    function removeTag(btn, hiddenName) {
        btn.closest('.tag').remove();
        _syncHidden(hiddenName);
    }

    function addTag(inputId, hiddenName) {
        const inp = document.getElementById('input_' + inputId);
        const val = inp.value.trim();
        if (!val) return;
        const container = document.getElementById('tags_' + hiddenName);
        const tag = document.createElement('span');
        tag.className = 'tag';
        tag.innerHTML = `<span class="tag-text">${val}</span>`
                      + `<button class="remove-tag" type="button"
                          onclick="removeTag(this,'${hiddenName}')">&times;</button>`;
        container.appendChild(tag);
        inp.value = '';
        _syncHidden(hiddenName);
    }

    document.addEventListener('DOMContentLoaded', () => {
        _initDnd();
        _refreshLocks();
        _updatePreview();
        document.querySelectorAll('[id^="input_"]').forEach(inp => {
            inp.addEventListener('keydown', e => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    const hiddenName = inp.id.replace('input_new_', '');
                    addTag(inp.id.replace('input_', ''), hiddenName);
                }
            });
        });
    });
""")


def _comp_row(comp: str, label: str, desc: str, enabled: bool, use_brackets: bool) -> Li:
    hidden_enabled_id  = f"hidden_comp_enabled_{comp}"
    hidden_brackets_id = f"hidden_comp_brackets_{comp}"
    toggle_cls   = "toggle-pill comp-toggle " + ("on" if enabled else "off")
    toggle_label = "Ligado" if enabled else "Desligado"
    bracket_cls  = "bracket-pill " + ("on" if use_brackets else "off")

    return Li(
        Span("\u2630", cls="dnd-handle"),
        Div(Strong(label), Small(desc), cls="dnd-info"),
        Div(
            Input(type="hidden", name=f"comp_enabled_{comp}",
                  id=hidden_enabled_id, value="true" if enabled else "false"),
            Input(type="hidden", name=f"comp_brackets_{comp}",
                  id=hidden_brackets_id, value="true" if use_brackets else "false"),
            Button(toggle_label, type="button", cls=toggle_cls,
                   onclick=f"_toggleComp(this, '{hidden_enabled_id}')"),
            Button("[ ]", type="button", cls=bracket_cls,
                   title="Envolver em colchetes",
                   onclick=f"_toggleBracket(this, '{hidden_brackets_id}')",
                   style="" if enabled else "visibility:hidden;",
                   id=f"bracket_btn_{comp}"),
            cls="dnd-controls",
        ),
        draggable="true",
        data_comp=comp,
        cls="dnd-item",
    )


def _tag_list_with_input(words: list, field_name: str, hidden_name: str) -> Div:
    tags = [
        Span(
            Span(w, cls="tag-text"),
            Button("\u00d7", cls="remove-tag", type="button",
                   onclick=f"removeTag(this, '{hidden_name}')"),
            cls="tag",
        )
        for w in words
    ]
    return Div(
        Input(type="hidden", name=hidden_name,
              value=",".join(words), id=f"hidden_{hidden_name}"),
        Div(*tags, id=f"tags_{hidden_name}", cls="tag-list"),
        Div(
            Input(type="text", id=f"input_{field_name}",
                  placeholder="Adicionar... (Enter)",
                  style="width:220px;display:inline-block;margin-right:8px;"),
            Button("+ Adicionar", type="button", cls="btn-secondary",
                   onclick=f"addTag('{field_name}', '{hidden_name}')",
                   style="font-size:0.82rem;padding:5px 12px;"),
            style="margin-top:8px;",
        ),
    )


def title_format_page(config, saved: bool = False):
    order_raw    = config.get_raw("title_components_order")    if hasattr(config, "get_raw") else "channel,status,datetime,title"
    enabled_raw  = config.get_raw("title_components_enabled")  if hasattr(config, "get_raw") else "channel,status,title"
    brackets_raw = config.get_raw("title_components_brackets") if hasattr(config, "get_raw") else ""
    exprs_raw    = config.get_raw("title_filter_expressions")  if hasattr(config, "get_raw") else ""
    strip_emojis = config.get_bool("title_strip_emojis")       if hasattr(config, "get_bool") else False

    if not order_raw.strip():   order_raw   = "channel,status,datetime,title"
    if not enabled_raw.strip(): enabled_raw = "channel,status,title"

    order_list   = [c.strip() for c in order_raw.split(",")    if c.strip()]
    enabled_set  = {c.strip() for c in enabled_raw.split(",")  if c.strip()}
    brackets_set = {c.strip() for c in brackets_raw.split(",") if c.strip()}
    exprs_list   = [e.strip() for e in exprs_raw.split(",")    if e.strip()]

    all_comp_keys = [k for k, _, _ in _ALL_COMPONENTS]
    for key in all_comp_keys:
        if key not in order_list:
            order_list.append(key)

    _info = {k: (lbl, desc) for k, lbl, desc in _ALL_COMPONENTS}

    dnd_items = []
    for comp in order_list:
        lbl, desc    = _info.get(comp, (comp, ""))
        is_enabled   = comp in enabled_set
        if comp == "title" and not enabled_set:
            is_enabled = True
        use_brackets = comp in brackets_set
        dnd_items.append(_comp_row(comp, lbl, desc, is_enabled, use_brackets))

    alert = Div("\u2705 Formato de título salvo com sucesso.",
                cls="alert alert-success") if saved else ""

    return _page_shell(
        "Formato de Título", "config_title_format",
        alert,
        _STYLES,
        _JS,
        P(
            "Defina quais componentes aparecem no título e arraste \u2630 para mudar a ordem. "
            "O botão ", Code("[ ]"),
            " envolve o componente em colchetes. "
            "O último componente ativo não pode ser desligado.",
            cls="text-muted",
            style="margin-bottom:20px;",
        ),
        Form(
            Input(type="hidden", name="title_components_order",
                  id="title_components_order", value=",".join(order_list)),
            # --- Card: Componentes ---
            Div(
                H2("Componentes e Ordem"),
                P("Arraste \u2630 para reordenar. Ligado/Desligado ativa o componente. [ ] envolve em colchetes.",
                  cls="text-muted"),
                Ul(*dnd_items, id="dnd-list", cls="dnd-list"),
                H3("Prévia"),
                P("Como o título aparecerá na playlist:", cls="text-muted"),
                Div(id="preview-output", cls="preview-box"),
                cls="card",
            ),
            # --- Card: Expressões a remover ---
            Div(
                H2("Expressões a remover dos títulos"),
                P(
                    "Expressões removidas do título do evento antes de exibir na playlist. "
                    "A comparação é ",
                    Strong("case-insensitive"),
                    " — \"ao vivo\" também remove \"AO VIVO\" e \"Ao Vivo\".",
                    cls="text-muted",
                ),
                _tag_list_with_input(exprs_list, "new_title_expr", "title_filter_expressions"),
                Div(
                    Input(type="hidden", name="title_strip_emojis",
                          id="hidden_title_strip_emojis",
                          value="true" if strip_emojis else "false"),
                    Button(
                        "Ligado" if strip_emojis else "Desligado",
                        type="button",
                        cls="toggle-pill " + ("on" if strip_emojis else "off"),
                        onclick="_toggleBool(this, 'hidden_title_strip_emojis')",
                    ),
                    Span("Remover emojis e símbolos especiais dos títulos",
                         cls="toggle-label"),
                    cls="bool-toggle",
                    style="margin-top:14px;",
                ),
                cls="card",
            ),
            Div(Button("Salvar", type="submit"), style="margin-top:8px;"),
            method="post",
            action="/config/title-format",
        ),
    )
