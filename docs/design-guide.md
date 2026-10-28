# 設計の説明書（これから入る人向け）

この文書は、camera-cloud-core の作りを、初めてコードを開く人が半日で追えるように書いた説明です。章の分け方は Malte Ubl の記事
「Design Docs at Google」（https://www.industrialempathy.com/posts/design-docs-at-google/ ）の考え方（Context and scope /
Goals and non-goals / The actual design / Alternatives considered / Cross-cutting concerns）にならっています。記事に使用条件の
記載は無いので、使ったのは章の分け方の考え方だけで、文は写していません。

## 1. 背景と範囲（Context and scope）

監視カメラの録画を、複数の会社（テナント）に、会社ごとに分けて見せるバックエンドの小型版です。カメラの数で増える部分（映像・
状態・録画の索引）は「stamp」という一式にして複数組に分け、会社の台帳・ログイン・API は全体で 1 つ（control plane）にしました
（[ADR 0006](adr/0006-stamps-and-control-plane.md)）。録画の中身を見て、動きがあった時刻と、写っている物の名前を出来事として
残す部分（[ADR 0011](adr/0011-motion-and-classification.md)）を、最後に足しました。

登場する部品と持ち主：

| 部品 | 作った人 | 何をするか |
|---|---|---|
| Keycloak | 既製 | 本人の確認だけ（会社・役割は持たない） |
| gateway（`services/gateway`） | 自作 | 外からの唯一の入口。`/api/` を API へ渡し、画面を配る |
| API（`services/api`） | 自作 | 会社・カメラ・権限・映像の権限（JWT）・出来事の一覧 |
| 書き込み役（同じ image の別の起動） | 自作 | 状態と録画の区切りを列に積み、まとめて DB に書く。出来事も書く |
| MediaMTX / Mosquitto / Valkey / PostgreSQL | 既製 | 映像の受け取りと再生 / 状態の仲介 / 列 / 台帳と索引 |
| motion / classify（`services/vision`） | 自作 | 動きを見つける（CPU）/ 動いた所の物に名前を付ける（GPU） |

## 2. 目標と、目標にしない事（Goals and non-goals）

目標：会社の間で映像・状態・出来事が漏れない事。既に出した API の形を壊さずに足せる事。1 台の PC で全部が動き、数字で確かめ
られる事。

目標にしない事（理由は各 ADR）：学習や追加学習、物の追跡、顔や人の識別、出来事の通知や検索の画面、動きの区間を区切りの
間でつなぐ事、2 つ目の stamp に vision の部品を置く事。作らない物の一覧は [ADR 0011](adr/0011-motion-and-classification.md)
の末尾にあります。

## 3. 設計の本体（The actual design）

### 3.1 要求の流れ（ブラウザ → API → 書き込み役 → DB）

```
ブラウザ ──ログイン──▶ Keycloak（本人の確認だけ）
   │
   └─ /api/v1/... （Bearer）─▶ gateway ──▶ API ──▶ PostgreSQL（台帳。stamp 1 は索引と出来事も同じ DB）
                                    │         │
        ① 会社と役割の確かめ ────────┘         └─▶ 映像の JWT（path 1 つ・短い命）を出す
        （API の rules.py）
   └─ 映像（JWT）────────────────────────────▶ MediaMTX   ② path が <会社>/<カメラ> の物だけ通す
```

読み方の順番：`services/gateway/gateway/app.py` の表（どの種類の呼び出しをどの API へ渡すか）→ `services/api/app/api.py`
の道筋 → `rules.py`（役割と操作）→ `stamps.py`（会社から stamp を引く 1 か所）→ `pgstore.py`（SQL）。

### 3.2 映像の流れ（MediaMTX → 区切り → 動き → 分類 → 出来事）

```
模擬カメラ ─RTSP─▶ MediaMTX ─60 秒ごとの区切り─▶ 書き込み役 ──列(ccc:segments)──▶ まとめ書き ─▶ 索引(recording_segments)
                                                                                           │ commit の後・確認の前に
                                                                                           ▼
                                  motion ◀──────────── ccc:recorded（索引に在る区切り）◀───┘
                                     │ 動きのあった秒を区間にする（OpenCV の背景差分。CPU）
                                     ├─▶ ccc:events（動きの出来事）──┐
                                     └─▶ ccc:motion（区間）          │
                                            │                       │
                                        classify（DETR。GPU）        │
                                            └─▶ ccc:events（物の出来事）─▶ 書き込み役 ─▶ 表 events ─▶ API ─▶ 画面
```

出来事は「分析すると決めたカメラ（`var/vision/cameras.txt`）の、索引に在る区切りが含む時刻と長さ」にしか書けません。
書き込み役が、カメラの一覧（[`intake.py` の `write_group` の中の `event_cameras`](../services/api/app/intake.py#L412)）と、同じ SQL の中のカメラ・stamp・区切り
（[`intake.py` の `INSERT_EVENTS`](../services/api/app/intake.py#L230)）で確かめます。worker が乗っ取られても、書けるのはそれだけです。

### 3.3 テナントの分離を守る 3 か所

会社の間の分離は、1 か所ではなく、入口の違う 3 か所で別々に守っています。どれか 1 つが破られても、残りは別の理由で止めます。

| 守る所 | 何で守るか | コード |
|---|---|---|
| API | 呼んだ人がそのテナントの所属で、その操作を許された役割か。他社の ID は存在しないのと同じ 404 | [`rules.py` の `authorize_tenant`](../services/api/app/rules.py#L57)、呼び出しの共通の入口 [`api.py` の `_tenant`](../services/api/app/api.py#L108) |
| MediaMTX | 映像の JWT は action 1 つと path 1 つ（`<テナント>/<カメラ>`）だけを許し、署名と `aud` と `exp` を MediaMTX が確かめる | JWT を作る [`video_jwt.py` の `issue`](../services/api/app/video_jwt.py#L57)、確かめる設定 [`mediamtx.yml`](../infra/mediamtx/mediamtx.yml#L10) |
| MQTT | カメラの役割は `cameras/%c/state`（`%c` は接続したカメラ自身の ID）への publish だけ | [`dynsec.py` の `CAMERA_STATE_TOPIC_ACL`](../services/api/app/dynsec.py#L26) |

出来事の道筋は、1 つ目（API）に乗っています。出来事を見る権限は映像を見る権限と同じ（パートナー管理者は見ない）で、他社の
出来事は 404 です。

### 3.4 既存の API を壊さずに道筋を足す手順

出来事の 2 つの道筋を足した時の手順がそのまま手順書です。

1. API に道筋を書く（`api.py`）。権限は既にある操作（`Op`）から選ぶ。他社は 404 に統一（`_tenant` と `_camera` を通す）。
2. gateway の表に足す：`app.py` の [`SHAPES`](../services/gateway/gateway/app.py#L23)（形）と [`ROUTES`](../services/gateway/gateway/app.py#L40)（どの API へ）。表に無い呼び出しは、
   既定の API に行く。
3. 契約を更新する：`scripts/export_openapi.py` で `docs/openapi/v2.json` を書き直す（単体試験が「ファイルとコードが同じ」を見る）。
   前の版の契約は `docs/openapi/v2.0.1.json` に写して残してあるので、**壊れる差分が無い**事を oasdiff で 2 組み
   （1 つ目の版と今、2 つ目の版と今）見て、`docs/validation/openapi_diff.md` に書く。
4. **前の版の結合試験を 1 行も変えずに通す**：`tests/integration`（1 つ目の版）と `tests/integration_v2`（2 つ目の版）の試験を書き換えずに、
   その 2 つの試験が通る事。新しい道筋の試験は `tests/integration_v3/` に足す。
5. 守りの試験が「許す物の一覧」を見ているなら、一覧を広げる形で直す（見る項目は減らさない）。

### 3.5 列（stream）に worker を足す手順

1. stream の名前と、入る欄を決める。**欄に path を入れない**（ID と名前だけ。worker が決まった形で path を作る）。
2. 読む側は `vision/common.py` の `Streams` と `run_loop` を使う（確認は処理の後。30 秒で取り直し、6 回目はその worker だけの捨て場
   `ccc:dead:<worker>`。書き込み役の `ccc:dead` は使わない：XADD は MAXLEN で足す先の stream を削れるので、足せる worker は消せもする）。
3. Valkey の資格を足す：`init_secrets.py` の `WORKER_RULES` に、使う命令と使う stream だけを書く。秘密の folder は worker ごとに分ける。
   ACL の中身が変わったら Valkey の再起動が要る。
4. `compose.yaml` に足す：stamp 1 の内部のネットワークだけ・port 無し・`read_only`・`cap_drop: ALL`・`mem_limit`（測って決める）。
   `test_static_config.py` の「このネットワークに居てよい物」の一覧を広げる。
5. 結果を書き込み役に書かせるなら、書き込み側の検査（分析するカメラの一覧・カメラ・stamp・区切り）を通す。表に新しい種類を足すなら、重ならない
   キーと `ON CONFLICT DO NOTHING` で、2 回書いても 1 行にする。

## 4. 選ばなかった案（Alternatives considered）

案ごとの理由は ADR にあります（ここに書き写すと食い違うので案内だけにします）：[0001 部品](adr/0001-components.md)、
[0002 テナントの表し方](adr/0002-tenant-model.md)、[0006 stamp](adr/0006-stamps-and-control-plane.md)、
[0007 列](adr/0007-queue-and-batch-writes.md)、[0011 動きと分類（YOLO / ONNX Runtime / 全部の区切りを分類）](adr/0011-motion-and-classification.md)、
[0012 GPU](adr/0012-gpu-in-a-container.md)。

## 5. 横断の事柄（Cross-cutting concerns）

- **秘密**：全部 init が 1 度だけ作り、サービスごとの folder に分けて読み取り専用で渡す。vision の worker は自分の Valkey の資格だけ。
  動画・重み・画像は repo にも image にも入れない（`var/` を読み取り専用でつなぐ）。
- **守り**：[ADR 0013](adr/0013-the-queues-network-and-its-new-members.md)（列のネットワークに足した 2 つと、足した守り）。
- **確かめ方**：単体試験は外に出ず、結合試験は動いている構成の外の口だけを叩く。守りの試験が本当に効くかは、コードを
  わざと壊して赤になるかを見る変異試験（`mutation/`、19 か所）で確かめる。動きの率は、作り方で正解が決まる映像で測る
  （[vision_truth.md](validation/vision_truth.md)、[vision_eval.md](validation/vision_eval.md)）。

## 6. レビューで見る所（設計）

Google の Engineering Practices「What to look for in a code review」の「Design」の節を、日本語に訳して抜粋しました
（原文：https://google.github.io/eng-practices/review/reviewer/looking-for.html ）。**原文を日本語にし、一部だけ抜き出した改変
版です。** 原文の文書は Creative Commons Attribution 3.0 License（https://creativecommons.org/licenses/by/3.0/ ）で公開されて
います。

> 設計：レビューで最初に見るべきは、変更の全体の設計です。変更に含まれるコード同士の関わり方は筋が通っていますか。この変更は
> このコードベースに置くべきですか、それともライブラリに置くべきですか。システムの残りとうまくつながりますか。この機能を足す
> のは今が良い時期ですか。

この説明書では、上の問いを次のように当てはめます：出来事の道筋は gateway の表・API・書き込み役のどこに置くのが筋か（3.4）。
worker は列に足すのが筋か、API に足すのが筋か（3.5）。分離の 3 か所のどれにも触れずに済むか（3.3）。
