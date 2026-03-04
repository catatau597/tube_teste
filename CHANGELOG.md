# Changelog — TubeWrangler

Todas as mudanças relevantes do projeto são documentadas aqui.
Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

---

## [Unreleased] — branch `dev`

### Adicionado

#### UI — Sidebar e layout base (`web/layout.py`)
- Novo arquivo `web/layout.py` com `_sidebar()` e `_page_shell()` reutilizáveis.
- Sidebar fixa (220px) com tema dark (`#0d1117` / `#161b22`).
- Navegação hierárquica: Dashboard → Proxy → Config (submenus) → Logs.
- Item ativo destacado com borda lateral azul (`#58a6ff`).
- CSS embutido com utilitários: `.card`, `.badge-*`, `.tag`, `.alert-*`, `.text-muted`.
- Responsivo: em telas < 768px, sidebar passa para posição relativa.

#### Rotas de configuração separadas por seção (`web/main.py`)
- `/config` agora redireciona (302) para `/config/credentials`.
- Novas rotas GET + POST por seção:
  - `/config/credentials` — API key, handles, IDs de canais.
  - `/config/scheduler` — intervalos, janelas de pré/pós-evento, horário ativo.
  - `/config/filters` — UI dedicada (ver abaixo).
  - `/config/output` — placeholder, thumbnails.
  - `/config/technical` — porta, fuso, stale hours, proxy base URL.
- Formulários genéricos gerados a partir de `AppConfig.get_all_by_section()`.

#### Página `/config/filters` — UI dedicada de filtros
- **Filtro de categoria**: toggle para ativar + campo de IDs permitidos (separado de `category_mappings`).
- **`category_mappings`**: agora documentado como "só renomeia para exibição, não filtra".
- **Filtro de Shorts por duração**: campo numérico `shorts_max_duration_s` (0 = desativado).
- **Filtro de Shorts por palavras**: campo `shorts_block_words` com tags interativas (adicionar/remover via JS sem recarregar página).
- **Expressões de título**: `title_filter_expressions` também com tags interativas.
- **Mapeamento de canais**: `channel_name_mappings` (campo texto).
- **VOD / Gravações**: `keep_recorded_streams`, `max_recorded_per_channel`, `recorded_retention_days`, `epg_description_cleanup`.
- **Agendamentos futuros**: `max_schedule_hours`, `max_upcoming_per_channel`.
- Checkboxes desmarcados enviam `false` explicitamente (via JS no submit).

#### Página `/logs` — painel de logging inline
- Painel colapsável no topo com botões DEBUG / INFO / WARNING / ERROR.
- Botão altera nível em runtime via `POST /api/logs/level` **e persiste no `config.db`**.
- Nível ativo destacado visualmente; feedback "✅ Nível alterado" por 3s.
- Filtro de visibilidade por nível (dropdown) sem recarregar página.
- Botão "Limpar" limpa o painel sem desconectar o SSE.
- Auto-scroll configurável via checkbox.

#### Novas rotas de API
- `GET /api/logs/level` — retorna nível atual do logger raiz.
- `POST /api/logs/level` — altera nível em runtime e persiste no DB.

#### Filtros de Shorts e categoria em runtime (`core/state_manager.py`)
- Função auxiliar `_parse_duration_seconds(iso)` — converte ISO 8601 para segundos.
- `update_streams()` aplica 3 filtros em ordem antes de inserir no estado:
  1. **Categoria**: descarta se `filter_by_category=true` e `categoryoriginal` não está em `allowed_category_ids`.
  2. **Shorts por palavras**: descarta se título ou tags contêm qualquer item de `shorts_block_words`.
  3. **Shorts por duração**: descarta se `0 < duration_s <= shorts_max_duration_s`.
- Streams bloqueados são removidos do estado se já existiam (evita persistir lixo).
- Upcoming/live com duração ainda desconhecida (0) **não** são bloqueados pelo filtro de duração.
- Log de summary ao final de cada ciclo: `+adicionados upd=N | ign categoria=X shorts(palavra)=Y shorts(dur)=Z`.

#### Novas chaves de configuração (`core/config.py`)
| Chave | Padrão | Seção | Descrição |
|---|---|---|---|
| `shorts_max_duration_s` | `62` | `filters` | Duração máx. em segundos para bloquear Shorts (0=off) |
| `shorts_block_words` | `#shorts,#short` | `filters` | Palavras em título/tags que identificam Shorts |
| `allowed_category_ids` | `17,22` | `filters` | IDs de categoria permitidos quando `filter_by_category=true` |

### Alterado

- `web/routes/proxy_dashboard.py`: substituído `nav` hardcoded por `_page_shell('Proxy Dashboard', 'proxy', ...)` — visual consistente com demais páginas.
- `core/config.py`: `category_mappings` agora documenta claramente que **não filtra** (só renomeia para exibição na UI/EPG).
- `core/config.py`: `update_many()` agora faz `try/except KeyError` internamente — chaves desconhecidas no form são ignoradas silenciosamente.
- `core/config.py`: `get_int()` agora retorna `0` em vez de lançar `ValueError` para valores não numéricos.
- `web/main.py`: todas as páginas HTML passam a usar `_page_shell()` em vez de `Titled()` + nav inline.
- `web/main.py`: rota `/` (Dashboard) usa badges coloridos por status (live/upcoming/vod/none).

### Removido

- `core/config.py`: chave obsoleta `allowed_category_ids` que estava em `_OBSOLETE_KEYS` (agora promovida a chave ativa).
- `core/config.py`: chaves de `_OBSOLETE_KEYS` removidas: `log_to_file`, `smart_player_log_level`, `smart_player_log_to_file` (já eram removidas na migração, documentado explicitamente).
- `web/routes/proxy_dashboard.py`: bloco `nav = Div(A(...), ...)` hardcoded removido.

---

## Histórico anterior

> Mudanças anteriores ao CHANGELOG não foram documentadas neste arquivo.
> Consulte o histórico de commits (`git log`) para referência.
