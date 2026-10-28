# The two workers that look at the recordings (motion: OpenCV, CPU; classify: PyTorch + DETR, GPU). One image, two start
# commands. Build context = repository root:
#   docker build -f services/vision/Dockerfile -t ccc-vision:3.0 .
# The model weights, the videos and the check pictures are NOT in the image: they are mounted read-only from var/ at run time.
FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HUB_OFFLINE=1 \
    HF_HOME=/tmp/hf \
    TRITON_CACHE_DIR=/tmp/triton

RUN useradd --system --uid 10002 --no-create-home --shell /usr/sbin/nologin vision

# The 57 pinned packages with their hashes (a layer of its own, before the code). --no-deps because the list holds every
# package that is needed; --only-binary because nothing is compiled at build time.
COPY services/vision/requirements.lock.txt /tmp/requirements.lock.txt
RUN pip install --no-cache-dir --require-hashes --no-deps --only-binary=:all: -r /tmp/requirements.lock.txt \
 && rm /tmp/requirements.lock.txt

WORKDIR /srv
COPY services/vision/vision /srv/vision

USER vision
CMD ["python", "-m", "vision.motion"]
