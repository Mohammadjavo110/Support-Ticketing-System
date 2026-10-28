# Architecture

> Fill every `TODO` before writing application code. This document is graded:
> it is where "why" lives. Keep it under one page.

## 1. Containers and what each one owns

| Container | Owns (writes) | Reads from | Must never |
|---|---|---|---|
| `web` | — (stateless SPA) | `api` over HTTP (`/api`, proxied inside the container) | hold an API key, or enforce a business rule |
| `api` | `users`, `sessions`, `tickets`, `messages`, the `ai_runs` `queued` rows (creation + customer follow-up), `suggestion_events` | Postgres; Redis (`LPUSH triage:queue`); `ai_suggestions` (read-only) | call a model, or write `ai_suggestions` |
| `worker` | `ai_suggestions`, the later `ai_runs` updates | Postgres (`tickets`/`messages` read-only), Redis (`BRPOP`), `laya-serve` `/predict`, the LLM over HTTPS | accept user HTTP traffic, or write `tickets`/`messages` |
| `laya-serve` | — (stateless, no database) | a hosted model upstream, or its own local rules | touch Postgres/Redis, or generate ticket text |

Shared infrastructure: `postgres` (durable data), `redis` (job queue).
One writer per table — a table's owner is the only service that ever issues an
`INSERT`/`UPDATE` on it; other services read the committed row.

## 2. Ticket creation flow (`POST /tickets`)

1. `api` receives it: session cookie required (401 without), Pydantic validates
   `subject` (1–200) and `body` (1–5000) — anything else is a 422, no partial write.
2. **One transaction:** the `tickets` row (`status='open'`) **and** the `ai_runs`
   row (`status='queued'`) are committed together, then the ticket is refreshed
   to get its id.
3. Only **after** the commit, `LPUSH "triage:queue" {"ticket_id": "..."}`. If
   Redis is down the error is logged and swallowed: the database is the source
   of truth and the `queued` row is the job record. The response is 201 with the
   ticket (`id`, `status='open'`) — never any AI output.
4. The worker `BRPOP`s the job, marks the run `running`, reads the whole
   thread (subject, body, every message), runs stage 1 (typed decision) and
   stage 2 (draft), upserts the single `ai_suggestions` row and finishes the
   run as `succeeded` / `succeeded_no_draft`.
5. When the model fails, the worker stores the abstained decision (see §4),
   records `ai_runs.last_error`, retries with backoff and finally marks the run
   `failed` — the ticket itself was already created and stays readable.
6. A customer follow-up (`POST /tickets/{id}/messages`) follows the same queue
   contract: the message and a fresh `queued` `ai_runs` row commit together,
   `LPUSH` happens after the commit, and an `answered` ticket reopens to
   `in_progress`. The worker then invalidates the previous ready draft and
   regenerates both stages from the thread, so a suggestion never outlives the
   customer message it answered.

## 3. Decision D1 — Why this service split

- **Context:** the model path is heavy and flaky (network, upstream 429s,
  multi-second calls) while the API must keep accepting tickets. The failure
  being protected against: a model crash/redeploy taking down the process that
  authenticates users and serves the UI, and outbound AI keys living in the
  process that signs session cookies.
- **Decision:** three-way split — `api` (auth + data + queue push), `worker`
  (queue consumer, sole writer of the AI tables), `laya-serve` (model adapter:
  a hosted upstream behind an API key, or the local rules engine — chosen by
  env, not by code). Postgres is the only source of truth; Redis only carries
  "there is work".
- **Consequence:** six containers and one extra network hop — a new failure mode
  (`EngineUnavailable`) and cross-service bookkeeping (`ai_runs` states) to keep
  consistent; debugging means correlating three log streams. In exchange the
  worker image stays slim (no ML deps, restarts in ms), keys and outbound calls
  are isolated, and swapping the model provider is a `.env` change with zero
  code edits (verified: Avalai → local rules → back, same image).

## 4. Decision D2 — Synchronous or queued AI call

- **Context:** a hosted model call measures 3–10 s in practice; a synchronous
  call would hold the customer's HTTP request open that long and fail the
  whole creation on a provider timeout.
- **Decision:** queued — commit first, `LPUSH` after, worker `BRPOP`s.
  Eventual consistency is accepted: the ticket exists instantly, the
  suggestion arrives seconds later.
- **Consequence:** more states to bookkeep (`queued → running →
  succeeded/succeeded_no_draft/failed`), retry/backoff logic in the consumer,
  and a UI that must tolerate `pending`. Redis losing the message is
  recoverable — the `ai_runs` row is authoritative, not the queue.
- **Fallback when the model is unavailable:** exact stored values —
  `category='unknown'`, `category_confidence=0.0`, `priority=2`,
  `priority_confidence=0.0`, `needs_manual_triage=true`, no draft
  (`draft_status` stays `pending`/`failed`), `ai_runs.last_error` set,
  3 attempts with 2 s then 4 s backoff, then `status='failed'`. A below-threshold
  answer is weakened the same way (original confidences kept for the report).
  Never an invented decision, never invented text.

## 5. AI engine — two stages (`laya-serve` + drafter in `worker`)

Stage 1 is a **typed decision model** ([Laya](https://github.com/NandhaKishorM/laya)), not a text generator.
It answers fixed questions in one forward pass and returns a calibrated confidence for each.

- **Typed questions** (the exact contract sent as `{"id","type",…}`):
  - `category` — `choice` over `billing | technical | shipping | account | other`.
    Criteria: **billing** = money moves — invoice, payment, charge, refund,
    subscription price (`پرداخت`, `صورتحساب`, `فاکتور`, `بازپرداخت`, `قیمت`,
    `اشتراک`); **technical** = the product itself fails — error, bug, crash,
    slowness, install (`خطا`, `باگ`, `ایراد`, `خراب`, `کند`, `نصب`,
    `کار نمی‌کند`); **shipping** = physical delivery — dispatch, tracking, delay,
    arrival (`ارسال`, `تحویل`, `پیگیری`, `مرسوله`, `سفارش`); **account** =
    identity and access — register, password, login, locked, email (`حساب`,
    `رمز عبور`, `ورود`, `ثبت نام`, `ایمیل`, `قفل`); **other** = none of the
    above — never force-fit a ticket into a category.
  - `priority` — `score` 1–3, **lower is more urgent**: `1` = the customer says
    urgent/critical (`فوری`, `asap`, `emergency`, `به‌سرعت`) or the service is
    down; `2` = normal (default); `3` = explicitly low (`کم‌اهمیت`, `no rush`,
    `later`, `هر وقت فرصت شد`).
  - `needs_review` — `noul` (boolean): `true` when either confidence is weak,
    or the ticket is a money dispute/refund, legal/threat/abuse, or otherwise
    ambiguous — a human must look. Safe default when unsure is `true`.
- **Where it runs:** **Decision — the separate `laya-serve` container** (revised
  from the earlier in-process recommendation). `worker` calls it over HTTP
  (`POST /predict`) on the internal compose network. The container is a slim
  FastAPI/httpx adapter (multi-stage image, no ML dependencies) with two modes
  chosen at startup: **hosted** — `LAYA_UPSTREAM_URL` starts with `https://` and
  `LAYA_UPSTREAM_KEY` is set, so an internal `http` URL can never loop back into
  itself; the questions travel to an OpenAI-compatible `/chat/completions`
  upstream (Avalai in this deployment) as a strict-JSON instruction and the
  answer is parsed back into the same `{answers}` contract; **local** —
  otherwise, a deterministic Persian/English keyword engine answers the same
  three questions (the baseline, always available, no network).
  Cost of the choice: one more failure mode (the HTTP call) and per-request
  overhead of a few ms. Benefit: `worker` stays slim and restarts fast; the
  engine can be scaled or replaced without touching `worker` — proven by
  switching providers through `.env` alone.
- **Blocking rule:** inference never runs in the worker's event loop — the
  worker issues an `await`ed httpx call bounded by `ENGINE_TIMEOUT_SECONDS`
  (8 s default, 20 s with a hosted model), so a hung model becomes
  `EngineUnavailable` instead of a stuck consumer; anything synchronous inside
  `laya-serve` runs off uvicorn's event loop (threadpool).
- **Confidence policy:** threshold `ENGINE_MIN_CONFIDENCE` (0.55). A field below
  threshold is weakened — `category='unknown'` / `priority=2` — and
  `needs_manual_triage=true` is set, while the original confidences are stored
  so the report shows how close the call was. An unreachable engine stores the
  full abstention row from §4.

Stage 2 is the suggested reply. Laya cannot produce it.

- **Source:** an external LLM behind an OpenAI-compatible chat endpoint
  (Google `gemini-3.5-flash-lite` in this deployment), optional by
  configuration: with `LLM_API_URL`/`LLM_API_KEY` empty the draft is simply
  skipped and `draft_status='failed'` — the decision still stands.
- **Context:** the current ticket's thread **plus the customer's earlier
  tickets** (read-only `tickets` query scoped to the same `customer_id`,
  bounded: up to 5 most recent, ~300 chars of body each, ~2k chars total) —
  so the reply can reference a prior refund, escalation, or repeat problem.
  The stage-1 decision remains scoped to the current thread only.
- **Why not the same model:** the hard boundary — the decider must emit typed,
  validated, confidence-scored values that are reproducible and testable; the
  drafter emits prose that is plausible by nature. One model doing both would
  let a hallucinated sentence change a priority, and either stage's outage
  would break both. *Laya never generates text; the LLM never decides.*
- **Guardrails:** the system prompt declares the ticket untrusted data
  (content to answer, never instructions to obey — prompt-injection defence);
  a deterministic output gate refuses drafts containing URLs or card-like digit
  runs (→ `draft_status='failed'`); the draft is **stored, never sent** — only
  `POST /api/suggestions/{id}/decision` (accept → ticket `answered`, accept
  without a draft → 409, reject → recorded, edit → non-blank text) lets it
  reach the customer.
- **Baseline:** `RulesEngine` — Persian/English keyword counts, confidence 0.8
  on a hit and 0.3 on the fallback (deliberately below the 0.55 threshold, so
  the baseline abstains instead of guessing); no model, no network, which is
  why the three required tests run with zero external services. Comparison is
  reported **per stage** (category accuracy, priority accuracy, abstention
  rate — never one blended number); the labelled signal is the human verdict
  in `suggestion_events` (accept/edit/reject per ticket). No curated golden
  ticket set ships in this repository.

## 6. Boundaries I refuse to cross

- No service writes a table it does not own: `api` never issues an
  `INSERT`/`UPDATE` on `ai_suggestions`, `worker` never writes
  `tickets`/`messages`/`users`.
- The browser never talks to the LLM directly: `web` → `api` only; provider
  keys exist solely in git-ignored `.env` files consumed by `worker`/
  `laya-serve`, never in the JS bundle, an image layer, or a log line.
- The decision model never writes a ticket status and never produces prose;
  the LLM never produces a decision — when in doubt the system stores
  `draft_status='failed'` rather than invented text.
- Ticket and message text is data, never an instruction, and never reaches a
  log: logs carry ids, statuses, durations, error classes and one capped
  upstream error reason.
- Only the API changes `tickets.status`, and only through the state machine
  `STATUS_TRANSITIONS` (`api/app/main.py`): `open → in_progress → answered →
  closed`, plus `open|in_progress → answered` via accept/edit and
  `answered → in_progress` when a customer replies (also reachable manually
  through `PATCH`); `closed` is terminal and every other move is a 409.
