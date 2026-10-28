"""The integration tests repeat the demo ids instead of importing the application; keep the two lists equal."""
import importlib.util
import pathlib

from app import seed_data as sd

ROOT = pathlib.Path(__file__).resolve().parents[2]


def load_ids():
    spec = importlib.util.spec_from_file_location("integration_ids", ROOT / "tests/integration/ids.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ids_equal_the_seed_data():
    ids = load_ids()
    assert (ids.TENANT_A, ids.TENANT_B) == (str(sd.TENANT_A), str(sd.TENANT_B))
    assert (ids.CAM_A1, ids.CAM_A2, ids.CAM_B1) == (str(sd.CAM_A1), str(sd.CAM_A2), str(sd.CAM_B1))
    assert ids.USER_SUB == {"partner-admin": str(sd.USER_PARTNER_ADMIN), "a-admin": str(sd.USER_A_ADMIN),
                            "a-viewer": str(sd.USER_A_VIEWER), "b-admin": str(sd.USER_B_ADMIN),
                            "b-viewer": str(sd.USER_B_VIEWER)}


def test_integration_tests_do_not_import_the_application():
    for f in (ROOT / "tests/integration").glob("*.py"):
        text = f.read_text(encoding="utf-8")
        assert "from app" not in text and "import app" not in text, f.name
