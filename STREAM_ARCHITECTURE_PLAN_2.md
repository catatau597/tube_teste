# Plano de Arquitetura de Streaming v2

Projeto: `tube_teste`
Branch de trabalho: `dev_codex`
Documento: substitui a estratégia incremental do proxy atual por uma estratégia de migração orientada ao `Dispatcharr`

Objetivo:

- abandonar o proxy live atual do `tube_teste` como baseline de evolução
- reaproveitar o máximo possível da arquitetura, lógica e código do `Dispatcharr`
- preservar o restante do `tube_teste`:
  - busca/coleta
  - estado
  - API de administração
  - playlists
  - dashboard geral
  - VOD fora do escopo imediato de live, salvo interfaces necessárias

Princípio:

- não reinventar o proxy
- não seguir refinando o fanout atual
- usar o `Dispatcharr` como implementação de referência, e não apenas como inspiração distante

---

## 1. Decisão Arquitetural

### 1.1 O que está sendo abandonado

O proxy live atual do `tube_teste` deixa de ser tratado como base de evolução.

Isso inclui, como baseline técnica:

- `core/proxy_manager.py` como motor principal de fanout
- `api_proxy_stream()` atual em `web/main.py` como implementação final de live
- heurísticas locais de buffer e egress criadas nesta branch

Importante:

- abandonar como baseline não significa apagar imediatamente
- o código atual pode permanecer temporariamente como fallback/control group até a migração terminar

### 1.2 O que será preservado

Continuam pertencendo ao `tube_teste`:

- scheduler
- discovery de vídeos/eventos
- integração com YouTube API
- gerenciamento de estado
- playlists e EPG
- dashboard e rotas administrativas
- configuração do projeto
- fluxo de thumbnails, categorias e filtros

### 1.3 Nova regra

Para streaming live:

- a referência funcional passa a ser o `Dispatcharr ts_proxy`
- qualquer novo design deve justificar por que diverge dele
- sem essa justificativa, a decisão padrão é portar o comportamento do `Dispatcharr`

---

## 2. Hipótese Central

O problema principal do live no `tube_teste` não está mais na busca da origem.

O problema principal está em:

- fanout para múltiplos clientes
- desacoplamento entre ingest e egress
- política de entrega por cliente
- ciclo de vida do stream compartilhado

Evidência prática:

- a origem HLS/FFmpeg está saudável em vários logs
- o buffer também ficou estável em várias rodadas
- a degradação aparece quando o número de clientes cresce

Conclusão:

- o gargalo dominante atual é o egress/fanout
- isso bate com o que o `Dispatcharr` resolve melhor que o `tube_teste`

---

## 3. Estratégia de Migração

### 3.1 Não vamos portar o projeto inteiro

O objetivo não é “rodar o Dispatcharr dentro do tube_teste”.

O objetivo é portar a camada de proxy live:

- estruturas
- contratos internos
- política de cliente
- buffer
- generator
- manager

### 3.2 Não vamos fazer copy-paste cego

Apesar de o `Dispatcharr` ser Python, há diferenças reais:

- usa `gevent`
- usa Redis no desenho original
- possui modelos/configs/servidores próprios
- parte do ciclo de vida presume ecossistema Django dele

Então a abordagem correta é:

- portar o desenho
- portar blocos de código reutilizáveis
- adaptar pontos de integração ao `tube_teste`

### 3.3 Regra de decisão

Se houver duas opções:

1. adaptar a lógica do `Dispatcharr`
2. inventar uma nova no `tube_teste`

A opção padrão deve ser `1`.

---

## 4. Escopo Inicial

### 4.1 Dentro do escopo

- live proxy compartilhado
- buffer do stream
- generator por cliente
- controle de atraso do cliente
- fanout multi-cliente
- métricas e status do stream
- integração da rota `/api/proxy/{video_id}`

### 4.2 Fora do escopo imediato

- VOD final
- Redis real em produção
- reescrever playlists
- reescrever scheduler
- reescrever dashboard inteiro
- migrar para stack Django/gevent do `Dispatcharr`

---

## 5. Arquitetura Alvo v2

### 5.1 Camadas

#### A. Stream Ingress

Responsabilidade:

- resolver origem live reproduzível
- escolher comando/processo de ingest
- iniciar ingest

No `tube_teste`, continua adaptado via:

- `core/stream_ingress.py`

Saída esperada:

- source metadata
- command line de ingest
- tipo de source

#### B. Live Proxy Core

Nova camada principal a ser portada do `Dispatcharr`.

Responsabilidade:

- manter buffer do canal
- manter estado do canal
- conectar clientes ao buffer existente
- lidar com cliente atrasado
- entregar dados de forma eficiente

Subcomponentes alvo:

- `core/live_proxy/stream_buffer.py`
- `core/live_proxy/stream_generator.py`
- `core/live_proxy/stream_manager.py`
- `core/live_proxy/models.py` ou equivalente leve para estado/config local

#### C. HTTP Adapter

Responsabilidade:

- traduzir requisição HTTP do `tube_teste` para o novo proxy core
- preservar as rotas existentes
- expor status para dashboard

Alvo:

- `web/main.py`
- `web/routes/proxy_dashboard.py`

---

## 6. O que será portado do Dispatcharr

### 6.1 Portar quase igual

Itens candidatos a portar com alterações pequenas:

- lógica de `stream_generator`
- estratégia de leitura adaptativa de chunks
- reposicionamento de cliente atrasado
- distinção entre cliente perto do live edge e cliente muito atrás
- lógica de keepalive quando não há chunk novo
- separação explícita entre buffer e generator

### 6.2 Portar com adaptação

Itens que exigem adaptação ao `tube_teste`:

- uso de Redis
- integração com servidor/proxy server central
- dependências `gevent`
- config helper
- logging framework
- metadata store de canal

### 6.3 Não portar

Itens que não são necessários neste momento:

- stack Django do `Dispatcharr`
- models do app de canais
- integrações administrativas dele
- pipeline completo de VOD do `Dispatcharr`

---

## 7. Escolha Técnica Importante

### 7.1 Redis agora ou depois

Decisão inicial:

- primeiro portar a lógica do `Dispatcharr` para memória local
- manter Redis como opcional/futuro

Razão:

- o objetivo imediato é validar o desenho de fanout
- introduzir Redis agora aumenta a superfície de mudança
- a comparação que interessa primeiro é:
  - proxy atual do `tube_teste`
  - proxy estilo `Dispatcharr`

### 7.2 Gevent agora ou depois

Decisão inicial:

- não migrar o projeto inteiro para `gevent`
- adaptar a lógica do `Dispatcharr` ao runtime atual do `tube_teste`

Razão:

- trocar o runtime agora mistura dois problemas:
  - arquitetura de proxy
  - modelo de concorrência

Se o desenho do `Dispatcharr` provar valor mesmo adaptado ao modelo atual, aí sim avaliamos runtime mais adiante.

---

## 8. Fases

### Fase 0 — Congelamento do Proxy Atual

Objetivo:

- parar de evoluir `core/proxy_manager.py` como solução final

Tarefas:

- não adicionar novas heurísticas complexas ao proxy atual
- manter só correções de contenção se algo bloquear o projeto
- usar o proxy atual apenas como referência/fallback temporário

Critério de saída:

- nenhuma nova funcionalidade estratégica entra no proxy antigo

### Fase 1 — Mapeamento do Dispatcharr

Objetivo:

- mapear exatamente o que será portado

Tarefas:

- listar módulos do `ts_proxy` realmente necessários
- classificar cada dependência em:
  - portar
  - adaptar
  - descartar
- documentar contratos mínimos

Entregáveis:

- tabela de mapeamento `Dispatcharr -> tube_teste`
- lista de dependências críticas

Critério de saída:

- escopo do porte fechado

### Fase 2 — Novo Core de Live Proxy

Objetivo:

- criar o novo núcleo live proxy separado do código atual

Tarefas:

- criar `core/live_proxy/`
- portar `stream_buffer`
- portar `stream_generator`
- portar `stream_manager`
- definir interfaces Python simples para integração com `web/main.py`

Critério de saída:

- novo core inicializável sem depender do proxy antigo

### Fase 3 — Adaptador HTTP

Objetivo:

- encaixar o novo core nas rotas existentes

Tarefas:

- adaptar `/api/proxy/{video_id}`
- adaptar `/api/proxy/status`
- integrar com dashboard sem mudar a UX básica

Critério de saída:

- live passando pela nova engine sem quebrar as rotas públicas

### Fase 4 — Teste de Fanout

Objetivo:

- validar o problema real do projeto: múltiplos clientes simultâneos

Tarefas:

- testar 2 clientes
- testar 4 clientes
- testar 8+ clientes
- comparar contra proxy antigo

Critério de aceite:

- desempenho melhor que o proxy atual em todos os cenários
- sem regressão em start/stop/status

### Fase 5 — Corte do Proxy Antigo

Objetivo:

- retirar o proxy antigo da linha principal

Tarefas:

- remover dependência funcional do `core/proxy_manager.py` antigo
- manter fallback temporário apenas se houver justificativa explícita
- simplificar `web/main.py`

Critério de saída:

- novo proxy torna-se o baseline oficial do `tube_teste`

---

## 9. Contratos Alvo

### 9.1 Ingress -> Live Proxy Core

Contrato desejado:

`ensure_stream(video_id, ingress_plan, metadata) -> LiveProxyHandle`

Responsabilidades:

- iniciar processo se não existir
- devolver handle compartilhado do canal

### 9.2 HTTP -> Generator

Contrato desejado:

`attach_client(video_id, client_id, ip, user_agent) -> async iterable[bytes]`

Responsabilidades:

- conectar cliente ao stream existente
- encapsular política de buffer/lag/keepalive

### 9.3 Dashboard

Contrato desejado:

`snapshot() -> list[dict]`

Campos mínimos:

- `video_id`
- `clients`
- `buffer_bytes`
- `process_alive`
- `process_pid`
- `uptime`
- `ingress_type`

---

## 10. Regras de Implementação

1. Não reimplementar lógica do zero se o `Dispatcharr` já resolver o mesmo problema.
2. Qualquer divergência importante deve ser documentada neste arquivo.
3. O proxy atual não pode voltar a receber heurísticas grandes.
4. Todo código novo de proxy live deve nascer em `core/live_proxy/`.
5. `web/main.py` deve virar adaptador fino, não motor de streaming.

---

## 11. Critérios de Sucesso

O plano só será considerado bem-sucedido se:

- 2 clientes simultâneos ficarem estáveis
- 4 clientes simultâneos forem assistíveis sem “travadeira geral”
- o live edge não ficar degradando progressivamente
- dashboard refletir clientes reais
- a solução final ficar mais próxima do `Dispatcharr` do que da implementação atual do `tube_teste`

---

## 12. Próximo Passo Obrigatório

Antes de qualquer novo ajuste no proxy atual, executar:

1. mapear os arquivos do `Dispatcharr ts_proxy`
2. definir o conjunto mínimo a portar
3. criar a estrutura `core/live_proxy/`

Qualquer nova tentativa de “calibrar” o proxy antigo fora disso deve ser tratada como exceção, não como caminho principal.
