"""Mutation check: break the code in nineteen places, one at a time, and record which tests turn red.

For each patch in mutation/patches: apply it, run the tests, undo it. The undo is `git apply -R`, and the patch is
refused up front if the file already differs from HEAD, so nothing of yours is ever overwritten.

  python mutation/run_mutations.py              unit tests only (no network)
  python mutation/run_mutations.py --integration  also the integration tests that should catch it, against the
                                                running stack started with compose.yaml + compose.test.yaml (the
                                                runner restarts the api container so it picks up the changed code)

Results are written to docs/validation/mutations.md.
"""
from __future__ import annotations

import argparse
import datetime
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
PATCHES = ROOT / "mutation" / "patches"
PY = [sys.executable, "-m", "pytest"]

# name -> (what is broken, integration tests that should catch it or None[, the services to restart so that they pick up
# the changed code; the API if not said])
API, WRITERS = ("api",), ("writer", "writer-s2")
MUTATIONS = {
    "m1_tenant_check": ("tenant check: any tenant membership counts for every tenant",
                        "tests/integration/test_03_isolation.py::test_company_a_cannot_reach_company_b_through_the_api"),
    "m2_jwt_path": ("video token: the path claim is left out",
                    "tests/integration/test_03_isolation.py::test_a_company_a_read_token_does_not_open_company_b"),
    "m3_jwt_exp": ("video token: the exp claim is left out", None),
    "m4_mqtt_role": ("MQTT role: cameras/%c/state becomes cameras/+/state", "tests/integration/test_04_mqtt.py"),
    "m5_state_rule": ("state rule: online is always 'recording'",
                      "tests/integration/test_04_mqtt.py::test_own_topic_is_accepted_and_shows_up_in_the_api"),
    "m6_enroll_jti_once": ("camera registration: the token id is no longer used up (a token works again)",
                           "tests/integration_v2/test_04_device_grant.py::test_the_same_token_cannot_register_twice"),
    "m7_enroll_azp_aud": ("camera registration: the client and the audience of the token are not checked",
                          "tests/integration_v2/test_04_device_grant.py::"
                          "test_the_device_token_opens_no_other_door_and_the_pages_token_does_not_register"),
    "m8_ack_before_commit": ("queue: entries are confirmed before they are written", None, WRITERS),
    "m9_batch_duplicates": ("queue: a group may hold the same camera twice (the newest is not picked)",
                            "tests/integration_v2/test_02_queue.py::"
                            "test_a_batch_with_the_same_camera_twice_does_not_fail_and_the_newer_state_stays", WRITERS),
    "m10_stamp_lookup": ("tenant to stamp: every tenant gets the first stamp's addresses and audience",
                         "tests/integration_v2/test_01_stamp2.py::"
                         "test_company_c_is_recording_on_the_second_stamp_and_its_urls_are_that_stamps"),
    "m11_tenant_limit": ("limit per tenant: nothing is ever refused",
                         "tests/integration_v2/test_03_limit.py::test_the_21st_call_within_a_second_gets_429_with_retry_after"),
    "m12_dead_at_sixth": ("queue: an entry goes to the dead stream at its 5th handing out instead of the 6th",
                          "tests/integration_v2/test_02_queue.py::"
                          "test_an_entry_that_cannot_be_written_goes_to_the_dead_stream_at_the_sixth_handing_out", WRITERS),
    "m13_shadow_counted": ("movement: a shadow (127 in the mask) is counted as foreground", None),
    "m14_score_boundary": ("naming: a score equal to the limit 0.9 is accepted (>= instead of >)", None),
    "m15_event_key": ("events: the same event written twice is no longer one row (no key to do nothing on)",
                      "tests/integration_v3/test_01_events.py::"
                      "test_the_writer_keeps_one_row_for_an_event_sent_twice_and_refuses_one_without_a_recording", WRITERS),
    "m16_event_without_recording": ("events: an event is written whatever time it has (no segment has to contain it)",
                                    "tests/integration_v3/test_01_events.py::"
                                    "test_the_writer_keeps_one_row_for_an_event_sent_twice_and_refuses_one_without_a_recording",
                                    WRITERS),
    "m17_event_api_tenant": ("events API: the list does not check that the caller belongs to the tenant",
                             "tests/integration_v3/test_01_events.py::"
                             "test_other_companies_and_the_partner_administrator_get_nothing"),
    "m18_event_camera_list": ("events: the writer takes events of every camera, not only of those that are looked at",
                              "tests/integration_v3/test_02_guards.py::"
                              "test_an_event_of_a_camera_that_is_not_looked_at_is_not_written_even_at_a_recorded_time", WRITERS),
    "m19_event_past_segment": ("events: an event may run past the end of its segment",
                               "tests/integration_v3/test_02_guards.py::test_an_event_that_runs_past_its_segment_is_not_written",
                               WRITERS),
}


SITES = {
    "m1_tenant_check": "services/api/app/rules.py, roles_in_tenant()",
    "m2_jwt_path": "services/api/app/video_jwt.py, KeyStore.issue() (path claim)",
    "m3_jwt_exp": "services/api/app/video_jwt.py, KeyStore.issue() (exp claim)",
    "m4_mqtt_role": "services/api/app/dynsec.py, CAMERA_STATE_TOPIC_ACL",
    "m5_state_rule": "services/api/app/rules.py, derive_state()",
    "m6_enroll_jti_once": "services/api/app/api.py, enroll_device() (the use of the token id)",
    "m7_enroll_azp_aud": "services/api/app/oidc.py, DeviceTokenVerifier.verify() (azp and aud)",
    "m8_ack_before_commit": "services/api/app/intake.py, write_group() (order of write and XACK)",
    "m9_batch_duplicates": "services/api/app/intake.py, newest_state_per_camera()",
    "m10_stamp_lookup": "services/api/app/api.py, Deps.stamp_info() (tenant to stamp)",
    "m11_tenant_limit": "services/api/app/throttle.py, TenantThrottle.take()",
    "m12_dead_at_sixth": "services/api/app/intake.py, Queue.reclaim() (the limit of handings out)",
    "m13_shadow_counted": "services/vision/vision/motion_core.py, foreground_share()",
    "m14_score_boundary": "services/vision/vision/classify_core.py, filter_by_score()",
    "m15_event_key": "services/api/app/intake.py, INSERT_EVENTS (the key of the event)",
    "m16_event_without_recording": "services/api/app/intake.py, INSERT_EVENTS (a segment that contains the time)",
    "m17_event_api_tenant": "services/api/app/api.py, list_events() (the tenant check)",
    "m18_event_camera_list": "services/api/app/intake.py, write_group() (the cameras that are looked at)",
    "m19_event_past_segment": "services/api/app/intake.py, INSERT_EVENTS (the end of the event inside the segment)",
}


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, **kw)


def patched_files(patch: pathlib.Path) -> list[str]:
    return re.findall(r"^\+\+\+ b/(.+)$", patch.read_text(encoding="utf-8"), re.M)


def failed_tests(output: str) -> list[str]:
    return sorted({m.group(1) for m in re.finditer(r"^FAILED (\S+)", output, re.M)})


def compose(*args: str) -> subprocess.CompletedProcess:
    return run(["docker", "compose", "-f", "compose.yaml", "-f", "compose.test.yaml", *args], timeout=300)


def restart(services: tuple[str, ...] = API) -> None:
    compose("restart", *services)
    for svc in services:
        for _ in range(60):
            if "healthy" in compose("ps", svc, "--format", "{{.Health}}").stdout:
                break
            time.sleep(2)


def remove_stray_cameras() -> None:
    """A mutation of the registration lets a test register a camera it did not expect; mark those deleted."""
    for db in ("postgres", "postgres-s2"):
        compose("exec", "-T", db, "psql", "-U", "ccc", "-d", "ccc", "-c",
                "UPDATE cameras SET deleted_at = now() WHERE name IN ('device test', 'race') AND deleted_at IS NULL")


def undo_broker_acl() -> None:
    """m4 adds an ACL to the live broker's camera role; take it out again."""
    compose("exec", "-T", "mosquitto", "sh", "-c",
            "mosquitto_ctrl -u admin -P \"$(cat /run/secret/dynsec_admin_password)\" dynsec removeRoleACL camera "
            "publishClientSend 'cameras/+/state'")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--integration", action="store_true")
    ap.add_argument("--only", nargs="+", metavar="NAME",
                    help="run these patches only and print the result; docs/validation/mutations.md is not rewritten")
    args = ap.parse_args()
    rows = []
    for name, (what, integ, *rest) in MUTATIONS.items():
        if args.only and name not in args.only:
            continue
        services = rest[0] if rest else API
        patch = PATCHES / f"{name}.patch"
        files = patched_files(patch)
        if run(["git", "diff", "--quiet", "--", *files]).returncode != 0:
            print(f"{name}: {files} differ from HEAD; commit or stash first", file=sys.stderr)
            return 2
        if run(["git", "apply", "--check", str(patch)]).returncode != 0:
            print(f"{name}: patch does not apply", file=sys.stderr)
            return 2
        run(["git", "apply", str(patch)], check=True)
        try:
            unit = run([*PY, "-q", "-m", "not integration", "--tb=no", "-rf", "-p", "no:cacheprovider"])
            unit_failed = failed_tests(unit.stdout)
            integ_failed: list[str] | None = None
            if args.integration and integ:
                restart(services)
                it = run([*PY, "-q", "-m", "integration", "--tb=no", "-rf", "-p", "no:cacheprovider", integ])
                integ_failed = failed_tests(it.stdout)
        finally:
            run(["git", "apply", "-R", str(patch)], check=True)
            if args.integration and integ:
                if name == "m4_mqtt_role":
                    undo_broker_acl()
                if name in ("m6_enroll_jti_once", "m7_enroll_azp_aud"):
                    remove_stray_cameras()
                restart(services)
        rows.append((name, what, unit_failed, integ_failed, unit.returncode != 0))
        print(f"{name}: unit red={len(unit_failed)}"
              + (f", integration red={len(integ_failed)}" if integ_failed is not None else ""))
    after = run([*PY, "-q", "-m", "not integration", "--tb=no", "-p", "no:cacheprovider"])
    ok = after.returncode == 0
    if args.only:
        for name, what, uf, inf, caught in rows:
            print(f"{name}: caught={caught}\n  unit red: {uf}\n  integration red: {inf}")
        print(f"unit tests after the last undo: {'green' if ok else 'RED'}")
    else:
        write_report(rows, args.integration, ok)
    return 0 if ok and all(r[4] for r in rows) else 1


def write_report(rows, with_integration: bool, clean_after: bool) -> None:
    out = ["# Mutation check", "",
           f"Run on {datetime.date.today().isoformat()}. Each change was applied alone, the tests were run, and the "
           "change was undone. A change counts as caught when at least one test turns red.", "",
           "| Patch | Site | What was broken | Unit tests red | Integration tests red |", "|---|---|---|---|---|"]
    for name, what, uf, inf, caught in rows:
        u = "<br>".join(f"`{t}`" for t in uf[:6]) + (f"<br>(+{len(uf) - 6} more)" if len(uf) > 6 else "")
        i = "not run" if inf is None else ("<br>".join(f"`{t}`" for t in inf[:4]) or "none")
        out.append(f"| `{name}` | `{SITES[name]}` | {what} | {u or 'none'} ({len(uf)}) | {i} |")
    out += ["", f"Unit tests after the last undo: {'green' if clean_after else 'RED (the working tree is not clean)'}.",
            "", "`m3_jwt_exp` is a unit-level check only: MediaMTX accepts a token without `exp`, so only the "
            "API's own tests can notice that the claim is missing.",
            "", "`m8_ack_before_commit` is a unit-level check only: from outside, the order of writing and confirming shows "
            "only when the database fails in between, which the unit test makes happen with a fake sink.",
            "", "`m13_shadow_counted` and `m14_score_boundary` are unit-level checks only: they are in the workers, which the "
            "integration tests reach through the whole way and not at these two values (the shadow, the score equal to the limit).",
            ""]
    (ROOT / "docs" / "validation" / "mutations.md").write_text("\n".join(out), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    sys.exit(main())
