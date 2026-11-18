# キュー：Google スプレッドシートの設計

- **Status**: 設計と実装（repo 内）。luna の n8n の設定変更、Credential の作成、スプレッドシートの用意、配備はオーナーが行う（§6）
- **更新**: 2026-11-01
- **関連**: [N8N_LUNA_MIGRATION_PLAN.md](N8N_LUNA_MIGRATION_PLAN.md) §2.4〜§2.6・§3・Phase C-4／C-6／C-7

## 1. 位置づけ

2026-11-01、キューを iCloud の xlsx から Google スプレッドシートに移すことにした（オーナーの決定）。

- 理由：luna は GUI でログインしていない状態で動かす（GUI の常時ログインは採らない）。macOS の iCloud Drive は、そのユーザーが GUI でログインしている間しか同期しない（luna-user の `bird` が動かない。2026-11-01 に確認）。iCloud を介したキューは、作業端末やモバイルとの受け渡しができない。
- n8n の標準の Google Sheets ノードで読み書きする。スプレッドシートのセルを直接更新するので、書式や他のシートは崩れない。openpyxl、Execute Command、バックアップと差分の確認は要らない（PR #56 は閉じた）。
- 作業端末やモバイルからは、Google スプレッドシート（ブラウザ、アプリ）で編集する。
- 途中保存（`article_draft_<row>.json`）は同期が要らないので、luna の n8n のファイルの置き場所に置く（Phase F）。参照画像は計画どおり repo から配る。

## 2. 全体の流れ

```
Webhook
 → Queue: Read          （Sheets：記事キューを読む。row_number＝実際の行番号）
 → Queue: Parse         （Code：行を item にし、sites.json の設定を付ける）
 → Filter Rows → Loop Over Items → Map Variables
 → Queue: Recheck       （Sheets：読み直す）
 → Queue: Check Claim   （Code：SITE_ID・KEYWORD を照合し、STATUS が 待機中／画像エラー か確かめる）
 → Queue: Claim         （Sheets：STATUS=処理中、実行ID、開始日時 を書く）
 → Queue: Claim Check   （Code：1 行を書けたか。行が見つからず 0 件なら止める）
 → Queue: Reread        （Sheets：読み直す）
 → Queue: Confirm Claim （Code：処理中で、実行ID が自分のものか確かめる）
 → Check Article Status → … P1〜P7 …
 → Cleanup              （Code：書き戻す値を作る）
 → Queue: Final Recheck （Sheets：読み直す）
 → Queue: Check Final   （Code：照合し、まだ自分が確保しているか確かめる）
 → Queue: Update        （Sheets：STATUS・WP_POST_ID・QUALITY・開始日時・完了日時・エラー内容 を書く）
 → Queue: Update Check  （Code：失敗・行が見つからないとき、WP 投稿 ID を出して止める）
 → Queue: Done → Discord: Notify Success
```

- 書き込みは、どれも `row_number` で行を指定する（Google Sheets ノードの update、照合する列は `row_number` だけ）。値はそのまま書く（`cellFormat: RAW`。「=」で始まるエラー文を数式にせず、実行 ID や日時を数値・日付に変えない）。
- 書き戻しでは、6 列すべてを書く（値のない列は空にする）。
- Test Workflow は、別の試験用のスプレッドシートを読み書きする。本番のスプレッドシートには触れない。
- キューの Code ノードは、ファイルにも `require` にも触れない。

### 2.1 途中保存（画像エラーからの再開）

n8n 2.x の Code ノードは `require('fs')` できない（`NODE_FUNCTION_ALLOW_BUILTIN` は未設定のまま。§6.4）。また、前の固定のパス `/home/node/.n8n-files` は Docker 版のもので、macOS にはない。そこで、途中保存は標準ノードで読み書きする。

```
保存：P6: Save Article（Code：中身とパスを作る）
 → P6: Draft To File（Convert to File：文字列をファイルにする）
 → P6: Write Draft（Read/Write Files from Disk：書く）
     成功 → P6: Draft Saved（STATUS を「画像エラー」にする値）→ Cleanup
     変換か書き込みで失敗 → P6: Draft Save Failed（STATUS を「エラー」にする値と理由）→ Cleanup
読込：Check Article Status（画像エラー）
 → Read Draft（Read/Write Files from Disk：読む）
 → Draft From File（Extract from File：文字列にする）
 → Load Saved Article（Code：JSON にし、SITE_ID・KEYWORD を照合する）→ Check P6 Mode
     どこかで失敗 → Draft Unusable（STATUS を「エラー」にする値と理由）→ Cleanup
```

- **保存できたときだけ「画像エラー」にする**。保存に失敗したら（変換でも書き込みでも）「エラー」と理由を書き戻す。読めないとき（ファイルがない・中身が壊れている・別の行の下書き）も「エラー」にする。どちらも `処理中` のまま残さない。「エラー」の行は、人が STATUS を「待機中」にすると最初から作り直す。途中保存のファイルを扱う標準ノード（6 つ）は、すべてエラー出力をつなぐ（テストで確かめている）。
- **例外：WP へのアップロードの失敗（`P6: Upload Abort`）は、意図して `処理中` のまま残す**。同じ形で保存し、保存の成否にかかわらず最後のノード（`P6: Abort Draft Saved`／`P6: Abort Draft Failed`）が、元のアップロードの失敗の理由を付けて throw する（Error Trigger → Discord）。画像の一部が WordPress のメディアに上がっている可能性があり、自動で再開すると重複して上がるため、人が確かめる（§4）。
- 置き場所は配備時に置き換える `__ENV:SHAMAN_DRAFT_DIR__`。ファイル名は本番が `article_draft_<row>.json`、Test Workflow が `test_article_draft_<row>.json`（試験用のスプレッドシートは本番と行番号が重なるので、本番の下書きを上書きしない）。

## 3. 認証（要件 1：新規の有料契約なしで使える方式）

Google Sheets ノードの 2 つの認証方式は、どちらも新しい有料契約なしで使える（Google Cloud のプロジェクトは課金を設定せずに作れ、Sheets API は無料の割り当ての範囲で使える）。生成する Workflow はどちらにも対応し、`generate_workflows.py` の `QUEUE_SHEETS_AUTH` で切り替える（テストで両方を確かめている）。

| | サービスアカウント（`serviceAccount`） | OAuth2（`oAuth2`） |
|---|---|---|
| 費用 | 無料 | 無料 |
| n8n の Credential | Google Service Account API | Google Sheets OAuth2 API |
| 読み書きできる範囲 | サービスアカウントに共有したスプレッドシートだけ | ログインした Google アカウントのすべてのスプレッドシート |
| 期限 | 鍵に期限はない（定期的に作り直す） | 同意画面が「テスト」だと、7 日で更新トークンが切れる。「本番」にすれば切れない（自分だけで使うなら未確認のアプリのままでよい） |
| 注意 | Google Workspace の組織では、組織のポリシーで鍵を作れないことがある | 同意画面と OAuth クライアントを作る手間がある |

**選んだ方式：サービスアカウント（既定。ただし実機で確かめるまでは確定しない）**。読み書きできる範囲を、共有したスプレッドシートに絞れるため。無人で動かしても期限切れで止まらない。

- **2025-04-15 以降に作ったサービスアカウントの制約**：n8n の文書（[Google Service Account credentials](https://docs.n8n.io/integrations/builtin/credentials/google/service-account/)）には、2025-04-15 以降に作ったサービスアカウントは「マイドライブ」にアクセスできず、共有ドライブだけにアクセスできる、と書かれている。一方、Google の文書（[Create access credentials](https://developers.google.com/workspace/guides/create-credentials)）は、特定のスプレッドシートをサービスアカウントに直接共有して読み書きする方法を示している。この設計は後者（2 つのスプレッドシートを直接共有する）に頼る。
- そのため、§7 の最初の試験で、試験用のスプレッドシートを `read` と `update` できることを確かめてから、既定の方式を確定する。
- できなければ、`QUEUE_SHEETS_AUTH = 'oAuth2'` に切り替え、同意画面を「本番」にする。共有ドライブに置く方法は採らない（Google Workspace の契約が要り、要件 1 に反する）。ドメイン全体の委任も採らない（n8n の文書も勧めていない）。
- 組織のポリシーで鍵を作れない場合も、OAuth2 に切り替える。

- Credential の名前は既定のままでよい（他のプロジェクトと共用できる。共用するかどうかは作るときにオーナーが決める）。Workflow JSON の `__ENV:SHAMAN_GOOGLE_SHEETS_CREDENTIAL_ID__` は、配備のときに、n8n にある Google Service Account API の Credential の ID で置き換える。その種類が 2 件以上あるときだけ、`Google / Sheets` という名前のものを使う（2026-11-05 から。前は Repository Variable に置いていた。`DOCS/CONNECTION_CHECK.md` §4）。
- API キーだけの方式（公開したスプレッドシートを読むだけ）と、Apps Script を Web アプリにする方式は採らない。前者は書き込めず、キューを公開することになる。後者は、別に管理するコードと公開の URL が増える。

## 4. 排他制御と重複投稿の防止（要件 2）

Google スプレッドシートには「値が X のときだけ書く」という条件付きの書き込みがない。そのため、読み直して照合するだけでは、2 つの実行が同じ行をほぼ同時に確保するのを防げない。次の 3 段で防ぐ。

1. **実行を 1 つずつにする（実行どうしの排他）**：luna の n8n で `N8N_CONCURRENCY_PRODUCTION_LIMIT=1` にする（§6.1）。SHAMANとASFNPS（SNSアフィリエイト収益化システム）は同じmacOS版n8nを共用し、この制限は**全プロジェクトの本番実行に共通**である。先行する本番実行の完了まで後続は待つ。ASFNPSがSHAMANの実行を待つことはオーナーが許容済み（2026-11-10）。ARBW（WordPressに蓄積された記事資産を知識として蓄積・更新・検索・再利用するシステム）も将来、短時間の受付だけを共有する計画だが、共存条件を確定するまでは本番化しない。手動実行はこの制限に含まれないため、試験は試験用のスプレッドシートと専用Workflowで行い、n8nの画面から本番Workflowを手動実行しない。
2. **行を確保してから処理する（重複投稿の防止）**：各行の処理の最初に、読み直して照合し、STATUS が「待機中」か「画像エラー」のときだけ、`STATUS=処理中` と `実行ID`（n8n の実行 ID）を書く。もう一度読み直し、`処理中` で `実行ID` が自分のものになっていることを確かめてから、LLM や WordPress を呼ぶ。他の実行が拾うのは「待機中」と「画像エラー」だけなので、確保した行は二度と処理されない。
3. **確保したままの行にだけ書き戻す**：最後に読み直して、照合が合い、まだ `処理中` で `実行ID` が自分のものであるときだけ書き戻す。そうでなければ書き戻さずに止める。

失敗したときの扱い：

- 途中で失敗すると（LLM、WordPress、書き戻しのどれでも）、行は `処理中` のまま残る。次の実行はこの行を拾わないので、**同じ記事を二度投稿しない**。
- 例外として、途中保存の失敗と、途中保存を読めないときは、投稿の前なので「エラー」を書き戻す（§2.1）。WP へのアップロードの失敗は、上の原則どおり `処理中` のまま残す。
- 書き戻しの前に止まったとき（`Queue: Check Final`）と、書き戻しそのものに失敗した・行が見つからなかったとき（`Queue: Update` → `Queue: Update Check`）は、エラーに WP 投稿 ID を入れる（Error Trigger から Discord に届く）。人が WordPress の下書きを確かめ、STATUS を直す。
- 1 つの行で照合や確保に失敗すると、その実行は止まり、残りの行は次の実行に回る（止まる側に倒す）。
- **途中保存の照合**：「画像エラー」の行の続きは、行番号で途中保存（`article_draft_<row>.json`）を探す。別の行の古い下書きを使わないよう、下書きに SITE_ID と KEYWORD を残し、読み込むときに行と照合する。一致しなければ使わず、STATUS を「エラー」にする（計画書 C-7、§2.1）。
- `処理中` のまま残った行は、次の実行の Filter Rows がスキップの理由として通知に出す。

**運用の決まり**（Google スプレッドシートに条件付きの書き込みがない以上、人の操作との競合は設定だけでは防げない。次を稼働の条件とする）：

- **本番の Workflow を手で実行しない**。n8n の画面からの実行は `N8N_CONCURRENCY_PRODUCTION_LIMIT` の数に入らず、Webhook の実行と並んで動き得る。試験は Test Workflow と試験用のスプレッドシートだけで行う。
- **人が編集してよいのは、新しい行を足すことと、`待機中`・`エラー`・`完了` の行だけ**。`処理中` の行と、実行の途中の行の SITE_ID・KEYWORD・STATUS・`実行ID` は変えない（変えれば、照合か確保の確認で止まる。止まる前に Claim が書いた場合は、人が直す）。
- **行を並べ替えない・途中に挿入しない・消さない**。行番号で確保と書き戻しをするため。要らない行は STATUS を `完了` などにして残す。
- `処理中` のまま残った行を直すときは、WordPress を確かめてから STATUS を書き換え、`実行ID` を空にする。

### 他プロジェクトと共用する際の条件（2026-11-10）

- `N8N_CONCURRENCY_PRODUCTION_LIMIT=1` は維持する。将来ARBWの受付を共有する際には、SHAMANキューの重複処理防止、Webhookの待機、処理開始の遅延、毎朝6時の定期実行、実行ログを本番化前に共存検証する。ARBWの受付1件によって**SHAMANに増える待機時間を10秒以内**とするのは設計上の受入基準であり、実測までは保証しない。超えた場合はARBW本番化を停止し、SHAMANを優先する。
- **ARBWがSHAMANを待つ時間は未決定**：SHAMANの実行は数十分かかる場合があり、その間に到着したARBW受付は本番実行枠が空くまで待つ。このためARBW受付の開始・完了を常に10秒以内にはできない。WebhookのHTTP応答を待つ方式か、受付を先に返す方式か、呼び出し元のタイムアウト、再送時の重複防止、許容待機時間は**ARBW本番化前にARBWリポジトリの設計で決める**。現時点でHTTP接続の挙動は実機未検証。
- 手動実行は上記の同時実行制限の対象外であり、別途排他が必要。SHAMANの本番Workflowの手動試験は禁止を継続する。n8n Community EditionではCredential（認証情報）やAPIキーのプロジェクト間の権限分離を強制できないため、名称だけの分離をセキュリティ境界として扱わない。

## 5. 設定の管理（要件 3：マルチサイトと Credential の分離）

「サイト共通設定」シートは廃止。新サイトは記事キューのSITE_IDからURLを導出する。`SHAMAN/config/sites.json` は既存のサイト表示設定を保持するための**任意設定**であり、新サイト追加の前提にはしない。

```json
{
  "kuro-aji-lab.com": {
    "SITENAME": "黒耳アジング研究所",
    "AUTHOR": "黒耳"
  }
}
```

- `IMG_FOLDER` は Google Drive の参考画像フォルダー（省略時は SITE_ID）。フォルダーが無い、または画像が0枚の場合は、参照なしで画像を生成する。Driveの権限エラーや通信エラーは異常として停止する。
- **マルチサイト**：キューの `SITE_ID` をドメインとして厳格に検査し、`https://<SITE_ID>` をWordPress送信先とする。新サイト追加に `sites.json` のPRは不要。n8n共通Credential `SHAMAN / WordPress / MultiSite` のUser・Passwordの式へ認証値を追加し、本番用スプレッドシート「SHAMAN本番用」の「サイト登録」タブA列（A1=`SITE_ID`、A2以降に小文字ドメインを1行1サイト）に登録してから、「記事キュー」タブのSITE_ID列（A列）に行を作る。登録は当面手作業（2026-11-15オーナー決定）。詳細は [WP_DYNAMIC_MULTISITE.md](WP_DYNAMIC_MULTISITE.md)。
- **Secret を持たない**：`generate_workflows.py` の `load_sites()` が、生成のたびに形を検査する。決めた項目以外（`WP_APP_PASSWORD` など）があれば生成を止める。WordPress の認証情報は n8n の Credential にだけ置く。
- **Credential の保管**：WordPress 認証値は1件の `SHAMAN / WordPress / MultiSite`（HTTP Basic Auth）Credential内の式でSITE_IDごとに選ぶ。サイト追加時は、この式のサイト設定を追加する。
- WordPress のアップロードと投稿には共通のHTTP Requestノード各1件を使う。Credential IDは配備時にn8nから解決する。既存の `Loop Over Items` はbatchSize=1のまま。詳細は [WP_DYNAMIC_MULTISITE.md](WP_DYNAMIC_MULTISITE.md)。
- `sites.json` には既存サイトの表示設定を保持する。ただし、**「SHAMAN本番用」の「サイト登録」タブに未登録のSITE_IDは、ドメイン形式が有効でも `Filter Rows` で除外され、記事・画像生成には進まない**（PR #101、実機実行1299で確認済み）。

**移行の状況**（2026-11-04）：

- 本番の Workflow は、`$env` と `require` を使わなくなった。外部への接続は、接続確認の Workflow と同じ組み立て関数と Credential で行う（[CONNECTION_CHECK.md](CONNECTION_CHECK.md) §7）。
- `Map Variables` は、`WP_USER`・`WP_APP_PASSWORD` を読まない。WordPress の認証は、共通 Credential 内の式が SITE_ID ごとに付ける。
- 参考画像は、Google Drive から読む（`Read Reference Images` の前の `P6: Find Image Folder` など）。

## 6. luna とオーナーの作業（この PR には含まない）

### 6.1 n8n の設定：本番の実行を 1 つずつにする

```sh
P=/Library/LaunchDaemons/com.shaman.n8n.plist
sudo cp -p "$P" "$P.pre-concurrency-20261101"
sudo plutil -replace EnvironmentVariables.N8N_CONCURRENCY_PRODUCTION_LIMIT -string 1 "$P"
sudo plutil -lint "$P"
sudo launchctl bootout system/com.shaman.n8n; sleep 3
sudo launchctl bootstrap system "$P"
sudo launchctl print system/com.shaman.n8n  # 起動状態を確認。設定値は下記plistlibで確認
```

- 戻し方：`sudo cp -p "$P.pre-concurrency-20261101" "$P"` のあと、`bootout` と `bootstrap` をもう一度行う。

### macOS LaunchDaemonの設定値確認（読み取り専用）

`deploy-shaman-n8n.yml` の「本番の同時実行を1件に制限していることを確認」と同じ`plistlib`で、必要なキーのみを確認する。秘密の値は表示しない。

```sh
sudo python3 - <<'PY'
import plistlib
with open('/Library/LaunchDaemons/com.shaman.n8n.plist', 'rb') as fh:
    data = plistlib.load(fh)
env = data.get('EnvironmentVariables') or {}
print('UserName:', data.get('UserName', '要確認'))
for key in ('N8N_CONCURRENCY_PRODUCTION_LIMIT', 'N8N_RESTRICT_FILE_ACCESS_TO'):
    print(key + ':', env.get(key, '要確認'))
PY
sudo launchctl print system/com.shaman.n8n
```

### 6.2 スプレッドシート

1. 今のキューのテンプレート（xlsx）を Google スプレッドシートに読み込む（ファイル → インポート）。シート名は `記事キュー`、見出しは 3 行目のまま（列の名前を変えない）。
2. 見出しに `実行ID` 列を足す（見出しの文字は `実行ID` と改行と `【自動】`。他の【自動】の列と同じ形）。
3. 「サイト共通設定」シートは消す（WordPress の認証情報が入っているため）。
4. 本番用と試験用の 2 つを作る。スプレッドシートの ID（URL の `/d/` と `/edit` の間）を、Repository Variable `SHAMAN_QUEUE_SPREADSHEET_ID` と `SHAMAN_QUEUE_TEST_SPREADSHEET_ID` に置く（Secret ではない）。

### 6.3 Credential（C-6）

- 共通：Google Cloud でプロジェクトを作り（課金の設定は要らない）、**Google Sheets API と Google Drive API の両方**を有効にする（n8n の Google Sheets ノードは Drive API も使う。n8n の文書 [Google Service Account credentials](https://docs.n8n.io/integrations/builtin/credentials/google/service-account/)）。
- サービスアカウントの場合：サービスアカウントと鍵（JSON）を作る。鍵は n8n の Credential `Google / Sheets` にだけ入れる。2 つのスプレッドシートを、サービスアカウントのメールアドレスに「編集者」として直接共有する（フォルダ単位の共有や共有ドライブには頼らない。§3）。
- OAuth2 の場合：OAuth の同意画面を作って「本番」にし、OAuth クライアント（ウェブアプリケーション。リダイレクト URI は n8n の Credential の画面に出るもの）を作る。
- Credential の名前は既定のままでよい（ID の登録は要らない。配備のスクリプトが n8n の一覧から種類で取る）。Google Service Account API の Credential が 2 件以上になるときだけ、SHAMAN の方を `Google / Sheets` にする。

### 6.4 途中保存の置き場所

n8n 2.x の Read/Write Files from Disk は、既定で `~/.n8n-files` の中しか読み書きできない（n8n の文書 [Read/Write Files from Disk](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.readwritefile/)）。LaunchDaemon で動く n8n の `~` が何になるかに頼らないよう、置き場所を決めて `N8N_RESTRICT_FILE_ACCESS_TO` で明示する。`NODE_FUNCTION_ALLOW_BUILTIN` は設定しない（Code ノードに `fs` を許さない）。

```sh
# 実行ユーザーは上記「macOS LaunchDaemonの設定値確認」のplistlibでUserNameを確認（下の例はluna-user）
U=luna-user
D=/Users/$U/.local/share/shaman-n8n-files
sudo install -d -o "$U" -m 700 "$D" "$D/drafts"
P=/Library/LaunchDaemons/com.shaman.n8n.plist
sudo cp -p "$P" "$P.pre-files-20261101"
sudo plutil -replace EnvironmentVariables.N8N_RESTRICT_FILE_ACCESS_TO -string "$D" "$P"
sudo plutil -lint "$P"
sudo launchctl bootout system/com.shaman.n8n; sleep 3
sudo launchctl bootstrap system "$P"
sudo launchctl print system/com.shaman.n8n  # 起動状態を確認。設定値は上記「macOS LaunchDaemonの設定値確認」のplistlibで確認
```

- Repository Variable `SHAMAN_DRAFT_DIR` に `$D/drafts` を置く（Workflow JSON の `__ENV:SHAMAN_DRAFT_DIR__` を配備時に置き換える）。
- 置き場所は `N8N_USER_FOLDER` の外にする（n8n の内部のファイルと分ける）。
- 戻し方：`sudo cp -p "$P.pre-files-20261101" "$P"` のあと、`bootout` と `bootstrap` をもう一度行う。

## 7. mainマージ後の自律DRY_RUN（2026-11-04 廃止）

自動 DRY_RUN（`verify_shaman_dryrun.py`、配備の Actions からの Webhook 実行、`e2e-recovery` のジョブ、`SHAMAN_E2E_READY`、試験専用のサイト設定 `sites.test.json`）は、2026-11-04 に廃止した。設定を読み込めるかしか確かめておらず、生成の中身を確かめられなかったため。

Test Workflow は、プロンプト評価 Workflow（`SHAMAN: Prompt Eval`、同じ ID `shaman_test`）に置き換えた。キューは読まず、LLM のステップを 1 つずつ評価する（[PROMPT_EVAL_DESIGN.md](PROMPT_EVAL_DESIGN.md)）。外部接続は、接続確認 Workflow で確かめる（[CONNECTION_CHECK.md](CONNECTION_CHECK.md)）。試験用のスプレッドシートの予約行（`__SHAMAN_AUTO_E2E__`）は、接続確認だけが使う。

### 7.1 残りの実機確認

Test Workflow はキューを使わなくなった（§7）。次の確認は、本番の Workflow で、試験用ではない最初の行を流すときに行う。



- **認証（最初に確かめる）**：§6.3 の Credential で、試験用のスプレッドシートを `Queue: Read` で読み、`Queue: Claim` で書けること。できなければ OAuth2 に切り替える（§3）。
- **Google Sheets ノードの形（本番の前に、試験用のスプレッドシートで必ず確かめる）**：n8n 2.35.7 の Google Sheets ノード（typeVersion 4.5）で、次が想定どおり動くか。多くは違えば止まる側に倒れる（照合と確保の確認で止まる）。ただし `row_number` がずれていると、`Queue: Claim` が隣の行に `処理中` を書いてから止まる（その行は人が直す）。そのため、最初の試験で `row_number` と実際の行が一致することを確かめる。
  - シートを名前で指定する（`sheetName` の `mode: name`）
  - 見出しを 3 行目、データを 4 行目からにする（`dataLocationOnSheet`）
  - 見出しの中の改行（`STATUS\n【自動】` など）を列の名前として扱う
  - `row_number` で行を指定して update する（`row_number` を列の対応に入れてよいか）
  - `cellFormat: RAW` で、値がそのまま書かれる
  - update が失敗したとき、エラー出力に `error` が入る
- **実行 ID**：`$execution.id` が、確保の書き込みと確認で同じ値として読めるか（数値と文字列の違いは、文字列にして比べている）。
- **同時の実行**：`N8N_CONCURRENCY_PRODUCTION_LIMIT=1` で、2 つ目の Webhook の実行が待つこと。
- **途中保存**：
  - `P6: Write Draft` が `SHAMAN_DRAFT_DIR` に `article_draft_<row>.json` を書き、STATUS が「画像エラー」になる。置き場所を書けなくしたとき（権限を外す）は、エラー出力に進み「エラー」になる。
  - 次の実行で、`Read Draft` → `Draft From File` が中身を `json.data` に文字列で渡し、画像だけを作り直す。
  - ファイルを消してから再開すると、`Read Draft` が 0 件か失敗になり、`Draft Unusable` に進んで「エラー」になる（`処理中` のまま残らない）。
  - n8n を再起動しても、途中保存が残る。
- **Webhook でアップロードされたファイル**：前の `Spreadsheet File` は、Webhook でアップロードされた xlsx を優先して読んでいた。この設計では、常にスプレッドシートを読む。

## 8. 旧 Queue API の片付け

PR #35・#54 の無承認の実行が luna と GitHub に残したもの（Test Workflow `SHAMAN / Blog / Pipeline Test`、Credential `SHAMAN Queue API`、Repository Variable `SHAMAN_QUEUE_CREDENTIAL_ID`、iCloud の `DATA/_TEST` の DRY_RUN のファイル、Lima VM の `shaman-queue-api.service` とトークン、`shamandeploy` の runner の設定）は、移行が終わってから、記録した ID を指定して片付ける（オーナーの指示）。Credential については、オーナーが前の Credential をすべて削除したので、残っていないかを確かめるだけでよい。

## 9. テスト

| 対象 | テスト |
|---|---|
| Workflow の構成 | `SHAMAN/test_generate_workflows.py` の `TestQueueSheets`（Sheets ノードの指定、見出しの行、`row_number` での update、認証の切り替え、確保と書き戻しの順番、Code ノードがファイルと `require` に触れないこと、Test Workflow の試験用のスプレッドシート） |
| sites.json | 同じく `TestSitesConfig`（Secret や知らない項目、`SITE_ID` の形）、`TestTestSites`（試験用サイトは Test Workflow だけに入り、本番に入らない） |
| Code ノード | `node_tests/__tests__/queue-sheets.test.js`（行の取り出し、確保、確保の確認、書き戻しの前の確認、止まり方）、`cleanup.test.js`（書き戻す値） |
| 途中保存 | `test_generate_workflows.py` の `TestP6SaveArticleOnMaxRetries`・`TestArticleRetryLaterPath`・`TestP6UploadAbort`・`TestDraftFiles`・`TestDraftSaveFailurePaths`（標準ノードでの読み書き、変換と書き込みの失敗の行き先を本番・試験の両方で、成功と失敗の分岐、`fs` と Linux のパスがないこと、Test Workflow のファイル名）、`node_tests/__tests__/p6-save-article.test.js`・`p6-upload-abort.test.js`・`load-saved-article.test.js`（中身、照合、失敗したときの STATUS） |
