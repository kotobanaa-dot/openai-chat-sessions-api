# Chat Sessions API

REST service for chat sessions backed by OpenAI. It stores the conversation,
sends the session's history as context on every turn, records the token usage
reported by OpenAI and computes the cost of each exchange plus the accumulated
cost of the session.

Stack: FastAPI + PostgreSQL + SQLAlchemy 2.0 (async) + Alembic, with the OpenAI
SDK behind an adapter.

---

## Live demo

A running instance is available at **https://pbc.midmoon.duckdns.org/** - a chat
UI at `/` and Swagger at `/docs`, no setup required.

The demo is behind a shared key: send `X-API-Key: <key provided separately>`
with every `/api/v1` call. The UI accepts the same key in its field, or as
`/#key=<key>` in the address, so a shared link opens the chat in one click.

The fragment form is deliberate: browsers never send the part after `#` to the
server, so the key stays out of access logs. A `?key=` query parameter also
works but is visible to the server, so prefer the fragment. Either way the value
is stored locally and stripped from the address bar on load. This trades some
secrecy for convenience and suits a throwaway demo key with a spending cap - not
a real credential.

Replies on the demo are capped at 400 tokens and sessions at 30 messages, which
keeps its spend bounded; a local run has no such caps.

---

## Quick start

Requires Docker. Nothing else - Python and PostgreSQL run inside the containers.

```bash
cp .env.example .env
# put your key into OPENAI_API_KEY in .env
docker compose up --build
```

That's the whole setup. The API container applies the migrations on start, so
the schema is created for you.

- API: http://localhost:8000
- Interactive docs (Swagger): http://localhost:8000/docs
- Demo chat UI: http://localhost:8000/

To stop and wipe the database:

```bash
docker compose down -v
```

### Running without Docker

The app reads its database URL from `DATABASE_URL`, so a local PostgreSQL works
too:

```bash
pip install -r requirements.txt
export DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/chat
alembic upgrade head
uvicorn app.main:app --reload
```

### Tests

```bash
docker compose exec api pytest -q
```

33 tests, none of which call OpenAI - the provider is faked, so the suite runs
offline and for free.

---

## Configuration

All settings live in `.env` (see `.env.example`).

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | - | Required. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Default model for new sessions. |
| `OPENAI_MAX_OUTPUT_TOKENS` | `512` | Hard cap per reply - a cost guard. |
| `OPENAI_MAX_RETRIES` | `3` | Retries on 429/5xx, handled by the SDK. |
| `DATABASE_URL` | compose value | Async SQLAlchemy URL. |
| `CONTEXT_MAX_MESSAGES` | `20` | How many past messages go to the model. |
| `MAX_MESSAGE_CHARS` | `8000` | Input length limit. |
| `MAX_MESSAGES_PER_SESSION` | `100` | Guards a public demo against runaway cost. |
| `API_KEY` | empty | If set, all `/api/v1` calls need `X-API-Key`. Empty = auth off. |

---

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/sessions` | Create a session. |
| `GET` | `/api/v1/sessions` | List sessions (paginated). |
| `GET` | `/api/v1/sessions/{id}` | Session, active history and its accumulated cost. |
| `POST` | `/api/v1/sessions/{id}/reset` | Start a fresh context, keeping the same session id. |
| `POST` | `/api/v1/sessions/{id}/messages` | Send a message; returns the reply, usage and cost. Accepts an optional `model`. |
| `GET` | `/api/v1/sessions/{id}/messages` | History of the active context only. |
| `GET` | `/api/v1/models` | Models this service can price, with the tariffs in use. |
| `GET` | `/health` | Liveness. |

### Example requests

Create a session:

```bash
curl -X POST http://localhost:8000/api/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{"title": "Demo", "system_prompt": "Answer in one short sentence."}'
```

```json
{
  "id": "a7b04d97-9ec5-4915-9ae9-a4da0312f17a",
  "model": "gpt-4o-mini",
  "total_tokens": 0,
  "total_cost": "0.00000000",
  "status": "active"
}
```

Send a message (`$SID` is the id from the previous call):

```bash
curl -X POST http://localhost:8000/api/v1/sessions/$SID/messages \
  -H "Content-Type: application/json" \
  -d '{"content": "My name is Oleh and my favourite number is 27."}'
```

```json
{
  "assistant_message": { "role": "assistant", "content": "Nice to meet you, Oleh!" },
  "usage": { "prompt_tokens": 29, "completion_tokens": 7, "cached_tokens": 0, "total_tokens": 36 },
  "cost": "0.00000855",
  "total_accumulated_cost": "0.00000855",
  "currency": "USD",
  "context_messages": 2
}
```

Ask a follow-up that only the history can answer:

```bash
curl -X POST http://localhost:8000/api/v1/sessions/$SID/messages \
  -H "Content-Type: application/json" \
  -d '{"content": "What is my name and my favourite number?"}'
```

```json
{
  "assistant_message": {
    "content": "Your name is Oleh and your favorite number is 27."
  },
  "cost": "0.00001515",
  "total_accumulated_cost": "0.00002370",
  "context_messages": 4
}
```

`context_messages` is deliberate: it shows how much history was actually sent,
which is otherwise invisible from the outside.

Get the session with its history and accumulated cost:

```bash
curl http://localhost:8000/api/v1/sessions/$SID
```

Answer a single message with a different model - the session default stays as it
was, and the cost is charged at the tariff of the model that actually answered:

```bash
curl -X POST http://localhost:8000/api/v1/sessions/$SID/messages \
  -H "Content-Type: application/json" \
  -d '{"content": "Same question, cheaper model.", "model": "gpt-5-nano"}'
```

Reset the conversation and keep working in the same session:

```bash
curl -X POST http://localhost:8000/api/v1/sessions/$SID/reset
```

```json
{
  "session_id": "a7b04d97-9ec5-4915-9ae9-a4da0312f17a",
  "generation": 2,
  "messages_archived": 4,
  "total_cost": "0.00000000",
  "lifetime_cost": "0.00002370"
}
```

After the reset `GET /sessions/{id}` returns an empty history and a zero
`total_cost`, while `lifetime_cost` still shows what the session has spent.

A ready-to-run collection is in [`requests.http`](requests.http) (VS Code REST
Client / JetBrains HTTP client) and [`postman_collection.json`](postman_collection.json).

---

## Pricing

Model tariffs are configuration, not code: they live in
[`pricing.yaml`](pricing.yaml) and are the only place a price appears. Adding a
model is a config edit; no route or service contains a cost formula.

Rates below are USD per 1M tokens, taken from
<https://platform.openai.com/docs/pricing> on **2026-08-19**.

| Model | Input | Cached input | Output |
|---|---|---|---|
| **gpt-4o-mini** (default) | $0.15 | $0.075 | $0.60 |
| gpt-4.1-mini | $0.40 | $0.10 | $1.60 |
| gpt-4.1-nano | $0.10 | $0.025 | $0.40 |
| gpt-5-mini | $0.25 | $0.025 | $2.00 |
| gpt-5-nano | $0.05 | $0.005 | $0.40 |

Cost formula, applied in `PricingService.calculate()`:

```
input_cost  = (prompt_tokens - cached_tokens) * input_rate / 1e6
            + cached_tokens * cached_input_rate / 1e6
output_cost = completion_tokens * output_rate / 1e6
```

Two details worth calling out:

- **Cached input is billed separately.** OpenAI reports
  `usage.prompt_tokens_details.cached_tokens` as a subset of `prompt_tokens` and
  charges it at a lower rate (half, for `gpt-4o-mini`). Charging the whole prompt
  at the full input rate would overstate the cost, so the cached part is
  subtracted before charging.
- **Money is `Decimal`, never `float`,** and is stored in `NUMERIC(18, 8)`.
  A session accumulates thousands of tiny amounts; binary floating point drifts
  away from the provider's invoice once those are summed. Amounts are quantised
  to 8 decimals, so the value returned by the API is exactly the value stored.

Each row in `message_usage` also keeps a **snapshot of the rates** used at the
time of the call, so changing `pricing.yaml` later never rewrites historical cost.

### Choosing a model per message

A session has a default model, and any single message can override it:

```json
{ "content": "Same question, cheaper model.", "model": "gpt-5-nano" }
```

The cost is charged at the tariff of the model that actually answered, and the
response echoes it back as `model`. Overriding does not change the session
default. A model with no tariff is rejected with 422 **before** the provider is
called, so an unsupported name costs nothing.

One thing worth knowing when comparing models: a cheaper per-token tariff does
not guarantee a cheaper answer. Reasoning models spend `reasoning_tokens`, which
OpenAI bills as output. In a like-for-like test, `gpt-5-nano` answered "Say OK."
with 203 completion tokens - 192 of them reasoning - and ended up costing about
twenty times more than `gpt-4o-mini`, which used 2. Those tokens are reported in
`unpriced_usage` so the difference is visible rather than mysterious.

### Resetting a session

`POST /sessions/{id}/reset` clears the conversation while keeping the session id.
After it, the history is empty, `total_cost` is zero, and the model gets a clean
context on the next message.

**Previous messages are archived, not deleted.** Each session carries a
`generation` counter; a reset increments it, and messages keep the generation
they were written in. Everything outside the current generation is invisible to
the API and to the model, but still present in the database.

The reason is accounting: the money was really spent, and deleting the record
of it would defeat the purpose of a service built to track cost. So a session
keeps two sets of counters - `total_*` for the active context, which a reset
zeroes, and `lifetime_*` for everything ever spent, which it never touches.
Physical deletion would look identical from outside while destroying the
spending history for good.

Resetting an already-empty context does nothing, so repeated calls do not pile
up empty generations. Reading archived generations has no endpoint: the change
request did not ask for one, and the data is there if it is ever needed.

### Usage categories that are not priced separately

- `reasoning_tokens` (reasoning models only) - OpenAI already includes these in
  `completion_tokens` and bills them at the output rate, so they are charged
  correctly. They are surfaced in the response under `unpriced_usage` for
  transparency but not billed twice.
- `cache_creation` / cache-write rates that some newer models list separately are
  not modelled: the default model has no such charge.
- Non-text modalities (audio, image, web-search tool calls) are out of scope.

---

## How it works

```
POST /sessions/{id}/messages
        |
        v
   ChatService                     orchestration
        |-- ChatRepository         locks the session row (SELECT ... FOR UPDATE)
        |-- persists user message  BEFORE calling the provider
        |-- ContextBuilder         system prompt + trimmed history + new message
        |-- LLMProvider            OpenAI adapter (retries, error mapping)
        |-- PricingService         usage -> cost
        `-- one transaction        assistant message + usage + session totals
```

Design decisions behind that:

- **The user's message is stored before the provider call.** If OpenAI fails,
  the input is still part of the conversation instead of being lost.
- **The session row is locked for the duration of an exchange.** Together with
  `UNIQUE(session_id, seq)` this stops two concurrent requests from claiming the
  same position in the dialogue.
- **Message, usage and session totals are written in one transaction.** The
  database never holds a state where a reply exists but its cost was not counted.
- **The provider sits behind a protocol** (`app/providers/base.py`), so the SDK
  can be swapped or faked. The test suite uses that seam to run offline.

### Layout

```
app/
  api/routes/       HTTP only - parse, delegate, serialise
  schemas/          Pydantic request/response models
  services/
    chat.py             orchestrates one exchange
    context_builder.py  builds and trims the prompt context
    pricing.py          the only place tokens are turned into money
  providers/
    base.py             provider protocol
    openai_provider.py  SDK adapter, retries, error mapping
  repositories/     database access
  models/           SQLAlchemy ORM
  core/             config and domain errors
migrations/         Alembic
static/index.html   demo chat UI
tests/              18 tests, no network
```

### Data model

- `sessions` - model, optional system prompt, `generation`, running totals
  (`total_prompt_tokens`, `total_completion_tokens`, `total_tokens`, `total_cost`).
- `messages` - `session_id`, `seq`, `role`, `content`, plus provider trace
  (`finish_reason`, `provider_response_id`). Unique on `(session_id, seq)`.
- `message_usage` - one row per assistant reply: token counts, the rate snapshot
  and the computed costs.

---

## Error handling

Every failure returns the same shape:

```json
{ "error": { "code": "session_not_found", "message": "...", "details": {} } }
```

| Case | Status | Code |
|---|---|---|
| Unknown session | 404 | `session_not_found` |
| Empty / oversized / malformed input | 422 | `validation_error` |
| Model not in `pricing.yaml` | 422 | `validation_error` |
| Session message limit reached | 409 | `session_message_limit` |
| OpenAI rate-limited, timed out, 5xx | 503 | `upstream_unavailable` |
| OpenAI rejected key, quota or model | 502 | `upstream_rejected` |
| Missing/invalid `X-API-Key` (when enabled) | 401 | `unauthorized` |
| Database failure | 500 | `database_error` |

Transient upstream failures are retried by the SDK with exponential backoff;
authentication failures are not retried, because they will not fix themselves.
Database driver messages are logged but never returned - they can carry the DSN.

---

## Known limitations

Deliberate scope decisions, not oversights:

1. **Pricing lives in YAML, not in a database table.** An alternative design is a
   versioned `model_pricing` table with `effective_from` / `effective_to`. The
   rate snapshot in `message_usage` already preserves historical accuracy, so the
   table was skipped. The `PricingService` interface would not change.
2. **Context is trimmed by message count, not by token budget.** A token-accurate
   trim needs `tiktoken` and per-model context limits. The strategy is isolated
   in `ContextBuilder`, so swapping it touches one class.
3. **No streaming.** Replies are returned in one response. Streaming would also
   require `stream_options={"include_usage": True}` to keep usage reporting.
4. **No authentication or multi-tenancy.** `X-API-Key` is a single shared secret
   for guarding a public deployment, not per-user auth: anyone holding the key
   can read and continue any session. Sessions carry no owner, so adding
   per-user access later means a schema change plus an authorisation check on
   every session route. Note also that an empty `API_KEY` disables the guard
   entirely - convenient locally, which is why the service logs a warning at
   startup when that happens outside a local environment.
5. **A failed exchange leaves the user's message unanswered rather than marked.**
   The message is committed before the provider call, so it survives a failure -
   but there is no `failed` placeholder row for the reply that never arrived, and
   no retry endpoint. The unanswered message simply becomes part of the context
   on the next attempt.
6. **No rate limiting per IP** and no idempotency key on message sends.
7. **Archived generations have no endpoint.** A reset keeps previous messages in
   the database under their generation number, but nothing exposes them: the
   change request did not ask for it. Adding it later is one query parameter.
8. **Cost is USD only,** with no currency conversion.
