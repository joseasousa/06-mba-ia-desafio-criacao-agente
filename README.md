# Assistente do Residencial Aurora

API conversacional em Python com Google ADK. O modelo conduz a conversa e transfere o atendimento entre especialistas, enquanto identidade, confirmação, idempotência, persistência e exclusividade são impostas pelo código e pelo SQLite.

## Arquitetura

O projeto usa Python 3.12+, FastAPI, `google-adk==2.9.1` e dois arquivos SQLite:

- `var/aurora.db`: apartamentos, áreas, reservas, visitantes e confirmações.
- `var/sessions.db`: sessões, state e eventos persistidos pelo `DatabaseSessionService` do ADK.

O `App` e a topologia estão em `src/aurora/agents.py`:

- `assistente_aurora`: agente principal. Classifica o pedido e transfere o controle; não recebe o regulamento nem possui tools de domínio.
- `especialista_reservas`: usa apenas tools de consulta, disponibilidade, reserva e cancelamento.
- `especialista_visitantes`: consulta e autoriza visitantes.
- `especialista_regulamento`: consulta um índice lexical local e recebe somente os artigos relevantes.

Há três especialistas porque os domínios têm exposições diferentes: reservas exigem concorrência e cobrança, visitantes liberam acesso, e o regulamento precisa de recuperação seletiva para não contaminar toda a janela de contexto.

`src/aurora/runtime.py` adapta o `Runner` à API. Ele cria sessões persistentes, coleta eventos, registra interrupções `adk_request_confirmation` e retoma a mesma `invocation_id` com uma `FunctionResponse`. Uma sessão aceita no máximo uma confirmação pendente; uma nova mensagem antes da resposta recebe `409`.

`src/aurora/domain.py` contém o armazenamento e as transações do condomínio. Os arquivos em `dados/` são somente leitura e servem exclusivamente como estado inicial.

## Garantias

### 1. Cobrança ou acesso somente após confirmação

Em `src/aurora/tools.py`, `reservar_area` é um `FunctionTool` cuja função `exige_confirmacao` consulta a taxa no banco; `autorizar_visitante` usa `require_confirmation=True`. O modelo não recebe um argumento capaz de dispensar essa confirmação.

`AuroraRuntime._capture_confirmations` registra o ID, a sessão, a invocação, o agente solicitante e a chamada original. `AuroraRuntime.answer_confirmation` aceita somente uma pendência daquela sessão, responde ao `adk_request_confirmation` e reutiliza a `invocation_id`. Depois de respondido, o registro deixa de estar pendente e o mesmo ID passa a retornar `409`.

As operações usam o ID original da chamada como chave idempotente. Assim, mesmo uma retomada repetida não cria efeito duplicado.

### 2. Cada sessão pertence a um apartamento

`AuroraRuntime.create_session` grava `apartamento` no state uma única vez. A função `_apartment` em `src/aurora/tools.py` lê exclusivamente `tool_context.state`; nenhuma tool conversacional declara `apartamento` como parâmetro.

Consultas e cancelamentos filtram pelo apartamento dentro de `DomainRepository`. `is_available` devolve apenas livre/ocupada e nunca retorna o apartamento, morador ou código da reserva conflitante.

### 3. Dados e conversas sobrevivem ao reinício

`AuroraRuntime` usa `DatabaseSessionService` com `sqlite+aiosqlite`, mantendo eventos e state em `var/sessions.db`. O domínio usa `var/aurora.db`, e cada operação abre uma transação durável.

O comando normal de restauração repõe reservas e visitantes, mas preserva sessões. A opção `--all` também remove o banco do ADK.

### 4. O regulamento é consultado, não carregado

`RegulationIndex` em `src/aurora/regulation.py` divide `dados/regulamento.md` por capítulo e artigo. `consultar_regulamento` retorna no máximo três trechos ranqueados e limitados; apenas esses trechos entram nos eventos do especialista.

O agente principal não contém nem recebe o texto do regulamento. Por exemplo, a busca por funcionamento da piscina aos domingos recupera o Art. 22, sem carregar capítulos de outros assuntos.

### 5. Dois moradores, uma reserva

O schema de `src/aurora/domain.py` cria o índice parcial:

```sql
CREATE UNIQUE INDEX ux_reserva_ativa_area_data
ON reservas(area, data) WHERE ativa = 1;
```

`DomainRepository.create_reservation` usa `BEGIN IMMEDIATE` e tenta o `INSERT` sob essa restrição. A decisão ocorre no instante da gravação; uma violação vira o resultado normal `indisponivel`, de modo que aprovações concorrentes respondem HTTP `200` e somente uma reserva permanece ativa.

Cancelamentos são lógicos. Códigos novos usam UUID e registros cancelados continuam no banco, portanto um código não é reutilizado.

## Como rodar

Pré-requisitos:

- Python 3.12 ou 3.13;
- [`uv`](https://docs.astral.sh/uv/);
- chave do Google AI Studio com acesso ao modelo escolhido.

Prepare o ambiente:

```bash
cp .env.example .env
```

Preencha `GOOGLE_API_KEY`. `GEMINI_MODEL` usa `gemini-3.8-flash` por padrão e pode ser alterado para um modelo disponível no projeto. Os caminhos dos dois bancos também podem ser personalizados.

Instale exatamente as dependências travadas:

```bash
uv sync
```

Restaure reservas e visitantes sem apagar sessões:

```bash
uv run aurora-reset
```

Para uma limpeza total, com a API parada:

```bash
uv run aurora-reset --all
```

Suba a API em `http://localhost:8000`:

```bash
uv run aurora-api
```

Alternativamente:

```bash
uv run uvicorn aurora.api:app --host 0.0.0.0 --port 8000
```

Execute a suíte determinística:

```bash
uv run pytest -q
```

Ela cobre dados iniciais, contrato HTTP, isolamento das tools, confirmação nativa do ADK com sessão SQLite, retomada, persistência após recriação do serviço, idempotência e a disputa simultânea de reservas. Um smoke conversacional real requer `GOOGLE_API_KEY` e deve seguir os 15 passos do enunciado, pois depende da disponibilidade e da cota ativa do Gemini.

Além dos testes unitários, a suíte determinística transfere uma solicitação do agente principal ao especialista de reservas, interrompe a execução na confirmação, recria completamente o `AuroraRuntime` e aprova usando a sessão SQLite persistida. Ela também dispara aprovações concorrentes pelas rotas HTTP e inspeciona os eventos gravados para comprovar o isolamento entre apartamentos.

O smoke opcional em `tests/test_live_evaluator_smoke.py` percorre o fluxo do avaliador com o Gemini real, incluindo negação, replay, reinício e disputa final. Para evitar consumo acidental de cota, ele só roda quando a chave está explicitamente exportada no ambiente do processo; uma chave presente apenas no `.env` não o habilita:

```bash
GOOGLE_API_KEY="sua-chave" uv run pytest -q tests/test_live_evaluator_smoke.py
```

No PowerShell:

```powershell
$env:GOOGLE_API_KEY = "sua-chave"
uv run pytest -q tests/test_live_evaluator_smoke.py
Remove-Item Env:GOOGLE_API_KEY
```

### Contrato HTTP

- `POST /sessoes`
- `POST /sessoes/{session_id}/mensagens`
- `POST /sessoes/{session_id}/confirmacoes`
- `GET /sessoes/{session_id}/eventos`
- `GET /apartamentos/{apartamento}/reservas`
- `GET /apartamentos/{apartamento}/visitantes`

A documentação OpenAPI fica disponível em `http://localhost:8000/docs`.
