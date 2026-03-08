"""
web/routes/config.py
-------------------
Rotas HTML de configuração e persistência.
"""
from __future__ import annotations

from fasthtml.common import *
from starlette.responses import RedirectResponse

from core.config import DEFAULTS
from web.app_deps import AppDeps
from web.layout import _page_shell
from web.routes.title_format import title_format_page as _title_format_page


_TOGGLE_STYLE = Style(
    """
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
    .bool-toggle .toggle-pill.on {
        background: #1f6feb;
        color: #fff;
        border-color: #388bfd;
    }
    .bool-toggle .toggle-pill.off {
        background: transparent;
        color: #8b949e;
        border-color: #30363d;
    }
    .bool-toggle .toggle-label {
        font-size: 0.9rem;
        color: #e6edf3;
    }
    .api-method-toggle {
        display: flex;
        gap: 10px;
        margin-bottom: 20px;
        flex-wrap: wrap;
    }
    .api-method-toggle button {
        flex: 1;
        min-width: 220px;
        padding: 12px 18px;
        border: 2px solid #30363d;
        border-radius: 8px;
        background: transparent;
        color: #8b949e;
        font-size: 0.88rem;
        font-weight: 600;
        cursor: pointer;
        transition: all 0.2s;
    }
    .api-method-toggle button.active {
        border-color: #388bfd;
        background: #1f6feb;
        color: #fff;
    }
    .api-method-toggle button .method-title {
        display: block;
        font-size: 0.95rem;
        margin-bottom: 4px;
    }
    .api-method-toggle button .method-desc {
        display: block;
        font-size: 0.75rem;
        opacity: 0.8;
    }
"""
)


_TOGGLE_JS = Script(
    """
    function _toggleBool(btn, hiddenId) {
        const hidden = document.getElementById(hiddenId);
        const isOn = hidden.value === 'true';
        hidden.value = isOn ? 'false' : 'true';
        btn.textContent = isOn ? 'Desligado' : 'Ligado';
        btn.className = 'toggle-pill ' + (isOn ? 'off' : 'on');
    }
    function _selectApiMethod(value) {
        document.getElementById('hidden_use_playlist_items').value = value;
        document.querySelectorAll('.api-method-toggle button').forEach(b => b.classList.remove('active'));
        document.getElementById('btn-api-' + value).classList.add('active');
    }
"""
)


def _bool_toggle(key: str, value: bool, label: str) -> Div:
    hidden_id = f"hidden_{key}"
    pill_cls = "toggle-pill on" if value else "toggle-pill off"
    pill_label = "Ligado" if value else "Desligado"
    return Div(
        Input(type="hidden", name=key, value="true" if value else "false", id=hidden_id),
        Button(
            pill_label,
            type="button",
            cls=pill_cls,
            onclick=f"_toggleBool(this, '{hidden_id}')",
        ),
        Span(label, cls="toggle-label"),
        cls="bool-toggle",
    )


def _bool_keys_for_section(section_key: str) -> list[str]:
    return [
        k for k, (_, sec, _, vtype) in DEFAULTS.items() if sec == section_key and vtype == "bool"
    ]


def _config_form_fields(rows: list) -> list:
    fields = []
    for row in rows:
        key = row["key"]
        value = row["value"]
        desc = row.get("description", "")
        vtype = row.get("value_type", "str")
        if vtype == "bool":
            fields.append(_bool_toggle(key, value.lower() == "true", desc or key))
        else:
            fields.append(
                Label(
                    Span(desc or key, style="display:block;margin-bottom:4px;"),
                    Input(name=key, value=value, type="text", id=f"field_{key}"),
                )
            )
    return fields


def _apply_bool_defaults(form_data: dict, section_key: str) -> dict:
    updates = dict(form_data)
    for k in _bool_keys_for_section(section_key):
        if k not in updates:
            updates[k] = "false"
    return updates


def _after_config_update(deps: AppDeps) -> None:
    if deps.config:
        deps.config.reload()
    if deps.scheduler and deps.config:
        deps.scheduler.reload_config(deps.config)


def register_config_routes(app, deps: AppDeps) -> None:
    def _config_page(section_key: str, title: str, active_key: str, saved: bool = False):
        sections = deps.config.get_all_by_section() if deps.config else {}
        rows = sections.get(section_key, [])
        fields = _config_form_fields(rows)
        alert = (
            Div("✅ Configurações salvas com sucesso.", cls="alert alert-success")
            if saved
            else ""
        )
        return _page_shell(
            title,
            active_key,
            alert,
            _TOGGLE_STYLE,
            _TOGGLE_JS,
            Div(
                Form(
                    *fields,
                    Div(Button("Salvar", type="submit"), style="margin-top:20px;"),
                    method="post",
                    action=f"/config/{section_key}",
                ),
                cls="card",
            ),
        )

    @app.get("/config")
    def config_redirect():
        return RedirectResponse("/config/credentials", status_code=302)

    @app.get("/config/credentials")
    def config_credentials(saved: str = ""):
        if not deps.config:
            return _page_shell("API & Credenciais", "config_credentials", P("Config não inicializado."))
        alert = (
            Div("✅ Configurações salvas com sucesso.", cls="alert alert-success")
            if saved == "1"
            else ""
        )
        api_keys_val = deps.config.get_raw("youtube_api_keys")
        use_playlist_items = deps.config.get_bool("use_playlist_items")
        method_val = "true" if use_playlist_items else "false"
        return _page_shell(
            "API & Credenciais",
            "config_credentials",
            alert,
            _TOGGLE_STYLE,
            _TOGGLE_JS,
            Div(
                Form(
                    Label(
                        Span(
                            "Chaves de API do YouTube (vírgula para múltiplas)",
                            style="display:block;margin-bottom:4px;",
                        ),
                        Input(
                            name="youtube_api_keys",
                            value=api_keys_val,
                            type="text",
                            id="field_youtube_api_keys",
                            placeholder="AIzaSy..., AIzaSy...",
                        ),
                    ),
                    H3("Método de API", style="margin-top:24px;margin-bottom:10px;"),
                    P(
                        "Escolha como buscar vídeos dos canais. O método playlistItems é mais "
                        "eficiente e economiza quota da API, mas alguns canais podem não funcionar.",
                        cls="text-muted",
                        style="font-size:0.85rem;margin-bottom:12px;",
                    ),
                    Input(
                        type="hidden",
                        name="use_playlist_items",
                        value=method_val,
                        id="hidden_use_playlist_items",
                    ),
                    Div(
                        Button(
                            Span("playlistItems", cls="method-title"),
                            Span("✅ Menos chamadas • Mais eficiente • Recomendado", cls="method-desc"),
                            type="button",
                            id="btn-api-true",
                            cls="active" if use_playlist_items else "",
                            onclick="_selectApiMethod('true')",
                        ),
                        Button(
                            Span("search.list", cls="method-title"),
                            Span("⚠️ Mais chamadas • Fallback/legado", cls="method-desc"),
                            type="button",
                            id="btn-api-false",
                            cls="active" if not use_playlist_items else "",
                            onclick="_selectApiMethod('false')",
                        ),
                        cls="api-method-toggle",
                    ),
                    Div(Button("Salvar", type="submit"), style="margin-top:20px;"),
                    method="post",
                    action="/config/credentials",
                ),
                cls="card",
            ),
        )

    @app.post("/config/credentials")
    async def config_credentials_save(req):
        form = await req.form()
        if deps.config:
            data = {k: v for k, v in dict(form).items() if k in ("youtube_api_keys", "use_playlist_items")}
            deps.config.update_many(data)
            _after_config_update(deps)
        return RedirectResponse("/config/credentials?saved=1", status_code=303)

    @app.get("/config/scheduler")
    def config_scheduler(saved: str = ""):
        return _config_page("scheduler", "Agendador", "config_scheduler", saved == "1")

    @app.post("/config/scheduler")
    async def config_scheduler_save(req):
        form = await req.form()
        if deps.config:
            deps.config.update_many(_apply_bool_defaults(dict(form), "scheduler"))
            _after_config_update(deps)
        return RedirectResponse("/config/scheduler?saved=1", status_code=303)

    @app.get("/config/playlist")
    def config_playlist_page(saved: str = ""):
        if not deps.config:
            return _page_shell("Playlist", "config_playlist", P("Config não inicializado."))

        alert = Div("✅ Configurações salvas com sucesso.", cls="alert alert-success") if saved == "1" else ""

        use_invisible = deps.config.get_bool("use_invisible_placeholder")
        placeholder_url = deps.config.get_raw("placeholder_image_url")
        thumb_dir = deps.config.get_raw("thumbnail_cache_directory")

        return _page_shell(
            "Playlist",
            "config_playlist",
            alert,
            _TOGGLE_STYLE,
            _TOGGLE_JS,
            Div(
                Form(
                    _bool_toggle(
                        "use_invisible_placeholder",
                        use_invisible,
                        "Usar placeholder invisível no M3U",
                    ),
                    Label(
                        Span(
                            "URL da imagem placeholder para streams sem thumb",
                            style="display:block;margin-bottom:4px;",
                        ),
                        Input(name="placeholder_image_url", value=placeholder_url, type="text"),
                    ),
                    Label(
                        Span("Diretório de cache de thumbnails", style="display:block;margin-bottom:4px;"),
                        Input(
                            value=thumb_dir,
                            type="text",
                            disabled=True,
                            style="background:#161b22;color:#8b949e;cursor:not-allowed;",
                        ),
                        P(
                            "[BLOQUEADO] Padrão do sistema gerenciado automaticamente. Não recomendamos alterar este valor.",
                            cls="text-muted",
                            style="font-size:0.78rem;margin-top:4px;margin-bottom:0;",
                        ),
                    ),
                    Div(Button("Salvar", type="submit"), style="margin-top:20px;"),
                    method="post",
                    action="/config/playlist",
                ),
                cls="card",
            ),
        )

    @app.post("/config/playlist")
    async def config_playlist_save(req):
        form = await req.form()
        if deps.config:
            data = _apply_bool_defaults(dict(form), "playlist_output")
            data.pop("thumbnail_cache_directory", None)
            deps.config.update_many(data)
            _after_config_update(deps)
        return RedirectResponse("/config/playlist?saved=1", status_code=303)

    @app.get("/config/technical")
    def config_technical(saved: str = ""):
        return _config_page("technical", "Técnico", "config_technical", saved == "1")

    @app.post("/config/technical")
    async def config_technical_save(req):
        form = await req.form()
        if deps.config:
            deps.config.update_many(_apply_bool_defaults(dict(form), "technical"))
            _after_config_update(deps)
        return RedirectResponse("/config/technical?saved=1", status_code=303)

    @app.get("/config/vod-verification")
    def config_vod_verification(saved: str = ""):
        if not deps.config:
            return _page_shell("Verificação de VODs", "config_vod_verification", P("Config não inicializado."))

        cfg = deps.config
        alert = Div("✅ Configurações salvas com sucesso.", cls="alert alert-success") if saved == "1" else ""

        post_live_enabled = cfg.get_bool("vod_post_live_check_enabled")
        initial_delay = cfg.get_raw("vod_post_live_initial_delay_seconds")
        health_enabled = cfg.get_bool("vod_health_check_enabled")
        health_interval = cfg.get_raw("vod_health_check_interval_minutes")

        return _page_shell(
            "Verificação de VODs",
            "config_vod_verification",
            alert,
            _TOGGLE_STYLE,
            _TOGGLE_JS,
            Div(
                H2("Verificação Pós-Live"),
                P(
                    "Após uma live terminar, verifica automaticamente se o VOD ficou disponível antes de incluí-lo na playlist.",
                    cls="text-muted",
                    style="font-size:0.85rem;margin-bottom:16px;",
                ),
                Form(
                    _bool_toggle("vod_post_live_check_enabled", post_live_enabled, "Ativar verificação pós-live"),
                    Label(
                        Span("Delay inicial (segundos)", style="display:block;margin-bottom:4px;"),
                        Input(
                            name="vod_post_live_initial_delay_seconds",
                            value=initial_delay,
                            type="number",
                            min="0",
                            step="1",
                            style="max-width:200px;",
                        ),
                    ),
                    Div(
                        "ℹ️ Retries automáticos após falha: ",
                        Strong("2min → 5min → 10min"),
                        cls="alert alert-info",
                        style="margin-top:8px;margin-bottom:16px;font-size:0.85rem;",
                    ),
                    H2("Health Check Periódico", style="margin-top:24px;"),
                    P(
                        "Verifica periodicamente se os VODs no cache ainda estão acessíveis. VODs indisponíveis são removidos das playlists geradas.",
                        cls="text-muted",
                        style="font-size:0.85rem;margin-bottom:16px;",
                    ),
                    _bool_toggle(
                        "vod_health_check_enabled",
                        health_enabled,
                        "Ativar health check periódico de VODs",
                    ),
                    Label(
                        Span("Intervalo de verificação (minutos)", style="display:block;margin-bottom:4px;"),
                        Input(
                            name="vod_health_check_interval_minutes",
                            value=health_interval,
                            type="number",
                            min="1",
                            step="1",
                            style="max-width:200px;",
                        ),
                    ),
                    Div(Button("Salvar", type="submit"), style="margin-top:20px;"),
                    method="post",
                    action="/config/vod-verification",
                ),
                cls="card",
            ),
        )

    @app.post("/config/vod-verification")
    async def config_vod_verification_save(req):
        form = await req.form()
        if deps.config:
            allowed_keys = {
                "vod_post_live_check_enabled",
                "vod_post_live_initial_delay_seconds",
                "vod_health_check_enabled",
                "vod_health_check_interval_minutes",
            }
            data = _apply_bool_defaults(
                {k: v for k, v in dict(form).items() if k in allowed_keys},
                "vod_verification",
            )
            deps.config.update_many(data)
            _after_config_update(deps)
        return RedirectResponse("/config/vod-verification?saved=1", status_code=303)

    @app.get("/config/filters")
    def config_filters(saved: str = ""):
        if not deps.config:
            return _page_shell("Filtros", "config_filters", P("Config não inicializado."))

        cfg = deps.config
        alert = Div("✅ Filtros salvos com sucesso.", cls="alert alert-success") if saved == "1" else ""

        filter_by_cat = cfg.get_bool("filter_by_category")
        allowed_ids = cfg.get_raw("allowed_category_ids")
        cat_mappings = cfg.get_raw("category_mappings")
        shorts_max_s = cfg.get_raw("shorts_max_duration_s")
        shorts_words_raw = cfg.get_raw("shorts_block_words")
        shorts_words = [w.strip() for w in shorts_words_raw.split(",") if w.strip()]
        epg_cleanup = cfg.get_bool("epg_description_cleanup")
        keep_recorded = cfg.get_bool("keep_recorded_streams")
        max_recorded = cfg.get_raw("max_recorded_per_channel")
        retention_days = cfg.get_raw("recorded_retention_days")
        max_schedule = cfg.get_raw("max_schedule_hours")
        max_upcoming = cfg.get_raw("max_upcoming_per_channel")

        def _tag_list_with_input(words: list, field_name: str, hidden_name: str) -> Div:
            tags = []
            for w in words:
                tags.append(
                    Span(
                        Span(w, cls="tag-text"),
                        Button("×", cls="remove-tag", type="button", onclick=f"removeTag(this, '{hidden_name}')"),
                        cls="tag",
                    )
                )
            return Div(
                Input(type="hidden", name=hidden_name, value=",".join(words), id=f"hidden_{hidden_name}"),
                Div(*tags, id=f"tags_{hidden_name}", cls="tag-list"),
                Div(
                    Input(
                        type="text",
                        id=f"input_{field_name}",
                        placeholder="Adicionar... (Enter)",
                        style="width:200px;display:inline-block;margin-right:8px;",
                    ),
                    Button(
                        "+ Adicionar",
                        type="button",
                        cls="btn-secondary",
                        onclick=f"addTag('{field_name}', '{hidden_name}')",
                        style="font-size:0.82rem;padding:5px 12px;",
                    ),
                    style="margin-top:8px;",
                ),
            )

        tags_js = Script(
            """
            function _syncHidden(hiddenName) {
                const container = document.getElementById('tags_' + hiddenName);
                const hidden = document.getElementById('hidden_' + hiddenName);
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
                    + `<button class="remove-tag" type="button" onclick="removeTag(this,'${hiddenName}')">×</button>`;
                container.appendChild(tag);
                inp.value = '';
                _syncHidden(hiddenName);
            }

            document.querySelectorAll('[id^="input_"]').forEach(inp => {
                inp.addEventListener('keydown', e => {
                    if (e.key === 'Enter') {
                        e.preventDefault();
                        const hiddenName = inp.id.replace('input_new_', '');
                        addTag(inp.id.replace('input_', ''), hiddenName);
                    }
                });
            });
"""
        )

        return _page_shell(
            "Filtros",
            "config_filters",
            alert,
            _TOGGLE_STYLE,
            _TOGGLE_JS,
            Form(
                Div(
                    H2("Filtro de Categoria"),
                    P(
                        "Quando ativo, apenas streams com IDs de categoria permitidos entram na playlist.",
                        cls="text-muted",
                    ),
                    _bool_toggle("filter_by_category", filter_by_cat, "Ativar filtro por categoria"),
                    Label(
                        Span("IDs permitidos (vírgula) — ex: 17,22,25", style="display:block;margin-bottom:4px;"),
                        Input(name="allowed_category_ids", value=allowed_ids, type="text"),
                    ),
                    Label(
                        Span(
                            "Renomear categorias: ID|Nome (vírgula) — não filtra, só exibe",
                            style="display:block;margin-bottom:4px;",
                        ),
                        Input(name="category_mappings", value=cat_mappings, type="text"),
                    ),
                    cls="card",
                ),
                Div(
                    H2("Filtro de Shorts"),
                    P(
                        "Shorts com duração conhecida são bloqueados pelo campo de segundos. "
                        "Para upcoming/live (duração ainda desconhecida), use palavras-chave.",
                        cls="text-muted",
                    ),
                    Label(
                        Span("Duração máxima (s) — 0 = desativado", style="display:block;margin-bottom:4px;"),
                        Input(name="shorts_max_duration_s", value=shorts_max_s, type="number", style="width:140px;"),
                    ),
                    Label(
                        Span("Palavras bloqueadas (título/tags)", style="display:block;margin-bottom:4px;"),
                        _tag_list_with_input(shorts_words, "new_short_word", "shorts_block_words"),
                    ),
                    cls="card",
                ),
                Div(
                    H2("VOD / Gravações"),
                    _bool_toggle(
                        "keep_recorded_streams",
                        keep_recorded,
                        "Manter streams gravados (ex-live) na playlist VOD",
                    ),
                    _bool_toggle(
                        "epg_description_cleanup",
                        epg_cleanup,
                        "Manter apenas o primeiro parágrafo da descrição no EPG",
                    ),
                    Label(
                        Span("Máximo de gravações por canal", style="display:block;margin-bottom:4px;"),
                        Input(name="max_recorded_per_channel", value=max_recorded, type="number", style="width:100px;"),
                    ),
                    Label(
                        Span("Dias de retenção de gravados", style="display:block;margin-bottom:4px;"),
                        Input(name="recorded_retention_days", value=retention_days, type="number", style="width:100px;"),
                    ),
                    cls="card",
                ),
                Div(
                    H2("Agendamentos futuros"),
                    Label(
                        Span("Limite futuro em horas para upcoming (ex: 72)", style="display:block;margin-bottom:4px;"),
                        Input(name="max_schedule_hours", value=max_schedule, type="number", style="width:100px;"),
                    ),
                    Label(
                        Span("Máximo de agendamentos por canal", style="display:block;margin-bottom:4px;"),
                        Input(name="max_upcoming_per_channel", value=max_upcoming, type="number", style="width:100px;"),
                    ),
                    cls="card",
                ),
                Div(Button("Salvar filtros", type="submit"), style="margin-top:8px;"),
                method="post",
                action="/config/filters",
            ),
            tags_js,
        )

    @app.post("/config/filters")
    async def config_filters_save(req):
        form = await req.form()
        if not deps.config:
            return RedirectResponse("/config/filters", status_code=303)
        deps.config.update_many(_apply_bool_defaults(dict(form), "filters"))
        _after_config_update(deps)
        return RedirectResponse("/config/filters?saved=1", status_code=303)

    @app.get("/config/title-format")
    def config_title_format(saved: str = ""):
        if not deps.config:
            return _page_shell("Formato de Título", "config_title_format", P("Config não inicializado."))
        return _title_format_page(deps.config, saved=saved == "1")

    @app.post("/config/title-format")
    async def config_title_format_save(req):
        if not deps.config:
            return RedirectResponse("/config/title-format", status_code=303)

        form = dict(await req.form())

        order_raw = form.get("title_components_order", "channel,status,title")
        deps.config.update_many({"title_components_order": order_raw})

        all_comps = [c.strip() for c in order_raw.split(",") if c.strip()]
        enabled = []
        for comp in all_comps:
            val = form.get(f"comp_enabled_{comp}", "false")
            if val == "true":
                enabled.append(comp)
        if "title" not in enabled:
            enabled.append("title")
        deps.config.update_many({"title_components_enabled": ",".join(enabled)})

        brackets = [c for c in all_comps if form.get(f"comp_brackets_{c}", "false") == "true"]
        deps.config.update_many({"title_components_brackets": ",".join(brackets)})

        exprs_raw = form.get("title_filter_expressions", "")
        deps.config.update_many({"title_filter_expressions": exprs_raw})

        strip_emojis = form.get("title_strip_emojis", "false")
        deps.config.update_many({"title_strip_emojis": strip_emojis})
        _after_config_update(deps)

        return RedirectResponse("/config/title-format?saved=1", status_code=303)

    @app.post("/config/channels")
    async def config_channels_save(req):
        if not deps.config:
            return RedirectResponse("/canais", status_code=303)
        form = dict(await req.form())
        mapping_val = form.get("channel_name_mappings", "")
        deps.config.update_many({"channel_name_mappings": mapping_val})
        _after_config_update(deps)
        return RedirectResponse("/canais?saved=1", status_code=303)
