"""docs/openapi/v1.json is the public shape of the first release, kept as it was; docs/openapi/v2.json is the current one and
must match the code. Both contain only the public /api/v1 paths."""
import hashlib
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
CRLF = bytes([13, 10])
SECOND_VERSION_SHA256 = "ef4d446d444d4f62e004b9bfeacc0b1bd4a1a2b62ee6c89fd745ae43a142cd6c"


def exporter():
    spec = importlib.util.spec_from_file_location("export_openapi", ROOT / "scripts/export_openapi.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_current_document_matches_the_code():
    assert (ROOT / "docs/openapi/v2.json").read_text(encoding="utf-8") == exporter().build()


def test_the_document_of_the_first_release_is_still_contained_in_the_current_one():
    """v1.json stays as the first release published it. Every operation in it is still there, with the same answers and
    the same inputs; the real check of 'nothing breaks' is oasdiff (docs/validation/openapi_diff.md)."""
    old = json.loads((ROOT / "docs/openapi/v1.json").read_text(encoding="utf-8"))
    new = json.loads((ROOT / "docs/openapi/v2.json").read_text(encoding="utf-8"))
    for path, ops in old["paths"].items():
        for method, op in ops.items():
            assert method in new["paths"][path], (path, method)
            cur = new["paths"][path][method]
            assert set(op["responses"]) <= set(cur["responses"]), (path, method)
            assert op.get("requestBody") == cur.get("requestBody"), (path, method)
            assert op.get("parameters") == cur.get("parameters"), (path, method)


def test_only_public_paths_are_in_the_contract():
    for name in ("v1", "v2"):
        doc = json.loads((ROOT / f"docs/openapi/{name}.json").read_text(encoding="utf-8"))
        assert all(p.startswith("/api/v1/") for p in doc["paths"]), sorted(doc["paths"])
        assert "/api/v1/camera/token" in doc["paths"] and "/api/v1/me" in doc["paths"]
    assert "/api/v1/devices/enroll" in json.loads((ROOT / "docs/openapi/v2.json").read_text(encoding="utf-8"))["paths"]


def test_the_copy_of_the_document_of_the_second_version_is_the_one_that_version_had():
    """docs/openapi/v2.0.1.json is what v2.json was in the second version, kept so that the current document can be
    compared with it (oasdiff, docs/validation/openapi_diff.md). Its sha256 (line ends as LF) is written here, taken from the
    file of that version, so the copy is that file byte for byte."""
    content = (ROOT / "docs/openapi/v2.0.1.json").read_bytes().replace(CRLF, b"\n")
    assert hashlib.sha256(content).hexdigest() == SECOND_VERSION_SHA256


def test_the_current_document_only_adds_to_the_one_of_the_second_version():
    old = json.loads((ROOT / "docs/openapi/v2.0.1.json").read_text(encoding="utf-8"))
    new = json.loads((ROOT / "docs/openapi/v2.json").read_text(encoding="utf-8"))
    for path, ops in old["paths"].items():
        assert ops == new["paths"][path], path           # every operation that was there is the same, word for word
    assert sorted(set(new["paths"]) - set(old["paths"])) == [
        "/api/v1/tenants/{tenant_id}/cameras/{camera_id}/events",
        "/api/v1/tenants/{tenant_id}/cameras/{camera_id}/events/{event_id}/playback"]
