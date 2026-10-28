# 0001. Components and their vulnerability checks

## Status
Accepted (2026-10-24).

## Context
The first version needs a login service, a media server, an MQTT broker, a database and a Python runtime, and it must run with zero cost and no credit card. Every component must have a licence that allows use, no vulnerability that is known and unfixed at the pinned version on the date of its check (the checks are below; for Valkey 9.1.2 an advisory published later is described at the end of its check), and no Chinese-origin product or model. Components are installed by the person running the repository, not by tooling inside the repository; the build line is in the README.

## Decision
Use the following, pinned to the versions below. Image digests were read with `docker image inspect` on 2026-10-24 after the images were pulled.

| Component | Version | Licence | Image digest (sha256) |
|---|---|---|---|
| Keycloak | 26.8.1 | Apache-2.0 | a78d6b79572168cb25886b80a8b5b8d4ed4390743e3eecb810538683e209dd6b |
| MediaMTX (`-ffmpeg` image) | 1.21.2 | MIT (ffmpeg inside the image is LGPL/GPL, see below) | c0feb4acc548d5f410d57d9046f62dfa355e2abc4098f5243de9e86430c77a95 |
| Mosquitto (alpine) | 2.1.2 | EPL-2.0 OR BSD-3-Clause | 860aeb5d63ce509508f6dcfb2bc8a1c1dd2bc60555b562b45ce90e52d8dabb0f |
| PostgreSQL (alpine) | 17.11 | PostgreSQL License | eee6c6e1b0894fa4fe0075eb054af9fd1cf39a35321c2d4110626cfe506411fb |
| Python (slim) | 3.12 | PSF-2.0 | a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 |

Python packages: the 30 pinned, hashed packages in `requirements.lock.txt` (the 9 used directly are fastapi 0.143.0, uvicorn 0.54.0, asyncpg 0.32.0, pyjwt 2.15.1, cryptography 50.0.2, aiomqtt 2.5.1, paho-mqtt 2.1.0, pytest 9.1.1, httpx 0.28.1; the other 21 are what they pull in). The API image installs them with `pip install --require-hashes --no-deps`. All 30 were checked against OSV on 2026-10-24 with 0 findings; certifi is 2026.10.19; the advisory GHSA-6q48-6c7w-9gw2 lists the affected range `>= 2019.03.09, < 2026.10.18` (first patched version 2026.10.18), so 2026.10.19 is outside it. The local `.venv` was checked on 2026-10-24: `pip check` reports "No broken requirements found" and `pip list` matches the lock (30 of 30 versions equal; the only extra package is pip itself).

Camera image (built 2026-10-24 from the MediaMTX `-ffmpeg` image): Alpine 3.24.2, with `apk add python3` giving python3 3.14.8-r0 and pip 26.2.1 inside a venv; paho-mqtt 2.1.0 installed from its hashed line in the lock. OSV reports 0 findings for python3, openssl, libffi, pip and paho-mqtt at these versions (checked 2026-10-24). Every `FROM` and every external `image:` in `compose.yaml` is pinned as `tag@sha256:<registry digest>`, the digests of the table above.

JavaScript packages (web, committed lock file `web/package-lock.json`): hls.js 1.7.3 (Apache-2.0), keycloak-js 26.2.4 (Apache-2.0). Both OSV 0 findings on 2026-10-24.

The answers of OSV, the output of `pip check`, the versions read inside the camera image, and (further below) the build times and
the size breakdown of the vision image were read from the screen when they were made; they are not kept in this repository. The
image Ids can be read again with `docker image inspect`.

Two licence exceptions outside MIT, Apache and BSD are accepted:
1. ffmpeg (LGPL/GPL) is called as a command inside the official MediaMTX `-ffmpeg` image. It is not bundled in this repository; only the image name is written down.
2. typing-extensions (PSF-2.0) and certifi (MPL-2.0, used unmodified) are pulled in by FastAPI and httpx.

### Vulnerability checks (2026-10-24)
- **Keycloak 26.8.1.** OSV shows GHSA-794g-x443-36f7 (SAML assertion validation) and GHSA-jgwc-jh89-rpgq against 26.8.1. This is a false positive caused by version ranges that lack a fixed-in version. GHSA-jgwc-jh89-rpgq (CVE-2024-9666, proxy headers): the
GitHub record lists `< 24.0.9` and `>= 25.0.0, < 26.0.6`; 26.8.1 is outside both. The GitHub advisory record (`https://api.github.com/advisories/GHSA-794g-x443-36f7`, published 2026-07-02) lists the ranges `<= 26.2.5`, `>= 26.3.0, <= 26.4.7` and `>= 26.5.0, < 26.5.5`; 26.8.1 is outside all three. The fixing commit b40a259 is contained in 26.8.1 (compare result ahead 2084 / behind 0, checked when the design was approved). This is not an unfixed vulnerability.
- **PostgreSQL 17.11.** The official security page (`https://www.postgresql.org/support/security/`, read 2026-10-24) lists every CVE that affects version 17; the highest fixed-in value for 17 is 17.11 (for example CVE-2026-19385, CVE-2026-16239, CVE-2026-15741 are fixed in 17.11). No entry for 17 is marked unfixed. 17.11 is therefore the first version that carries all of the listed fixes.
- **Mosquitto 2.1.2.** OSV does not cover it. The ChangeLog (`https://mosquitto.org/ChangeLog.txt`) section for 2.1.2 (2026-02-09) has no Security item, and 2.1.2 is the newest section in the part read; the GitHub security advisories page of the project (`https://github.com/eclipse-mosquitto/mosquitto/security/advisories`) says there are no published advisories. The security list on mosquitto.org stops in August 2023 (newest entry CVE-2023-0809, fixed in 2.0.16), so it cannot be used as evidence for later versions. Only the first 100000 of 159810 characters of the ChangeLog were read; the newest entries are at the top.

## Consequences
- Updating any pinned component means a new row here and a new vulnerability check.
- The Mosquitto check rests on two sources that can lag behind a fix; it is a check at a date, not a guarantee.
- The ffmpeg exception ties the camera simulator and the MediaMTX container to the `-ffmpeg` image.

## Alternatives not chosen
- **psycopg**: LGPL-3.0. asyncpg is used instead.
- **Redis**: 7.2 and earlier are BSD-3-Clause; from Redis 8 the licence is a choice of RSALv2, SSPLv1 or AGPLv3 (Redis `LICENSE.txt`), none of which is among the licences accepted here. The first version needs no queue; a Valkey queue is considered later.
- **Ultralytics YOLO**: AGPL-3.0.
- **Keycloak 26.4.7 and earlier 26.4 releases**: inside an affected range of GHSA-794g-x443-36f7 (`>= 26.3.0, <= 26.4.7`).
- **Dex, Ory Hydra, Authelia**: MediaMTX documents a Keycloak procedure for JWT authentication, and the device authorisation grant planned for a later version is available in Keycloak.

---

## Added with the later version (2026-10-25)

Digests were read with `docker image inspect` after the images were pulled (or built). The first row of 0001 stays as it was.

| Component | Version | Licence | Image digest (sha256) |
|---|---|---|---|
| Valkey (alpine) | 9.1.2 | BSD-3-Clause | 48332870af354a799964c0012ae1194a0bf2bf894eb508f945810596dc2d8d11 |
| oasdiff (run once from its image, not part of the product) | 1.33.0 | Apache-2.0 | 6263a96dd2ef0726c54e21fea9b8e1607eac4841add0079324b424c1f52b819c |

Python: `requirements.lock.txt` now has 31 packages (the 30 above and **valkey 6.1.1**, MIT, 2025-08-11, no dependencies on CPython 3.12;
6.2.0 is younger than two weeks). The load-test image (`loadtest/requirements.lock.txt`, 37 hashed packages: Locust 2.46.7 (MIT, 2026-10-04) and
everything it pulls in on Linux with CPython 3.12, plus paho-mqtt 2.1.0) is separate and never part of the product's images. Locust 2.47.0 is not
used because it is younger than two weeks. Both lists were checked against PyPI's JSON and OSV on 2026-10-24: 0 findings.

Images built here (not pulled; the product's own code on the pinned base `python:3.12-slim` and, for the camera, the pinned MediaMTX image):

| Image | Id (sha256, first 12) | Built from |
|---|---|---|
| `ccc-api:1.0` (the release's image, also named `ccc-api:1.0.1-frozen`) | e266beb5bf6a | the release of the first version (its code is not in this repository); never built again; used for the load test of the first version and, in the move of 0008, as the first API |
| `ccc-api:2.0` (API, writer, init) | 70b57299bf8e | `services/api` as released in the second version (the Id recorded in `docs/validation/load_v2/environment.json`). The code in this repository differs from it in `services/api/app` (the stamp rule for new tenants, the camera list asking only its own stamp); `compose.test.yaml` and `compose.load.later.yaml` mount this repository's code over it |
| `ccc-gateway:2.0` | 2139f8321770 | `services/gateway`, `web/` as released in the second version (the images of the third version, below, are built from the later code) |
| `ccc-camera:2.0` | ce3dbf28d56b | `services/camera` as released in the second version (the image of the third version, below, is built from the later code) |
| `ccc-loadtest:1.0` | f19e0554930f | `loadtest/` |

### Exceptions added (accepted)
1. Younger than two weeks: brotli 1.2.1 (load-test image only) and certifi 2026.10.19 (also in the API image, see above) (the versions before them carry published
   advisories: GHSA-9v8h-qqgm-4m35 for brotli up to 1.2.0, GHSA-6q48-6c7w-9gw2 for certifi before 2026.10.18; Locust's HTTP client pulls brotli in).
2. Licences outside MIT, Apache and BSD, in the load-test image only: zope-event and zope-interface (ZPL-2.1, pulled in by gevent), bidict (MPL-2.0), greenlet (MIT AND PSF-2.0). Used unmodified.
3. oasdiff (Apache-2.0) is run once from its published image against two files; nothing of it is in this repository.

### Vulnerability check of Valkey
Valkey's published advisories (GitHub, read 2026-10-24) list four high-severity problems in 9.1.0 and 9.1.1 (a use-after-free in stream
deserialization, one with TLS, one in the RDMA handler and one in the Lua script debugger); the first two are fixed in 9.1.1, the last two only
in 9.1.2, so **9.1.2 is the first version that has all four fixes**. The release notes of 9.1.1 list the first two fixes and those of 9.1.2 the last two. The queue is reachable only from
its writer (an internal network, an ACL account, no published port), which also narrows what these problems could reach.

Added 2026-10-28. After that check, on 2026-10-26, the Valkey project published GHSA-f4q9-j22m-xqq9 (severity high, "Integer truncation in
Valkey cluster publish message construction causes a heap buffer overflow"). Its GitHub record (read on 2026-10-28 with
`gh api repos/valkey-io/valkey/security-advisories`) lists `<= 9.1.2` as affected and 9.1.3 as patched. The sentence "no known unfixed
vulnerability at the pinned version" was therefore true on 2026-10-24 and is not true for 9.1.2 today. The advisory says: "Servers that
do not have cluster mode enabled are not affected." This repository does not use cluster mode: the `valkey-server` command of both queues
in `compose.yaml` has no `cluster-enabled` and no configuration file is passed, and in the `valkey.conf` of 9.1.2 the line is the
comment `# cluster-enabled yes`. The command line inside the running container was read with
`docker exec camera-cloud-core-valkey-1 sh -c 'tr "\0" " " < /proc/1/cmdline'`. The version is not changed here.

### Alternatives not chosen (added)
- **Redis** for the queue: see its licences above. Valkey is BSD-3-Clause; its licence file keeps the Redis copyright of 2006-2020.
- **Locust 2.47.0:** younger than two weeks.
- **A custom MQTT load generator:** Locust ships an MQTT user (`locust.contrib.mqtt`), so none was written.

---

## Added with the workers that look at the recordings (2026-10-25)

A third image, `ccc-vision:3.0`, holds the motion and classify workers (0011, 0012). It is made from the pinned base
`python:3.12-slim` (the same digest as the others) and `services/vision/requirements.lock.txt`: 57 packages with their hashes,
installed without dependency resolution and from wheels only. The list was made by following the dependencies of `torch` 2.14.1
(+cu130), `torchvision` 0.29.1 (+cu130; both taken from PyTorch's own wheel server by address and hash), `transformers` 5.19.0,
`opencv-python-headless` 4.14.0.94, `numpy` 2.5.4 and `valkey` 6.1.1 on Linux with CPython 3.12 (PyPI's JSON and the wheels'
metadata); `timm` is not in it (the `no_timm` revision of the model needs none). It was checked against OSV on 2026-10-25: 0 findings.

| Image | Id (first 12) | Digest (sha256) | Size | Built from |
|---|---|---|---|---|
| `ccc-vision:3.0` | 8f5610882ff3 | 8f5610882ff3e63e89a22b1c03a5aca61339d3e0d1988e9bfa16e6aba381f6c2 | 9.54 GB (Python packages 5.9 GB: `nvidia` 3.2 GB, `torch` 1.2 GB, `triton` 0.9 GB) | `services/vision/Dockerfile`. First build 384.7 s, of which the package layer 269.3 s, the base already in the cache at the pinned digest (that build had only the package list and an empty code folder: Id 69ac0b92fd04). The second build, with the code of the workers, took about 2 s, the package layer being cached (Id 568c4d0a931a); built again with the code after the review of the third version |
| `ccc-api:3.0` (API, writer, init) | 34e27e3c1fbe | 34e27e3c1fbecbc6c8c91233b9f426f602fe74d0d21847afae841823ac99c8d5 | 271 MB | `services/api` with the code that added the events; about 2 s (cached layers; Id 5e56e1ff67fe); built again with the code after the review of the third version |
| `ccc-gateway:3.0` | 13150b31697a | 13150b31697a6ba801f58b37257143639af749797b3c6a78aeb6152d27ef9541 | 272 MB | `services/gateway` and `web/` (the list of events); about 2 s |
| `ccc-camera:3.0` | 29925b5c79d6 | 29925b5c79d65675ece13f271aaf23665d0cb3a4bbfe54a24a44f7749886babc | 360 MB | `services/camera` (`SOURCE_FILE`, `CRED_FILE`); about 2 s |

The digests are those of `docker image inspect` (the image Id). The tags 2.0 are the images of the second version and stay on the machine.

The four images of the table were built again on 2026-10-26 (`docker compose -f compose.yaml -f compose.test.yaml --profile vision build
api gateway cam-a1 motion`, from the code of this repository's first commit (the one that took seven values from models: 0004, 0005, 0007, 0009, 0011)). The Ids
they had before were `ccc-api:3.0` 766a12e2374e, `ccc-gateway:3.0` e3849aae98a6, `ccc-camera:3.0` f4455fe7e255 and `ccc-vision:3.0`
d8d872cccc44. The stack was brought up on them (`up -d`), and the memory limits of `compose.yaml` as it is in this repository (0012) are not part of
an image.

On the machine of the author: `docker run --rm --gpus=all ccc-vision:3.0 python -c "import torch;print(torch.cuda.is_available(),
torch.cuda.get_device_name(0))"` printed `True NVIDIA GeForce RTX 5080`. The image holds no video, no weights and no picture: they are
fetched by hand (below) into `var/`, which git and the image build leave out (`.gitignore`, `.dockerignore`).

### Exceptions accepted with this list
1. Three versions are younger than two weeks because the versions before them have published advisories, and these are the ones that
   fix them (OSV, read 2026-10-25): **pillow 13.0.0** (2026-10-16; 12.3.0 has 41), **filelock 4.2.1** (2026-10-17; 4.1.0 has 6),
   **setuptools 84.1.0** (2026-10-19; 84.0.0 has GHSA-xmj3-9gf7-h52w).
2. The CUDA libraries: fifteen `nvidia-*` packages (most with `LicenseRef-NVIDIA-Proprietary`) that `torch` requires on Linux. The
   licence is NVIDIA's CUDA EULA. They are used inside a container on this machine and are not distributed.
3. The FFmpeg libraries (LGPL) inside OpenCV's wheel decode the cameras' video. They are named in `THIRD_PARTY_NOTICES.md`.
   The OSV query names a package by ecosystem and name (OSV API: "Package Objects can be described by package name AND ecosystem OR by the package URL"); the OSV pages read do not say what is inside a wheel, so "0 findings" is an answer about the PyPI package and this repository does not claim it covers the FFmpeg binaries inside; the container's limits are in 0013.
4. Files fetched by hand, not built: the model weights (166,708,629 bytes, Apache-2.0), Big Buck Bunny 640x360 (121,284,117 bytes,
   CC BY 3.0) and one COCO val2017 picture (173,131 bytes). Their hashes and sources are in `docs/validation/vision_truth.md`.

### Alternatives not chosen (added)
- **`timm` and the `main` revision of DETR:** the model card names the `no_timm` revision for use without `timm`.
- **PyTorch's CUDA 12.6 wheels:** they do not contain the architecture of this machine's card (0012).
- **ONNX Runtime, YOLO of Ultralytics:** 0011.

