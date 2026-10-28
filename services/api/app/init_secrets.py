"""One-shot job run by the `init` container: creates the signing key and the shared secrets, once.

Layout under the volume (each service mounts only its own sub folder, read only):
  api/        video_signing_key.pem  hook_secret  dynsec_admin_password  mqtt_ingest_password  cameras/<uuid>.secret
  mediamtx/   hook_secret
  mosquitto/  dynsec_admin_password
  cameras/<uuid>/secret               (one folder per demo camera)
  stamp<N>/   hook_secret  mqtt_ingest_password  valkey_password     (the writer of stamp N; no signing key)
              db_password (and, for a stamp after the first, mosquitto<N>/ mediamtx<N>/ postgres<N>/ for that stamp's broker,
              video server and database, and copies in api/ for the control plane)
  valkey<N>/  users.acl                                              (the queue of stamp N; the passwords only as hashes)
  vision1/motion/    valkey_password     (the motion worker's account on stamp 1's queue)
  vision1/classify/  valkey_password     (the classify worker's account; each worker mounts only its own folder)
Files that already exist are left alone, so running it again changes nothing. The signing key in particular must
survive restarts: MediaMTX keeps the JWKS it fetched for an hour. The one exception is the queue's account file
(valkey<N>/users.acl): it is rewritten when what it should say has changed (a new account, a new rule), and the queue has
to be restarted to read it again; the passwords in it are the ones that already exist.

Every file is readable by its owner only (0400). `api/` belongs to the API's user (uid 10001, the `app` user of
services/api/Dockerfile); the other folders belong to root, which is the user MediaMTX's hook, Mosquitto's start
script and the simulated cameras run as. The owners and modes are set again at every run, so files written by an
earlier version are tightened too.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import secrets
import sys

from . import seed_data
from .seed import camera_secret_name
from .video_jwt import generate_private_key_pem

API_UID = 10001
ROOT_UID = 0
VALKEY_UID = 999   # the user the Valkey image runs as (a container setting in compose.yaml)
VISION_UID = 10002  # the user of the motion and classify workers (services/vision/Dockerfile)
STAMPS = (1, 2)
VISION_STAMPS = (1,)   # only the first stamp has the motion and classify workers (docs/adr/0011)
VISION_WORKERS = ("motion", "classify")
# What the writer may do on its queue: streams, and the few commands the client library sends when it connects.
CONNECT_RULES = "+ping +hello +client|setinfo +client|setname"
VALKEY_RULES = f"~ccc:* -@all +@stream {CONNECT_RULES}"
# What each worker may do: only the stream commands it uses, and each only on the streams it uses (selectors: a rule in
# brackets has its own keys and commands). It reads one stream as a member of its group, and adds to the others.
# motion: reads ccc:recorded; adds to ccc:motion, ccc:events and its own dead stream.  classify: reads ccc:motion; adds to
# ccc:events and its own dead stream. Not the writer's ccc:dead: an XADD can trim the stream it adds to (MAXLEN / MINID), and
# no rule can forbid that argument, so adding to ccc:dead would also be emptying it.
_READ = "+xreadgroup +xack +xautoclaim +xpending +xgroup|create"
WORKER_RULES = {
    "motion": f"-@all {CONNECT_RULES} (~ccc:recorded {_READ}) (~ccc:motion ~ccc:events ~ccc:dead:motion +xadd)",
    "classify": f"-@all {CONNECT_RULES} (~ccc:motion {_READ}) (~ccc:events ~ccc:dead:classify +xadd)",
}


def _write_new(path: pathlib.Path, data: str | bytes) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data if isinstance(data, bytes) else data.encode())
    tmp.chmod(0o400)
    tmp.replace(path)
    return True


def _write_changed(path: pathlib.Path, data: str) -> bool:
    """Write the file unless it already says exactly this (it may be read only, and is replaced, not edited)."""
    if path.exists() and path.read_text(encoding="utf-8") == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data.encode())
    tmp.chmod(0o400)
    tmp.replace(path)
    return True


def owner_of(root: pathlib.Path, path: pathlib.Path) -> int:
    top = path.relative_to(root).parts[0]
    if top == "api" or (top.startswith("stamp") and top[5:].isdigit()):
        return API_UID
    if top.startswith("valkey") and top[6:].isdigit():
        return VALKEY_UID
    if top.startswith("vision") and top[6:].isdigit():
        return VISION_UID
    return ROOT_UID


def lock_down(root: pathlib.Path) -> None:
    """Owner-only files (0400) and folders (0500), owned as described in the module text."""
    chown = getattr(os, "chown", None)  # not on Windows, where only the unit tests run this
    for p in sorted(root.rglob("*"), reverse=True):  # children before their folder
        if chown is not None:
            chown(p, owner_of(root, p), -1)
        p.chmod(0o500 if p.is_dir() else 0o400)


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _kept_or_new(path: pathlib.Path, nbytes: int) -> str:
    """The secret already in the file, or a new one (the file is written by the caller)."""
    return _read(path) if path.exists() else secrets.token_urlsafe(nbytes)


def init_secrets(root: pathlib.Path) -> list[str]:
    made: list[str] = []
    stamp_dirs = [f"{kind}{n}" for n in STAMPS for kind in ("stamp", "valkey")]
    stamp_dirs += [f"{kind}{n}" for n in STAMPS if n > 1 for kind in ("mosquitto", "mediamtx", "postgres")]
    stamp_dirs += [f"vision{n}/{w}" for n in VISION_STAMPS for w in VISION_WORKERS]
    for d in ("api", "api/cameras", "mediamtx", "mosquitto", "cameras", *stamp_dirs):
        (root / d).mkdir(parents=True, exist_ok=True)
        (root / d).chmod(0o700)  # writable while this job runs; lock_down() closes it again

    if _write_new(root / "api" / "video_signing_key.pem", generate_private_key_pem()):
        made.append("video_signing_key")

    hook = _read(root / "api" / "hook_secret") if (root / "api" / "hook_secret").exists() else secrets.token_urlsafe(32)
    admin = (_read(root / "api" / "dynsec_admin_password") if (root / "api" / "dynsec_admin_password").exists()
             else secrets.token_urlsafe(24))
    for rel, value in (("api/hook_secret", hook), ("mediamtx/hook_secret", hook),
                       ("api/dynsec_admin_password", admin), ("mosquitto/dynsec_admin_password", admin)):
        if _write_new(root / rel, value):
            made.append(rel)
    if _write_new(root / "api" / "mqtt_ingest_password", secrets.token_urlsafe(24)):
        made.append("mqtt_ingest_password")

    # The writer of each stamp: its own copies of the closed-segment secret and the intake password (the API sets that
    # user up in the broker), and the password for its queue. Valkey gets the password as a SHA-256 hash only.
    # Stamp 1 reuses the secrets of the first version; every later stamp has secrets of its own for everything: its
    # database, its broker's administration, its closed-segment calls, its intake user. The API (the control plane)
    # keeps copies of what it needs to reach that stamp, in its own folder.
    ingest = _read(root / "api" / "mqtt_ingest_password")
    for n in STAMPS:
        sd_ = root / f"stamp{n}"
        files: list[tuple[pathlib.Path, str]] = []
        if n == 1:
            hook_n, ingest_n = hook, ingest
        else:
            hook_n = _kept_or_new(sd_ / "hook_secret", 32)
            ingest_n = _kept_or_new(sd_ / "mqtt_ingest_password", 24)
            admin_n = _kept_or_new(root / f"mosquitto{n}" / "dynsec_admin_password", 24)
            db_n = _kept_or_new(sd_ / "db_password", 24)
            files += [(root / f"mediamtx{n}" / "hook_secret", hook_n),
                      (root / f"mosquitto{n}" / "dynsec_admin_password", admin_n),
                      (root / f"postgres{n}" / "db_password", db_n), (sd_ / "db_password", db_n),
                      (root / "api" / f"stamp{n}_db_password", db_n),
                      (root / "api" / f"stamp{n}_mqtt_admin_password", admin_n),
                      (root / "api" / f"stamp{n}_mqtt_ingest_password", ingest_n)]
        files += [(sd_ / "hook_secret", hook_n), (sd_ / "mqtt_ingest_password", ingest_n)]
        for path, value in files:
            if _write_new(path, value):
                made.append(path.relative_to(root).as_posix())
        if _write_new(sd_ / "valkey_password", secrets.token_urlsafe(24)):
            made.append(f"stamp{n}/valkey_password")
        digest = hashlib.sha256(_read(sd_ / "valkey_password").encode()).hexdigest()
        acl = "user default off\n" + f"user writer on #{digest} {VALKEY_RULES}\n"
        if n in VISION_STAMPS:
            for w in VISION_WORKERS:
                if _write_new(root / f"vision{n}" / w / "valkey_password", secrets.token_urlsafe(24)):
                    made.append(f"vision{n}/{w}/valkey_password")
                wd = hashlib.sha256(_read(root / f"vision{n}" / w / "valkey_password").encode()).hexdigest()
                acl += f"user {w} on #{wd} {WORKER_RULES[w]}\n"
        if _write_changed(root / f"valkey{n}" / "users.acl", acl):
            made.append(f"valkey{n}/users.acl")

    for cid, _tid, _name in seed_data.CAMERAS + seed_data.EXTRA_CAMERAS:
        api_file = root / "api" / camera_secret_name(cid)
        value = _read(api_file) if api_file.exists() else secrets.token_urlsafe(32)
        if _write_new(api_file, value):
            made.append(f"camera {cid}")
        cam_dir = root / "cameras" / str(cid)
        if cam_dir.exists():
            cam_dir.chmod(0o700)
        _write_new(cam_dir / "secret", value)
    lock_down(root)
    return made


if __name__ == "__main__":
    target = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/secrets")
    created = init_secrets(target)
    print("init: created" if created else "init: nothing to do", ", ".join(created))
