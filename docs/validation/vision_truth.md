# The check video and what is true about it

The motion worker is measured against a video whose true answer is fixed by how the video is made, not by looking at it. The
three parts and the second at which each starts were chosen as times in the source.

## What was fetched (by hand, outside the build; hashes are of the files as fetched on 2026-10-25)

| File (under `var/`, which git does not track) | Bytes | sha256 |
|---|---|---|
| `bbb/BigBuckBunny_640x360.m4v.zip` | 121,284,117 | `7118242b6728d40c871479c5b3c0f0fb27d748089df15d7f1b469f297c74a2d6` |
| `bbb/BigBuckBunny_640x360.m4v` (from the zip) | 121,283,919 | `738e2f999860553d056dd79c952f58f63cbb73892a57c72342ce9e5330d9d2d7` |
| `models/detr-resnet-50/pytorch_model.bin` | 166,708,629 | `883e4b0a898b87cc8ce28b33e04f86413984f67f7a44e96de5d05473bb0931b4` |
| `models/detr-resnet-50/config.json` | 6,602 | `ea65a2708a8fb81c71fffb93c8f68e43b7f8f8ee7b931597e3f1a94fdf683d3b` (git blob `3d5049b0...0d88`) |
| `models/detr-resnet-50/preprocessor_config.json` | 401 | `6842c9a3895e41cfd135d0c2f925dd46a1224aeb6e943b040cf597f394a60791` (git blob `a249a9ae...d053`) |
| `check/000000039769.jpg` | 173,131 | `dea9e7ef97386345f7cff32f9055da4982da5471c48d575146c796ab4563b04e` |

The video's publisher gives no hash, so the one above is the value found when it was fetched (the size matches the publisher's
listing). The model files are revision `no_timm` (commit `70120ba84d68ca1211e007c4fb61d0cd5424be54`); the weights' hash is the one
the model repository lists, the two small files are checked by their git blob ids.

## How the check video is made

`var/bbb/check_clip.mp4`, 180 seconds, built with the ffmpeg of the camera image (`ccc-camera`, ffmpeg 8.1.2), nothing else.

| Seconds of the check video | Content | Motion? |
|---|---|---|
| 0 to 60 | the picture at 30.000 s of the source, held still | no |
| 60 to 120 | the source from 60.000 s to 120.000 s, as it is | yes |
| 120 to 180 | the picture at 150.000 s of the source, held still | no |

The source is 640x359 at 24 fps (read on 2026-10-28 from the file `BigBuckBunny_640x360.m4v` with `ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,r_frame_rate -of default=nw=1`, which printed `h264`, `640`, `359`, `24/1`); it is scaled to 640x360 and brought to 15 fps. The encoding is the one the simulated camera
uses (`services/camera/camera.py`, `make_loop_clip`): 15 fps, 640x360, `libx264` baseline, `-pix_fmt yuv420p`, `-g 30
-keyint_min 30 -sc_threshold 0`, no audio.

```
# the two held pictures: the first frame at or after the given second
ffmpeg -y -ss 30  -i BigBuckBunny_640x360.m4v -frames:v 1 -vf scale=640:360 still_30.png
ffmpeg -y -ss 150 -i BigBuckBunny_640x360.m4v -frames:v 1 -vf scale=640:360 still_150.png
# the video
ffmpeg -y -loop 1 -framerate 15 -t 60 -i still_30.png -ss 60 -t 60 -i BigBuckBunny_640x360.m4v -loop 1 -framerate 15 -t 60 -i still_150.png \
  -filter_complex "[0:v]scale=640:360,fps=15,format=yuv420p,setpts=PTS-STARTPTS[a];[1:v]scale=640:360,fps=15,trim=duration=60,format=yuv420p,setpts=PTS-STARTPTS[b];[2:v]scale=640:360,fps=15,format=yuv420p,setpts=PTS-STARTPTS[c];[a][b][c]concat=n=3:v=1:a=0[v]" \
  -map "[v]" -an -r 15 -c:v libx264 -preset veryfast -profile:v baseline -pix_fmt yuv420p -g 30 -keyint_min 30 -sc_threshold 0 check_clip.mp4
```

(`-preset veryfast` is the camera's; it was run as `docker run --rm --entrypoint ffmpeg -v <var/bbb>:/clip ccc-camera:2.0 ...`.)

Result (`ffprobe -count_frames`): h264 Constrained Baseline, 640x360, 15 fps, 2,700 frames, 180.000 s; 7,829,211 bytes.

| File | sha256 |
|---|---|
| `check_clip.mp4` | `2a7805c7aca5151c4c946bfb529ee98dadcc9296056ce532664b7a7c7b6e47d4` |
| `still_30.png` | `a33eb7f99aa7cc5151622d89f7a823389da2e2293de15a962aad0e847cd1484a` |
| `still_150.png` | `34f9c03b0b490c798cf83fba636c304291917842cc8abca77cac939539d4ab6d` |

## The true answer, second by second

Second `s` (the interval from `s` to `s+1` of the check video) is "motion" if `60 <= s < 120`, and "no motion" otherwise.

Not counted: the seconds next to the three joins (60, 120, and the wrap from 180 back to 0). A join is a scene cut, and the worker
needs a few frames to rebuild its picture of the background after one; the seconds inside the "end width" before and after each
join are left out of both the true answer and the measured one. The end width is the GOP of 2 s (a key frame every two seconds, as Apple's HLS authoring specification asks), plus 1 s (this margin is the author's design decision; no model was found for it), plus the seconds of the
frames the worker skips after a start or a cut (K = 15 frames = 1 second; the reasons are in
`docs/adr/0011-motion-and-classification.md`): **4 seconds on each side of a join**, so the seconds 0 to 3, 56 to 63, 116 to 123 and
176 to 179 are not counted.

Inside 60 to 120 the source has stretches where the picture itself is almost still (cuts to a quiet shot). They stay "motion" in
the true answer, because the answer is fixed by how the video was made and is not corrected after the worker was run. The
seconds the worker misses for that reason are reported as they come out in `vision_eval.md`.

The picture of the classification is not made here: its true answer is the model card's published output for the check picture.
