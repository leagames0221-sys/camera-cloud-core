# What the motion and classify workers do on the check video and on the model card's picture

Measured on 2026-10-25 on the machine of the author (NVIDIA GeForce RTX 5080, Windows with Docker Desktop and WSL 2), with the code
of this repository, by `tools/vision_eval.py` (the tables are its printed output; the output was not kept as a file). The way the check video was made, and so the true answer, is in
[vision_truth.md](vision_truth.md); the numbers K, S, T below were fixed before this was run, from synthetic pictures
([ADR 0011](../adr/0011-motion-and-classification.md)), and **were not changed to fit the result**. On 2026-10-26 S was changed
from 0.5 to Frigate's value 0.8 (a cut is more than 0.8 of the picture, ADR 0011), not to fit the result; the movement part was then run
again (`tools/vision_eval.py motion`, on the CPU of the same machine, the same segments) and printed the same table and the same missed
seconds as below. No target value is set for the movement rates (no source was found for one; the numbers are given as they are).

## Values used

| Value | Used | Meaning |
|---|---|---|
| K | 15 frames (1 s at 15 fps) | the first frames of every new subtractor only teach it |
| S | 0.8 (0.5 on 2026-10-25; the same table with both) | a frame with more than this share of the picture in the foreground is a scene cut: start again |
| T | 0.005 (ZoneMinder's zone preset "Fast, medium sensitivity", ADR 0011) | a second whose median foreground share is above this is a motion second |
| MOG2 | history 500, varThreshold 16, detectShadows true | OpenCV's defaults |
| End width | 4 s on each side of a join | GOP 2 s + 1 s + K = 1 s; the seconds 0-3, 56-63, 116-123 and 176-179 are not counted |

## Movement: second by second against the way the video was made

The true answer: seconds 60 to 119 move, the other seconds do not (the video is a held picture there). The segments are cut from
`check_clip.mp4` with ffmpeg's segment muxer into pieces of 60 seconds (`tools/vision_eval.py segments`), the cuts shifted by 0, 20
and 40 seconds (a model is made for every segment, as in the service, so a segment boundary can fall anywhere). Counted seconds:
156 of 180.

| Cuts shifted by | Counted seconds | Motion seconds found (TP) | Missed (FN) | False alarms (FP) | Recall | Precision |
|---|---|---|---|---|---|---|
| 0 s | 156 | 46 | 6 | 0 | 0.885 | 1.000 |
| 20 s | 156 | 45 | 7 | 0 | 0.865 | 1.000 |
| 40 s | 156 | 45 | 7 | 0 | 0.865 | 1.000 |

Seconds missed: shift 0: 70, 71, 73, 85, 91, 92. Shift 20: 70, 71, 73, 80, 85, 91, 92. Shift 40: 70, 71, 73, 85, 91, 92, 100. No false alarm
in any of the three.

Reading it: 70, 71, 73, 85, 91 and 92 are missed in all three ways: in those seconds the share of changed pixels was not above T. The
film has stretches where little moves (a quiet shot; the pictures of those seconds were not looked at to find out), and the true
answer still calls them movement because it was fixed by how the video was made and is not corrected afterwards. The second missed only in one way (80 for the cuts at 20, 100 for the cuts at 40) is the first second of a new segment,
which a new subtractor only learns from. Nothing in the held pictures (seconds 4 to 55 and 124 to 175) was called movement.

## Naming: the model card's picture

The classify code (`Detector` with the real model, the picture read by OpenCV as BGR and turned to RGB as the worker does; OpenCV's `imgcodecs.hpp` says of `imread`: "In the case of color images, the decoded images will have the channels stored in **B G R** order.", and of `CAP_PROP_CONVERT_RGB`: "Boolean flags indicating whether images should be converted to BGR." in `videoio.hpp`)
on `000000039769.jpg`. The pass rule: the same names with the same counts, and every score and every coordinate of every box equal
to the card's up to the decimals the card prints (3 for a score, 2 for a coordinate), by numpy's rule for "equal up to desired
precision", `abs(desired-actual) < 1.5 * 10**(-decimal)` (`numpy.testing.assert_almost_equal`; ADR 0011). Run again on 2026-10-26
with that rule, in the image `ccc-vision:3.0` with the code of this repository mounted (`tools/vision_eval.py card`, printed output):

| Name | Card's score | Our score | Largest difference of a box coordinate (pixels) | Overlap (IoU) | Passes |
|---|---|---|---|---|---|
| remote | 0.998 | 0.9982 | 0.0082 | 0.9998 | yes |
| remote | 0.996 | 0.9960 | 0.0097 | 0.9996 | yes |
| couch | 0.995 | 0.9955 | 0.0033 | 1.0000 | yes |
| cat | 0.999 | 0.9988 | 0.0068 | 0.9999 | yes |
| cat | 0.999 | 0.9987 | 0.0111 | 0.9999 | yes |

Result: **pass** (remote 2, couch 1, cat 2; nothing else). The run of 2026-10-25, under the rule of that day (score and overlap at
least 0.9, a rule without a source), printed the same names, the same scores to three decimals and an overlap of 1.000 for each. The model was loaded with no missing, unexpected or wrongly shaped weight.

## Naming: one picture a second of the moving part of the check video (not graded)

Seconds 60 to 119 of the check video, the first frame of each second, named by the same code. **No score here is graded**: the film
is a cartoon, its main characters (a rabbit, a squirrel) are not among the 80 class names the model has (`id2label` in its `config.json` has 91 entries, 11 of them `N/A`), and the cartoon is far from the
training pictures; the names are what the model says, shown as they came.

pictures with nothing above 0.9: 18 of 60
pictures a name was found in: {"bird": 18, "person": 14, "apple": 7, "kite": 6, "dog": 4, "sheep": 4, "cat": 3, "umbrella": 2, "elephant": 1}

| Second of the video | Names and scores found (score above 0.9) |
|---|---|
| 60 | (nothing above 0.9) |
| 61 | (nothing above 0.9) |
| 62 | bird 0.997 |
| 63 | person 0.999 |
| 64 | (nothing above 0.9) |
| 65 | (nothing above 0.9) |
| 66 | dog 0.967 |
| 67 | (nothing above 0.9) |
| 68 | person 0.919 |
| 69 | bird 0.987 |
| 70 | (nothing above 0.9) |
| 71 | (nothing above 0.9) |
| 72 | person 0.998, dog 0.965 |
| 73 | person 0.998, dog 0.993 |
| 74 | (nothing above 0.9) |
| 75 | (nothing above 0.9) |
| 76 | umbrella 0.951 |
| 77 | (nothing above 0.9) |
| 78 | (nothing above 0.9) |
| 79 | cat 0.963 |
| 80 | kite 0.997, bird 0.984 |
| 81 | bird 0.938, kite 0.922 |
| 82 | dog 0.937 |
| 83 | bird 0.996 |
| 84 | bird 0.952 |
| 85 | apple 0.991, bird 0.944 |
| 86 | apple 0.942 |
| 87 | cat 0.998, apple 0.983 |
| 88 | bird 0.924 |
| 89 | apple 0.973 |
| 90 | apple 0.981 |
| 91 | bird 0.993, apple 0.976 |
| 92 | bird 0.937 |
| 93 | bird 0.924 |
| 94 | bird 0.959 |
| 95 | (nothing above 0.9) |
| 96 | sheep 0.932, bird 0.912 |
| 97 | bird 0.935, sheep 0.932 |
| 98 | bird 0.975, kite 0.975, sheep 0.953 |
| 99 | bird 0.978, kite 0.973, sheep 0.952 |
| 100 | person 0.994 |
| 101 | person 0.995, cat 0.980 |
| 102 | bird 0.977 |
| 103 | (nothing above 0.9) |
| 104 | person 0.982 |
| 105 | person 0.998 |
| 106 | person 0.986 |
| 107 | person 0.996 |
| 108 | person 0.909 |
| 109 | person 0.971, bird 0.916 |
| 110 | person 0.990, kite 0.978 |
| 111 | person 0.978, umbrella 0.915 |
| 112 | (nothing above 0.9) |
| 113 | kite 0.943 |
| 114 | (nothing above 0.9) |
| 115 | (nothing above 0.9) |
| 116 | elephant 0.995 |
| 117 | (nothing above 0.9) |
| 118 | apple 0.935 |
| 119 | (nothing above 0.9) |

## Time for a picture (GPU)

60 pictures (read on 2026-10-25, when the worker gave the model 8 pictures at a time; since 2026-10-26 it gives 1, ADR 0011); in batches of 8: 28.3 ms a picture (1.70 s in all); one at a time: 28.2 ms a picture; model loaded in 4.1 s

(The model's own time on the GPU, the picture already in memory, the classify worker running next to it on the same GPU. It does not
include reading the file, which the worker does from the start of a segment, in order.)
