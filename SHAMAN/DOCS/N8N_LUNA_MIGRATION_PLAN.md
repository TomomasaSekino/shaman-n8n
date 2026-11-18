# SHAMAN n8n luna 移行・デプロイ計画

Status: 完了（2026-11-10）。本文のPhaseは当時の計画として保存。
Date: 2026-10-28（v0.1: 2026-09-22）

> 本書は当時の移行計画を残した記録。lunaへの移行は2026-11-10に完了しており、旧Windows / Lima / Docker / iCloud / xlsx手順は現在使わない。
> 参照: ASFNPS `docs/project/N8N_MIGRATION_PLAN.md` / `docs/operations/N8N_CONFIGURATION_POLICY.md` / `scripts/deploy_n8n_workflow.py`

---

## この計画以降の変更点（現行の運用）

- macOS版 n8n 2.35.7がlunaでLaunchDaemon常駐（2026-11-01から）。旧Lima / Docker / bindfs / iCloudは不使用。旧構成の設定記録（`LUNA_N8N_SETTINGS.md`・`SHAMAN/infra/luna/`）と取得スクリプトは2026-11-18に削除した（必要ならGitの履歴から参照）。
- キューは標準Google Sheetsノード（[設計](QUEUE_GOOGLE_SHEETS_DESIGN.md)）。
- WordPressは共通Credential 1件で`https://<SITE_ID>`に送信。「サイト登録」タブの未登録SITE_IDは受付拒否（[詳細](WP_DYNAMIC_MULTISITE.md)）。
- 配備はオーナーによるmainマージ時のみ[deploy-shaman-n8n.yml](../../.github/workflows/deploy-shaman-n8n.yml)。実行は[Actions](RUN_FROM_ACTIONS.md)または毎日6:00 JST・最大1行の[Daily Schedule](UNATTENDED_OPERATION.md)。監視は5分ごとの`com.shaman.watchdog`。
- `SHAMAN/Docker_Environment/`の旧2ファイルは**PR #105 で削除（2026-11）**。

## 1. 目的と前提

### 1.1 現状

- n8n は作業端末（Windows）の `C:\n8n` で動いている。構成は Docker、`n8n:latest`、SQLite。
- Workflow は `SHAMAN/generate_workflows.py` で生成し、`docker exec n8n n8n import:workflow` で取り込んでいる。
- Secret の読み先は2か所ある。
  - `$env`：LLM の API キー、Discord の Webhook URL
  - キュー xlsx の「サイト共通設定」シート：WordPress の認証情報
- GitHub Actions は存在しない。

### 1.2 目的

- 本番の実行基盤を、luna 上の Lima VM で動く n8n（ASFNPS と共有）へ移す。
- GitHub main を正本（SoT）とし、GitHub Actions から n8n へ再現可能な形で配備する。
- 今の開発ループ（pytest → generate → npm test → LLM レビュー）は維持する。

### 1.3 前提

- ASFNPS は 2026-10 に本番の Workflow を luna へ配備済みで、次の2点は実証された。SHAMAN はこれをそのまま使う。
  - main への push → luna の self-hosted runner → `127.0.0.1:5678` の公開 API で、名前をキーに作成・更新する配備経路
  - Credential の参照方式（§2.2）
- これ以外の SHAMAN 固有の項目（HTTP 呼び出しの移行、キューと画像の置き場所、iCloud 経由の入力、Webhook の activate）は、SHAMAN 自身の Phase で検証する。
- 即時移行はしない。Phase ごとに試験しながら段階的に進める。

---

## 2. 確定した方針（2026-09-22 合意）

### 2.1 開発と反映の流れ（2026-10-28 改訂）

- ローカルの n8n（`C:\n8n`）は破棄する。移行後の SHAMAN の実行環境は、すでに設定済みの luna の共有 n8n だけとし、SHAMAN 側でその設定を変えない（§2.4）。
  - ローカルの n8n は移行失敗時の復帰先にしない（Phase G のロールバック）。`SHAMAN/Docker_Environment/` は、切り替えの判定後に削除する（Phase G-5）。**実施済み：PR #105 で削除（2026-11）。**
- ソースの流れは一方向に限る。
  - `git 作業ツリー → generate_workflows.py → PR → CI → main へ merge → GitHub Actions → luna`
- n8n の UI での編集は正本として扱わない。export して repo に戻すこともしない。
- luna への手動 import は禁止する。
- 実機での確認は、luna に配備した Test Workflow（`SHAMAN / Blog / Pipeline Test`）で行う。Test Workflow では本番データを扱わず、試験用のキューと試験用の出力先を使う。
  - **2026-11-04 追記**：Test Workflow は、プロンプト評価 Workflow（`SHAMAN: Prompt Eval`、同じ ID）に置き換えた。LLM のステップを 1 つずつ評価し、キュー・WordPress・Discord は使わない（`PROMPT_EVAL_DESIGN.md`）。外部接続は接続確認 Workflow で確かめる（`CONNECTION_CHECK.md`）。G-1 の統合ゲート（Test Workflow での E2E）と、本番の公開の条件 `SHAMAN_E2E_PASSED_SHA` の扱いは、別に決め直す。

### 2.2 Secret

- Secret はすべて n8n Credentials に置き、`$env` と xlsx から Secret をなくす。
- Credential 名は `SHAMAN / <SERVICE> / <PURPOSE>` とする。
  - **2026-11-01 追記**：他のプロジェクトと共用できる Credential は共用する。共用するものの名前には、プロジェクト名を入れない（`<SERVICE> / <PURPOSE>`）。SHAMAN だけが使うものに限り、`SHAMAN / ` を付ける。§3 の名前は、この規則に読み替える（§3 の追記）。
  - Workflow JSON には Credential の名前と、ID の置き換え用の記号 `__ENV:SHAMAN_<SERVICE>_CREDENTIAL_ID__` を書く（ASFNPS と同じ方式）。
  - 実際の ID は GitHub の Repository Variable に置き、配備スクリプトが配備時に置き換える。値が空なら配備を止める。ID は Secret ではない。
  - **2026-11-05 変更**：Credential の ID は Repository Variable に置かない。配備スクリプトが n8n の公開 API（`GET /api/v1/credentials`）で一覧を取り、ノードに書いた種類の Credential が 1 件だけならその ID で置き換える（名前は問わない。同じ種類が 2 件以上のときだけ名前で選ぶ。決まらなければ止める）。配備用の API キーには `credential:list` の権限が要る（`DOCS/CONNECTION_CHECK.md` §4）。参考画像のフォルダーの ID は `SHAMAN/config/drive.json` に置く。
  - Git で管理する `SHAMAN/workflows/*.json` は置き換え前の記号のまま保つ。`generate → git diff --exit-code` の再現性を崩さないため、端末固有の ID は書き込まない。
  - ID の変数が未設定・空のときは止める。存在しない ID を指したとき、再配備後も Credential との紐付けが保たれることは E2E で確認する（§6）。
- Credential は、luna にすでにある有効なもの（Phase A で再発行した値が入っているもの）を共有・再利用し、足りないものだけを作る。xlsx から WordPress の認証情報を取る方式は廃止する。
  - **2026-11-01 追記**：macOS 版の n8n では、Credential は初期化から始める。前に使っていた Credential は、オーナーが値を変更したうえで削除した。§3 の Credential は、すべて macOS 版の n8n に新しく作る（C-6。作るのはオーナー）。ID は、これまでどおり Repository Variable に置く。
- n8n の API キーはプロジェクトごとにスコープを絞って発行し、Repository Secret `N8N_DEPLOY_API_KEY` に保存する。

### 2.3 Secret でない実行時設定

（2026-10-28 決定、PR #32 のレビューを受けて v0.2 で改訂）

- **環境変数へのアクセス**：共有 n8n の `N8N_BLOCK_ENV_ACCESS_IN_NODE=true`（n8n v2 の既定）を維持する。`$env` を読めるように解除すると、共有 n8n のプロセスの環境変数すべてと、Code ノードの `fs` 経由で機密ファイルが読める経路が残るため。ASFNPS など他プロジェクトの設定は変更しない。
- **モデル設定**：モデル名は `generate_workflows.py` のモデルレジストリで一元管理し、生成 JSON に埋め込む。Workflow は実行時に `$env` を読まない。`IMAGE_PROVIDER`、`OPENAI_IMAGE_QUALITY` などの Secret でない設定も、同じく generate 時に埋め込むか `sites.json` から取る。
- **モデルの変更**：GitHub で変更 → CI → PR レビュー → マージ → luna へ配備、の1経路に統一する。luna 上で値を手で変える経路は作らない。
- **認証情報**：API キーなどは引き続き n8n Credentials で管理する（§2.2）。モデル名などの設定と認証情報を混同しない。

### 2.4 luna の既存設定に合わせる（2026-10-28 改訂）

- luna の n8n（2.39.8、ASFNPS と共有）はすでに設定済みである。SHAMAN のためにイメージ、`NODE_FUNCTION_ALLOW_*`、`EXECUTIONS_TIMEOUT` などのインスタンスの設定を変えない。
- 今の SHAMAN が特別な設定を必要とするのは、Code ノードで `require('fs')`、`require('path')`、`require('xlsx')`、`require('exceljs')`、`require('https')` などを使っているためである。これらを n8n 標準のノードに置き換え、Code ノードでは `require` を使わない（Phase C-3）。
  - ファイルの読み書き：Read/Write Files from Disk
  - xlsx の読み込み：Extract from File
  - xlsx への書き戻し：下の「キューの書き戻し」の方式による（Convert to File は使わない）
  - HTTP の呼び出し：HTTP Request（Credential を参照する）
- 実行時間の上限は、インスタンスの設定ではなく Workflow ごとのタイムアウト（`settings.executionTimeout`。インスタンスの上限の範囲内）で指定する。
- **キューの書き戻し**：Extract from File はブックを JSON に変え、Convert to File は JSON から新しいブックを作るノードで、既存のブックの書式、他のシート、数式、列幅を保ったまま特定のセルだけを更新する手段にはならない。そのため次のようにする。
  - 既存のブックを保ったまま対象のセルだけを更新できる手段を、Phase C で選んで確定する。候補は、n8n の SSH ノードから luna 上のスクリプト（例：Python の openpyxl）を実行する方式（ASFNPS と同じ形で、共有 n8n の設定は変えない）。この方式は、n8n から到達できる SSH サーバーとアクセス権があることが前提になる。採る場合は、キューの読み込み（SSH での取得など）から書き戻しまでの経路を Phase C で一貫して確定する。
  - どの手段でも、次を満たさない構成では本番のキューを書き換えない（fail-closed）。
    - 書き込みの前に原本をバックアップする。
    - 書き込んだ結果を原本と比べ、対象のセル以外（シートの一覧、他のセルの値と数式、書式）が変わっていないことを確かめる。変わっていれば書き込みを捨てて止め、Discord に通知する。
    - 一時ファイルに書いてから置き換え、途中で失敗しても壊れたブックが残らないようにする。異常時はバックアップから戻す。
- **iCloud のキューと参照画像の受け渡し**：共有 n8n への設定の追加を必要条件にしない。上の SSH 方式なら、キューは luna 上で読み書きでき、マウントは要らない。参照画像の受け渡し方法も Phase F で決める。共有 n8n の設定に手を入れる案を採る場合は、変更の範囲が SHAMAN に閉じていて、他プロジェクトの Workflow の動作が変わらないことを合格条件にする。

- **2026-10-30 追記（構築結果）**：オーナーが luna に SHAMAN 専用のマウントを構築した。経路は iCloud の `SHAMAN` → Lima 9p → bindfs → n8n の `/home/node/.n8n-files/SHAMAN` で、再起動後の読み書きまで確認している。構成は `SHAMAN/infra/luna/README.md`、記録は `LUNA_N8N_SETTINGS.md` §4 にある。
  - キュー・参照画像・途中保存の置き場所と読み込みは、このマウントを使う。上の SSH 方式は、書き戻しの手段の候補として残す。書き戻しの手段は C-3 で確定する。
  - Workflow は、ファイルを読み書きする前に目印の `.shaman-mount-ok` を確かめ、無ければ止まる（fail-closed。infra README §3）。
  - GUI からログアウトしている間の iCloud の新規同期は、まだ確立していない。解決するまで、キューを本番で使わない。

- **2026-11-01 追記（macOS 版と Google スプレッドシート）**：luna の n8n が macOS 版（LaunchDaemon、2.35.7）に変わり、上のマウントは使わなくなった。さらに、luna は GUI でログインしていない状態で動かすため iCloud が同期しない（luna-user の `bird` が動かない）。GUI の常時ログインは採らないので、**キューは Google スプレッドシートに移し、n8n の標準の Google Sheets ノードで読み書きする**（オーナーの決定）。上の書き戻しの手段の選定（SSH／openpyxl）は不要になった。設計は [QUEUE_GOOGLE_SHEETS_DESIGN.md](QUEUE_GOOGLE_SHEETS_DESIGN.md)（認証、排他制御と重複投稿の防止、sites.json によるマルチサイトと Credential の分離）。途中保存の置き場所は、Phase F で決め直す。

### 2.5 ファイルとデータの置き場所

| 対象 | 性質 | 正本の置き場所 | luna への渡し方 |
|---|---|---|---|
| 参照画像（今は `C:\n8n\img`、約14ファイル・3.6MB） | 素材（コード側の資産） | repo の `SHAMAN/assets/img/<siteId>/` | main への merge を契機に、配備ジョブが luna へ同期する（受け渡し方法は Phase F。§2.4） |
| 「サイト共通設定」（URL・執筆者名・一人称・画像フォルダ・META_KEY） | Secret でない設定 | repo の `SHAMAN/config/sites.json`。xlsx のシートは廃止する | Workflow の生成時に埋め込む |
| WordPress の認証情報 | Secret | n8n Credentials | luna で手作業で作る |
| キューの行（KEYWORD / STATUS / 結果 / ログ） | 業務データ（人が入力し、n8n が書き戻す） | iCloud Drive 上の xlsx | luna の iCloud Drive → Lima VM → コンテナの `/home/node/.n8n-files` |
| キューのテンプレート（列の定義） | 仕様 | repo（`SHAMAN/DOCS/SEO記事キュー_テンプレート.xlsx`） | - |
| `article_draft_<row>.json` | 再開用の途中保存 | luna の data フォルダだけ（Git には入れない） | 移送しない |

- キューは、作業端末からも luna からも参照しやすく、モバイルからも操作できることを理由に iCloud Drive で受け渡す。
- 入力画面（UI）や DB は当面作らない。次のどれかが起きたら再検討する。
  - バルク実行の件数が増えたとき
  - 複数人で編集するようになったとき
  - 競合コピーの検知が繰り返し発生したとき

### 2.6 キューの行の特定方法

- ID 列は追加しない。行を複製したときに ID が重複する問題を新たに生むため。
- 代わりに、行番号の算出を直し、照合によって fail-closed にする（Phase C-7）。

### 2.7 CI の実行場所（2026-10-28 決定）

- CI も luna の self-hosted runner で動かす。GitHub-hosted の無料枠を使い切って開発が止まる事態を避けるため（self-hosted は現時点で無料、分数にも数えない）。
- 形は未決（§5）。推奨は CI 専用の Lima VM。別ユーザーはカーネルと localhost を共有するため、境界が弱い。
- 個人アカウントのリポジトリでは runner はリポジトリ単位で登録され、ASFNPS や ARBW 用の既存 runner を SHAMAN から指定することはできない。そのため SHAMAN 用の登録が必要になる（同じ VM・同じユーザーの下に置くことはできる）。共有するには organization への移行が必要で、本計画の範囲外とする。
- Lima VM を選ぶ場合は、VM の OS と、VM から luna 本体（`host.lima.internal`）への通信の遮断方法を Phase F で決める。
- 安全条件：
  - CI から luna の localhost（n8n の `:5678`、Lima の公開ポート）への通信を pf で遮断する。
  - CI 側に Secret を置かない。本番のデータと設定のディレクトリは読めない権限にする。
  - 配備ジョブは CI とは別のユーザー・別の runner で動かす。
- luna が使えないときのために、GitHub-hosted を予備として残す（Phase E）。自動では切り替えない。オーナーが明示して手動実行したときだけ使う。

---

## 3. 対象の Credential

| 名前 | 種類（要確認） | 置き換える元 |
|---|---|---|
| `SHAMAN / Google / Gemini` | Google Gemini API | `$env.GOOGLE_AI_API_KEY` |
| `SHAMAN / Anthropic / Strengthen` | Anthropic API | `$env.ANTHROPIC_API_KEY` |
| `SHAMAN / OpenAI / Image` | OpenAI API | `$env.OPENAI_API_KEY` |
| `SHAMAN / Discord / Notify` | Discord Webhook | `$env.DISCORD_WEBHOOK_URL` |
| `SHAMAN / WordPress / <siteId>` | WordPress API | xlsx の `WP_USER` / `WP_APP_PASSWORD` |

- WordPress の Credential はサイトごとに作れる。ただし、どの Credential を使うかはノードごとに固定され、式で行ごとに切り替えることはできない。
- そのため、サイトごとの分岐は `sites.json` から生成する（Phase C-4）。
- **2026-11-01 追記（名前の読み替え）**：§2.2 の追記の規則で、名前は次のようにする。共用するかどうかは、作るとき（C-6）にオーナーが決める。
  - 共用する前提：`Google / Gemini`、`Anthropic / API`、`OpenAI / API`、`Google / Sheets`（キュー。[QUEUE_GOOGLE_SHEETS_DESIGN.md](QUEUE_GOOGLE_SHEETS_DESIGN.md) §3）
  - WordPress：`WordPress / <siteId>`（サイトで決まり、プロジェクト名は要らない。サイトごとに分ける）
  - Discord：通知先のチャンネルが SHAMAN 専用なら `SHAMAN / Discord / Notify`、共用のチャンネルなら `Discord / <チャンネル>`
  - Workflow JSON に書く ID の置き換え用の記号（`__ENV:SHAMAN_<SERVICE>_CREDENTIAL_ID__`）は変えない。SHAMAN の repo の Repository Variable の名前なので、共用の Credential でも、その ID を SHAMAN の repo に置く。

---

## 4. 実施計画

### Phase A：Secret の衛生対策（他の Phase と独立）

- **再発行**
  - 過去の Git 履歴（2026-03〜04 のコミット）に、`.env` とキュー xlsx が含まれていた。
  - その中に、本物と同じ形式の Google API キーと WordPress のアプリケーションパスワードがある。
  - 該当するキーとパスワードを再発行し、古いものは失効させる。新しい値は Credential に入れる。
- **全履歴のスキャン**
  - gitleaks で全履歴をスキャンする。`--redact` で値を伏せて出力する。
  - 見つかったものは同様に再発行する。
- **再発防止**
  - CI に gitleaks を組み込む（Phase E）。
- **履歴の書き換え**
  - repo は private でフォークもないため、必須とはしない。再発行を根本対策とする。
- `C:\n8n\data\bkup` 内の古いファイルも同様に扱う。

### Phase B：SHAMAN を luna の既存設定に合わせる（2026-10-28 改訂）

- luna の n8n の今の設定を記録する（版は 2.39.8 で確認済み）。対象は `NODE_FUNCTION_ALLOW_*`、`EXECUTIONS_TIMEOUT` と `EXECUTIONS_TIMEOUT_MAX`、ファイルアクセスの制限（`N8N_RESTRICT_FILE_ACCESS_TO`）、`WEBHOOK_URL`、Apache の経路。読み取り専用で取れる値（n8n の `/rest/settings` が返す版やタイムアウトの上限など）は、配備用 runner のジョブで取得して記録する。取れない値だけをオーナーが確認する。
- 今の SHAMAN の Workflow が、その設定の中で動かない箇所を洗い出す（Code ノードの `require`、ファイルのパス、実行時間、`$env`）。
- 洗い出した箇所を Phase C の改修項目に割り当てる。

### Phase C：Workflow を Credential 前提に改修する（TDD）

対象は `SHAMAN/generate_workflows.py` と各テスト。

1. **`$env` 参照の除去**
   - `Map Variables` などからモデル名、`IMAGE_PROVIDER`、`OPENAI_IMAGE_QUALITY`、`IMG_PATH` の `$env` 参照を取り除き、generate 時にモデルレジストリと `sites.json` の値を埋め込む（§2.3）。
2. **LLM の HTTP Request ノード**
   - ヘッダーの `$env` 参照をやめ、各サービス専用の Credential を参照させる。
3. **Code ノードの `require` を標準ノードへ移す（§2.4）**
   - 途中保存と参照画像の読み込みを Read/Write Files from Disk に、キューの読み込みを Extract from File に置き換える。
   - キューの書き戻しは §2.4 の方式で作り直す。方式を確定し、バックアップ、差分の検証、一時ファイルからの置き換え、異常時の復旧をテストで確かめる（exceljs を使っていた理由である、ブックを保つことを満たすため）。
   - 外部への呼び出しは HTTP Request ノードへ移す。Code ノードは Credential を使えないため。
   - 対象は Gemini の画像生成と、WordPress 呼び出しすべて。
   - 特に `WP: Upload All Images` は、base64 を「Convert to File」でバイナリに変えてから HTTP Request でアップロードする。
   - 過去の 500 エラー対策（今は native https を使っている）が再発しないか、検証用サイトで確認する。
4. **複数サイト対応**
   - `SHAMAN/config/sites.json` を新設する。中身は siteId・baseUrl・credentialName・metaKey・imgDir などで、Secret は入れない。
   - generator は「Switch（siteId）→ サイトごとの WordPress 呼び出し」を生成する。
   - 台帳にない siteId は処理を止める（fail-closed）。
5. **Discord 通知**
   - `$env.DISCORD_WEBHOOK_URL` の参照を Credential 経由に変える。
6. **パスワードの受け渡しをやめる**
   - `Map Variables` から `wpUser` / `wpAppPassword` / `wpUrl` を削除する。
   - 今は WordPress のパスワードが実行データに平文で残っているが、これで解消する。
7. **キュー処理の改修**
   - 「サイト共通設定」シートを廃止し、`sites.json` に移す。読み込み処理（`Spreadsheet File`）とテンプレートを更新する。
   - **行番号**：空行を除く前に、実際の Excel の行の位置で `rowIndex` を振る。今は除いた後に振っているため、空行より下の行で書き戻し先がずれる疑いがある。テストで再現を確認する。
   - **書き戻し前の照合**：書き戻しの直前に、対象行の SITE_ID と KEYWORD が読み込み時と一致するか確かめる。一致しなければ書き込まずに止め、Discord に通知する。
   - **下書きの照合**：途中保存に SITE_ID と KEYWORD を記録し、「画像エラー」から再開するときに照合する。一致しなければ止める。
   - **iCloud の競合コピー**：実行の前後に「SEO記事キュー 2.xlsx」のような競合コピーを検知したら、止めて Discord に通知する。
   - 運用ルール：実行中はキューを編集しない。
8. **参照画像**
   - `SHAMAN/assets/img/<siteId>/` へ移す。画像フォルダのパスは `sites.json` から取る。
9. **テスト**
   - pytest で生成物を検査する。
     - `$env.*API_KEY`、`$env.DISCORD_WEBHOOK_URL`、Code ノード内での Basic 認証の組み立てが残っていないこと
     - モデル名を含め、`$env` の参照が生成 JSON に1つも残っていないこと
     - JSON を再生成しても、コミット済みの JSON と差分が出ないこと
     - WordPress / LLM の全呼び出しノードに Credential 参照があること
     - `sites.json` の全サイトに分岐があること
   - Jest で次を確認する。
     - 空行があっても `rowIndex` が実際の Excel の行と一致する
     - 書き戻し前の照合で一致しないと止まる
     - 下書きの照合で一致しないと止まる
     - 競合コピーを検知すると止まる
   - `test_phase2_content_quality_guard.py` が引き続き通ること。
10. **Phase C の完了条件**
    - 静的なテストと生成物の検証まで（pytest、`npm test`、LLM レビュー）。pytest で、生成 JSON の Code ノードに `require(` が残っていないことも検査する。
    - 実機の E2E は、配備経路（Phase E）と Credential・ファイルの配置（Phase F）が揃ってから、Phase G-1 で行う。

### Phase D：（v0.2 で削除）

- Credential 参照の試験は独立の Phase にしない。ASFNPS で実証済みの方式（§2.2）を採り、参照の仕組みはノードの種類に依らないため、Phase G-1 の Test Workflow の E2E の中で確認する。

### Phase E：配備経路（GitHub Actions）

- **self-hosted runner**
  - luna に SHAMAN 用の runner を別途登録する。
- **配備スクリプト `scripts/deploy_n8n_workflow.py`**
  - ASFNPS から移植する。共通ツール化は Policy §13 で後回しにしているため、コピーで始める。
  - ASFNPS から引き継ぐ動作：
    - 名前をキーにした作成 / 更新
    - 同名が複数ある場合は止める（fail-closed）
    - ページング処理
    - `__ENV:<NAME>__` を配備時の環境変数（Repository Variable）で置き換え、空なら止める（Credential の ID）
  - 追加する機能（ASFNPS には無い。すべて実装しテストが通ることを Phase E の完了条件とする）：
    - 公開 API が受け付けない項目（`id`, `active`, `meta`, `pinData`, `tags` など。実際の版で確認）を送信前に取り除く
    - 複数 Workflow（本番 / Test）を配備する
    - Webhook を有効にする（activate）
    - 参照画像を luna のマウント先へ同期する
  - 配備の対象はプロジェクトで絞らない（全プロジェクトのオーナーが同じため）。代わりに、SHAMAN が送り出す側の名前を確かめる。
    - 配備できる Workflow 名は許可リスト（`SHAMAN / Blog / Pipeline`、`SHAMAN / Blog / Pipeline Test`）で管理し、リポジトリに置く。
    - 配備する JSON の名前が `SHAMAN / ` で始まらない、または許可リストに無い場合は、API を呼ぶ前に止める。
    - 更新するのは、許可リストの名前と完全一致する既存 Workflow だけにする。同名が複数あれば止める。
    - これらを拒否する場合のテストを pytest に入れる。
  - HTTP を mock した pytest を付ける。
- **`.github/workflows/ci.yml`**（luna の CI 用 runner、PR と main。§2.7）
  - pytest → generate → 生成した JSON とコミット済み JSON の差分チェック → `npm ci && npm test` → gitleaks
  - `runs-on` は luna の runner に固定し、自動で GitHub-hosted に切り替えない。luna が使えないときだけ、オーナーが `workflow_dispatch` の入力で GitHub-hosted を指定して手動実行する。
- **`.github/workflows/n8n-deploy.yml`**（luna の self-hosted runner）
  - 最初は `workflow_dispatch` だけで動かす。安定したら「main への push で、パスを絞って起動」に切り替える。
  - concurrency を設定し、CI の成功を前提にする。
  - `127.0.0.1:5678` に直接つなぐ。
- **Workflow 名**
  - `SHAMAN / Blog / Pipeline` と `SHAMAN / Blog / Pipeline Test` にする（Policy の命名規則に合わせる。v0.2 で決定）。
  - 生成 JSON の `id`（`shaman` / `shaman_test`）は変えない。公開 API は `id` を受け付けないので、配備時に取り除く（上の「追加する機能」）。
- **旧名からの移行**
  - 旧名は `SHAMAN` と `SHAMAN: Test Workflow`。
  - luna：SHAMAN の Workflow はまだ無いので、新しい名前で新規作成になる。初回の配備の前に、旧名の Workflow が luna に無いことを確認する。あれば止め、オーナーが確認する（配備スクリプトは旧名の Workflow を変更・削除しない）。
  - Webhook の競合：Webhook のパスは n8n のインスタンス全体で共有される。activate の前に、同じパスとメソッドの Webhook を持つ Workflow が他に無いことを確認し、あれば activate せず止める。他のプロジェクトの Workflow は変更しない。
- **一時 Workflow の扱い**
  - luna に常設する SHAMAN の Workflow は、許可リストの2件（本番と Test）だけにする。診断や検証のための使い捨て Workflow は無期限に残さない。
  - 作るときに、用途・期限・作成された実際の ID を記録する（記録先は PR か Issue）。
  - 検証が終わったとき、失敗したときのどちらでも削除する。異常終了で消せなかったものは、次の作業の前に残っていないかを確認して削除する。
  - 削除してよいのは、記録した SHAMAN の ID の Workflow だけにする。名前だけで判断して削除しない。
  - 一時 Workflow の削除に失敗した場合、その検証作業は未完了として扱う。削除できたことを確認するまで、完了と報告しない。

### Phase F：luna 側の準備と検証

- `N8N_BLOCK_ENV_ACCESS_IN_NODE=true` のまま SHAMAN の Workflow が動くことを確認する（§2.3）。SHAMAN のために共有 n8n の env は追加・変更しない。
- luna の既存の有効な Credential を再利用し、足りないものだけを作る（§2.2）。使う Credential の ID を Repository Variable に登録する。
- キュー xlsx と参照画像の置き場所を luna 上に作る。受け渡しの方式は §2.4 による。
- CI 用の runner を §2.7 の形で用意し、安全条件を確認する。
- **iCloud Drive の経路を実機で検証する**
  - キューのフォルダを「ダウンロードしたままにする」に設定し、中身がクラウドにだけある状態を避ける。
  - Lima（バックグラウンドのプロセス）から iCloud Drive にアクセスできるか確認する。macOS のプライバシー保護（TCC）で拒否される場合があるので、権限の付与が必要かを見る。
  - 日本語のファイル名、n8n からの書き戻し（§2.4 の方式）と、それが作業端末・モバイルへ同期されることを確認する。マウントを使う場合は、Lima VM とコンテナへのマウントも確認する。
  - 競合コピーを検知する処理が動くことを確認する。
- 参照画像の同期先を用意し、配備ジョブによる同期を確認する。
- 途中保存（data フォルダ）のバックアップ方法を決める。

### Phase G：切り替え

前提：Phase C、E、F が完了していること（配備経路、Credential、キューと画像の配置）。

1. **統合ゲート**：Test Workflow を Actions から luna へ配備し、検証用の WordPress サイトと試験用のキューで E2E を行う（Credential の参照、キューの書き戻しの保持と fail-closed、画像のアップロード、Discord への通知）。合格したコミットの SHA、Workflow の ID、公開状態を「復帰の基準」として記録する。
2. 本番のキュー xlsx を新しい形式（「サイト共通設定」シートなし）で作り直し、iCloud Drive に置く。入力済みの行があれば、必要なキーワードと処理状態だけを確かめて引き継ぐ。旧ブックの書式やシートの完全な再現は、移行の条件にしない。
3. 本番 Workflow を配備する。配備したコミットの SHA、Workflow の ID、公開状態を記録する。
4. ローカルの本番 Workflow がまだ動いていれば停止する。二重投稿を防ぐため、並行稼働はしない。
5. luna の本番で1記事が正常に完了したら、切り替えを判定する。判定後に `SHAMAN/Docker_Environment/` を削除する。**実施済み：PR #105 で削除（2026-11）。**

- WordPress への投稿は `status: draft` のままにし、人が最終確認してから公開する。
- **ロールバック**
  - 旧 Windows 環境（ローカルの n8n）は復帰先にしない。旧 API キーは失効済みで、旧 n8n から引き継ぐデータも無いため。
  - 停止するのは SHAMAN の本番 Workflow だけにする。共有 n8n そのものは止めない。
  - **復帰の基準ができる前（G-1 の合格前）**：戻せる luna の版が無いので、SHAMAN の Workflow を停止したまま、修正 → CI → 再配備で直す。
  - **復帰の基準ができた後**：SHAMAN の本番 Workflow を停止し、記録した基準のコミットを配備し直す。
    - 配備が `workflow_dispatch` だけの段階では、`workflow_dispatch` の入力で基準のコミットの SHA を渡し、`actions/checkout` でその SHA を明示して取り出す（実行の ref は branch か tag なので、それだけでは対象のコミットを指定できない）。main への push で自動配備する段階に移った後は、基準の版に戻す revert の PR をマージして配備する。
    - 配備後、Workflow の ID、公開状態、配備したコミットが記録と一致することを確かめる。
  - **データ**：Git の revert ではキュー、途中保存、Credential、投稿済みの記事は戻らない。
    - 二重投稿を防ぐため、停止の時点で「処理中」だった行は、WordPress に下書きが作られたかを確かめてから状態を直す。確かめるまでは再実行しない。
  - 上の手順で戻せない場合は停止したままにし、復旧したとは報告しない。
- **移行後の確認**：正常系、異常系（Error Trigger から Discord への通知）、再実行、n8n 再起動後の状態、実行履歴に Secret が出ていないこと。

### Phase H：文書の更新

- `SHAMAN/CLAUDE.md`：開発ループ、Credential とモデル設定のルール（`$env` を使わない）、ローカルの n8n を使わないこと
- `README.md`
- 設計書 v8 §4-3（import / activate の手順と ID の記述）
- 新たに追加する文書：
  - Credential 一覧（名前・種類・用途のみ）
  - luna の n8n の設定の記録（Phase B）
  - 運用手順書（runbook）

---

## 5. 未決事項

- 共有 n8n の実行環境の定義をどの repo に置くか（SHAMAN にも ASFNPS にも属さない）
- 配備の自動起動に切り替える時期
- CI 用 runner の形（§2.7。推奨：CI 専用の Lima VM）
- API キーのスコープ設定

---

## 6. 検証

| Phase | 確認内容 |
|---|---|
| C | `pytest`（生成 JSON に `$env` 参照が無いこと、再生成で差分が出ないことを含む） / `python generate_workflows.py` / `npm test` / LLM レビュー / Code ノードに `require(` が残っていないこと / キューの書き戻しの保持と fail-closed のテスト |
| E | 配備スクリプトの pytest。一時 Workflow が削除されたこと（削除に失敗したら未完了）。`workflow_dispatch` で2回配備し、重複せず更新されること。Credential の ID の変数が空なら止まること。許可リストに無い名前・`SHAMAN / ` で始まらない名前を拒否すること。旧名の Workflow や Webhook のパスの競合があれば止まること。CI が luna の runner で通ること |
| F | `N8N_BLOCK_ENV_ACCESS_IN_NODE=true` のまま動くこと。Credential と ID の登録。キューと画像の受け渡し（共有 n8n の設定に手を入れた場合は、他プロジェクトに影響が無いこと）。CI 用 runner の安全条件 |
| G | 統合ゲート（G-1）の E2E：Credential 参照、ID 未設定で止まること、再配備後も紐付けが保たれること、キューの書き戻しの保持と fail-closed、下書きの作成、画像のアップロード、Discord への通知。復帰の基準の記録。WordPress への投稿が下書きのままであること。n8n 再起動後の状態。実行履歴に Secret が出ていないこと |
