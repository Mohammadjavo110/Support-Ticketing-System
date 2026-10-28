# Support Ticket System

A Persian (RTL) customer-support ticket system with an AI classification,
prioritisation and reply-suggestion engine.

Customers open tickets, a background worker triages each ticket with a two-stage
engine (typed decisions first, drafted reply second), and an agent works through
a sorted inbox and accepts, edits or rejects the suggested answer.

---

## 1. Prerequisites

- Docker Engine 24+ with Docker Compose v2 (`docker compose version`)
- Nothing else: Python, Node and PostgreSQL are all installed **inside**
  containers. A clean machine only needs Docker.

Optional, for running the test suite outside Docker:

- Python 3.11+ (3.12 recommended, that is what the images use)

---

## 2. Quick start

```bash
cp .env.example .env          # then edit the values (see section 3)
docker compose up --build -d
```

Once the healthchecks are green:

- Web UI (Persian, RTL): <http://localhost:5173>
- API: <http://localhost:8000> — health at <http://localhost:8000/healthz>

If port 8000 is already taken on your machine, set `API_PORT` in `.env`
(default `8000`) and the API is published on that port instead. The UI does
not care: it calls same-origin `/api` and the Vite dev server proxies to
`http://api:8000` over the compose network.

Demo users are seeded on the first start:

| role | email | password |
| --- | --- | --- |
| agent | `agent@example.com` | `agent123` |
| customer | `customer@example.com` | `customer123` |

The UI labels these roles as Persian strings (`کارشناس` / `مشتری`); the API and
the database always use `agent` / `customer`.

`.env` is git-ignored on purpose. Only `.env.example` is committed, and it
contains no real secret.

---

## 3. Environment variables

All configuration comes from the environment (`.env` is passed to every
service). See `.env.example` for the full list.

**Infrastructure**

- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` — database bootstrap
- `SECRET_KEY` — session cookie signing; change it outside local use

**Engine (stage 1 — typed decisions)**

- `ENGINE_PROVIDER` — `laya` (HTTP to `laya-serve`), `rules` (in-process
  keyword rules) or `fake` (tests)
- `LAYA_API_URL` — where the **worker** POSTs `{text, questions, model}`;
  inside compose this is the internal `http://laya-serve:8000/predict`
- `LAYA_UPSTREAM_URL`, `LAYA_UPSTREAM_KEY` — where **`laya-serve`** sends the
  typed questions in hosted mode (an external model API). Hosted activates
  only when the URL starts with `https://` **and** the key is non-empty, so
  an internal `http` URL can never create a request loop. A URL containing
  `/chat/completions` is called as an OpenAI-compatible
  `{model, messages}` endpoint; any other URL keeps the legacy
  `{text, questions}` contract.
- `LAYA_API_KEY` — **optional** bearer token the worker sends to `laya-serve`
  itself; leave it empty behind the compose network
- `LAYA_MODEL`, `ENGINE_MIN_CONFIDENCE` (0.55), `ENGINE_TIMEOUT_SECONDS` (8)

**Drafting (stage 2 — generated text)**

- `LLM_API_URL`, `LLM_API_KEY`, `LLM_MODEL`, `LLM_TIMEOUT_SECONDS` — any
  OpenAI-compatible chat endpoint (e.g. Google Gemini's
  `…/v1beta/openai/chat/completions`)
- When `LLM_API_URL` or `LLM_API_KEY` is empty the draft is skipped:
  `draft_status='failed'`, while the stage-1 decision is still stored. The
  result is an honest "no suggestion", never fabricated text.

---

## 4. Project structure

```
.
├── docker-compose.yml       six services, healthchecks, service_healthy deps
├── .env.example             every variable, no secrets
├── requirements-dev.txt     app deps + pytest for the local test run
├── pytest.ini
├── api/                     FastAPI: auth, tickets, inbox, decisions
│   ├── app/main.py          all endpoints
│   ├── app/schema.sql       the canonical PostgreSQL schema (7 tables)
│   └── app/security.py      bcrypt hashing + HttpOnly session cookie
├── worker/                  background triage worker (arq host)
│   ├── app/engine.py        Protocol + Rules/Laya/Fake engines, abstention
│   ├── app/drafter.py       stage 2, prompt-injection and policy guards
│   ├── app/pipeline.py      the job; writes ai_suggestions + ai_runs only
│   └── app/worker.py        BRPOP consumer with manual retry
├── laya-serve/              model adapter container (hosted or local mode)
├── web/                     React + Vite (dev server), Persian RTL UI
├── tests/                   the required tests, no external services
└── docs/architecture.md     architecture decision record + diagrams
```

**Ownership (one writer per table)**

- `api` owns `users`, `sessions`, `tickets`, `messages`, plus the initial
  `ai_runs` row and the `suggestion_events` row written by a decision.
- `worker` owns `ai_suggestions` and the later `ai_runs` updates.
- Nothing else writes `tickets.status` — only the decision endpoint does.

---

## 5. One ticket, end to end

1. The customer submits `POST /api/tickets`. In a **single transaction** the API
   commits the ticket (`status='open'`) and an `ai_runs` row
   (`status='queued'`), and only afterwards pushes
   `{"ticket_id": ...}` onto the Redis list `triage:queue`. If Redis is down,
   the ticket still exists; the queued row is the source of truth.
2. The worker pops the job (`BRPOP`), marks the run `running` and reads the
   whole thread — subject, original body and every message so far.
3. **Stage 1 — decisions.** The engine answers three typed questions:
   `category` (choice), `priority` (score 1–3) and `needs_review` (noul). The
   worker calls `laya-serve` over HTTP (`/predict`), which is either a hosted
   model behind an API key or the deterministic local engine.
4. **Abstention.** If a confidence is below `ENGINE_MIN_CONFIDENCE`, the worker
   stores `category='unknown'` and `needs_manual_triage=true` instead of
   guessing, and keeps the original confidence values.
5. The decision is upserted into `ai_suggestions` — exactly one row per ticket,
   never a second insert.
6. **Stage 2 — the draft.** The drafter asks the LLM for a reply to the
   customer's newest message, drafted from the whole thread. The conversation
   text is declared untrusted data in the system prompt (prompt-injection
   defence), and a policy gate refuses drafts containing URLs or 16-digit
   numbers, which turns them into `draft_status='failed'`.
7. The run finishes as `succeeded` (draft ready), `succeeded_no_draft` (decision
   only) or `failed`, with `latency_ms` and `last_error` recorded.
8. The agent opens `GET /api/inbox`, sorted by manual-triage flag, then
   priority, then age, and sees the category, confidence and draft.
9. `POST /api/suggestions/{id}/decision`:
   - `accepted` → the draft becomes an outbound message, the ticket moves to
     `answered`, an `suggestion_events` row is written. Accepting a failed or
     missing draft returns **409** — there is nothing to accept.
   - `edited` → requires `text`, which becomes the outbound message, same
     status change and an event with `edited=true`.
   - `rejected` → only the event; no message, status unchanged.
10. The customer sees the answer in the thread. Closed tickets are never
    reopened; a customer opens a new ticket instead.
11. **A new customer message invalidates the old suggestion.** `POST
    /api/tickets/{id}/messages` commits the message together with a fresh
    `queued` `ai_runs` row (and reopens an `answered` ticket to `in_progress`),
    then pushes the ticket onto `triage:queue` again. The worker drops the
    previous ready draft (`draft_status='pending'`) and rebuilds decision and
    draft from the whole thread, so the agent only ever sees a suggestion that
    matches the customer's latest message.

### Ticket statuses

The schema allows four statuses — `open`, `in_progress`, `answered`, `closed` —
and transitions follow one explicit state machine (`STATUS_TRANSITIONS` in
`api/app/main.py`); anything else is a **409**, so an inconsistent status can
never be written:

- `open → in_progress` — the agent starts reviewing
  (`PATCH /api/tickets/{id}/status`, agent only).
- `open|in_progress → answered` — **only** by accepting or editing a
  suggestion; the decision endpoint writes the status and the outbound message
  together, so `answered` always means "a reply went out".
- `answered → in_progress` — the ticket reopens when the customer sends a new
  message (written by `add_message` together with the message), or manually via
  `PATCH /api/tickets/{id}/status`; the previous suggestion stops being offered
  either way.
- `in_progress|answered → closed` — the agent closes the ticket
  (`PATCH /api/tickets/{id}/status`).
- `closed` is terminal: no transition leaves it, accepting a suggestion on a
  closed ticket is a 409, and a customer reply to a closed ticket is refused
  (they open a new ticket instead).

The `PATCH` endpoint deliberately has no `answered` target, so the "answered ⇒
a message exists" invariant has exactly one writer.

---

## 6. Engine architecture

Two stages with a hard boundary between them:

- **The engine never generates text.** It returns typed, validated decisions
  only.
- **The LLM never decides.** It drafts text from decisions that were already
  made; if it is unavailable, the decisions still stand.

`laya-serve` runs in one of two modes, decided at startup:

- **hosted** — `LAYA_UPSTREAM_URL` starts with `https://` **and**
  `LAYA_UPSTREAM_KEY` is non-empty. The container is a thin adapter: it
  forwards the typed questions with `Authorization: Bearer <key>` and
  normalises the upstream response. Two dialects are understood — an
  OpenAI-compatible `/chat/completions` gateway (questions travel as a
  strict-JSON instruction) and the legacy `{text, questions}` contract. The
  `https` requirement makes a self-referencing loop impossible.
- **local** — everything else. A deterministic Persian/English keyword engine
  answers the same three questions. This is a baseline, not a transformer, and
  the container logs which mode it is in.

Upstream failures return HTTP 503 with a machine-readable code
(`upstream_timeout`, `upstream_unreachable`, …) so the worker can record
`last_error` and retry, while `/healthz` stays local so the container
healthcheck keeps passing when the model is down.

No ticket text, draft text or API key is ever written to a log — only ids,
statuses, durations, error classes and a short upstream error reason
(capped, whitespace-collapsed).

---

## 7. Tests

The three required tests run with **no external services**: no PostgreSQL, no
Redis, no engine container, no LLM. `tests/conftest.py` points the API at a
throwaway SQLite database, replaces the queue client with an in-memory stub and
configures an engine URL nothing listens on.

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt   # .venv/bin/pip on Linux/macOS
.venv\Scripts\python -m pytest
```

`requirements-dev.txt` pulls in `api/requirements.txt` plus `pytest` and
`aiosqlite`. Nothing from `docker-compose.yml` is required to run them.

- `tests/test_two_customers_are_isolated.py` — one customer can neither list,
  read, write nor decide on another customer's ticket; the agent can see both.
- `tests/test_ticket_created_when_engine_down.py` — with the engine provably
  unreachable, the ticket is created, queued exactly once and no suggestion is
  invented.
- `tests/test_suggestion_accept_reject.py` — accept creates the outbound
  message, reject only records the decision, a failed draft cannot be accepted
  (409), an edit without text is rejected (422).
- `tests/test_ticket_status_transitions.py` — the status state machine: legal
  moves succeed, illegal moves and anything out of `closed` are 409, a customer
  can never change a status.
- `tests/test_customer_reply_retriage.py` — a customer message re-queues a
  triage run and reopens an `answered` ticket; an agent message queues nothing
  and changes no status; `closed` still refuses customer replies with 409 and
  no job.

The test suite is deliberately excluded from the runtime images (see
`api/.dockerignore`) so they stay slim — run it on the host, as above.

---

## 8. Known limitations

- **The default engine is a keyword baseline.** Out of the box `laya-serve`
  runs in local mode; it is deterministic and honest about its confidence, but
  it is not a trained model. Hosted mode works but is not exercised by
  default — it needs an `https://` URL and an API key.
- **No LLM key means no drafts.** `draft_status='failed'` and the accept button
  is disabled in the UI. Decisions, triage and the inbox are unaffected.
- **The web container runs the Vite dev server**, deliberately: it keeps the
  demo to one `docker compose up` on a clean machine. It is not a production
  front end (no nginx, no bundled assets).
- **At-most-once queue consumption.** `BRPOP` removes the job before processing
  it, so a worker crash mid-job loses the message; the ticket and its `queued`
  `ai_runs` row remain and can be re-queued manually. Retries inside a running
  worker are handled (3 attempts, exponential backoff).
- **The worker itself has no unit test.** The pytest suite runs the API with no
  Postgres/Redis/LLM, so worker behaviour (whole-thread drafting, stale-draft
  invalidation) is pinned through its contract — the `ai_runs` rows and queue
  pushes the API commits — and by `scripts/smoke.py` / `scripts/e2e_persian.py`
  against the running stack.
- **Single-node by design.** One PostgreSQL, one Redis, no horizontal scaling,
  no cloud deployment, no Kubernetes.
- **Session cookies are not rotated** and there is no rate limiting on login.
- Out of scope per the brief: payments, email/social integrations, analytics
  dashboards, a separate auth service.

---

## 9. Commands

```bash
docker compose up --build -d     # build and start everything
docker compose logs -f api worker # follow the API and worker logs
docker compose logs -f laya-serve # see which engine mode is active
docker compose ps                 # health of all six services
docker compose down               # stop, keep the database volume
docker compose down -v            # purge: stop and delete the data volume
```

---

## 10. Git workflow

The repository follows a two-branch model:

- **`main`** — the primary branch. It only ever receives a merge from `dev`,
  one merge per release. Every commit reachable from `main` is therefore
  tested code.
- **`dev`** — the development branch. Every feature, fix, test and docs change
  lands here, always through its own branch and always with a `--no-ff` merge
  so the branch point stays visible in the history.

Branch naming and where each one merges:

| branch | kind | merges into |
| --- | --- | --- |
| `feat/*` | new capability | `dev` |
| `fix/*` | bug fix | `dev` |
| `test/*` | test coverage | `dev` |
| `docs/*` | documentation | `dev` |
| `chore/*` | tooling, scripts | `dev` |

Typical cycle:

```bash
git checkout dev
git checkout -b feat/thing        # 1. branch off dev
# ... commit your work ...
git checkout dev                  # 2. integrate into dev
git merge --no-ff feat/thing
git checkout main                 # 3. release
git merge --no-ff dev
```

Nothing is pushed from a feature branch: `dev` is the only integration point,
and `main` is the only thing that leaves the machine as a release.

---

## 11. Further reading

- `docs/architecture.md` — the architecture decision record (why `laya-serve`
  is its own container, ownership rules, failure modes).
- `../MANUAL.md` — the build guide this project follows.
