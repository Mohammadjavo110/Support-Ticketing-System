"""laya-serve — typed decision engine called by the worker over POST /predict.

Mode is chosen once from the environment at startup:
  hosted — LAYA_API_URL is an https endpoint AND LAYA_API_KEY is set:
           a thin adapter that forwards the typed questions upstream.
  local  — otherwise: a deterministic Persian/English rules baseline.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import hosted, rules
from .config import load_settings
from .schemas import PredictRequest, PredictResponse

settings = load_settings()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger("laya.serve")


@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("laya-serve ready mode=%s model=%s", settings.mode, settings.model)
    yield


app = FastAPI(
    title="laya-serve",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/healthz")
def healthz() -> dict:
    # Cheap and self-contained: never touches the upstream, so the Docker
    # healthcheck stays green even when the hosted model is down.
    return {"status": "ok", "mode": settings.mode}


@app.post("/predict", response_model=PredictResponse)
async def predict(body: PredictRequest):
    started = time.perf_counter()
    model = (body.model or "").strip() or settings.model
    questions = [q.model_dump(exclude_none=True) for q in body.questions]

    if settings.mode == "hosted":
        try:
            answers = await hosted.predict(settings, body.text, questions, model)
        except hosted.UpstreamError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            log.warning(
                "predict failed mode=hosted model=%s latency_ms=%d error=%s",
                model, latency_ms, exc.code,
            )
            return JSONResponse(
                status_code=503,
                content={
                    "error": exc.code,
                    "detail": str(exc),
                    "mode": "hosted",
                    "latency_ms": latency_ms,
                },
            )
    else:
        answers = rules.decide(body.text, questions, settings.min_confidence)

    latency_ms = int((time.perf_counter() - started) * 1000)
    log.info("predict mode=%s model=%s latency_ms=%d", settings.mode, model, latency_ms)
    return PredictResponse(answers=answers, model=model, mode=settings.mode, latency_ms=latency_ms)
