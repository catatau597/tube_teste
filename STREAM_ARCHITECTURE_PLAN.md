# Plano de Arquitetura de Streaming

Projeto: `tube_teste`
Branch de trabalho: `dev_codex`
Objetivo: parar o ciclo de correções locais e reorganizar o streaming em três camadas estáveis:

1. entrada do stream
2. gerenciamento do stream
3. saída para clientes

Este documento é a referência de execução para qualquer mudança futura de stream/live/VOD nesta branch.
Mudanças fora desta ordem tendem a reintroduzir regressões.

---

## 1. Problema Atual

Hoje o projeto mistura responsabilidades em pontos críticos:

- `web/main.py` decide estratégia de ingest, cria processos, faz fallback e também serve clientes.
- `core/proxy_manager.py` mistura buffer, clientes, processos e ciclo de vida do stream live.
- `core/vod_proxy.py` está em evolução, mas ainda não é uma camada isolada da mesma forma que o live.
- `core/player_router.py` concentra parte da lógica de entrada, mas ainda é acoplado ao formato de saída esperado.
- `core/playlist_builder.py` conhece diretamente endpoints que dependem da implementação interna.

Efeitos práticos observados:

- live instável
- VOD sujeito a regressões de áudio/seek/sessão
- dashboard parcial
- correções locais em uma rota quebram outras
- problemas do editor aumentam porque os módulos não têm contratos claros

---

## 2. Arquitetura Alvo

### 2.1 Camada A — Entrada do Stream

Responsabilidade:

- descobrir a origem reproduzível do conteúdo
- escolher a ferramenta de ingest correta
- abrir a conexão com a origem

Entradas esperadas:

- `video_id`
- `status`
- `watch_url`
- `user_agent`
- flags de debug

Saídas esperadas:

- objeto descritivo de origem (`StreamSource`)
- metadados do stream
- método de ingest escolhido

Regras:

- live e VOD não compartilham a mesma estratégia padrão
- live YouTube deve priorizar HLS resolvido + FFmpeg com pacing
- streamlink fica como fallback de ingest, não como regra implícita
- VOD proxy HTTP não pode depender da URL pensada para FFmpeg TS

Componentes alvo:

- `core/player_router.py`
- novo módulo dedicado para ingest, se necessário: `core/stream_ingress.py`

Contrato alvo:

- `resolve_live_source(...) -> StreamSource`
- `resolve_vod_source(...) -> StreamSource`
- `build_live_ingest_command(...) -> list[str]`
- `build_placeholder_command(...) -> list[str]`

### 2.2 Camada B — Gerenciamento do Stream

Responsabilidade:

- manter estado do stream em execução
- gerenciar clientes e sessão
- controlar buffer ou conexão persistente
- reinício, cleanup, timeout, métricas

Subdivisão obrigatória:

- `LiveStreamManager`
- `VODSessionManager`

Regras:

- live usa buffer compartilhado por `video_id`
- VOD usa sessão persistente por `session_id`
- nenhum manager decide qual ferramenta de ingest usar
- nenhum manager conhece playlist M3U

Componentes alvo:

- `core/proxy_manager.py` -> somente live
- `core/vod_proxy.py` -> somente VOD

Contratos alvo:

- `LiveStreamManager.ensure_started(video_id, source)`
- `LiveStreamManager.attach_client(video_id, client_id, ...)`
- `LiveStreamManager.stop(video_id)`
- `VODSessionManager.ensure_session(video_id, session_id, source)`
- `VODSessionManager.open_request(session_id, method, range_header)`
- `VODSessionManager.cleanup_session(session_id)`

### 2.3 Camada C — Saída para Clientes

Responsabilidade:

- expor endpoints HTTP
- aplicar redirects de sessão
- transformar objetos do manager em `Response`/`StreamingResponse`
- renderizar dashboard e playlists

Regras:

- endpoint não cria estratégia de ingest
- endpoint não implementa buffer
- endpoint usa managers e contratos explícitos

Componentes alvo:

- `web/main.py`
- `web/routes/proxy_dashboard.py`
- `core/playlist_builder.py`

Contratos alvo:

- `/api/proxy/{video_id}` -> live/upcoming/placeholder
- `/api/vod/{video_id}` -> cria sessão e redireciona
- `/api/vod/{video_id}/{session_id}` -> usa sessão persistente
- `/api/proxy/status` -> agrega live + sessões VOD

---

## 3. Estratégia Técnica por Tipo de Conteúdo

### 3.1 Live

Entrada:

- resolver HLS real do YouTube
- usar FFmpeg com `-re` como ingest primário para HLS
- usar streamlink apenas como fallback controlado

Gerenciamento:

- buffer compartilhado por `video_id`
- clientes entram alguns chunks atrás da cabeça do buffer
- pulo controlado para clientes atrasados
- métricas por cliente

Saída:

- `/api/proxy/{video_id}`
- playlists live apontam sempre para `/api/proxy`
- dashboard mostra live ativos

### 3.2 VOD

Entrada:

- resolver URL única reproduzível com áudio
- priorizar formato progressivo/HTTP adequado para `Range`
- não usar URL de `bestvideo+bestaudio` quando a saída for proxy HTTP direto

Gerenciamento:

- sessão por `session_id`
- mesma sessão reaproveita `requests.Session` e URL final
- `Range` deve ser normalizado e validado
- cleanup por inatividade

Saída:

- `/api/vod/{video_id}` faz redirect para sessão
- `/api/vod/{video_id}/{session_id}` serve o conteúdo
- playlists VOD apontam para `/api/vod/{video_id}`
- dashboard mostra sessões VOD ativas

---

## 4. Plano de Execução

### Fase 0 — Congelamento e Inventário

Objetivo:

- registrar arquitetura alvo
- mapear arquivos que pertencem a cada camada
- parar mudanças “pontuais” fora do plano

Checklist:

- este documento criado
- inventário atual por camada
- problemas conhecidos listados

Critério de saída:

- nenhuma próxima mudança de streaming acontece sem citar a fase afetada

### Fase 1 — Contratos e Fronteiras

Objetivo:

- garantir que cada camada tenha responsabilidades explícitas

Tarefas:

- remover decisões de ingest dos endpoints onde possível
- concentrar contratos públicos de live e VOD
- eliminar helpers ambíguos em `web/main.py`

Critério de saída:

- `web/main.py` não contém lógica de baixo nível de stream além de orquestração HTTP

### Fase 2 — Live Estável

Objetivo:

- estabilizar live antes de tocar em otimizações secundárias

Tarefas:

- live YouTube via HLS + FFmpeg `-re` como padrão
- fallback explícito para streamlink
- revisar tamanho e ritmo do buffer
- revisar métricas de atraso e timeout

Critério de aceite:

- dois clientes simultâneos em live sem travamento observado
- dashboard exibe clientes ativos
- logs sem crescimento explosivo do buffer incompatível com playback real

### Fase 3 — VOD com Sessão Persistente

Objetivo:

- garantir áudio, `Range` e continuidade por sessão

Tarefas:

- sessão por URL redirecionada
- reuso de conexão por sessão
- resolver URL adequada a playback com áudio
- tratar `HEAD`, `GET`, `206`, `416`, `403`

Critério de aceite:

- VOD com áudio
- mesmo player consegue reabrir/seek sem reiniciar sessão do zero
- requisições sequenciais usam o mesmo `session_id`

### Fase 4 — Dashboard e Observabilidade

Objetivo:

- tornar o estado operacional visível

Tarefas:

- unificar `live` e `vod` em `/api/proxy/status`
- exibir tipo do stream
- mostrar clientes/sessões ativas
- tornar erros e redirects visíveis em log

Critério de aceite:

- dashboard reflete atividade real de live e VOD

### Fase 5 — Limpeza e Qualidade

Objetivo:

- reduzir problemas do editor e remover código transitório

Tarefas:

- classificar as indicações do VS Code
- corrigir imports não usados e referências mortas
- remover compatibilidades provisórias que não sejam mais necessárias

Critério de aceite:

- base mais limpa
- warnings reais separados de ruído do editor

---

## 5. Inventário Atual por Camada

### Entrada do Stream

- `core/player_router.py`
- `core/stream_ingress.py`
- partes de `web/main.py`

### Gerenciamento do Stream

- `core/proxy_manager.py`
- `core/vod_proxy.py`

### Saída para Clientes

- `web/main.py`
- `web/routes/proxy_dashboard.py`
- `core/playlist_builder.py`

---

## 6. Status de Execução

### Fase 0

Status:

- concluída

Entrega:

- arquitetura alvo registrada neste documento
- problemas atuais listados
- arquivos principais mapeados por camada

### Fase 1

Status:

- em andamento

Concluído nesta fase:

- criada a camada `core/stream_ingress.py`
- `web/main.py` passou a consumir `resolve_proxy_ingress_plan(...)`
- fallback de live após fast-fail passou a usar `build_live_fallback_ingress_plan(...)`
- decisão de placeholder deixou de depender de regra espalhada no endpoint

Ainda falta nesta fase:

- reduzir mais dependências diretas de `web/main.py` com detalhes do fluxo legado
- consolidar melhor os contratos públicos de live e VOD para que os endpoints só orquestrem HTTP

### Próxima execução obrigatória

Depois desta fase, a próxima intervenção deve mirar a Fase 2:

- validar se live está entrando por HLS + FFmpeg `-re` como caminho principal
- medir se o buffer deixa de crescer acima da velocidade real de playback
- só então mexer novamente em VOD ou dashboard

### Fase 2

Status:

- em andamento

Concluído nesta fase:

- buffer live ampliado para absorver burst de HLS sem expulsar cliente cedo
- cliente novo passa a entrar alguns chunks atrás da cabeça do buffer
- catch-up do cliente ficou mais agressivo quando o atraso cresce
- saída live passou a enviar payloads agregados por lote, não chunk por chunk
- endpoint live agora espera um pré-buffer curto antes de liberar o cliente
- métricas de buffer live passaram a usar bytes reais, não suposição fixa por read()
- startup de live passou a exigir serialização por `video_id` para evitar dois processos concorrentes do mesmo canal
- cliente de live só deve contar como ativo depois do primeiro payload entregue, não na abertura inicial da requisição

Hipótese operacional desta fase:

- o live atual sofre mais com burst na entrada e início precoce do cliente do que com falta de dados absoluta
- por isso a prioridade é dar folga ao buffer e estabilizar o egress antes de novas mudanças arquiteturais

Próxima subfase obrigatória após a calibração base:

- evoluir thresholds fixos de live para política adaptativa, mas só depois de termos sinais mais confiáveis
- separar melhor o que é global do stream e o que é individual por cliente
- tornar dinâmicos, por cliente, pelo menos:
  - distância inicial do live edge
  - tamanho de lote de entrega
  - critério de cliente "atrasado"
  - política de catch-up e jump

Observação:

- nesta etapa atual os valores continuam fixos por pragmatismo de calibração
- isso não é o desenho final desejado

Transição de fase:

- com a estabilidade base do live atingida, as próximas mudanças devem entrar como melhorias controladas
- prioridade imediata:
  - observabilidade limpa para separar problema do proxy de descontinuidade da origem
  - manter a calibração fixa estável
  - só depois retomar as melhorias estruturais restantes

Status da política adaptativa:

- tentada e revertida
- a primeira versão, baseada só em duração da conexão e taxa média em kbps, introduziu backlog crônico em alguns clientes
- efeito observado:
  - clientes permaneciam recebendo dados, mas ficavam vários MB atrás do live edge
  - o VLC entrava em "carregando" e clientes simultâneos abriam com defasagem grande

Decisão atual:

- manter thresholds fixos enquanto a base live estiver estável
- registrar a política adaptativa como melhoria futura, não como baseline atual

Pré-requisitos para retomar política adaptativa:

- distinguir melhor throughput instantâneo de throughput médio
- detectar backlog crônico mesmo sem stall completo
- considerar sinais por cliente e por stream antes de ajustar:
  - distância inicial do live edge
  - tamanho do lote de entrega
  - limiar de jump
  - janela de tolerância antes do jump

## 6. Regras de Trabalho nesta Branch

- não misturar correção de live e VOD na mesma mudança sem motivo explícito
- não colocar lógica de sessão/buffer diretamente em rota HTTP
- não usar uma URL resolvida para FFmpeg como se fosse automaticamente adequada a proxy HTTP direto
- qualquer regressão deve ser associada a uma fase deste plano
- sempre validar comportamento com:
  - `HEAD /api/vod/...`
  - `GET /api/vod/...`
  - `Range` em `/api/vod/...`
  - dois clientes simultâneos em live
  - dashboard `/api/proxy/status`

---

## 7. Próximo Passo Imediato

Executar Fase 1:

- revisar o código atual e alinhar `web/main.py`, `core/proxy_manager.py`, `core/vod_proxy.py` e `core/playlist_builder.py` aos contratos acima
- em seguida atacar Fase 2 e Fase 3 separadamente
