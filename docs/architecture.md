# Architecture

> Fill every `TODO` before writing application code. This document is graded:
> it is where "why" lives. Keep it under one page.

## 1. Containers and what each one owns

| Container | Owns (writes) | Reads from | Must never |
|---|---|---|---|
| `web` | — | `api` over HTTP | hold an API key, or enforce a business rule |
| `api` | TODO | TODO | TODO |
| `worker` | TODO | TODO | TODO |

Shared infrastructure: `postgres` (durable data), `redis` (job queue).

## 2. Ticket creation flow (`POST /tickets`)

1. TODO — which service receives the request, and what it validates
2. TODO — what is written, in which table, inside which transaction
3. TODO — what is put on the queue, and what the response returns
4. TODO — what the worker does when the model answers
5. TODO — what the worker does when the model fails

## 3. Decision D1 — Why this service split

- **Context:** TODO (what forces the split; name the failure you are protecting against)
- **Decision:** TODO
- **Consequence:** TODO (what becomes harder; at least one real cost)

## 4. Decision D2 — Synchronous or queued AI call

- **Context:** TODO
- **Decision:** TODO
- **Consequence:** TODO
- **Fallback when the model is unavailable:** TODO (exact field values stored)

## 5. AI engine — two stages (`laya-serve` + drafter in `worker`)

Stage 1 is a **typed decision model** ([Laya](https://github.com/NandhaKishorM/laya)), not a text generator.
It answers fixed questions in one forward pass and returns a calibrated confidence for each.

- **Typed questions:** TODO — write the `choice` (category), `score` (priority) and `noul`
  (needs-human-review) definitions, and the exact criteria text for each option.
- **Where it runs:** **Decision — the separate `laya-serve` container** (revised from the earlier
  in-process recommendation). `worker` calls it over HTTP (`/predict`) on the internal
  Compose network; the checkpoint is `laya-multilingual` (Persian tickets), baked into the
  image at build time, no nginx in front — everything runs locally.
  Cost of the choice: one more failure mode (the HTTP call), a bigger image (torch + weights),
  and per-request overhead of a few ms — negligible against ~33 ms inference.
  Benefit: `worker` stays slim and restarts fast; heavy deps are isolated; the engine can be
  scaled or replaced without touching `worker`.
- **Blocking rule:** TODO — Laya is CPU-bound and synchronous; it must not block the async event loop.
- **Confidence policy:** TODO — threshold, and what is stored when it abstains
  (expected: `category=unknown`, `needs_manual_triage=true`).

Stage 2 is the suggested reply. Laya cannot produce it.

- **Source:** TODO — retrieval over past resolved tickets, an LLM, or a hybrid.
- **Why not the same model:** TODO
- **Guardrails:** TODO — ticket text enters as data only; output is a draft, never sent automatically.

- **Baseline:** TODO — the rules-only classifier, and the labelled ticket set used to compare it
  with Laya (report accuracy per stage, not one blended number).

## 6. Boundaries I refuse to cross

- TODO e.g. "no service reads another service's table"
- TODO e.g. "the browser never talks to the LLM directly"
- TODO e.g. "the decision model never writes a ticket status"
