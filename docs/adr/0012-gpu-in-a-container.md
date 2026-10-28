# 0012. Using the GPU from a container

## Status
Accepted (2026-10-25).

## Context
The classify worker (0011) needs a GPU. The machine this was built and measured on has an NVIDIA GeForce RTX 5080 (16,303 MiB,
`nvidia-smi`, driver 610.88; read on that machine, the output is not kept) and Docker Desktop on Windows. The service must still start on a machine without a GPU.

## Decision
**Docker Desktop and the GPU.** Docker Desktop passes a GPU to Linux containers (`--gpus`) only on Windows with the WSL 2 backend
(Docker's documentation for GPU support in Docker Desktop). That backend is what is used.

**Compose.** The GPU is asked for in the Compose file with `deploy.resources.reservations.devices` (`driver: nvidia`,
`count: 1`, `capabilities: [gpu]`; `capabilities` is required), on the `classify` service only. `motion` asks for none.

**CUDA 13.0 wheels.** The RTX 5080 is a Blackwell card (compute capability 12.0, NVIDIA's list of CUDA GPUs). PyTorch's own table of the architectures that
each CUDA build supports lists Blackwell (10.0, 12.0 + PTX) for the CUDA 13.0 build and not for the 12.6 build (PyTorch's
RELEASE notes). The image therefore has the `+cu130` builds of `torch` and `torchvision`, taken from PyTorch's own wheel server by
address and hash (`services/vision/requirements.lock.txt`). CUDA 13.x needs a driver of 580 or later (NVIDIA's release notes,
table of minor-version compatibility); 610.88 qualifies. The check made on the image: `docker run --rm --gpus=all ccc-vision:3.0
python -c "import torch;print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"` printed `True NVIDIA GeForce RTX 5080`.
The CUDA libraries are installed as the Python packages that `torch` requires on Linux (`nvidia-*`); they stay in the image on this
machine and are not distributed (their licence is NVIDIA's; see `THIRD_PARTY_NOTICES.md`).

**Profile and restart.** `motion` and `classify` are in the profile `vision`, so a plain `docker compose up` does not start them and
works without a GPU. `classify` does not start when there is no CUDA device or when the weights do not fill the model (it exits with
status 2 and says why), and has `restart: on-failure:3`: it is started again at most three times and then stays stopped instead
of looping. With the profile on a machine without a GPU, Docker is expected to refuse to create `classify` (no device driver for the
reservation; not tried here, there is one GPU), and `motion` still runs and writes its movement events.

**Memory.** PostgreSQL 17 says of `shared_buffers`, which
`compose.yaml` leaves at its default of 128MB, that "If you have a dedicated database server with 1GB or more of RAM, a reasonable
starting value for shared_buffers is 25% of the memory in your system." That is a starting value for `shared_buffers` on a server with 1GB or more of RAM, not a size for a container; reading 128MB as 25 % of 512 MiB and setting both databases to 512 MiB is the author's decision. Where the product publishes a rule for its memory limit, the limit follows it: Keycloak's sizing page says
"As memory limit, subtract the non-heap memory from the value above and divide the result by 0.7." with 1250 MB and 300 MB of non-heap
memory (1360 MiB). For every other container the limit is the highest value of the kernel's own `memory.peak` after a whole run of the
integration tests plus 15 %, the margin of the Vertical Pod Autoscaler of Kubernetes, whose flag `recommendation-margin-fraction`
(default 0.15) is "Fraction of usage added as the safety margin to the recommended request" (VPA `docs/flags.md`); the highest reading
is used instead of the VPA's percentile. `docker stats` was tried first and left out the file cache the kernel counts (four cameras
were killed at the limit it gave, and that limit was replaced). Valkey stays at 320 MiB because its `maxmemory` of 256 MB has to fit
under it, and `init` at 128 MiB (not read). The readings, the rules, and the table of every container are in `docs/validation/memory_limits.md`
and `docs/validation/memory/`. For the workers: `classify` 1411.5 MiB, so **1624 MiB**; `motion` 91.95 MiB, so **106 MiB**.
Until 2026-10-26 the limits were 3 GiB and 384 MiB, read once with `memory.peak` (2.2 GiB and 216 MiB; the readings were not kept) while
`classify` gave the model 8 pictures at a time; it gives 1 since then (0011). The GPU's own memory is not limited by Compose.

**What the container is allowed.** Both workers: the network of the queue only (0013), no published port, a read-only root
file system with `/tmp` as memory (the libraries' caches go there: `HF_HOME`, `TRITON_CACHE_DIR`), all capabilities dropped,
`no-new-privileges`, an unprivileged user (uid 10002). The recordings, the model files, the check picture and the list of cameras
are mounted read only.

## Consequences
- The image is large (9.54 GB; 0011 lists where it goes). It is built once by hand and is not rebuilt for a change of the code
  (the packages are a layer of their own, before the code).
- A machine without an NVIDIA GPU, or with Docker on Linux without the NVIDIA container toolkit, runs everything but `classify`. The
  results that need the GPU are recorded in `docs/validation/vision_eval.md` with the pictures of the screen in the README.
- The versions follow one GPU generation and one driver; another card may need another build of the wheels.
