"""
web/layout.py
-------------
Layout base compartilhado: sidebar + shell de página.
Usado por todas as rotas HTML do projeto.
"""

from fasthtml.common import *


_SIDEBAR_CSS = """
* { box-sizing: border-box; }

body {
    margin: 0;
    font-family: system-ui, sans-serif;
    background: #0d1117;
    color: #e6edf3;
}

/* ---------- Sidebar ---------- */
#sidebar {
    position: fixed;
    top: 0; left: 0;
    width: 220px;
    height: 100vh;
    background: #161b22;
    border-right: 1px solid #30363d;
    padding: 0;
    overflow-y: auto;
    z-index: 100;
    display: flex;
    flex-direction: column;
}

#sidebar .brand {
    padding: 18px 16px 12px;
    font-size: 1.05rem;
    font-weight: 700;
    color: #58a6ff;
    border-bottom: 1px solid #30363d;
    letter-spacing: .02em;
    text-decoration: none;
    display: block;
}

#sidebar nav {
    padding: 10px 0;
    flex: 1;
}

#sidebar .nav-item {
    display: block;
    padding: 7px 18px;
    color: #8b949e;
    text-decoration: none;
    font-size: 0.9rem;
    border-left: 3px solid transparent;
    transition: color .15s, background .15s;
}

#sidebar .nav-item:hover {
    color: #e6edf3;
    background: #21262d;
}

#sidebar .nav-item.active {
    color: #58a6ff;
    border-left-color: #58a6ff;
    background: #21262d;
    font-weight: 600;
}

#sidebar .nav-group-label {
    display: block;
    padding: 12px 18px 4px;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: .08em;
    color: #484f58;
    cursor: default;
}

#sidebar .nav-sub {
    display: block;
    padding: 5px 18px 5px 30px;
    color: #8b949e;
    text-decoration: none;
    font-size: 0.85rem;
    border-left: 3px solid transparent;
    transition: color .15s, background .15s;
}

#sidebar .nav-sub:hover {
    color: #e6edf3;
    background: #21262d;
}

#sidebar .nav-sub.active {
    color: #58a6ff;
    border-left-color: #58a6ff;
    background: #21262d;
    font-weight: 600;
}

#sidebar .sidebar-footer {
    padding: 12px 18px;
    font-size: 0.75rem;
    color: #484f58;
    border-top: 1px solid #30363d;
}

/* ---------- Main content ---------- */
#main-content {
    margin-left: 220px;
    padding: 28px 36px;
    min-height: 100vh;
}

h1, h2, h3 { color: #e6edf3; }

h1 { font-size: 1.4rem; margin-bottom: 20px; border-bottom: 1px solid #30363d; padding-bottom: 10px; }
h2 { font-size: 1.15rem; margin-top: 28px; margin-bottom: 12px; }
h3 { font-size: 0.98rem; margin-top: 20px; margin-bottom: 8px; color: #8b949e; }

/* ---------- Tables ---------- */
table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
th { text-align: left; padding: 8px 10px; background: #21262d; color: #8b949e;
     font-weight: 600; border-bottom: 1px solid #30363d; }
td { padding: 7px 10px; border-bottom: 1px solid #21262d; vertical-align: top; }
tr:hover td { background: #161b22; }

/* ---------- Forms ---------- */
form label {
    display: block;
    margin-bottom: 14px;
    font-size: 0.88rem;
    color: #8b949e;
}

form input[type=text], form input[type=number],
form select, form textarea {
    display: block;
    width: 100%;
    margin-top: 4px;
    padding: 7px 10px;
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 6px;
    color: #e6edf3;
    font-size: 0.9rem;
}

form input[type=checkbox] {
    margin-right: 6px;
    width: auto;
    display: inline;
}

button, .btn {
    padding: 7px 18px;
    background: #238636;
    color: #fff;
    border: none;
    border-radius: 6px;
    cursor: pointer;
    font-size: 0.9rem;
    font-weight: 600;
    transition: background .15s;
}
button:hover, .btn:hover { background: #2ea043; }

.btn-danger { background: #da3633; }
.btn-danger:hover { background: #f85149; }

.btn-secondary {
    background: #21262d;
    border: 1px solid #30363d;
    color: #e6edf3;
}
.btn-secondary:hover { background: #30363d; }

/* ---------- Cards / sections ---------- */
.card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 20px 24px;
    margin-bottom: 20px;
}

.badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 0.75rem;
    font-weight: 600;
}
.badge-live     { background: #1a7f37; color: #fff; }
.badge-upcoming { background: #9a6700; color: #fff; }
.badge-vod      { background: #1f6feb; color: #fff; }
.badge-none     { background: #30363d; color: #8b949e; }
.badge-active {
    background: #1a3a1a;
    color: #3fb950;
    border: 1px solid #238636;
    padding: 2px 10px;
}
.badge-frozen {
    background: #1c2a3a;
    color: #58a6ff;
    border: 1px solid #1f6feb;
    padding: 2px 10px;
}

/* ---------- Tags (filtros) ---------- */
.tag-list { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.tag {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 3px 10px;
    background: #21262d;
    border: 1px solid #30363d;
    border-radius: 14px;
    font-size: 0.82rem;
    color: #e6edf3;
}
.tag .remove-tag {
    cursor: pointer; color: #8b949e;
    font-size: 0.9rem; line-height: 1;
    background: none; border: none; padding: 0;
}
.tag .remove-tag:hover { color: #f85149; background: none; }

/* ---------- Alerts ---------- */
.alert {
    padding: 10px 16px;
    border-radius: 6px;
    margin-bottom: 16px;
    font-size: 0.88rem;
}
.alert-success { background: #1a7f3722; border: 1px solid #1a7f37; color: #3fb950; }
.alert-error   { background: #da363322; border: 1px solid #da3633; color: #f85149; }
.alert-info    { background: #1f6feb22; border: 1px solid #1f6feb; color: #58a6ff; }

/* ---------- Utilities ---------- */
.text-muted { color: #8b949e; font-size: 0.85rem; }
.mt-0 { margin-top: 0; }
.mb-0 { margin-bottom: 0; }
code {
    background: #21262d;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 0.85rem;
    color: #e6edf3;
}

/* ---------- Responsive ---------- */
@media (max-width: 768px) {
    #sidebar { width: 100%; height: auto; position: relative; }
    #main-content { margin-left: 0; padding: 16px; }
}
"""


def _sidebar(active: str = "") -> Div:
    """
    Sidebar fixa com navegação.
    `active` deve ser uma das chaves:
      dashboard, canais, proxy,
      config_credentials, config_scheduler, config_filters,
      config_output, config_technical, logs
    """
    def nav_item(label, href, key):
        cls = "nav-item active" if active == key else "nav-item"
        return A(label, href=href, cls=cls)

    def nav_sub(label, href, key):
        cls = "nav-sub active" if active == key else "nav-sub"
        return A(label, href=href, cls=cls)

    return Div(
        A("\u26a1 TubeWrangler", href="/", cls="brand"),
        Nav(
            nav_item("\U0001f3e0  Dashboard", "/",       "dashboard"),
            nav_item("\U0001f4fa  Canais",    "/canais",  "canais"),
            nav_item("\U0001f4e1  Proxy",      "/proxy",   "proxy"),

            Span("Configura\u00e7\u00f5es", cls="nav-group-label"),
            nav_sub("\U0001f511  Credenciais",  "/config/credentials", "config_credentials"),
            nav_sub("\U0001f550  Agendador",    "/config/scheduler",   "config_scheduler"),
            nav_sub("\U0001f39b\ufe0f   Filtros",      "/config/filters",     "config_filters"),
            nav_sub("\U0001f4e4  Output",        "/config/output",      "config_output"),
            nav_sub("\U0001f527  T\u00e9cnico",   "/config/technical",   "config_technical"),

            nav_item("\U0001f4cb  Logs",          "/logs",   "logs"),
        ),
        Div("TubeWrangler v3.x", cls="sidebar-footer"),
        id="sidebar",
    )


def _page_shell(title: str, active: str, *content) -> Any:
    """
    Shell completo: <html> com sidebar + área de conteúdo.
    """
    return Html(
        Head(
            Meta(charset="utf-8"),
            Meta(name="viewport", content="width=device-width, initial-scale=1"),
            Title(f"{title} \u2014 TubeWrangler"),
            Link(
                rel="stylesheet",
                href="https://cdn.jsdelivr.net/npm/pico.css@2/css/pico.min.css",
            ),
            Style(_SIDEBAR_CSS),
        ),
        Body(
            _sidebar(active),
            Div(
                H1(title),
                *content,
                id="main-content",
            ),
        ),
    )
