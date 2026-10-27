FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120

RUN useradd --create-home --uid 10001 appuser

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir --retries 10 -r requirements.txt

COPY --chown=appuser:appuser app ./app

USER appuser

# arq hosts the process; the queue consumer itself is a task started on startup
# (see app/worker.py: the producer LPUSHes onto a plain list, which arq's own
# sorted-set queue never sees).
CMD ["arq", "app.worker.WorkerSettings"]
