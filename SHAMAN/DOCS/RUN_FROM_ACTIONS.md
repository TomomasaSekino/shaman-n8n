# Actions から n8n の Workflow を実行する

2026-11-06 から。テストを人の手から切り離すため、luna の n8n にある SHAMAN の Workflow を、GitHub の Actions の画面から実行できるようにした。

## 1. 仕組み

- 3 つの Workflow（本番 `SHAMAN`、プロンプト評価 `SHAMAN: Prompt Eval`、接続確認 `SHAMAN: Connection Check`）に、**合言葉付きの Webhook** を入口として付ける（`generate_workflows.py` の `runWebhookNode`）。
  - 認証は Header Auth。ヘッダー `X-SHAMAN-Run-Token` の値が、n8n の Credential `SHAMAN / Run Webhook` と一致しなければ、n8n が 403 で拒む。
  - `responseMode` は `lastNode`。実行が終わるまで応答しない。失敗すれば n8n が 500 を返す。
  - プロンプト評価・接続確認には、画面から実行するための Manual Trigger も残す。
- Webhook は、Workflow を**公開**していないと受け付けない。そのため、配備（`deploy-shaman-n8n.yml`、オーナーによる main へのマージ）で 3 つとも公開する。
  - 公開しても、入口は合言葉付きの Webhook だけなので、勝手には動かない。本番が実行されても、WordPress には下書きで投稿する。
  - 合言葉を確かめない Webhook を含む Workflow は、配備のスクリプトが拒む。
  - 前にあった本番の公開の条件（`SHAMAN_PRODUCTION_READY`・`SHAMAN_E2E_PASSED_SHA`）と、接続確認の配備のスイッチ（`SHAMAN_CONNECTION_CHECK_READY`）は無くした。Repository Variable に残っていれば、消してよい。
- 実行は Actions の **「SHAMAN | n8n Workflow実行」**（`.github/workflows/run-shaman-n8n.yml`）。
  - luna の配備用 runner（`luna-shaman-deploy`）から、`127.0.0.1:5678` の Webhook に POST し（`tools/luna/run_shaman_workflow.py`）、終わるまで待つ。
  - 結果（最後のノードの出力。長いものは 20000 文字まで）を Actions のログに出す。プロンプト評価は、結果ごとの測定値（トークン数を含む）も返す（`shamanRun.results`、2026-11-09）。実行の中身は n8n の実行履歴にも残る。
  - **成否は、Workflow が最後に返す実行結果（`shamanRun`）で決める。** HTTP 200 でも、`shamanRun.ok` が `true` でなければ Actions を失敗にする。`shamanRun` が無い応答も失敗にする。

| Workflow | 実行結果を返すノード | ok になる条件 |
|---|---|---|
| 本番 | `Run Result`（全行の処理の後、キューを読み直す `Queue: Result Read` の次） | この実行（実行ID）が確保したキューの行が 1 行以上あり、すべて STATUS「完了」。「エラー」「画像エラー」が 1 行でもあれば ng。行が無ければ ng |
| プロンプト評価 | `Eval: Run Result`（`Eval: Log End` の次） | 「評価の実行」の状態が「完了」。「エラーあり」は ng。待機中の行が無い・入力が無いときは `Eval: Stop` が ng と理由を返す |
| 接続確認 | `Conn: Result` | NG のサービスが無い。NG があれば、NG のサービスと理由を返す |

結果のノードの次の **`Run: Check`**（3 つの Workflow の最後のノード）が、結果が ok でなければ例外で止める。n8n の実行は **Error** で終わる（途中で止まった・NG なのに Succeeded にしない。2026-11-06）。

**途中で黙って止まらない。** n8n は、あるノードの出力が 0 件だと、その先を動かさずに実行を Succeeded で終える。それを無くすため、3 つの Workflow で次のようにしている（`test_no_node_can_stop_the_run_silently` で確かめる）。

- Code ノードは、出力が 0 件なら例外にする（`generate_workflows.py` の `_no_silent_stop` がコードを包む）。エラー文は `SHAMAN_RUN_NG: 「<ノード名>」の出力が 0 件です`。
- Google Sheets の update は、row_number の行が見つからないと 0 件を返す。そのため alwaysOutputData にし、次の `… Check`（`sheetsUpdateCheckNode`：`Queue: Claim Check`・`Queue: Update Check`・`Eval: Claim Run Check`・`Eval: Mark Done Check`・`Eval: Mark Failed Check`）が、1 行を書けたことを確かめる。書けなければ `SHAMAN_RUN_NG: 「<ノード名>」で書き込めた行が 0 件です`。`Queue: Update` の失敗も `Queue: Update Check` で止める（WP 投稿 ID を出す。前の `Queue: Update Failed` は無くした）。
- Google Sheets の read・Drive の検索は alwaysOutputData（次の Code が「行が無い」で止める）。
- IF・Switch・Loop・エラー出力は、全部の出力をつなぐ。プロンプト評価の Switch は、どの工程にも当たらない item を `Eval: Unknown Step` で止める。
- 接続確認は、どのノードも出力が 0 件でも次へ進め、判定まで必ず進む。

Webhook（lastNode）の応答は、例外のときは中身の無い HTTP 500 になる。そのため `run_shaman_workflow.py` が、n8n の API（`N8N_DEPLOY_API_KEY`。読むだけ）で**この呼び出しの実行**を読み、止まったノードとエラー文をログに出す。結果が返らなかったときも同じように読む。

- 止まったノードの開始時刻と所要時間、実行全体の時間も出す（通信が切れたのか、時間切れかを見分ける）。
- 呼び出しごとに乱数の印（`nonce`）を Webhook に送り、その印を受け取った実行だけを使う。画面からの実行などと重なっても、別の実行の原因を出さない。見つからなければ「`cannot identify the n8n execution of this run`」とだけ出す。
- API キーの権限：`workflow:list`（Workflow の一覧）・`execution:list`（実行の一覧）・`execution:read`（実行の中身）。足りなければ、その旨だけ出す（Actions は失敗のまま。n8n の実行履歴には Error が残る）。
- ログに出す文は、API キー・合言葉・認証ヘッダー・URL の `key=` などの値を `***` に伏せる（`redact`）。
  - 起動できるのは、オーナーが main から起動したときだけ。

| 選ぶ値 | Workflow | Webhook の path | 待つ上限 |
|---|---|---|---|
| `connection` | `SHAMAN: Connection Check` | `shaman-connection-check` | 10 分 |
| `test` | `SHAMAN: Prompt Eval`（`test:` の入力欄の値で「評価の実行」に行を足し、その 1 行を実行） | `shaman-prompt-eval` | 60 分 |
| `production` | `SHAMAN`（キューの待機中の行） | `blog-pipeline-upload` | 5 時間 |

## 2. 最初の 1 回だけ、オーナーがすること

**この PR をマージする前に**行う。Credential が無いと、配備のスクリプトが `n8n Credential not found: SHAMAN / Run Webhook (httpHeaderAuth)` で止まる。

1. 合言葉を作る。例：ターミナルで `openssl rand -hex 32`。
2. luna の n8n で Credential を作る。
   - 種類：**Header Auth**
   - Name：`X-SHAMAN-Run-Token`
   - Value：1 の合言葉
   - Credential の名前：`SHAMAN / Run Webhook`（Header Auth の Credential がほかに無ければ、既定の名前のままでもよい）
3. GitHub の Repository の Settings → Secrets and variables → Actions → **Secrets** に、`SHAMAN_RUN_WEBHOOK_TOKEN` を作り、1 の合言葉を入れる。

## 3. 実行のしかた

1. GitHub の Actions → 「SHAMAN | n8n Workflow実行」→ 「Run workflow」。
2. Branch は `main`、Workflow は `connection` / `test` / `production` から選ぶ。
   - `test` のときだけ、`test:` で始まる入力欄（ステップ・KEYWORD・OPTION1・SITE_ID・入力の結果ID・入力を取る本番の実行ID・基準の結果ID・モデル・回数）を使う。既定の値のままなら P1 を 1 回動かす。評価のシートと見出しは、Workflow が無ければ作る（[PROMPT_EVAL_DESIGN.md](PROMPT_EVAL_DESIGN.md) §10）。本番が途中で止まったときは、入力を取る本番の実行ID に n8n の実行 ID を書くと、止まったステップだけを同じ入力で試せる（同 §4.3）。
3. 終わったら、ジョブのログの `SHAMAN_RUN_OK` の下に結果が出る。失敗なら `SHAMAN_RUN_FAILED` と理由が出る。
4. **実行を読むだけ**：`inspect_execution` に n8n の実行 ID を書くと、Workflow は動かさず、その実行の状態（動いている途中か・終わったか）・最後に動いたノード・エラー・ノードごとの開始時刻と所要時間（最後の 25 件）をログに出す（`SHAMAN_INSPECT_OK`）。データの中身（記事の本文など）は出さない。Workflow の選択は何でもよい。動いている実行の後ろに並ばない。
5. **止まった行の再開**（production だけ）：本番の実行が途中で止まる（異常終了・エラー）と、「記事キュー」の行は STATUS「処理中」のまま残り、次の実行は拾わない。`recover_execution` にその n8n の実行 ID（行の「実行ID」）を書いて production を動かすと、`run_shaman_workflow.py` が n8n の API（読むだけ）で次を確かめてから、その行を最初からやり直す。人がシートを手で戻さなくてよい（2026-11-09）。
   - その実行が本番の Workflow の実行で、止まっている（`crashed`・`error`・`canceled`）。
   - その実行の記録（`runData`・`lastNodeExecuted`）が読める。無い・空・形が違うときは、WordPress に送っていないと言えないので止める。
   - その実行で、WordPress のノード（名前に `WP` を含むもの）と、そこからつながる先のノード（`Build WP Post`・`Parse WP Response`・キューの完了の書き込みなど。次の行へ戻るループ `Loop Over Items` まで）が 1 つも動いておらず、止まったノードもそれより前（例：実行 11 の `P6: Generate Image`）。そうでなければ、送信中・送信済みの可能性があるので止める（WordPress を確かめてから手で直す）（PR #90 レビュー B2）。
   - 本番の実行がほかに動いていない（`running`・`waiting`・`new` が無い）。
   - Workflow は、STATUS が「処理中」・実行ID がその実行・WP_POST_ID が空の行**だけ**を拾う（`Filter Rows`・`Queue: Check Claim`）。再開のときは、ほかの「待機中」「画像エラー」の行は処理しない。対象の行が無ければ何もせずに止まる（PR #90 レビュー B1）。拾った行の実行ID は、新しい実行の ID に書き換わる。

| エラー | 意味 |
|---|---|
| `HTTP 403` | 合言葉が n8n の Credential と一致しない |
| `HTTP 404` | Webhook が登録されていない（Workflow がまだ公開されていない。main へのマージで配備される） |
| `workflow finished with NG: …` | 最後まで動いたが、キューや評価の行が完了していない。理由と行の一覧がログに出る |
| `no explicit run result (shamanRun)` | 実行結果のノードまで進まずに終わった（ふつうは起きない。起きたら n8n の実行履歴を見る） |
| `HTTP 500` | Workflow が途中で失敗した・NG だった。下に `n8n execution …: last node=…, error=SHAMAN_RUN_NG: …` が出る |
| `Cannot reach … timed out` | n8n に届かない、または待つ上限を超えた。上限を超えたときは、下にこの呼び出しの実行の状態と最後に動いたノードが出る（2026-11-08、本番の実行 11 が 5 時間応答しなかった） |
