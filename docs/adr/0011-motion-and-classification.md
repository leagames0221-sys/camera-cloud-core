# 0011. Movement first, then names: two workers that look at the recordings

## Status
Accepted (2026-10-25).

## Context
The service records every camera in segments of 60 seconds and keeps an index of them. The next question is what is in the
recordings. Naming the things in a picture with a neural network costs GPU time; most of what a camera records is a picture that
does not change. The recordings are in the video server's volume, the index is in the stamp's database, and the queue between
them (0007) already exists. The check of the work is done on a video whose true answer was fixed by how it was made
(`docs/validation/vision_truth.md`), and on the model card's published output.

## Decision
**Two steps and two workers.** `motion` (CPU, OpenCV) finds the seconds of a segment in which something moves. For each run of
such seconds it puts an entry on `ccc:motion` and a movement event on `ccc:events`. `classify` (GPU, PyTorch) takes the entries
of `ccc:motion`, takes one picture for each second of the run (the first frame at or after that second, reading the file from
its start, without seeking) and names what is in the pictures with DETR; it puts one event for each name and picture on
`ccc:events`. Movement is image processing and is not called AI anywhere in this project; only the second step is. A movement
event does not need the GPU, so it exists on a machine without one.

**The path.** The writer of stamp 1, after the commit that put a group of segments in the index and before it confirms the group,
announces on `ccc:recorded` every segment of the group that is in the index (also one that was there before), with the camera,
the start, the length and the file name, never a path. A crash between the commit and the announcement leaves the group
unconfirmed, so it is written again and announced again: at least once. A failure of the announcement itself is counted
(`recorded_not_announced`) and does not stop the writing. The reconciliation of the control plane (0005) tells the writer, over
the writer's internal door, which segments it added when a closed-segment call had been lost; the writer looks each of them up in
the index and announces those it finds. `motion` reads `ccc:recorded`; the events of both workers go to `ccc:events`; the writer
puts them in the table `events` (one more migration for the control database, which is stamp 1's, and one for the others).

**The table.** An event is unique by camera, kind, label and start time. `label` is `NOT NULL DEFAULT ''` (a movement event has
the empty text) because PostgreSQL's UNIQUE does not take two NULLs for equal. The row is written only if, in the same statement,
the camera exists, is not deleted and belongs to the stamp, and a segment of that camera that is not deleted contains the time of
the event and its end, with one second of slack for the last second of a segment (a segment whose length is not known counts as
120 seconds, the longest the index accepts); before that, events of a camera that is not in the list of cameras that are looked
at (below) are left out and counted. So a worker that was taken over can write only events of those cameras, at times for which a
recording is in the index, and no event longer than a segment. Writing the same event twice leaves one row. The key
has no box, so two cats in one picture are one event: the one with the higher score is kept.

**Which cameras.** The simulated cameras of the test pattern move all the time. `motion` therefore reads only the segments of the
cameras listed in `var/vision/cameras.txt` (mounted read only, read again for every entry). The camera of the check video is
registered through the API for company B by `tools/setup_check_camera.py` and put in that list; the seeded cameras and their list
are not changed. Only stamp 1 has the workers (there is one GPU); the form for another stamp is the same service with that stamp's
queue and `ANNOUNCE_RECORDED` on.

**What `motion` does.** The source is OpenCV's tutorial and sample for `BackgroundSubtractorMOG2`, with the default arguments of
`createBackgroundSubtractorMOG2` (history 500, varThreshold 16, detectShadows true). In its mask 255 is foreground and 127 a
shadow; only 255 is counted. The other parts are below. **S** follows Frigate's motion detector and **T** a zone preset of
ZoneMinder; the model per segment, **K** and taking the median of a second are the author's design decisions; no model was found for them (looked at: OpenCV's tutorial, sample and header, which give no such values;
Frigate's motion detector, whose values are for another method, see the table; ZoneMinder's "Warmup Frames", "how many frames the zma
analysis daemon should process but not examine when it starts", the same idea as K, which gives no number and is 0 by default
(`WarmupCount ... default '0'`, `db/zm_create.sql.in`)):

| Value | Choice | Why |
|---|---|---|
| model per segment | a new subtractor for every segment; the segment is read from its first frame | the segments are 60 seconds and are looked at one at a time, in no order; the model of the last one would not be there |
| K = 15 frames | the first K frames of a new subtractor only teach it (one second at 15 fps) | the first frame becomes the background and the model learns fastest at first (the learning rate is chosen automatically: 1/min(2 x frames seen, history), OpenCV's `bgfg_gaussmix2.cpp`); a second of frames is also the unit of the answer, so the first second of every segment is not judged |
| S = 0.8 | a frame in which more than 0.8 of the picture is foreground is a scene cut: the subtractor is made again from that frame, which is the first frame it learns from; that frame and the K - 1 frames after it are not judged | Frigate's motion detector (`frigate/motion/improved_motion.py`, 0.18.2) recalibrates "if calibrating or the motion contours are > 80% of the image area (lightning, ir, ptz)"; its setting `lightning_threshold` has the default 0.8 (`frigate/config/camera/motion.py`). Until 2026-10-26 the value was 0.5, which had no source. What the change means, from the trial (below): three pairs of unrelated pictures gave 62.5 %, 54.1 % and 33.7 %, so none of them is taken for a cut now; their first frames are foreground until the model takes the new picture in (not measured for a whole picture; in the trial a lasting change of 5 % of the picture was taken in after 8 frames). Frigate's documentation names the same trade: a lower value makes motion detection "more likely to ignore large amounts of motion such as a person approaching a doorbell camera". On the check video the movement table is the same with 0.8 as with 0.5 (`docs/validation/vision_eval.md`) |
| T = 0.005 | the value of a second is the median of the foreground share of its judged frames; a second above T is a motion second; runs of consecutive motion seconds are one interval | T is ZoneMinder's smallest alarmed area of the zone preset "Fast, medium sensitivity", 0.5 per cent of the zone (`ZonePresets` in `db/zm_create.sql.in`: check method `AlarmedPixels`, `MinAlarmPixels` 0.5, units `Percent`; its zone guide: "If the pixel count or area percentage is above the minimum Alarmed Area, but less than the maximum Alarmed Area, an alarm is triggered."). Of the three presets that use only the alarmed-pixels check, as this worker does, the high sensitivity one (0.1 %) is below the noise of a still picture in the trial and the low one (3 %) above every second of the 5 % square (both below); the medium one is the only one between them. The same order of size is Motion's default `threshold` of 1500 changed pixels at its default picture of 640 x 480 (0.49 %). The median of a second has no source. In the trial the second values of a still picture stay below T for noise of up to 6 grey levels (at most 0.41 %; one single frame reached 0.56 %), and a square of 5 % of the picture moving 3 pixels a frame is above T in every second (0.86 to 2.01 %). A square of 1 % is above T only in its later seconds when it is of one grey (0.31 to 0.81 %) and not at all when it is textured (0.19 to 0.43 %): the model takes the inside of a slow object into the background within a few frames, so only its moving edges count. Things smaller than a few percent of the picture that move slowly are not seen |

The trial is `tools/motion_trial.py` (run it to get the numbers above): the worker's own code on pictures of 640 x 360 made
from blurred noise with a fixed seed, a still one with Gaussian noise of 0, 2, 4 and 6 grey levels; a square that moves 3 pixels a
frame, of 0.2 %, 1 % and 5 % of the picture, of one grey and textured; three pairs of unrelated pictures for the cut; a slow
brightening of one grey level in ten frames (no foreground at all); a change that stays, absorbed after 8 frames by a model that
has seen 30 frames (the fast early learning rate), which is why only the first frames of a change show. The values were chosen
earlier with a script that was not kept; the numbers this record gave for it (a 1 % square at 1.0 %, unrelated pictures at 92 to
98 %) are not what the kept script gives, so they were replaced by its output and the values were not changed. **K, S and T were not
adjusted to the check video's answer** (`docs/validation/vision_eval.md` was written after them; S was changed afterwards to Frigate's
value, not to the answer, and the movement part was run again with it).

**The ends of the check.** Around each join of the check video (and the start of a segment) the answer is not counted for a width
of 2 s (a GOP, the camera keeps a key frame every 2 seconds, as Apple's HLS authoring specification asks: "Key frames (IDRs) SHOULD
be present every two seconds.") + 1 s + K frames = 1 s, that is 4 seconds on each side (the extra 1 s is the author's design decision; no model was found for it, and it decides which seconds the measured recall counts).

**What `classify` does.** `DetrImageProcessor` and `DetrForObjectDetection` as in the model card's example, with `threshold=0.9`
(the card: keep detections with a score above 0.9) and a second filter in our code that also takes only a score above 0.9, so
that the boundary is ours and tested. The picture is turned from BGR (OpenCV: "the decoded images will have the channels stored in **B G R** order", `imgcodecs.hpp`) to RGB before it goes to the processor, because the
card's example opens the picture with PIL. The pictures are given to the model **one at a time**: the pipelines guide of transformers v5.19.0 says, in its rule of thumb for
batching, "If you are latency constrained (live product doing inference), don't batch." (the line before it: "Measure performance
on your load, with your hardware."). That line is about live inference; this worker reads recordings, so applying it here is the author's reading. Until 2026-10-26 they were given 8 at a time, with no model; the highest memory of the
container (about 2.2 GiB, 0012) and the times in `docs/validation/vision_eval.md` were read with 8 and are history, not the figures of
one at a time (the memory is read again, 0012). Looked at and not taken: transformers' DETR page, whose "batch size of 2 per GPU" is
about the memory of training. The revision is `no_timm`
(commit 70120ba84d68ca1211e007c4fb61d0cd5424be54), the one the card names for use without the timm package; the file is in
`var/models/`, mounted read only, and the libraries are told not to go online (`HF_HUB_OFFLINE=1`). Two checks stop the worker at
start: no CUDA device, and `from_pretrained(..., output_loading_info=True)` reporting any missing, unexpected or wrongly shaped
weight (a missing backbone weight would silently stay random). The `config.json` of that revision has `"dilation": null` where
transformers 5.19 wants a boolean and documents the default `False` (the main revision's file says false); only that value is
replaced before the model is built, and the loading check is what shows that the weights still fit. Each event carries the name,
the score and the model with its commit.

**Both workers** take one entry at a time, confirm it after the handler returned, take an entry again that is not confirmed in 30
seconds (looking every 30 seconds, as the writer does, 0007), and move one that has been handed out more than 5 times to a dead stream of their own (`ccc:dead:motion`,
`ccc:dead:classify`; the rules of 0007, the reason for separate streams in 0013). They open only
`<recordings>/<tenant>/<camera>/<name>.mp4` with ids that are lower case UUIDs and a name of the shape MediaMTX gives; anything
else is refused before a file is opened.

## Alternatives not chosen
- **YOLO of Ultralytics.** AGPL-3.0, with a separate licence for use in a service (see 0001). DETR is Apache-2.0.
- **Running DETR through ONNX Runtime.** From version 1.27 the `onnxruntime-gpu` package of PyPI is built with CUDA 13.0 by
  default (ONNX Runtime's CUDA execution provider page). An image with it might be lighter than one with PyTorch; that was not
  measured. The built image of this decision is 9.54 GB, of which the Python packages are 5.9 GB: `nvidia` 3.2 GB, `torch` 1.2 GB,
  `triton` 0.9 GB. An ONNX image was not built, so its size is not measured here. It was not chosen because the exported model
  would have to be shown to give the same answer as the model card (one more step of checking), and PyTorch on the card's own
  example gives the published output as it is.
- **Naming every segment.** It would run the GPU for the whole recording of every camera. Movement first keeps it to the
  seconds in which something changes.
- **Joining intervals across segments.** Not done (see Not built): the model of a segment does not know the one before.

## Consequences
- The events live longer than the recordings (MediaMTX deletes after a day, the events are not deleted). The list marks an event
  whose video is gone and the route to play it answers 410 (0004).
- A moving object that stops is absorbed into the background within seconds, and a scene in which more than 0.8 of the picture changes at
  once is a cut and not movement (a smaller change of the whole scene is movement for its first frames); a long still shot inside a moving video is "no motion" for the worker, while the answer of the check video counts it
  as motion because the video was made that way (the misses are reported as they are).
- The first second of every segment, and of every scene after a cut, is not judged, so a movement that begins in it is seen one
  second late at best.
- The announcement of the segments that the reconciliation added is not repeated: if the writer cannot be reached when the
  reconciliation tells it, those segments are in the index and are not looked at.
- DETR's classes are COCO's: the rabbit, squirrel and other animals of the check video are not among them, and a cartoon is far
  from its training pictures, so the names that come out for it are shown as they are and not graded.
- The check of the model card's output depends on GPU arithmetic. The card prints scores to three decimals and boxes to two, so
  "the same as the card" is taken as numpy's "equal up to desired precision": `numpy.testing.assert_almost_equal` "verifies that the
  elements of `actual` and `desired` satisfy `abs(desired-actual) < float64(1.5 * 10**(-decimal))`", with decimal 3 for a score and 2
  for each coordinate of a box (numpy's own note recommends `assert_allclose` for comparing computed floating point numbers; the card's
  numbers are rounded decimals, which is what a number of decimals describes). The rule was chosen before the run that it judges; on
  2026-10-26 the largest differences were 0.0005 for a score and 0.0111 pixels for a coordinate (`docs/validation/vision_eval.md`).
  Until 2026-10-26 the rule was "score at least 0.9, overlap of the boxes at least 0.9", which had no source. Looked at as well:
  transformers' own test of this model (`tests/models/detr/test_modeling_detr.py`, v5.19.0) compares scores and boxes with
  `rtol=2e-4, atol=2e-4`, but against its own unrounded values for the main revision (timm backbone) and a PNG of the picture, so that
  tolerance cannot be used against the card's rounded numbers; COCO's IoU thresholds (0.50 to 0.95) judge a detector against the
  truth, not a second run of the same model.

## Not built
- Workers on the second stamp (one GPU; only the form is written above). Joining movement across segments.
- Training or fine-tuning, tracking of things from picture to picture, recognising faces or people, notifications about events,
  searching events (the screen only lists them).
- A load test of the workers: only the time for a picture is recorded (`docs/validation/vision_eval.md`).
- A time limit for the events of their own (they outlive the recordings; an old one only says its video is gone).
- Naming on the CPU when there is no GPU: the worker stops. What it gives is shown in `vision_eval.md` and in the pictures of the
  README for those who have no GPU.
