"""The classify worker, without torch: the model is stood in for by an object that answers as told, so what is checked is
everything around it (the channel order, the score limit, the loading check, the events that are put on the stream)."""
import json
import pathlib
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from vision import classify, common as c
from vision.classify_core import (BATCH_SIZE, MODEL_NAME, SCORE_MIN, Detection, Detector, ModelNotUsable,
                                  best_per_label, bgr_to_rgb, check_loading_info, filter_by_score, pick_frames,
                                  repaired_config, require_cuda)

T_ID, C_ID = "20000000-0000-4000-8000-00000000000b", "30000000-0000-4000-8000-0000000000b2"
NAME = "2026-10-25_09-00-00-000000.mp4"


def D(label, score):
    return Detection(label, score, (0.0, 0.0, 1.0, 1.0))


class Recording:
    """A model that remembers the pictures it was given and answers one prepared list for each."""

    def __init__(self, answers):
        self.answers, self.seen = list(answers), []

    def infer(self, rgb_images):
        self.seen.append([im.copy() for im in rgb_images])
        return [self.answers.pop(0) for _ in rgb_images]


def test_the_model_gets_red_green_blue_and_not_the_blue_green_red_of_opencv():
    bgr = np.zeros((2, 3, 3), np.uint8)
    bgr[..., 0], bgr[..., 1], bgr[..., 2] = 10, 20, 30        # blue 10, green 20, red 30
    model = Recording([[]])
    Detector(model).detect([bgr])
    (rgb,) = model.seen[0]
    assert (rgb[0, 0] == [30, 20, 10]).all()                  # red first
    assert (bgr[0, 0] == [10, 20, 30]).all()                  # the frame itself is untouched
    assert rgb.flags["C_CONTIGUOUS"] and (bgr_to_rgb(bgr) == rgb).all()


def test_only_a_score_above_the_limit_counts_not_at_it():
    assert SCORE_MIN == 0.9
    dets = [D("cat", 0.9), D("cat", 0.9000001), D("couch", 0.899), D("remote", 1.0)]
    assert [d.score for d in filter_by_score(dets)] == [0.9000001, 1.0]
    model = Recording([dets])
    assert [d.label for d in Detector(model).detect([np.zeros((2, 2, 3), np.uint8)])[0]] == ["cat", "remote"]


def test_one_event_per_name_in_a_picture_the_surest_one():
    best = best_per_label([D("cat", 0.95), D("remote", 0.99), D("cat", 0.999), D("remote", 0.91)])
    assert [(d.label, d.score) for d in best] == [("cat", 0.999), ("remote", 0.99)]
    assert best_per_label([]) == []


def test_the_pictures_are_given_to_the_model_in_batches_and_the_answers_stay_in_order():
    frames = [np.full((2, 2, 3), i, np.uint8) for i in range(BATCH_SIZE * 2 + 1)]
    model = Recording([[D(str(i), 0.95)] for i in range(len(frames))])
    out = Detector(model).detect(frames)
    assert [len(b) for b in model.seen] == [BATCH_SIZE, BATCH_SIZE, 1]
    assert [d[0].label for d in out] == [str(i) for i in range(len(frames))]
    assert BATCH_SIZE == 1          # a live product does not batch (the transformers pipelines guide, docs/adr/0011)


def test_a_larger_batch_size_gives_batches_of_that_size_and_the_answers_stay_in_order():
    frames = [np.full((2, 2, 3), i, np.uint8) for i in range(7)]
    model = Recording([[D(str(i), 0.95)] for i in range(len(frames))])
    out = Detector(model, batch_size=3).detect(frames)
    assert [len(b) for b in model.seen] == [3, 3, 1]
    assert [d[0].label for d in out] == [str(i) for i in range(len(frames))]


# ---------------------------------------------------------------- loading and the GPU

def test_the_worker_does_not_run_with_a_weight_missing_or_over_or_of_the_wrong_shape():
    check_loading_info({"missing_keys": set(), "unexpected_keys": set(), "mismatched_keys": set(), "error_msgs": []})
    for bad in ({"missing_keys": {"model.query_position_embeddings.weight"}}, {"unexpected_keys": ["x"]},
                {"mismatched_keys": [("w", (1,), (2,))]}):
        with pytest.raises(ModelNotUsable):
            check_loading_info({"missing_keys": set(), "unexpected_keys": set(), "mismatched_keys": set(), **bad})
    for not_a_report in (None, [], "ok"):
        with pytest.raises(ModelNotUsable):
            check_loading_info(not_a_report)


def test_the_worker_does_not_run_without_cuda():
    class Torch:
        class cuda:
            available = False

            @classmethod
            def is_available(cls):
                return cls.available

    with pytest.raises(ModelNotUsable):
        require_cuda(Torch)
    Torch.cuda.available = True
    require_cuda(Torch)


def test_the_null_in_the_config_of_the_no_timm_revision_is_the_librarys_default_and_nothing_else_changes():
    raw = json.loads((pathlib.Path(__file__).resolve().parents[2] / "var/models/detr-resnet-50/config.json")
                     .read_text(encoding="utf-8")) if (pathlib.Path(__file__).resolve().parents[2]
                                                       / "var/models/detr-resnet-50/config.json").exists() else \
        {"dilation": None, "d_model": 256, "use_timm_backbone": False}
    fixed = repaired_config(raw)
    assert fixed["dilation"] is False
    assert {k: v for k, v in fixed.items() if k != "dilation"} == {k: v for k, v in raw.items() if k != "dilation"}
    assert repaired_config({"dilation": True})["dilation"] is True       # a value that is set is left alone


# ---------------------------------------------------------------- the pictures of an interval

def test_for_each_second_of_the_interval_the_first_frame_at_or_after_it():
    frames = (np.full((1, 1, 3), i, np.uint8) for i in range(10_000))       # a generator: read from the start, in order
    got = pick_frames(frames, 15.0, [2, 3, 4])
    assert [int(f[0, 0, 0]) for _, f in got] == [30, 45, 60] and [t for t, _ in got] == [2.0, 3.0, 4.0]


def test_reading_stops_at_the_last_wanted_second_and_a_short_file_gives_what_it_has():
    read = []

    def frames():
        for i in range(1000):
            read.append(i)
            yield np.zeros((1, 1, 3), np.uint8)
    pick_frames(frames(), 15.0, [0, 1])
    assert read[-1] == 15                                       # frame 15 is the first of second 1: nothing after it is read
    got = pick_frames((np.zeros((1, 1, 3), np.uint8) for _ in range(20)), 15.0, [0, 1, 2, 3])
    assert [t for t, _ in got] == [0.0, 1.0]                    # the file has 20 frames: seconds 2 and 3 are not there
    assert pick_frames(iter([]), 15.0, [0]) == [] and pick_frames(iter([np.zeros((1, 1, 3))]), 15.0, []) == []


# ---------------------------------------------------------------- the worker

class Out:
    def __init__(self):
        self.added = []

    def add(self, stream, fields):
        self.added.append((stream, fields))


def entry(**kw):
    f = {"tenant_id": T_ID, "camera_id": C_ID, "segment_started_at": "2026-10-25T09:00:00+00:00", "file_name": NAME,
         "offset_s": "4", "length_s": "3"}
    f.update(kw)
    return f


def root_with_segment(tmp_path):
    root = tmp_path / "rec"
    f = root / T_ID / C_ID / NAME
    f.parent.mkdir(parents=True)
    f.write_bytes(b"")
    return root


def test_an_interval_gives_one_event_per_name_and_picture_with_the_time_of_the_picture(tmp_path):
    root, out, counters = root_with_segment(tmp_path), Out(), c.Counters()
    answers = [[D("cat", 0.97), D("cat", 0.93), D("remote", 0.95)], [], [D("couch", 0.99)]]
    opened = []

    def opener(path):
        opened.append(path)
        return 15.0, (np.zeros((4, 4, 3), np.uint8) for _ in range(10_000))

    handle = classify.make_handler(out, root, Detector(Recording(answers)), counters, opener=opener)
    handle(entry())
    assert opened == [root / T_ID / C_ID / NAME]
    base = datetime(2026, 10, 25, 9, 0, 0, tzinfo=timezone.utc)
    got = [(f["label"], f["score"], f["started_at"]) for _, f in out.added]
    assert got == [("cat", "0.9700", (base + timedelta(seconds=4)).isoformat()),
                   ("remote", "0.9500", (base + timedelta(seconds=4)).isoformat()),
                   ("couch", "0.9900", (base + timedelta(seconds=6)).isoformat())]
    stream, first = out.added[0]
    assert stream == c.EVENT_STREAM
    assert first == {"tenant_id": T_ID, "camera_id": C_ID, "kind": "object", "label": "cat", "score": "0.9700",
                     "started_at": first["started_at"], "duration_ms": "0", "model": MODEL_NAME}
    assert counters.values == {"pictures": 3, "object_events": 3}
    assert MODEL_NAME.endswith("70120ba84d68ca1211e007c4fb61d0cd5424be54")


def test_an_entry_with_a_bad_path_number_or_time_is_refused_and_no_file_is_opened(tmp_path):
    root, out = root_with_segment(tmp_path), Out()
    opened = []
    handle = classify.make_handler(out, root, Detector(Recording([])), c.Counters(),
                                   opener=lambda p: opened.append(p) or (15.0, iter([])))
    for bad in (entry(file_name="../x.mp4"), entry(offset_s="-1"), entry(length_s="0"), entry(length_s="99999"),
                entry(segment_started_at="2026-10-25T09:00:00"), entry(tenant_id="x"),
                entry(file_name="2026-10-25_09-01-00-000000.mp4")):
        with pytest.raises(c.Refused):
            handle(bad)
    assert opened == [] and out.added == []


def test_the_worker_does_not_run_when_the_loader_reports_an_error():
    with pytest.raises(ModelNotUsable, match="1 errors"):
        check_loading_info({"missing_keys": set(), "unexpected_keys": set(), "mismatched_keys": set(),
                            "error_msgs": ["size mismatch for class_labels_classifier.weight"]})
