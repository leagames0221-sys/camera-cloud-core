"""The API description that the running service publishes is the saved current one, and the saved description of the first
release is not broken by it (oasdiff, run from its published image against the two files)."""
import json
import pathlib
import subprocess

import httpx
import pytest

from conftest import BASE, ROOT

pytestmark = pytest.mark.integration

OASDIFF = "tufin/oasdiff:v1.33.0"


def test_the_running_service_publishes_the_saved_current_description():
    live = json.dumps(httpx.get(f"{BASE}/api/v1/openapi.json", timeout=20).json(), indent=2, ensure_ascii=False,
                      sort_keys=True) + "\n"
    assert (ROOT / "docs/openapi/v2.json").read_text(encoding="utf-8") == live


@pytest.mark.parametrize("level", ["ERR", "WARN"])
def test_no_breaking_difference_from_the_first_release(level):
    try:
        subprocess.run(["docker", "image", "inspect", OASDIFF], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("the oasdiff image is not on this machine")
    p = subprocess.run(["docker", "run", "--rm", "-v", f"{ROOT / 'docs' / 'openapi'}:/specs:ro", OASDIFF, "breaking",
                        "/specs/v1.json", "/specs/v2.json", "--fail-on", level], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "No breaking changes" in p.stdout
