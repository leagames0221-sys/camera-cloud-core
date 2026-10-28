# Load-test image: Locust and the programs in this folder. Build context = repository root.
# Not part of the product; nothing from here goes into the other images.
FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# The 37 pinned packages (Locust 2.46.7 and everything it pulls in on Linux, CPython 3.12, plus paho-mqtt), with
# hashes. --no-deps because the list holds everything; --only-binary so that nothing is compiled in the image.
COPY loadtest/requirements.lock.txt /tmp/requirements.lock.txt
RUN pip install --no-cache-dir --require-hashes --no-deps --only-binary=:all: -r /tmp/requirements.lock.txt \
 && rm /tmp/requirements.lock.txt

WORKDIR /app
COPY loadtest/common.py loadtest/locustfile.py loadtest/setup_load.py loadtest/probe.py /app/
# The volume mounted here (data file, claims, results) is created from this folder on first use.
RUN mkdir /work

CMD ["sleep", "infinity"]
