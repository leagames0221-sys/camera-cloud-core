r"""Registers the camera of the check video for company B, through the running API, the way a tenant administrator does.

  .venv\Scripts\python tools\setup_check_camera.py

What it does (and does nothing if it was done already):
  1. signs in as `b-admin` (the password is DEMO_USER_PASSWORD from the environment or from `.env`) with the same
     authorization code + PKCE walk as the integration tests;
  2. registers a camera named "確かめの映像" in company B's tenant (`POST /api/v1/tenants/<B>/cameras`); the answer holds the
     camera's secret once;
  3. puts the camera's id, secret and addresses in the file `credentials.json` of the Docker volume `checkcam` (the camera
     `cam-b2` reads it from there; it waits for the file);
  4. adds the camera's id to `var/vision/cameras.txt`, the list of cameras the motion worker looks at.

The seeded cameras are not touched and the list of seeded cameras does not change. If the camera is already registered and
its secret is in the volume, nothing happens; if it is registered but the secret is not in the volume (the secret cannot be
read again), the camera is deleted and registered anew.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "services" / "camera"), str(ROOT / "tests" / "integration")]

CAMERA_NAME = "確かめの映像"
VOLUME = "camera-cloud-core_checkcam"          # <compose project>_<volume>
CAMERAS_FILE = ROOT / "var" / "vision" / "cameras.txt"
GATEWAY = os.environ.get("GATEWAY", "http://localhost:8000")
IMAGE = os.environ.get("CAMERA_IMAGE", "ccc-camera:3.0")      # any image with a shell; it only copies a file into the volume


def credentials_from_answer(answer: dict) -> dict:
    """What the camera program keeps: the same fields it keeps after it registered itself (camera.py, load_or_enroll)."""
    from camera import split_urls
    cid, host, port, rtsp = split_urls(answer)
    return {"camera_id": cid, "secret": answer["secret"], "mqtt_host": host, "mqtt_port": port, "rtsp_base": rtsp}


def with_camera_id(text: str, camera_id: str, name: str = CAMERA_NAME) -> str:
    """The list of cameras to look at with this camera in it once (other lines, comments included, are kept)."""
    ids = {line.split("#", 1)[0].strip() for line in text.splitlines()}
    if camera_id in ids:
        return text
    if text and not text.endswith("\n"):
        text += "\n"
    return text + f"{camera_id}  # {name}\n"


def without_camera_id(text: str, camera_id: str) -> str:
    return "".join(line for line in text.splitlines(keepends=True) if line.split("#", 1)[0].strip() != camera_id)


def find_by_name(cameras: list[dict], name: str = CAMERA_NAME) -> dict | None:
    return next((c for c in cameras if c.get("name") == name), None)


def demo_password() -> str:
    if os.environ.get("DEMO_USER_PASSWORD"):
        return os.environ["DEMO_USER_PASSWORD"]
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DEMO_USER_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("DEMO_USER_PASSWORD is not set (environment or .env)")


def docker(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], input=stdin, capture_output=True, text=True)


def volume_credentials() -> dict | None:
    """The credentials in the volume, or None (no volume, no file, not readable)."""
    r = docker("run", "--rm", "-v", f"{VOLUME}:/cred:ro", "--entrypoint", "sh", IMAGE, "-c", "cat /cred/credentials.json")
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


def put_credentials(creds: dict) -> None:
    if docker("volume", "inspect", VOLUME).returncode != 0:
        # made the way Compose makes it, so that Compose takes it as its own
        made = docker("volume", "create", "--label", "com.docker.compose.project=camera-cloud-core", "--label",
                      "com.docker.compose.volume=checkcam", VOLUME)
        if made.returncode != 0:
            raise SystemExit(f"cannot create the volume: {made.stderr.strip()}")
    # the secret goes in through the standard input, not through the command line
    r = docker("run", "--rm", "-i", "-v", f"{VOLUME}:/cred", "--entrypoint", "sh", IMAGE, "-c",
               "umask 077 && cat > /cred/credentials.json", stdin=json.dumps(creds))
    if r.returncode != 0:
        raise SystemExit(f"cannot write the credentials: {r.stderr.strip()}")


def main() -> int:
    import httpx
    from oidc_flow import login

    token = login("b-admin", demo_password())["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=f"{GATEWAY}/api/v1", headers=h, timeout=30) as api:
        me = api.get("/me")
        me.raise_for_status()
        tenants = {e["tenant_id"] for e in me.json()["entries"] if e["role"] == "tenant_admin"}
        if len(tenants) != 1:
            raise SystemExit("b-admin is expected to administer exactly one tenant")
        (tenant,) = tenants
        cameras = api.get(f"/tenants/{tenant}/cameras")
        cameras.raise_for_status()
        existing = find_by_name(cameras.json())
        if existing is not None:
            have = volume_credentials()
            if have and have.get("camera_id") == existing["id"]:
                CAMERAS_FILE.parent.mkdir(parents=True, exist_ok=True)
                old = CAMERAS_FILE.read_text(encoding="utf-8") if CAMERAS_FILE.exists() else ""
                CAMERAS_FILE.write_text(with_camera_id(old, existing["id"]), encoding="utf-8", newline="\n")
                print(f"already registered: {existing['id']}")
                return 0
            api.delete(f"/tenants/{tenant}/cameras/{existing['id']}").raise_for_status()
            old = CAMERAS_FILE.read_text(encoding="utf-8") if CAMERAS_FILE.exists() else ""
            CAMERAS_FILE.write_text(without_camera_id(old, existing["id"]), encoding="utf-8", newline="\n")
            print(f"the secret of {existing['id']} was not in the volume: deleted, registering again")
        r = api.post(f"/tenants/{tenant}/cameras", json={"name": CAMERA_NAME})
        r.raise_for_status()
        answer = r.json()
    put_credentials(credentials_from_answer(answer))
    CAMERAS_FILE.parent.mkdir(parents=True, exist_ok=True)
    old = CAMERAS_FILE.read_text(encoding="utf-8") if CAMERAS_FILE.exists() else ""
    CAMERAS_FILE.write_text(with_camera_id(old, answer["id"]), encoding="utf-8", newline="\n")
    print(f"registered: {answer['id']} in tenant {tenant}; start it with: docker compose --profile vision up -d cam-b2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
