"""The pure parts of tools/vision_eval.py: the true answer of the check video, the scoring, and the judgement of the card's output."""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("vision_eval", ROOT / "tools/vision_eval.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)

from vision.classify_core import Detection  # noqa: E402


def test_the_true_answer_is_how_the_video_was_made_and_the_seconds_at_the_joins_are_not_counted():
    assert [s for s in range(180) if ev.is_motion(s)] == list(range(60, 120))
    counted = [s for s in range(180) if ev.is_counted(s)]
    assert 59 not in counted and 56 not in counted and 55 in counted and 64 in counted and 63 not in counted
    assert 116 not in counted and 123 not in counted and 124 in counted and 115 in counted
    assert [s for s in range(180) if not ev.is_counted(s)] == [0, 1, 2, 3, 56, 57, 58, 59, 60, 61, 62, 63, 116, 117, 118, 119,
                                                                120, 121, 122, 123, 176, 177, 178, 179]


def test_scoring_counts_each_kind_of_second_and_ignores_the_uncounted_ones():
    predicted = set(range(70, 100)) | {10, 130} | {62}          # 62 is next to a join: not counted
    r = ev.score_seconds(predicted)
    assert (r["tp"], r["fp"], r["fn"]) == (30, 2, 22 - 0)
    assert r["tp"] + r["fn"] == 52 and r["recall"] == 30 / 52 and r["precision"] == 30 / 32
    assert r["false_alarm"] == [10, 130] and r["missed"][0] == 64
    assert ev.score_seconds(set())["recall"] == 0.0 and ev.score_seconds(set())["precision"] is None


def test_the_cuts_of_the_three_ways():
    assert ev.cut_times(0) == [60, 120] and ev.cut_times(20) == [20, 80, 140] and ev.cut_times(40) == [40, 100, 160]


def test_overlap_of_boxes():
    assert ev.iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert ev.iou((0, 0, 10, 10), (10, 0, 20, 10)) == 0.0
    assert abs(ev.iou((0, 0, 10, 10), (5, 0, 15, 10)) - 1 / 3) < 1e-9


def D(name, score, box):
    return Detection(name, score, box)


CARD_OUT = [D(n, s, b) for n, s, b in ev.CARD]


def test_the_cards_own_output_passes_and_the_order_does_not_matter():
    assert ev.judge_card(CARD_OUT)["pass"]
    assert ev.judge_card(list(reversed(CARD_OUT)))["pass"]


def test_a_missing_extra_wrong_or_displaced_thing_fails():
    assert not ev.judge_card(CARD_OUT[:-1])["pass"]                                       # a cat is missing
    assert not ev.judge_card(CARD_OUT + [D("dog", 0.95, (0, 0, 5, 5))])["pass"]            # a name the card does not have
    low = [D(n, 0.89 if n == "couch" else s, b) for n, s, b in ev.CARD]
    assert not ev.judge_card(low)["pass"]                                                  # a score far from the card's
    moved = [D(n, s, (b[0] + 60, b[1], b[2] + 60, b[3])) if n == "couch" else D(n, s, b) for n, s, b in ev.CARD]
    assert not ev.judge_card(moved)["pass"]                                                # a box that moved away


def shifted(score_by: float = 0.0, box_by: float = 0.0):
    return [D(n, s + score_by, (b[0], b[1], b[2] + box_by, b[3])) if n == "couch" else D(n, s, b) for n, s, b in ev.CARD]


def test_equal_up_to_the_printed_decimals_is_numpys_rule_and_a_little_more_fails():
    # numpy.testing.assert_almost_equal: abs(desired - actual) < 1.5 * 10**(-decimal); 3 decimals for scores, 2 for boxes
    assert ev.judge_card(shifted(score_by=0.0014))["pass"] and ev.judge_card(shifted(score_by=-0.0014))["pass"]
    assert not ev.judge_card(shifted(score_by=0.0016))["pass"]
    assert ev.judge_card(shifted(box_by=0.014))["pass"] and not ev.judge_card(shifted(box_by=0.016))["pass"]


def test_the_output_measured_on_the_gpu_passes():
    # the unrounded output of the classify code on the card's picture (docs/validation/vision_eval.md, 2026-10-26)
    measured = [D("remote", 0.998218834400177, (40.159, 70.8096, 175.5461, 117.9882)),
                D("remote", 0.9960122108459473, (333.2366, 72.5542, 368.3219, 187.6697)),
                D("couch", 0.9954861998558044, (-0.0221, 1.1504, 639.7267, 473.7626)),
                D("cat", 0.9988009929656982, (13.2468, 52.0512, 314.0164, 470.9354)),
                D("cat", 0.9986775517463684, (345.4008, 23.8611, 640.3723, 368.7155))]
    assert ev.judge_card(measured)["pass"]
