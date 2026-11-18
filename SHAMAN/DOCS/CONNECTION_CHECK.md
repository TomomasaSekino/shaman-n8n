# SHAMAN 接続確認 Workflow

（2026-11-03 作成）

## 1. 目的

luna の n8n から、本番で使う外部サービスにすべてつながるかを確かめる。記事は作らない。

- プロンプトの評価（`PROMPT_EVAL_DESIGN.md`）とは**別に**実施する。
- 「別の Workflow は増やさない」という以前の制約は、2026-11-03 にオーナーが変更した。接続確認は、独立した最小構成の Workflow にする。

## 2. 構成

| 項目 | 値 |
|---|---|
| Workflow の名前 | `SHAMAN: Connection Check` |
| Workflow の ID | `shaman_connection_check` |
| 生成元 | `SHAMAN/connection_check.py`（`generate_workflows.py` を実行すると、一緒に生成される） |
| 生成物 | `SHAMAN/workflows/SHAMAN_Connection_Check.json` |
| 起動 | Manual Trigger だけ。Webhook はない。公開（publish）しない |

外部への接続のノードは、`generate_workflows.py` の組み立て関数（`apiHttp`・`wordpressHttp`・`discordNode`・`driveSearchNode`・`driveDownloadNode`・`sheetsNode`）で作る。本番の Workflow も、同じ関数と同じ Credential に移す（§7）。API キーや Webhook の URL は、ノードにも `$env` にも書かない。認証は、n8n の Credential が付ける。

## 3. 確かめること

ノードは 1 本の列につなぐ。どのノードも、失敗しても次へ進め、最後にまとめて判定する。そのため、1 回の実行で、失敗したサービスをすべて確認できる。

| サービス | すること | OK の条件 |
|---|---|---|
| Google Sheets | 試験用シートを読み、予約行（KEYWORD `__SHAMAN_AUTO_E2E__`、1 件だけ）の `実行ID` 列に `CONN-<実行ID>` を書いて、読み直す | 書いた値が、読み直した行にある |
| Gemini | モデルの一覧を取得する | 本番で使うモデル（`MODEL_PRO`・`MODEL_FLASH_LITE`・`MODEL_IMAGE`）が一覧にある |
| Anthropic | 本番で使うモデル（`MODEL_CLAUDE`）の情報を取得する | 取得したモデルの ID が一致する |
| OpenAI | 本番で使うモデル（`MODEL_IMAGE_OPENAI`）の情報を取得する | 取得したモデルの ID が一致する |
| WordPress | `sites.json` の各サイトで、`/wp-json/wp/v2/users/me?context=edit` を読む（読むだけ） | ユーザー ID が返る。`sites.json` が空なら「未確認」で NG |
| Google Drive（サイトごと） | 本番の P6 と同じく、`SHAMAN/IMG` の中の、名前が IMG_FOLDER（無ければ SITE_ID）のフォルダーを探し、その中の画像を 1 枚ダウンロードする（2026-11-06 から。前は IMG の中の最初のフォルダーを使っていた） | サイトごとに、画像の中身を取得できる |
| Discord | 結果の一覧を、通知用のチャンネルに送る | 送信がエラーにならない |

最後の `Conn: Result` ノードで判定する。すべて OK なら実行は成功し、`SHAMAN_CONNECTION_CHECK_OK` を返す。1 つでも NG があれば、`SHAMAN_CONNECTION_CHECK_FAILED: <サービス>（<理由>）/ …` と、`shamanRun.ok: false` を返す。次の `Run: Check` が、NG なら例外で止め、実行を Error で終わらせる（エラー文は `SHAMAN_RUN_NG: <サービス>（<理由>）/ …`。Actions はこれを n8n の API で読んでログに出す。[RUN_FROM_ACTIONS.md](RUN_FROM_ACTIONS.md)）。

- `sites.json` にサイトがないうちは、WordPress を確かめられないので、実行は必ず失敗する（WordPress だけが NG になる）。確かめていない接続を OK とは扱わない。ほかのサービスの結果は、エラー文と Discord の一覧で確認する。
- Anthropic・OpenAI は、指定したモデルの情報を取得できれば OK とする（別名を指定すると、日付つきの正式な ID で返ることがあるため）。
- Discord に送るエラー文では、URL の `key=` などの値を伏せ、1 件 300 文字までにする。
- 予約行を 1 件に特定できないとき（0 件・複数件・読み込みの失敗）は、書き込まない（`Conn: Sheets Row Found?` で分ける）。Google Sheets を NG にして、ほかのサービスの確認は続ける。
- モデルの情報や一覧を取得しても、課金はされない。記事の生成や画像の生成はしない。
- 書き込むのは、試験用シートの予約行の `実行ID` 列と、Discord の通知だけ。本番のシートと WordPress には書かない。
- 予約行は、接続確認のためだけの行（前は自動 DRY_RUN と共用していた。自動 DRY_RUN は 2026-11-04 に廃止）。

## 4. Credential

Credential は、オーナーが luna の n8n に作る。値（API キー・パスワード・Webhook の URL）は n8n の中だけに置き、GitHub には置かない。

**Credential の ID は、Repository Variable に登録しない**（2026-11-05 に変更）。配備のスクリプトが、n8n の公開 API（`GET /api/v1/credentials`。Secret は返らない）で Credential の一覧を取り、ID を Workflow に入れる（2026-11-05 から）。

- **名前は問わない**。n8n が付けた既定の名前（`Anthropic API` など）のままでよい。その**種類の Credential が n8n に 1 件だけなら、それを使う**。
- 同じ種類が 2 件以上あるときだけ、下の表の名前と一致する 1 件を使う（大文字・小文字・空白も同じ）。他のプロジェクトの Credential と同じ種類になるとき（例：Google Service Account API）は、SHAMAN が使う方の名前を表のとおりにする。
- LLM（Gemini・Anthropic・OpenAI）の Credential は他のプロジェクトと共用するので、名前にプロジェクト名を入れない。
- 配備のスクリプトは、ノードの Credential の名前も n8n の実際の名前に置き換えてから送る。n8n は保存のとき名前を実際の名前に直すので、置き換えないと読み戻しが一致しない（`n8n workflow readback mismatch`）。一致しないときは、違っていたノードの名前と項目名をエラーに出す（値は出さない）。

| 同じ種類が 2 件以上あるときの名前 | n8n の種類 | Workflow の置き換え用の記号 |
|---|---|---|
| `Google / Sheets` | Google Service Account API（`googleApi`）。シートと Google Drive の両方に使う | `SHAMAN_GOOGLE_SHEETS_CREDENTIAL_ID` |
| `Google / Gemini` | Google Gemini(PaLM) API（`googlePalmApi`） | `SHAMAN_GEMINI_CREDENTIAL_ID` |
| `Anthropic / API` | Anthropic（`anthropicApi`） | `SHAMAN_ANTHROPIC_CREDENTIAL_ID` |
| `OpenAI / API` | OpenAI（`openAiApi`） | `SHAMAN_OPENAI_CREDENTIAL_ID` |
| `SHAMAN / Discord / Notify` | Discord Webhook（`discordWebhookApi`） | `SHAMAN_DISCORD_CREDENTIAL_ID` |
| `SHAMAN / WordPress / MultiSite` | HTTP Basic Auth（`httpBasicAuth`） | `SHAMAN_WORDPRESS_MULTISITE_CREDENTIAL_ID` |

- 1 件に決まらない Credential が 1 つでもあれば、決まらないものを全部並べて、n8n を変える前に配備を止める（`not found`：その種類が 0 件 / `is ambiguous (name not matched)`：同じ種類が 2 件以上あり、名前も一致しない / `is ambiguous`：名前も一致するものが 2 件以上）。エラーには、その種類の Credential の名前が並ぶ（値は出ない）。
- 一覧を取るには、配備用の API キー（`N8N_DEPLOY_API_KEY`）に `credential:list` の権限が要る。権限がなければ、配備を止める（`cannot list n8n Credentials`）。
- Google Drive は、シートと同じサービスアカウントの Credential（`Google / Sheets`）で読む（`DRIVE_AUTH = 'serviceAccount'`、2026-11-10）。サービスアカウント（`shaman-sheets@example-project.iam.gserviceaccount.com`）には、Google Drive の `SHAMAN/IMG` を閲覧者として共有する。
  - 前は OAuth2 の `SHAMAN / Google Drive`（2026-11-03 に認証）を使っていた。2026-11-10 に本番の実行 45 が `P6: Find Image Folder` で止まった（`The credential "SHAMAN / Google Drive" needs to be reconnected`）。OAuth2 は、同意画面が「テスト」だとトークンが 7 日で切れ、人がログインし直すまで使えない。認証から 7 日目だったので、この期限が原因と見ている（同意画面の設定は確かめていない）。無人で動かすので、期限の無いサービスアカウントに揃えた。`SHAMAN / Google Drive` は、どの Workflow も使わない（n8n から削除してよい）。
- 参考画像のフォルダー（Google Drive の `SHAMAN/IMG`）の ID は、`SHAMAN/config/drive.json` の `IMG_FOLDER_ID` に置く（URL の `/folders/` の後ろ。Secret ではない）。生成のときに Workflow に入る。
- 配備のスクリプトは、ID（Credential・スプレッドシート）が英数字と `-` `_` だけでできていることを確かめる。引用符などが入っていれば、配備を止める。試験用と本番のスプレッドシートの ID が同じでも止める。

## 5. 配備と実行

1. オーナーが、§4 の Credential を作る（名前は既定のままでよい。同じ種類が 2 件以上になるときだけ表の名前にする）。
2. main へのマージで、Actions が接続確認 Workflow を配備し、読み戻して一致を確かめてから**公開**する（入口は合言葉付きの Webhook `shaman-connection-check` だけ。[RUN_FROM_ACTIONS.md](RUN_FROM_ACTIONS.md)）。配備はプロンプト評価 Workflow とは別のジョブ（`deploy-connection-check`）で行うので、そちらの配備が失敗しても、接続確認 Workflow は配備される。配備の条件（オーナーによる main へのマージだけ）は同じ。
3. Actions の「SHAMAN | n8n Workflow実行」で `connection` を選んで実行する。n8n の画面で「Execute workflow」から実行してもよい。
4. 結果は、`Conn: Result` の成否と Discord の通知で確認する。実行の中身は、実行履歴に残る（成功・失敗とも保存する設定）。

本番で使うサイトの登録（共通 Credential の式と「サイト登録」タブ）は、[WP_DYNAMIC_MULTISITE.md](WP_DYNAMIC_MULTISITE.md) の手順で行う。この接続確認が WordPress を確かめるのは、`SHAMAN/config/sites.json` にあるサイトだけ。新しいサイトもこの接続確認で確かめるには、`sites.json` にも足す。WordPress のノードは増やさない。

## 6. この Workflow で分からないこと

- 記事の中身（P1〜P5 の生成、P4 の品質チェック）
- 画像の生成（P6）と、WordPress へのメディアのアップロード・記事の投稿（P7）
- サイトごとの参考画像のフォルダーがそろっているか

出力の品質は、プロンプト評価 Workflow（`PROMPT_EVAL_DESIGN.md`）で、ステップごとに確かめる。

## 7. 本番の Workflow との関係（2026-11-04 移行）

本番の Workflow も、この Workflow と同じ組み立て関数・同じ Credential で外部につなぐ。`$env` と `require` は使わない（プロンプト評価 Workflow も、本番の組み立て関数で作るので同じ）。

| 本番のノード | 前（n8n 2.x では動かない） | 今 |
|---|---|---|
| Gemini（P1〜P3、P5、P6 の分析）・Claude（P4） | `$env` の API キーをヘッダーに書く | `apiHttp`（`googlePalmApi`・`anthropicApi`） |
| 参考画像（P6） | Code ノードの `require('fs')` でローカルのフォルダーを読む | `P6: Find Image Folder` → `P6: List Reference Images` → `P6: Download Reference Image`（Google Drive の `SHAMAN/IMG/<IMG_FOLDER または SITE_ID>/`、最大 16 枚） |
| 画像の生成（P6） | Code ノードの `require('https')` と `$env.OPENAI_API_KEY` | `Build Image Requests`（multipart の本文を組み立てる）→ `P6: Generate Image`（`openAiApi`）→ `Collect Generated Images` |
| WordPress（P6 のメディア、P7 の投稿） | Code ノードの自前HTTP認証 | 共通 `WP: Upload Media`・`WP: Create Post`（HTTP Basic Auth）。SITE_IDでCredential内の認証値を選ぶ。記事は従来どおり逐次実行 |
| Discord | `$env.DISCORD_WEBHOOK_URL` | `discordNode`（`discordWebhookApi`） |
| モデル名・画像の設定 | `$env` で上書きできた | `generate_workflows.py` の定数を、生成のときに埋め込む |

- **画像の生成のリクエスト**：OpenAI の images/edits は、参考画像を `image[]` の繰り返しで受け取る。HTTP Request ノードの multipart は項目の数が固定なので、本文を Code ノードで組み立て、バイナリとして送る。
- **再試行**：429・5xx・通信エラーになった節だけを、10 秒待って作り直す（最初の 1 回を含めて 3 回まで。`P6: Retry Images?` → `P6: Retry Wait`）。そのほかの 4xx（`moderation_blocked` など）は作り直さない。WordPress のメディアのアップロードは、再試行しない（失敗すれば、今までどおり途中保存して「画像エラー」にする）。
- **サイトが登録されていないとき**：本番用スプレッドシート「SHAMAN本番用」の「サイト登録」タブに無い SITE_ID は、`Filter Rows` で止める。P1 より前なので、記事も画像も作らない（PR #101。2026-11-15 の実行 1299 で確認）。
- **参考画像の上限**：1 サイトで 16 枚まで、合計 10MB まで（節ごとのリクエストに、すべての参考画像が入るため）。合計が上限を超えたら、`Read Reference Images` で止める。16 枚を超える画像があると、Drive が返した 16 枚を使う。
- **Discord**：成功の通知は 1900 文字までにし、送れなくても次の行の処理へ進む。エラーの通知は、エラー文の `key=` などの値を伏せる。
- **配備**：本番とプロンプト評価の Workflow も、§4 の Credential を参照する。配備の前に、§4 の Credential を作っておく（なければ、配備のスクリプトが「n8n Credential not found」で止まる）。
- **実機で未確認**：Code ノードが作ったバイナリの Content-Type（`multipart/form-data; boundary=…`）が、そのまま HTTP Request ノードから送られるか。Code ノードの `this.helpers.getBinaryDataBuffer`。プロンプト評価は画像の生成を扱わないので、本番の最初の 1 件で確かめる。

