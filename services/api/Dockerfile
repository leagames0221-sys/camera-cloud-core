# Used for three services: the API, the writer of a stamp (a different start command) and the one-shot init job. Build context = repository root.
FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app

# The 31 pinned packages, with hashes. --no-deps because the list already contains everything that is needed.
COPY requirements.lock.txt /tmp/requirements.lock.txt
RUN pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements.lock.txt \
 && rm /tmp/requirements.lock.txt

WORKDIR /srv
COPY services/api/app /srv/app
COPY services/api/migrations /srv/migrations
COPY services/api/stamp_migrations /srv/stamp_migrations

USER app
EXPOSE 8000
CMD ["uvicorn", "--factory", "app.main:build_app", "--host", "0.0.0.0", "--port", "8000"]
