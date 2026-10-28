"""The motion worker: the finding of the seconds with movement (numpy pictures, OpenCV's background subtraction), the checks on
what is read from the stream, and what the worker puts on the streams."""
import pathlib
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from vision import common as c
from vision import motion
from vision.motion_core import MotionParams, SecondValue, foreground_share, motion_intervals, second_values

H, W, FPS = 90, 160, 15


def scene(seed: int) -> np.ndarray:
    """A picture with some structure (blurred noise), values 40..215, three channels."""
    import cv2
    r = np.random.default_rng(seed)
    img = cv2.GaussianBlur(r.integers(0, 256, (H, W, 3), dtype=np.uint8), (0, 0), 8)
    return cv2.normalize(img, None, 40, 215, cv2.NORM_MINMAX)


def noisy(base: np.ndarray, n: int, sigma: float = 2.0, seed: int = 0):
    r = np.random.default_rng(seed)
    for _ in range(n):
        yield np.clip(base.astype(np.float32) + r.normal(0, sigma, base.shape), 0, 255).astype(np.uint8)


def jumping(frames, side: int, scale: float | None = None, seed: int = 9):
    """In every frame a square of the given size somewhere else: movement that does not repeat, so that the subtractor cannot
    learn it as background (a square that goes back and forth over the same places is learned within seconds). The square has a
    colour of its own, or, with `scale`, is the picture itself made darker by that factor (a shadow or a dark object)."""
    r = np.random.default_rng(seed)
    for f in frames:
        f = f.copy()
        x, y = int(r.integers(0, W - side)), int(r.integers(0, H - side))
        if scale is None:
            f[y:y + side, x:x + side] = r.integers(0, 256, 3, dtype=np.uint8)
        else:
            f[y:y + side, x:x + side] = (f[y:y + side, x:x + side] * scale).astype(np.uint8)
        yield f


# ---------------------------------------------------------------- the seconds

def test_a_still_noisy_picture_has_no_motion_second():
    values = second_values(noisy(scene(1), 5 * FPS), FPS)
    assert len(values) == 5 and motion_intervals(values) == []
    assert all(v.share is None or v.share <= 0.005 for v in values)


def test_the_first_frames_only_teach_the_subtractor_so_the_first_second_has_no_value():
    values = second_values(jumping(noisy(scene(2), 4 * FPS), 40), FPS)
    assert values[0].share is None                 # K = 15 frames = the whole first second
    assert all(v.share is not None for v in values[1:])


def test_a_moving_object_makes_motion_seconds_and_they_are_joined_into_one_interval():
    values = second_values(jumping(noisy(scene(3), 6 * FPS), 40), FPS)      # 40x40 of 160x90 = 11 % of the picture
    assert [v.share > 0.005 for v in values[1:]] == [True] * 5
    assert motion_intervals(values) == [(1, 5)]


def test_a_scene_cut_rebuilds_the_subtractor_so_the_new_picture_is_not_seen_as_movement():
    a, b = scene(4), scene(5)
    frames = list(noisy(a, 3 * FPS, seed=1)) + list(noisy(b, 4 * FPS, seed=2))
    values = second_values(frames, FPS)
    # seconds 3 (the cut, skipped after the rebuild) to the end: nothing moves
    assert motion_intervals(values) == []
    # without the rebuild the old picture is still the background and the new one is "foreground" for a long time
    no_rebuild = second_values(frames, FPS, MotionParams(cut_share=2.0))
    assert any(v.share is not None and v.share > 0.5 for v in no_rebuild[3:])


def test_a_shadow_is_not_foreground_but_a_dark_object_is():
    base = np.full((H, W, 3), 180, np.uint8)

    shadow = second_values(jumping([base] * 75, 30, scale=0.8), FPS)       # darker by a fifth: MOG2 calls it a shadow (127)
    dark = second_values(jumping([base] * 75, 30, scale=0.3), FPS)         # much darker: foreground (255)
    assert motion_intervals(shadow) == [] and all(v.share in (None, 0.0) for v in shadow)
    assert motion_intervals(dark) != [] and dark[2].share > 0.05


def test_the_mask_value_127_is_not_counted_and_255_is():
    mask = np.zeros((10, 10), np.uint8)
    mask[0, :5] = 127
    mask[1, :3] = 255
    assert foreground_share(mask) == 0.03


# ---------------------------------------------------------------- the boundaries (with a subtractor that answers as told)

class Scripted:
    """A subtractor that returns a prepared mask for each frame it is given (frames are the numbers of the masks)."""

    made = 0

    def __init__(self, masks):
        self.masks = masks
        Scripted.made += 1

    def apply(self, frame):
        return self.masks[int(frame[0, 0, 0])]


def masks_with(fg_pixels: int) -> np.ndarray:
    m = np.zeros((100, 100), np.uint8)
    m.flat[:fg_pixels] = 255
    return m


def run_scripted(fg_pixels: list[int], params: MotionParams):
    masks = [masks_with(n) for n in fg_pixels]
    frames = [np.full((100, 100, 3), i, np.uint8) for i in range(len(masks))]
    Scripted.made = 0
    values = second_values(frames, FPS, params, new_subtractor=lambda p: Scripted(masks))
    return values, Scripted.made


P0 = MotionParams(skip_frames=0)       # judge every frame; T = 0.005 of 10 000 pixels = 50 pixels, S = 0.8 = 8 000 pixels


def test_a_second_is_motion_only_above_the_threshold_not_at_it():
    at, _ = run_scripted([50] * 15, P0)
    above, _ = run_scripted([51] * 15, P0)
    assert motion_intervals(at, P0) == [] and motion_intervals(above, P0) == [(0, 1)]


def test_the_value_of_a_second_is_the_median_of_its_frames():
    most_high, _ = run_scripted([500] * 8 + [0] * 7, P0)
    most_low, _ = run_scripted([500] * 7 + [0] * 8, P0)
    assert most_high[0].share == 0.05 and most_low[0].share == 0.0


def test_a_frame_with_more_than_s_of_the_picture_in_the_foreground_is_a_cut_and_s_itself_is_not():
    # Frigate recalibrates when the moving share is above lightning_threshold (`pct_motion > ...`), not at it
    _, above_s = run_scripted([0] * 3 + [8001] + [0] * 3, MotionParams(skip_frames=1))
    _, at_s = run_scripted([0] * 3 + [8000] + [0] * 3, MotionParams(skip_frames=1))
    assert above_s == 2 and at_s == 1                  # the subtractor was made again above S, not at it


def test_a_cut_frame_is_not_counted_and_the_next_k_frames_are_not_judged():
    p = MotionParams(skip_frames=3)
    # frames 0-2 only teach; frame 15 is a cut (90 % of the picture); frames 15, 16, 17 teach the new subtractor and are not
    # judged; frames 18 to 29 are: six with 40 % and six with nothing, whose median is the mean of the two middle ones, 0.2.
    # (Had the frames 16 and 17 been judged, eight of the fourteen would be high and the median 0.4.)
    values, _ = run_scripted([0] * 15 + [9000] + [4000] * 8 + [0] * 6 + [0] * 15, p)
    assert values[0].share == 0.0
    assert values[1].share == pytest.approx(0.2)


def test_intervals_join_consecutive_seconds_and_a_second_without_a_value_ends_a_run():
    v = [SecondValue(i, s) for i, s in enumerate([None, 0.0, 0.1, 0.2, 0.0, 0.3, None, 0.4, 0.4])]
    assert motion_intervals(v) == [(2, 2), (5, 1), (7, 2)]
    assert motion_intervals([]) == []


# ---------------------------------------------------------------- reading a file

def test_a_video_file_is_read_in_order_with_its_frame_rate(tmp_path):
    import cv2
    from vision.video import open_frames
    path = tmp_path / "t.mp4"
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 15, (64, 48))
    if not out.isOpened():
        pytest.skip("this OpenCV build cannot write mp4")
    for i in range(30):
        out.write(np.full((48, 64, 3), i * 8, np.uint8))
    out.release()
    fps, frames = open_frames(path)
    got = list(frames)
    assert fps == 15.0 and len(got) == 30 and got[0].shape == (48, 64, 3)
    assert got[0].mean() < got[-1].mean()
    with pytest.raises(c.Refused):
        open_frames(tmp_path / "missing.mp4")


# ---------------------------------------------------------------- what is read from a stream

T_ID, C_ID = "20000000-0000-4000-8000-00000000000b", "30000000-0000-4000-8000-0000000000b2"
NAME = "2026-10-25_09-00-00-000000.mp4"


def test_a_segment_path_is_only_tenant_camera_file_inside_the_recordings(tmp_path):
    root = tmp_path / "recordings"
    assert c.segment_path(root, T_ID, C_ID, NAME) == root / T_ID / C_ID / NAME
    for bad in (("../x", C_ID, NAME), (T_ID, "..", NAME), (T_ID.upper(), C_ID, NAME), ("/etc", C_ID, NAME),
                (T_ID, C_ID, "../" + NAME), (T_ID, C_ID, "/etc/passwd"), (T_ID, C_ID, "a/" + NAME),
                (T_ID, C_ID, "..\\" + NAME), (T_ID, C_ID, "C:\\x.mp4"), (T_ID, C_ID, NAME + "/.."),
                (T_ID, C_ID, "x.mp4"), (T_ID, C_ID, None), (None, C_ID, NAME), (T_ID, C_ID, NAME[:-4] + ".ts")):
        with pytest.raises(c.Refused):
            c.segment_path(root, *bad)


def test_the_list_of_cameras_that_are_looked_at(tmp_path):
    f = tmp_path / "cameras.txt"
    assert c.read_allowed(f) == set()                   # no file: none
    f.write_text(f"# the check camera\n{C_ID}  # B company\n\nnot-a-uuid\n{C_ID.upper()}\n", encoding="utf-8")
    assert c.read_allowed(f) == {uuid.UUID(C_ID)}


def test_times_and_numbers_from_a_stream_are_checked():
    assert c.parse_time("2026-10-25T09:00:00+09:00") == datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc)
    for bad in (None, "", "yesterday", "2026-10-25T09:00:00"):
        with pytest.raises(c.Refused):
            c.parse_time(bad)
    assert c.parse_int("5", 0, 10) == 5
    for bad in ("x", None, "11", "-1"):
        with pytest.raises(c.Refused):
            c.parse_int(bad, 0, 10)


# ---------------------------------------------------------------- the worker

class Out:
    def __init__(self):
        self.added: list[tuple[str, dict]] = []

    def add(self, stream, fields):
        self.added.append((stream, fields))


def segment_file(tmp_path) -> pathlib.Path:
    root = tmp_path / "rec"
    f = root / T_ID / C_ID / NAME
    f.parent.mkdir(parents=True)
    f.write_bytes(b"")
    return root


def opener_with_motion(seconds_still: int, seconds_moving: int):
    def opener(path):
        still = list(noisy(scene(7), seconds_still * FPS, seed=3))
        moving = list(jumping(noisy(scene(7), seconds_moving * FPS, seed=4), 40))
        return float(FPS), iter(still + moving)
    return opener


def recorded(**kw):
    f = {"tenant_id": T_ID, "camera_id": C_ID, "started_at": "2026-10-25T09:00:00+00:00", "duration_ms": "60000",
         "file_name": NAME}
    f.update(kw)
    return f


def test_a_segment_of_a_camera_in_the_list_gives_a_motion_entry_and_a_motion_event(tmp_path):
    root, out, counters = segment_file(tmp_path), Out(), c.Counters()
    cams = tmp_path / "cameras.txt"
    cams.write_text(C_ID + "\n", encoding="utf-8")
    handle = motion.make_handler(out, root, cams, counters, opener=opener_with_motion(4, 5))
    handle(recorded())
    (s1, entry), (s2, event) = out.added
    assert (s1, s2) == (c.MOTION_STREAM, c.EVENT_STREAM)
    # 4 still seconds (the first of them is only for teaching), then 5 seconds of movement
    first = int(entry["offset_s"])
    assert (first, int(entry["length_s"])) == (4, 5)
    assert entry["segment_started_at"] == "2026-10-25T09:00:00+00:00" and entry["file_name"] == NAME
    at = datetime(2026, 10, 25, 9, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=first)
    assert event["started_at"] == at.isoformat() and event["kind"] == "motion" and event["label"] == ""
    assert event["duration_ms"] == str(int(entry["length_s"]) * 1000) == entry["duration_ms"]
    assert event["score"] == "" and event["tenant_id"] == T_ID and event["camera_id"] == C_ID
    assert counters.values["motion_intervals"] == 1


def test_a_camera_that_is_not_in_the_list_is_confirmed_without_being_looked_at(tmp_path):
    root, out, counters = segment_file(tmp_path), Out(), c.Counters()
    called = []
    handle = motion.make_handler(out, root, tmp_path / "no_such_list.txt", counters,
                                 opener=lambda p: called.append(p) or (15.0, iter([])))
    handle(recorded())
    assert out.added == [] and called == [] and counters.values == {"camera_not_looked_at": 1}


def test_a_path_that_is_not_a_segment_is_refused_before_a_file_is_opened(tmp_path):
    root, out, counters = segment_file(tmp_path), Out(), c.Counters()
    cams = tmp_path / "cameras.txt"
    cams.write_text(C_ID + "\n", encoding="utf-8")
    opened = []
    handle = motion.make_handler(out, root, cams, counters, opener=lambda p: opened.append(p) or (15.0, iter([])))
    for bad in (recorded(file_name="../../etc/passwd"), recorded(file_name="x.mp4"), recorded(tenant_id="..")):
        with pytest.raises(c.Refused):
            handle(bad)
    with pytest.raises(c.Refused):         # a file that is not there (deleted after a day)
        handle(recorded(file_name="2026-10-25_09-01-00-000000.mp4"))
    assert opened == [] and out.added == []


# ---------------------------------------------------------------- the stream rules (Valkey stood in for)

class FakeValkey:
    """The parts of a Valkey stream with one consumer group that the workers use."""

    def __init__(self):
        self.streams: dict[str, list[tuple[str, dict]]] = {}
        self.delivered: dict[str, int] = {}
        self.pending: dict[str, dict[str, list]] = {}
        self.n = 0

    def xgroup_create(self, stream, group, id="0", mkstream=False):
        if (stream, group) in self.delivered:
            raise RuntimeError("BUSYGROUP Consumer Group name already exists")
        self.streams.setdefault(stream, [])
        self.delivered[(stream, group)] = 0

    def xadd(self, stream, fields, maxlen=None, approximate=True):
        self.n += 1
        eid = f"{1700000000000 + self.n}-0"
        self.streams.setdefault(stream, []).append((eid, dict(fields)))
        return eid

    def xreadgroup(self, group, consumer, streams, count=None, block=None):
        out = []
        for stream in streams:
            start = self.delivered.get((stream, group), 0)
            entries = self.streams.get(stream, [])[start:start + (count or 10)]
            self.delivered[(stream, group)] = start + len(entries)
            for eid, f in entries:
                self.pending.setdefault(stream, {})[eid] = [f, 1]
            if entries:
                out.append([stream, entries])
        return out

    def xack(self, stream, group, *ids):
        for i in ids:
            self.pending.get(stream, {}).pop(i, None)

    def xautoclaim(self, stream, group, consumer, min_idle, start, count=None):
        entries = []
        for eid, rec in sorted(self.pending.get(stream, {}).items())[:count]:
            rec[1] += 1
            entries.append((eid, rec[0]))
        return ["0-0", entries, []]

    def xpending_range(self, stream, group, mn, mx, count):
        return [{"message_id": e, "times_delivered": r[1]} for e, r in sorted(self.pending.get(stream, {}).items())
                if mn <= e <= mx]


def test_an_entry_is_confirmed_after_its_handler_returned_and_kept_when_it_failed():
    r = FakeValkey()
    s = c.Streams(r, "motion", "m1")
    s.ensure_group("ccc:recorded")
    s.ensure_group("ccc:recorded")                      # the second call is the BUSYGROUP case: no error
    for n in range(3):
        s.add("ccc:recorded", {"n": str(n)})
    seen = []

    def handler(f):
        seen.append(f["n"])
        if f["n"] == "1":
            raise RuntimeError("boom")
        if f["n"] == "2":
            raise c.Refused("not for us")

    counters = c.Counters()
    c.handle_entries(s, "ccc:recorded", s.take("ccc:recorded", 10, None), handler, counters)
    assert seen == ["0", "1", "2"]
    assert list(r.pending["ccc:recorded"]) == ["1700000000002-0"]       # only the one that failed is still waiting
    assert counters.values == {"handled": 2, "errors": 1, "refused": 1}


def test_an_entry_that_keeps_failing_is_tried_up_to_the_5th_handing_out_and_buried_at_the_6th():
    r = FakeValkey()
    s = c.Streams(r, "motion", "m1")
    s.ensure_group("ccc:recorded")
    s.add("ccc:recorded", {"x": "y"})
    counters = c.Counters()

    def failing(f):
        raise RuntimeError("never works")

    c.handle_entries(s, "ccc:recorded", s.take("ccc:recorded", 1, None), failing, counters)      # handing out no. 1
    for _ in range(4):                                                                         # nos. 2 to 5: again
        c.take_again(s, "ccc:recorded", failing, counters)
    assert r.pending["ccc:recorded"] and "ccc:dead" not in r.streams
    c.take_again(s, "ccc:recorded", failing, counters)                                         # no. 6: given up
    assert r.pending["ccc:recorded"] == {}
    (dead,) = r.streams["ccc:dead"]
    assert dead[1]["stream"] == "ccc:recorded" and counters.values["dead"] == 1


def test_an_entry_given_up_on_goes_to_the_dead_stream_the_streams_were_made_with():
    r = FakeValkey()
    s = c.Streams(r, "motion", "m1", c.DEAD_STREAMS["motion"])
    s.ensure_group("ccc:recorded")
    s.add("ccc:recorded", {"x": "y"})
    s.take("ccc:recorded", 1, None)
    s.bury("ccc:recorded", [("1-0", {"x": "y"})])
    assert "ccc:dead" not in r.streams and r.streams["ccc:dead:motion"][0][1]["stream"] == "ccc:recorded"


def test_the_two_workers_make_their_streams_with_their_own_dead_stream(monkeypatch):
    from vision import classify, classify_core
    made = []

    class Recording(c.Streams):
        def __init__(self, *a):
            made.append(a)
            super().__init__(*a)

    monkeypatch.setattr(c, "Streams", Recording)
    monkeypatch.setattr(c, "run_loop", lambda *a, **k: None)
    monkeypatch.setattr(motion, "build_client", lambda: FakeValkey())
    monkeypatch.setattr(classify, "build_client", lambda: FakeValkey())
    monkeypatch.setattr(classify_core.DetrBackend, "load", classmethod(lambda cls, d: object()))
    monkeypatch.setattr("signal.signal", lambda *a: None)
    assert motion.main() == 0 and classify.main() == 0
    assert [a[1:] for a in made] == [("motion", "motion-1", "ccc:dead:motion"), ("motion", "motion-1", "ccc:dead:motion"),
                                     ("classify", "classify-1", "ccc:dead:classify")]
