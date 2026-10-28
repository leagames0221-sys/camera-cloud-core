# Third-party notices

This repository's own code is original work, under the MIT licence (`LICENSE`). The projects below are used as software that runs next to it, or are the
source of the design ideas named in `docs/sources.md`. Their licence texts are reproduced here as those licences require.
Nothing from a project under a copyleft or non-commercial licence has been copied (see `docs/sources.md`: ideas only).

| Project | What is used | Licence |
|---|---|---|
| MediaMTX 1.21.2 | runs as a container image (`bluenviron/mediamtx:1.21.2-ffmpeg`); settings and behaviour described in its documentation and source were followed | MIT |
| Keycloak 26.8.1 | runs as a container image (`quay.io/keycloak/keycloak:26.8.1`) | Apache-2.0 |
| keycloak-js 26.2.4 | `keycloak.js` is served to the browser, unmodified | Apache-2.0 |
| hls.js 1.7.3 | `hls.min.js` is served to the browser, unmodified | Apache-2.0 |
| Eclipse Mosquitto 2.1.2 | runs as a container image (`eclipse-mosquitto:2.1.2-alpine`); not copied or modified | EPL-2.0 OR BSD-3-Clause |
| PostgreSQL 17.11 | runs as a container image (`postgres:17.11-alpine`) | PostgreSQL License |
| Python 3.12 | base of the API and gateway images | PSF-2.0 |
| Valkey 9.1.2 | runs as a container image (`valkey/valkey:9.1.2-alpine`); the queue of each stamp; not copied or modified | BSD-3-Clause |
| valkey-py 6.1.1 | the Python client of Valkey, installed unmodified (one of the packages below) | MIT |
| Locust 2.46.7 and the packages it pulls in | only in the load-test image (`loadtest/`), never in the images of the product; installed unmodified from a hashed list of 37. Not under MIT, BSD or Apache: zope-event and zope-interface (ZPL-2.1), bidict and certifi (MPL-2.0), greenlet (MIT AND PSF-2.0) | MIT (Locust) |
| oasdiff 1.33.0 | run once from its published image (`tufin/oasdiff:v1.33.0`) to compare two API descriptions; not part of this repository | Apache-2.0 |
| Python packages | the 31 packages in `requirements.lock.txt`, installed unmodified | MIT, BSD, Apache-2.0 (paho-mqtt: EPL-2.0 OR BSD-3-Clause, the BSD option is used); typing-extensions is PSF-2.0 and certifi is MPL-2.0 (used unmodified) |
| ffmpeg | called as a command inside the MediaMTX `-ffmpeg` image; not included in this repository | LGPL / GPL (see ffmpeg's own notices in the image) |
| OpenCV (opencv-python-headless 4.14.0.94) | the motion worker's background subtraction and the reading of video files; the wheel carries its own FFmpeg libraries, which decode the cameras' video | Apache-2.0 (OpenCV); FFmpeg inside the wheel: LGPL (see below) |
| PyTorch 2.14.1 (+cu130), torchvision 0.29.1 (+cu130), transformers 5.19.0 and the other packages of the vision image | `services/vision/`, image `ccc-vision:3.0`, installed unmodified from a hashed list of 57 (see below) | BSD-3-Clause / Apache-2.0 / MIT and others, listed below |
| NVIDIA CUDA libraries (15 `nvidia-*` packages that torch requires on Linux) | inside the vision image, used on this machine only; not distributed | NVIDIA proprietary: NVIDIA CUDA EULA (see below) |
| DETR (`facebook/detr-resnet-50`, revision `no_timm`, commit 70120ba84d68ca1211e007c4fb61d0cd5424be54) | the model weights, fetched by hand into `var/models/`, mounted read only; not in the repository or in any image | Apache-2.0 |
| Big Buck Bunny (640x360) | the source of the check video; fetched by hand into `var/bbb/`; not in the repository or in any image | CC BY 3.0 |
| COCO val2017 picture 000000039769 | the check picture of the model card's example; fetched by hand into `var/check/` | annotations CC BY 4.0; the picture is under the Flickr terms of use |
| Frigate | its source was read (a recording is checked before it is counted; rows are written in groups); no code copied | MIT |

Full texts follow. Container images carry their own licence files inside the image.

---

## MediaMTX (MIT)

Source: https://github.com/bluenviron/mediamtx


MIT License

Copyright (c) 2019 aler9

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.


---

## Frigate (MIT)

Source: https://github.com/blakeblackshear/frigate


The MIT License

Copyright (c) 2026 Frigate, Inc. (Frigate™)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.


---

## hls.js (Apache-2.0)

Source: https://github.com/video-dev/hls.js . The file `LICENSE` of the 1.7.3 package, unchanged:


Copyright (c) 2017 Dailymotion (http://www.dailymotion.com)

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

src/remux/mp4-generator.js and src/demux/exp-golomb.ts implementation in this project
are derived from the HLS library for video.js (https://github.com/videojs/videojs-contrib-hls)

That work is also covered by the Apache 2 License, following copyright:
Copyright (c) 2013-2015 Brightcove


THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.


---

## keycloak-js (Apache-2.0)

Source: https://github.com/keycloak/keycloak-js . Copyright 2016 Red Hat, Inc. and/or its affiliates and other
contributors (from the header of `lib/keycloak.js`). The file `LICENSE.txt` of the 26.2.4 package, unchanged:


                                 Apache License
                           Version 2.0, January 2004
                        http://www.apache.org/licenses/

   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION

   1. Definitions.

      "License" shall mean the terms and conditions for use, reproduction,
      and distribution as defined by Sections 1 through 9 of this document.

      "Licensor" shall mean the copyright owner or entity authorized by
      the copyright owner that is granting the License.

      "Legal Entity" shall mean the union of the acting entity and all
      other entities that control, are controlled by, or are under common
      control with that entity. For the purposes of this definition,
      "control" means (i) the power, direct or indirect, to cause the
      direction or management of such entity, whether by contract or
      otherwise, or (ii) ownership of fifty percent (50%) or more of the
      outstanding shares, or (iii) beneficial ownership of such entity.

      "You" (or "Your") shall mean an individual or Legal Entity
      exercising permissions granted by this License.

      "Source" form shall mean the preferred form for making modifications,
      including but not limited to software source code, documentation
      source, and configuration files.

      "Object" form shall mean any form resulting from mechanical
      transformation or translation of a Source form, including but
      not limited to compiled object code, generated documentation,
      and conversions to other media types.

      "Work" shall mean the work of authorship, whether in Source or
      Object form, made available under the License, as indicated by a
      copyright notice that is included in or attached to the work
      (an example is provided in the Appendix below).

      "Derivative Works" shall mean any work, whether in Source or Object
      form, that is based on (or derived from) the Work and for which the
      editorial revisions, annotations, elaborations, or other modifications
      represent, as a whole, an original work of authorship. For the purposes
      of this License, Derivative Works shall not include works that remain
      separable from, or merely link (or bind by name) to the interfaces of,
      the Work and Derivative Works thereof.

      "Contribution" shall mean any work of authorship, including
      the original version of the Work and any modifications or additions
      to that Work or Derivative Works thereof, that is intentionally
      submitted to Licensor for inclusion in the Work by the copyright owner
      or by an individual or Legal Entity authorized to submit on behalf of
      the copyright owner. For the purposes of this definition, "submitted"
      means any form of electronic, verbal, or written communication sent
      to the Licensor or its representatives, including but not limited to
      communication on electronic mailing lists, source code control systems,
      and issue tracking systems that are managed by, or on behalf of, the
      Licensor for the purpose of discussing and improving the Work, but
      excluding communication that is conspicuously marked or otherwise
      designated in writing by the copyright owner as "Not a Contribution."

      "Contributor" shall mean Licensor and any individual or Legal Entity
      on behalf of whom a Contribution has been received by Licensor and
      subsequently incorporated within the Work.

   2. Grant of Copyright License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      copyright license to reproduce, prepare Derivative Works of,
      publicly display, publicly perform, sublicense, and distribute the
      Work and such Derivative Works in Source or Object form.

   3. Grant of Patent License. Subject to the terms and conditions of
      this License, each Contributor hereby grants to You a perpetual,
      worldwide, non-exclusive, no-charge, royalty-free, irrevocable
      (except as stated in this section) patent license to make, have made,
      use, offer to sell, sell, import, and otherwise transfer the Work,
      where such license applies only to those patent claims licensable
      by such Contributor that are necessarily infringed by their
      Contribution(s) alone or by combination of their Contribution(s)
      with the Work to which such Contribution(s) was submitted. If You
      institute patent litigation against any entity (including a
      cross-claim or counterclaim in a lawsuit) alleging that the Work
      or a Contribution incorporated within the Work constitutes direct
      or contributory patent infringement, then any patent licenses
      granted to You under this License for that Work shall terminate
      as of the date such litigation is filed.

   4. Redistribution. You may reproduce and distribute copies of the
      Work or Derivative Works thereof in any medium, with or without
      modifications, and in Source or Object form, provided that You
      meet the following conditions:

      (a) You must give any other recipients of the Work or
          Derivative Works a copy of this License; and

      (b) You must cause any modified files to carry prominent notices
          stating that You changed the files; and

      (c) You must retain, in the Source form of any Derivative Works
          that You distribute, all copyright, patent, trademark, and
          attribution notices from the Source form of the Work,
          excluding those notices that do not pertain to any part of
          the Derivative Works; and

      (d) If the Work includes a "NOTICE" text file as part of its
          distribution, then any Derivative Works that You distribute must
          include a readable copy of the attribution notices contained
          within such NOTICE file, excluding those notices that do not
          pertain to any part of the Derivative Works, in at least one
          of the following places: within a NOTICE text file distributed
          as part of the Derivative Works; within the Source form or
          documentation, if provided along with the Derivative Works; or,
          within a display generated by the Derivative Works, if and
          wherever such third-party notices normally appear. The contents
          of the NOTICE file are for informational purposes only and
          do not modify the License. You may add Your own attribution
          notices within Derivative Works that You distribute, alongside
          or as an addendum to the NOTICE text from the Work, provided
          that such additional attribution notices cannot be construed
          as modifying the License.

      You may add Your own copyright statement to Your modifications and
      may provide additional or different license terms and conditions
      for use, reproduction, or distribution of Your modifications, or
      for any such Derivative Works as a whole, provided Your use,
      reproduction, and distribution of the Work otherwise complies with
      the conditions stated in this License.

   5. Submission of Contributions. Unless You explicitly state otherwise,
      any Contribution intentionally submitted for inclusion in the Work
      by You to the Licensor shall be under the terms and conditions of
      this License, without any additional terms or conditions.
      Notwithstanding the above, nothing herein shall supersede or modify
      the terms of any separate license agreement you may have executed
      with Licensor regarding such Contributions.

   6. Trademarks. This License does not grant permission to use the trade
      names, trademarks, service marks, or product names of the Licensor,
      except as required for reasonable and customary use in describing the
      origin of the Work and reproducing the content of the NOTICE file.

   7. Disclaimer of Warranty. Unless required by applicable law or
      agreed to in writing, Licensor provides the Work (and each
      Contributor provides its Contributions) on an "AS IS" BASIS,
      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
      implied, including, without limitation, any warranties or conditions
      of TITLE, NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A
      PARTICULAR PURPOSE. You are solely responsible for determining the
      appropriateness of using or redistributing the Work and assume any
      risks associated with Your exercise of permissions under this License.

   8. Limitation of Liability. In no event and under no legal theory,
      whether in tort (including negligence), contract, or otherwise,
      unless required by applicable law (such as deliberate and grossly
      negligent acts) or agreed to in writing, shall any Contributor be
      liable to You for damages, including any direct, indirect, special,
      incidental, or consequential damages of any character arising as a
      result of this License or out of the use or inability to use the
      Work (including but not limited to damages for loss of goodwill,
      work stoppage, computer failure or malfunction, or any and all
      other commercial damages or losses), even if such Contributor
      has been advised of the possibility of such damages.

   9. Accepting Warranty or Additional Liability. While redistributing
      the Work or Derivative Works thereof, You may choose to offer,
      and charge a fee for, acceptance of support, warranty, indemnity,
      or other liability obligations and/or rights consistent with this
      License. However, in accepting such obligations, You may act only
      on Your own behalf and on Your sole responsibility, not on behalf
      of any other Contributor, and only if You agree to indemnify,
      defend, and hold each Contributor harmless for any liability
      incurred by, or claims asserted against, such Contributor by reason
      of your accepting any such warranty or additional liability.

   END OF TERMS AND CONDITIONS

   APPENDIX: How to apply the Apache License to your work.

      To apply the Apache License to your work, attach the following
      boilerplate notice, with the fields enclosed by brackets "[]"
      replaced with your own identifying information. (Don't include
      the brackets!)  The text should be enclosed in the appropriate
      comment syntax for the file format. We also recommend that a
      file or class name and description of purpose be included on the
      same "printed page" as the copyright notice for easier
      identification within third-party archives.

   Copyright [yyyy] [name of copyright owner]

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.


---

## Keycloak server (Apache-2.0)

Source: https://github.com/keycloak/keycloak . It is used as an unmodified container image. Its licence is the same
Apache License, Version 2.0 as above (text: https://raw.githubusercontent.com/keycloak/keycloak/HEAD/LICENSE.txt).

---

## Eclipse Mosquitto (EPL-2.0 OR BSD-3-Clause)

Source: https://github.com/eclipse-mosquitto/mosquitto . It is used as an unmodified container image. The project states:


This project is dual licensed under the Eclipse Public License 2.0 OR the
Eclipse Distribution License 1.0 as described in the epl-v20 and edl-v10 files.

SPDX-License-Identifier: EPL-2.0 OR BSD-3-Clause

---

## Python packages (licence as stated in each package's metadata, 2026-10-24)

| Package | Version | Licence |
|---|---|---|
| aiomqtt | 2.5.1 | BSD |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| asyncpg | 0.32.0 | Apache-2.0 |
| certifi | 2026.10.19 | Mozilla Public 2.0 (MPL 2.0) |
| cffi | 2.1.1 | MIT-0 |
| click | 8.5.0 | BSD-3-Clause |
| colorama | 0.4.6 | BSD |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| fastapi | 0.143.0 | MIT |
| h11 | 0.16.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD |
| idna | 3.20 | BSD-3-Clause |
| iniconfig | 2.3.1 | MIT |
| opentelemetry-api | 1.45.1 | Apache-2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| paho-mqtt | 2.1.0 | OSI Approved |
| pluggy | 1.6.0 | MIT |
| pycparser | 3.11 | BSD-3-Clause |
| pydantic | 2.14.0 | MIT |
| pydantic_core | 2.50.0 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |
| PyJWT | 2.15.1 | MIT |
| pytest | 9.1.1 | MIT |
| starlette | 1.7.0 | BSD-3-Clause |
| typing-inspection | 0.4.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| uvicorn | 0.54.0 | BSD-3-Clause |
| valkey | 6.1.1 | MIT |

The package texts are installed with the packages themselves (in the image: `/usr/local/lib/python3.12/site-packages/*.dist-info/`).

---

## Valkey (BSD-3-Clause)

Source: https://github.com/valkey-io/valkey (the image `valkey/valkey:9.1.2-alpine` is used as it is)

BSD 3-Clause License

Copyright (c) 2024-present, Valkey contributors
Copyright (c) 2006-2020, Redis Ltd.
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice,
   this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

---

## valkey-py (MIT)

Source: https://github.com/valkey-io/valkey-py

MIT License

Copyright (c) 2022-2023, Redis, inc.
Copyright (c) 2024-present, valkey-py contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

## Locust (MIT)

Source: https://github.com/locustio/locust

The MIT License

Copyright (c) 2009-2026, Carl Byström, Jonatan Heyman, Lars Holmberg

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

---

## Packages of the vision image (licence as stated in each package's metadata, read inside the image on 2026-10-25; where the
metadata has no licence field, the licence file in the package's `.dist-info` folder, read on 2026-10-25)

The list is `services/vision/requirements.lock.txt` (57 packages, installed with their hashes, unmodified).

| Package | Version | Licence |
|---|---|---|
| annotated-doc | 0.0.5 | MIT |
| anyio | 4.15.1 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| cuda-bindings | 13.4.3 | Apache-2.0 |
| cuda-pathfinder | 1.8.3 | Apache-2.0 |
| cuda-toolkit | 13.0.3.0 | no licence field and no licence file in the package (a meta-package without code); the packages it requires are NVIDIA proprietary |
| filelock | 4.2.1 | MIT |
| fsspec | 2026.9.0 | BSD-3-Clause |
| h11 | 0.16.0 | MIT |
| hf-xet | 1.7.0 | Apache-2.0 |
| httpcore2 | 2.13.1 | BSD-3-Clause |
| httpx2 | 2.13.1 | BSD-3-Clause |
| huggingface-hub | 2.2.0 | Apache-2.0 |
| idna | 3.20 | BSD-3-Clause |
| jinja2 | 3.1.6 | BSD-3-Clause (the licence file `licenses/LICENSE.txt`; the metadata has only the classifier BSD License) |
| markdown-it-py | 4.2.0 | MIT (the licence files `licenses/LICENSE` and `LICENSE.markdown-it`; the metadata has only the classifier) |
| markupsafe | 3.0.4 | BSD-3-Clause |
| mdurl | 0.1.2 | MIT (the licence file `LICENSE`; the metadata has only the classifier) |
| mpmath | 1.3.0 | BSD |
| networkx | 3.7 | BSD-3-Clause |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| nvidia-cublas | 13.1.1.3 | NVIDIA proprietary (LicenseRef-NVIDIA-Proprietary; NVIDIA CUDA EULA) |
| nvidia-cuda-cupti | 13.0.85 | NVIDIA proprietary (LicenseRef-NVIDIA-Proprietary; NVIDIA CUDA EULA) |
| nvidia-cuda-nvrtc | 13.0.88 | NVIDIA proprietary (LicenseRef-NVIDIA-Proprietary; NVIDIA CUDA EULA) |
| nvidia-cuda-runtime | 13.0.96 | NVIDIA proprietary (LicenseRef-NVIDIA-Proprietary; NVIDIA CUDA EULA) |
| nvidia-cudnn-cu13 | 9.24.0.43 | NVIDIA proprietary (LicenseRef-NVIDIA-Proprietary; NVIDIA CUDA EULA) |
| nvidia-cufft | 12.0.0.61 | NVIDIA proprietary (LicenseRef-NVIDIA-Proprietary; NVIDIA CUDA EULA) |
| nvidia-cufile | 1.15.1.6 | NVIDIA proprietary (LicenseRef-NVIDIA-Proprietary; NVIDIA CUDA EULA) |
| nvidia-curand | 10.4.0.35 | NVIDIA proprietary (LicenseRef-NVIDIA-Proprietary; NVIDIA CUDA EULA) |
| nvidia-cusolver | 12.0.4.66 | NVIDIA proprietary (LicenseRef-NVIDIA-Proprietary; NVIDIA CUDA EULA) |
| nvidia-cusparse | 12.6.3.3 | NVIDIA proprietary (LicenseRef-NVIDIA-Proprietary; NVIDIA CUDA EULA) |
| nvidia-cusparselt-cu13 | 0.8.1 | NVIDIA Proprietary Software (NVIDIA CUDA EULA) |
| nvidia-nccl-cu13 | 2.30.7 | NVIDIA proprietary (LicenseRef-NVIDIA-Proprietary; NVIDIA CUDA EULA) |
| nvidia-nvjitlink | 13.4.92 | NVIDIA proprietary (LicenseRef-NVIDIA-Proprietary; NVIDIA CUDA EULA) |
| nvidia-nvshmem-cu13 | 3.4.5 | NVIDIA proprietary (LicenseRef-NVIDIA-Proprietary; NVIDIA CUDA EULA) |
| nvidia-nvtx | 13.0.85 | Apache 2.0 (the package's licence field; its classifier says Other/Proprietary) |
| opencv-python-headless | 4.14.0.94 | Apache 2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pillow | 13.0.0 | MIT-CMU |
| pygments | 2.21.0 | BSD-2-Clause |
| pyyaml | 6.0.3 | MIT |
| regex | 2026.9.29 | Apache-2.0 AND CNRI-Python |
| rich | 15.0.0 | MIT |
| safetensors | 0.8.0 | Apache-2.0 (the licence file `licenses/LICENSE`; the metadata has only the classifier) |
| setuptools | 84.1.0 | MIT |
| shellingham | 1.5.4 | ISC License |
| sympy | 1.14.0 | BSD |
| tokenizers | 0.23.3 | Apache Software License (the package's classifier; the wheel has no licence file; upstream: huggingface/tokenizers, Apache-2.0) |
| torch | 2.14.1+cu130 | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND MIT |
| torchvision | 0.29.1+cu130 | BSD (the package's licence field) |
| tqdm | 4.70.1 | MPL-2.0 AND MIT |
| transformers | 5.19.0 | Apache 2.0 License |
| triton | 3.8.0 | MIT |
| truststore | 0.10.4 | MIT |
| typer | 0.27.3 | MIT |
| typing-extensions | 4.16.0 | PSF-2.0 |
| valkey | 6.1.1 | MIT |

The licence files are installed with the packages (in the image: `/usr/local/lib/python3.12/site-packages/*.dist-info/`). Three
versions are younger than two weeks because they are the ones that fix published vulnerabilities (pillow 13.0.0, filelock 4.2.1,
setuptools 84.1.0; see `docs/adr/0001-components.md`).

### NVIDIA CUDA libraries (NVIDIA proprietary)
The fifteen `nvidia-*` packages above (and `cuda-toolkit`, a meta-package) are NVIDIA's CUDA libraries, which PyTorch's Linux wheel
requires. The licence is the CUDA Toolkit EULA (https://docs.nvidia.com/cuda/eula/index.html). They are used inside a container on
the machine of the author, to run the model. They are not distributed: the EULA's grant to distribute parts of the SDK requires, among
other things, that the application has material additional functionality of its own and that they are only accessed by it
(section 1.1.2 of the EULA); neither the repository nor any image is published.

### FFmpeg inside OpenCV's wheel (LGPL)
`opencv-python-headless` contains FFmpeg libraries (LGPL) which OpenCV uses to decode video. They are used unmodified through
OpenCV and are not changed or linked by anything of this repository. The FFmpeg of the camera and MediaMTX images is a separate
program, called as a command.

### Big Buck Bunny (CC BY 3.0)
(c) copyright 2008, Blender Foundation / www.bigbuckbunny.org. The film is licensed under the Creative Commons Attribution 3.0
licence (https://creativecommons.org/licenses/by/3.0/). A video made from a part of it is used as the check video; the film and
what is made from it are not in this repository (`var/` is not tracked).

### DETR and COCO
DETR: Carion et al., "End-to-End Object Detection with Transformers" (2020); the weights of `facebook/detr-resnet-50` are under
Apache-2.0 (https://huggingface.co/facebook/detr-resnet-50). The model was trained on COCO 2017 (https://cocodataset.org): the
annotations are CC BY 4.0, the pictures belong to their owners and are under the Flickr terms of use. The one COCO picture used
here (the model card's example) is in `var/check/` and not in the repository.
