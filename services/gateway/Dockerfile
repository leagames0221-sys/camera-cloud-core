# Gateway + the static viewing page. Build context = repository root.
# web/node_modules must already be present (the two JavaScript packages are fetched before the build, see the README);
# only two files and their licence texts are used from it.
FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app

COPY requirements.lock.txt /tmp/requirements.lock.txt
RUN pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements.lock.txt \
 && rm /tmp/requirements.lock.txt

WORKDIR /srv
COPY services/gateway/gateway /srv/gateway
COPY web/index.html web/app.js web/style.css /srv/web/
COPY web/node_modules/hls.js/dist/hls.min.js /srv/vendor/hls.min.js
COPY web/node_modules/hls.js/LICENSE /srv/vendor/hls.js.LICENSE
COPY web/node_modules/keycloak-js/lib/keycloak.js /srv/vendor/keycloak.js
COPY web/node_modules/keycloak-js/LICENSE.txt /srv/vendor/keycloak-js.LICENSE.txt

USER app
EXPOSE 8000
CMD ["uvicorn", "--factory", "gateway.app:build_app", "--host", "0.0.0.0", "--port", "8000"]
