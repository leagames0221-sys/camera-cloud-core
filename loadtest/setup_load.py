"""Creates (or removes) the data of the load test through the public API: 5 load tenants with 60 cameras each,
plus one tenant with one camera that only the lag probe uses. The users are the realm's l-admin (tenant admin of every
load tenant) and l-viewer (viewer of the five load tenants); partner-admin creates the tenants and makes l-admin their
admin. The camera secrets are shown by the API only once, so they are written to the data file in the /work volume.

  python setup_load.py            create everything, write the data file
  python setup_load.py --remove   delete the cameras through the API and remove the data file
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time

import common as c


def ensure_tenant(partner_token: str, name: str, existing: dict[str, str]) -> str:
    if name in existing:
        return existing[name]
    # stamp 1 on purpose: the later version puts a new tenant on the stamp with the fewest tenants, and the load test runs with
    # the second stamp switched off (the first version ignores the extra field)
    st, body = c.api("POST", "/api/v1/tenants", partner_token, {"name": name, "stamp_id": 1})
    if st != 201:
        raise SystemExit(f"could not create tenant {name}: {st}")
    return body["id"]


def make_cameras(admin: c.TokenCache, tenant_id: str, name: str, n: int) -> list[dict]:
    out = []
    for i in range(1, n + 1):
        for _ in range(20):          # the later version limits a tenant to 100 calls a second: wait and ask again
            st, body = c.api("POST", f"/api/v1/tenants/{tenant_id}/cameras", admin.get(),
                             {"name": f"{name}-cam-{i:02d}"})
            if st != 429:
                break
            time.sleep(0.2)
        if st != 201:
            raise SystemExit(f"could not register a camera in {name}: {st}")
        out.append({"id": body["id"], "secret": body["secret"]})
    return out


def create() -> None:
    if os.path.exists(c.DATA_FILE):
        raise SystemExit("the data file exists: run with --remove first")
    partner, admin = c.TokenCache("partner-admin"), c.TokenCache("l-admin")
    st, tenants = c.api("GET", "/api/v1/tenants", partner.get())
    if st != 200:
        raise SystemExit(f"could not list tenants: {st}")
    existing = {t["name"]: t["id"] for t in tenants}
    data: dict = {"tenants": []}
    for name in c.TENANT_NAMES + [c.PROBE_TENANT_NAME]:
        tid = ensure_tenant(partner.get(), name, existing)
        st, _ = c.api("POST", f"/api/v1/tenants/{tid}/members", partner.get(),
                      {"user_id": c.USER_SUB["l-admin"], "role": "tenant_admin"})
        if st not in (200, 201):
            raise SystemExit(f"could not make l-admin the admin of {name}: {st}")
        if name != c.PROBE_TENANT_NAME:
            st, _ = c.api("POST", f"/api/v1/tenants/{tid}/members", admin.get(),
                          {"user_id": c.USER_SUB["l-viewer"], "role": "viewer"})
            if st not in (200, 201):
                raise SystemExit(f"could not make l-viewer a viewer of {name}: {st}")
        n = 1 if name == c.PROBE_TENANT_NAME else c.CAMERAS_PER_TENANT
        data["tenants"].append({"name": name, "id": tid, "cameras": make_cameras(admin, tid, name, n)})
        print(f"{name}: {tid}, {n} cameras", flush=True)
    os.makedirs(os.path.dirname(c.DATA_FILE), exist_ok=True)
    tmp = c.DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, c.DATA_FILE)


def remove() -> None:
    if not os.path.exists(c.DATA_FILE):
        print("no data file; nothing to remove")
        return
    admin = c.TokenCache("l-admin")
    data = c.load_data()
    for t in data["tenants"]:
        for cam in t["cameras"]:
            for _ in range(20):
                st, _ = c.api("DELETE", f"/api/v1/tenants/{t['id']}/cameras/{cam['id']}", admin.get())
                if st != 429:
                    break
                time.sleep(0.2)
            if st not in (204, 404):
                print(f"camera {cam['id']}: {st}", file=sys.stderr)
    os.remove(c.DATA_FILE)
    shutil.rmtree(c.CLAIM_DIR, ignore_errors=True)
    print("removed")


if __name__ == "__main__":
    remove() if "--remove" in sys.argv[1:] else create()
