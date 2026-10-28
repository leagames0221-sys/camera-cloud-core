"""Writes the API's OpenAPI document to docs/openapi/v2.json. docs/openapi/v1.json is the document of the first release, kept
as it was: the reference for the check that the later API breaks nothing (oasdiff, docs/validation/openapi_diff.md).

  .venv\\Scripts\\python scripts\\export_openapi.py          write the file
  .venv\\Scripts\\python scripts\\export_openapi.py --check  fail if the file differs from the code
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.api import create_app  # noqa: E402
from app.config import Settings  # noqa: E402


async def _never():
    raise RuntimeError("the OpenAPI document is built without starting the services")


def build() -> str:
    app = create_app(Settings(database_url="", background=False), _never)
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


if __name__ == "__main__":
    target = ROOT / "docs" / "openapi" / "v2.json"
    text = build()
    if "--check" in sys.argv:
        sys.exit(0 if target.read_text(encoding="utf-8") == text else 1)
    target.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {target} ({len(text)} bytes)")
