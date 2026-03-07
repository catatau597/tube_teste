# Fase 1 — Mapeamento Dispatcharr `ts_proxy` -> `tube_teste`

Contexto desta fase:
- baseline de evolução para live proxy passa a ser `Dispatcharr/apps/proxy/ts_proxy`
- proxy antigo do `tube_teste` fica apenas como fallback temporário
- sem migração total para Django/gevent/Redis nesta etapa

## 1) Tabela objetiva de mapeamento

| Dispatcharr (`ts_proxy`) | Papel no Dispatcharr | Destino no `tube_teste` | Classificação | Decisão prática |
|---|---|---|---|---|
| `stream_buffer.py::StreamBuffer` | Buffer de chunks TS + política de leitura adaptativa | `core/live_proxy/stream_buffer.py` | Portar quase igual | Portar estrutura e heurísticas de `get_optimized_client_data`, mantendo backend em memória local (sem Redis). |
| `stream_generator.py::StreamGenerator` | Loop por cliente (attach, catch-up, keepalive, timeout, cleanup) | `core/live_proxy/stream_generator.py` | Portar quase igual | Portar fluxo principal de geração por cliente; adaptar pontos de estado distribuído para estado local. |
| `stream_manager.py::StreamManager` | Ciclo de vida da ingestão, retry/reconnect, health checks | `core/live_proxy/stream_manager.py` | Adaptar | Portar lógica de manager, removendo dependências Django/Redis e mantendo integração com `core/stream_ingress.py`. |
| `client_manager.py::ClientManager` | Registro de clientes, atividade, disconnect/heartbeat | `core/live_proxy/client_registry.py` (ou dentro de `stream_manager.py`) | Portar quase igual | Portar modelo local de clientes e métricas; descartar heartbeat Redis e pubsub cross-worker. |
| `channel_status.py::ChannelStatus` | Snapshot detalhado de canal para dashboard/debug | `core/live_proxy/status.py` + adapter em `web/main.py` | Adaptar | Portar montagem de métricas; substituir leitura Redis por objetos in-memory do novo core. |
| `constants.py` | Estados/eventos/campos centrais do proxy | `core/live_proxy/models.py` | Portar quase igual | Portar enums/constantes relevantes para live proxy local. |
| `config_helper.py` | Acesso centralizado a parâmetros do proxy | `core/live_proxy/config.py` | Portar quase igual | Portar helper; backend de config passa a `AppConfig` do `tube_teste`. |
| `utils.py::create_ts_packet` | Pacotes TS keepalive/error sintéticos | `core/live_proxy/ts_packets.py` (opcional) | Adaptar | Manter apenas se necessário para keepalive explícito; caso não seja necessário, usar keepalive por pacing sem pacote sintético. |
| `utils.py::detect_stream_type` | Classificação de URL para estratégia de ingest | `core/stream_ingress.py` (interface) | Adaptar | Reusar lógica se útil para seleção de ingest; origem primária continua o planner de ingress do `tube_teste`. |
| `server.py::ProxyServer` | Orquestração global + ownership multi-worker + Redis PubSub | Não portar como está | Descartar (nesta fase) | Substituir por manager local single-process em `core/live_proxy/` (sem ownership distribuído). |
| `redis_keys.py` | Chaves Redis padronizadas | Não aplicável agora | Descartar (nesta fase) | Sem Redis nesta etapa; manter como referência para futura camada distribuída opcional. |
| `views.py::stream_ts` | Endpoint HTTP de streaming | `web/main.py::/api/proxy/{video_id}` | Adaptar | Portar fluxo/semântica para adapter fino; endpoint atual vira ponte para `core/live_proxy`. |
| `views.py::channel_status` | Endpoint status detalhado | `web/main.py::/api/proxy/status` + `/api/proxy/debug/{video_id}` | Adaptar | Manter contrato de dashboard atual e enriquecer com snapshot do novo core. |
| `views.py::stop_channel` / `stop_client` | Controle administrativo de sessão/canal | `web/main.py` + APIs admin existentes | Adaptar | Portar semântica mínima de stop/cancel sem dependência DRF/Django. |
| `views.py::stream_xc`, `change_stream`, `next_stream` | Integrações XC e troca de stream por modelo de canal | Fora de escopo imediato | Descartar | Não necessário para live YouTube atual. |
| `url_utils.py` | Resolução/troca de streams com modelos Django/M3U | Fora de escopo imediato | Descartar | `tube_teste` usa fluxo próprio via YouTube/ingress planner. |
| `services/channel_service.py` | Serviço de canais (Django) | Fora de escopo imediato | Descartar | Não migrar domínio de canais do Dispatcharr. |
| `services/log_parsers.py` | Parsers de logs FFmpeg/Streamlink | `core/live_proxy/log_parsers.py` (futuro) | Adaptar (futuro) | Útil para observabilidade; não bloqueia Fase 2 inicial. |
| `http_streamer.py::HTTPStreamReader` | Reader HTTP em thread -> pipe | `core/live_proxy/http_reader.py` (opcional) | Adaptar (opcional) | Só portar se necessário para origem HTTP direta; para YouTube atual pode ficar fora do MVP. |
| `apps.py`, `urls.py` | Integração Django app/rotas | Não aplicável | Descartar | Sem migração de framework. |

## 2) Dependências críticas identificadas

Dependências para portar agora:
- núcleo de buffer + generator + manager + client tracking
- contrato de snapshot para dashboard (`clients`, `buffer_bytes`, `process_alive`, `process_pid`, `uptime`, `ingress_type`)
- adapter HTTP em `web/main.py` para `/api/proxy/{video_id}`
- integração com `core/stream_ingress.py` como única fonte de plano de ingest

Dependências do Dispatcharr que exigem adaptação:
- `Redis` (estado distribuído, TTL, pubsub, ownership)
- `gevent` (`gevent.sleep`, eventos e timers cooperativos)
- modelos Django (`Channel`, `Stream`, M3U profiles, permissões DRF)
- utilitários internos (`log_system_event`, websocket infra, config TSConfig)

## 3) Contratos mínimos fechados para Fase 2

Contratos internos alvo no `tube_teste`:
- `ensure_stream(video_id, ingress_plan, metadata) -> LiveProxyHandle`
- `attach_client(video_id, client_id, ip, user_agent) -> async iterator[bytes]`
- `detach_client(video_id, client_id) -> None`
- `snapshot(video_id: str | None = None) -> dict | list[dict]`
- `stop_stream(video_id) -> bool`

Campos mínimos de snapshot por stream:
- `video_id`
- `clients`
- `buffer_chunks`
- `buffer_bytes`
- `process_alive`
- `process_pid`
- `uptime`
- `ingress_type`

## 4) Itens explicitamente fora da Fase 1/2 inicial

- migração para Django
- migração para gevent
- Redis obrigatório
- failover multi-worker por ownership distribuído
- features XC/M3U avançadas específicas do Dispatcharr

## 5) Saída da Fase 1

Critério de saída atendido com este documento:
- módulos do `ts_proxy` necessários listados
- classificação `portar / adaptar / descartar` definida por módulo
- dependências críticas e contratos mínimos fechados para iniciar `core/live_proxy/`
