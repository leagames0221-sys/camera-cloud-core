"""Pure parts of the simulated camera: payloads, token redaction, ffmpeg progress parsing."""
import json

from camera import clip_bitrate_kbps, offline_payload, online_payload, parse_progress, redact, state_topic


def test_topic_is_the_cameras_own():
    assert state_topic("abc") == "cameras/abc/state"


def test_payloads():
    assert json.loads(online_payload()) == {"state": "online"}
    assert json.loads(online_payload(15.0, 83.123)) == {"state": "online", "fps": 15.0, "bitrate": 83.12}
    assert json.loads(offline_payload("will")) == {"state": "offline", "reason": "will"}
    assert json.loads(offline_payload("normal")) == {"state": "offline", "reason": "normal"}


def test_token_never_survives_redaction():
    line = "[rtsp @ 0x1] method DESCRIBE failed: 401 rtsp://mediamtx:8554/a/b?token=eyJhbGciOi.xx.yy&x=1"
    out = redact(line)
    assert "eyJ" not in out and "token=***" in out and "x=1" in out
    assert redact("Output #0, rtsp, to 'rtsp://h/p?token=abc'") == "Output #0, rtsp, to 'rtsp://h/p?token=***'"
    assert redact("nothing to hide") == "nothing to hide"


def test_progress_parsing_keeps_latest_values():
    s: dict = {}
    for line in ("frame=10\n", "fps=14.97\n", "bitrate=  83.1kbits/s\n", "bitrate=84.0kbits/s\n", "bitrate=N/A\n",
                 "fps=bad\n"):
        parse_progress(line, s)
    assert s == {"fps": 14.97, "bitrate": 84.0}


def test_rtsp_output_reports_no_bitrate_so_the_clip_average_is_used(tmp_path):
    # ffmpeg prints `bitrate=N/A` on every progress block for an RTSP output; the parser must not invent one ...
    s: dict = {}
    for line in ("fps=15.00\n", "bitrate=N/A\n", "total_size=N/A\n"):
        parse_progress(line, s)
    assert "bitrate" not in s
    # ... and the clip's own average (bytes * 8 / seconds / 1000) is what the stream carries with -c copy
    clip = tmp_path / "loop.mp4"
    clip.write_bytes(b"x" * 250_000)
    assert clip_bitrate_kbps(str(clip), 20) == 100.0
    assert clip_bitrate_kbps(str(tmp_path / "missing.mp4"), 20) is None
