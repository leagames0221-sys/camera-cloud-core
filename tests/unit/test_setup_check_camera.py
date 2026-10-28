"""The pure parts of tools/setup_check_camera.py: the credentials file, the list of cameras to look at."""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("setup_check_camera", ROOT / "tools/setup_check_camera.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

CID = "30000000-0000-4000-8000-0000000000f1"


def test_the_credentials_file_has_what_the_camera_program_reads():
    answer = {"id": CID, "tenant_id": "t", "name": "x", "credential_type": "secret", "secret": "s3cret",
              "mediamtx_url": f"rtsp://mediamtx:8554/20000000-0000-4000-8000-00000000000b/{CID}",
              "mqtt_url": "mqtt://mosquitto:1883", "mqtt_client_id": CID, "mqtt_topic": f"cameras/{CID}/state"}
    creds = tool.credentials_from_answer(answer)
    assert creds == {"camera_id": CID, "secret": "s3cret", "mqtt_host": "mosquitto", "mqtt_port": 1883,
                     "rtsp_base": "rtsp://mediamtx:8554"}
    from camera import load_credentials_file
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "credentials.json"
        f.write_text(json.dumps(creds), encoding="utf-8")
        assert load_credentials_file(str(f)) == creds


def test_the_camera_is_in_the_list_once_and_the_rest_of_the_file_stays():
    text = "# cameras that are looked at\n30000000-0000-4000-8000-0000000000aa  # another\n"
    once = tool.with_camera_id(text, CID)
    assert once.splitlines() == ["# cameras that are looked at", "30000000-0000-4000-8000-0000000000aa  # another",
                                 f"{CID}  # {tool.CAMERA_NAME}"]
    assert tool.with_camera_id(once, CID) == once                              # not twice
    assert tool.with_camera_id("", CID) == f"{CID}  # {tool.CAMERA_NAME}\n"
    assert tool.with_camera_id("no newline at the end", CID).startswith("no newline at the end\n")
    assert tool.without_camera_id(once, CID) == text


def test_the_camera_is_found_by_its_name():
    cams = [{"id": "1", "name": "A"}, {"id": CID, "name": tool.CAMERA_NAME}]
    assert tool.find_by_name(cams)["id"] == CID and tool.find_by_name(cams[:1]) is None


def test_the_tool_does_not_put_the_secret_on_a_command_line():
    source = (ROOT / "tools/setup_check_camera.py").read_text(encoding="utf-8")
    assert "stdin=json.dumps(creds)" in source and "umask 077" in source
