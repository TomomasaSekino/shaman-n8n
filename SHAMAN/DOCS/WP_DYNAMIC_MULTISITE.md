# WordPress 共通認証とサイト追加（PR #97）

## サイト追加の手順
**サイト登録は当面手作業で行い、自動化しない**（2026-11-15、オーナー決定）。

1. 追加する WordPress のドメインを `SITE_ID` として決める（例：`kuro-aji-lab.com`）。小文字で、`https://` やパスは付けない。
2. n8n の共通 Credential `SHAMAN / WordPress / MultiSite`（HTTP Basic Auth）の **User と Password の両方の式**に、この `SITE_ID` と対応する認証値を追加する。
3. 本番用 Google スプレッドシート **「SHAMAN本番用」** の **「サイト登録」** タブの **A列**に `SITE_ID` を追加する。**A1 は見出し `SITE_ID`、A2 以下は1行につき1サイト**の小文字ドメイン（例：`kuro-aji-lab.com`）とし、`https://` やパスは付けない。
4. 同じ「SHAMAN本番用」の **「記事キュー」** タブに記事の行を追加し、**SITE_ID列（A列）**に登録したドメインを記入する。

**②のCredentialへの追加と③の「サイト登録」タブへの登録は両方必須。**
- **③が無い場合**：未登録の `SITE_ID` は `Filter Rows` で除外され、記事も画像も生成されない（実機実行1299で確認済み）。
- **②が無い場合**：③の登録があれば受付は通るが、WordPressへの送信時に認証エラーで止まる。**P1〜P6の処理は先に走るため、LLM・画像生成等のAPI利用料金が発生する。** 「サイト登録」タブはCredentialの式への登録有無までは検査しない。

**「サイト登録」タブにはパスワードなどの秘密の値を書かない。秘密の認証値はn8nのCredentialの中だけに置く。**

**新規サイトの登録に `sites.json` のPR・マージは不要。** WordPress のURLは `https://<SITE_ID>` から実行時に決まり、サイト数にかかわらず投稿・アップロードのHTTPノードは2つだけです。サイト名、著者、一人称、BASEURLなどの情報はキューの既存の任意列で指定できます。未指定時は既定値を使います。参照画像のフォルダー名は既定で SITE_ID。既存サイトの表示設定を維持するための `config/sites.json` は**任意の上書き設定**として残します。

## 認証
n8n に必要な共有 Credential は1件です。
- 名称：`SHAMAN / WordPress / MultiSite`
- 種類：HTTP Basic Auth（`httpBasicAuth`）
- ユーザー名とパスワードをそれぞれ「Expression」に切り替え、SITE_IDをキーに選びます。秘密値は n8n Credential の中だけで保持し、GitHub/キュー/Workflow JSON に配置しません。

User（例。山括弧内を実値に置換）：

```javascript
{{ (() => {
  const users = {
    'kuro-aji-lab.com': '<ユーザー名1>',
    'kuromimi.online': '<ユーザー名2>'
  };
  const value = users[$json.siteId];
  if (!value) throw new Error('WP_CREDENTIAL_SITE_ID_NOT_CONFIGURED: ' + $json.siteId);
  return value;
})() }}
```

Password（例。山括弧内を実値に置換）：

```javascript
{{ (() => {
  const passwords = {
    'kuro-aji-lab.com': '<アプリケーションパスワード1>',
    'kuromimi.online': '<アプリケーションパスワード2>'
  };
  const value = passwords[$json.siteId];
  if (!value) throw new Error('WP_CREDENTIAL_SITE_ID_NOT_CONFIGURED: ' + $json.siteId);
  return value;
})() }}
```

未登録のSITE_IDは式の評価時に明示的なエラーで停止し、**空のBasic認証でWordPressへ送らない**ようにします。コード上でも送信直前にドメイン形式とURLの整合性を検証します。このCredential式がn8n 2.35.7の実機で各ループごとに再計算されるかは、実機で検証するまで未確認です。実機確認なしで本番運用可能と扱いません。

## 参照画像なしの画像生成
共有ルート `SHAMAN/IMG` が見えることを最初に確認します。サービスアカウントが共有を失っている場合は `SHAMAN_DRIVE_IMG_ACCESS_LOST` として停止し、参照画像なしへは切り替えません。共有ルートにアクセスでき、`SHAMAN/IMG/<SITE_ID>` のフォルダーが無い場合、またはフォルダー内の参照画像が0枚の場合だけ、`referenceImages: []` のまま生成へ進みます。そのときは「参照画像なし（フォルダ無し／0枚）」をキューのQUALITYとDiscord通知へ記録します。OpenAIは `/v1/images/generations`、参照画像があれば `/v1/images/edits` を利用し、参照画像なしでは専用プロンプトを選びます。Geminiも専用プロンプトに切り替えます。Driveの通信・アクセスエラー、画像ダウンロード失敗は異常として扱います。

## 既存テストWorkflowによる実機検証

PR #97では既存 `SHAMAN: Prompt Eval` に次の2工程を追加する（本番Workflowではない）。

- `WP`：`SHAMAN | n8n Workflow実行` の `test` で `eval_step=WP` を選ぶ。`eval_wp_sites` に認証を検証する2〜5ドメインをカンマ区切りで入れる。回数はサイト数で自動設定され、**同じHTTP GETノードを1サイトずつ**呼ぶ。各サイトのHTTP 200・ユーザーIDを別々の評価結果として記録する。WordPressには書き込まない。
- `WPD`：`eval_step=WPD`、`eval_wp_test_site` に `test.` または `staging.` で始まるサブドメインを指定し、`eval_wp_draft_confirm` を明示的に有効にした場合だけ実行。さらに試験専用送信先として `config/wp_test_sites.json` に**個別に承認されたドメインだけ**を許可する（初期値 `[]`＝すべて拒否）。`sites.json` の有無では安全性を判定しない。承認済み試験ドメインにだけ**下書き1件**を作成し、HTTP 201・`status=draft`・投稿IDを確認する。公開投稿はしない。下書きの削除は自動で行わない。
- `P6`：従来どおり、評価用データの節を1つ使い画像を1枚だけ生成する。参照画像フォルダー不在または空の場合は `images/generations` を選び、参照画像があれば `images/edits` を選ぶ。

試験工程はコード・CIで実装したのみで、**n8n 2.35.7の実機でまだ実行していない**。既存の `test` Workflowを破壊しないよう、マージ前の実機検証には配備先・Credentialの事前条件を確認する。新しい共有Credentialが無ければ配備できない。アプリケーションパスワードやユーザー名をCI、GitHub、試験用スプレッドシートに転記しない。

## 本番配備前の自動実機ゲート
オーナーがPRをマージした後、`deploy-shaman-n8n.yml` の `stage-test` は、試験Workflowの配備と公開に続けて `WP` を2サイト（`kuro-aji-lab.com`, `kuromimi.online`）で実行する。認証の実際のHTTP結果、SITE_IDに応じたCredential値の再評価を検査し、**失敗したら `publish-production` は実行されない**。マージしたこと自体では本番Workflowの公開を進めない。試験の結果と理由は実機の履歴・Actionsログ・試験用スプレッドシートに残る。

`P6` の画像生成（API利用料金が生じる）と `WPD` の試験サイトへの下書き生成は自動実行しない。それぞれ明示的な評価実行が必要。

## マージの前提
1. まずn8nに**共通Credentialを作成して式へ既存サイトの認証情報を登録**する。
2. 別サイトに切り替えた際に認証が取り違えられないことをn8n 2.35.7で検証する。画像なしも試験用Workflow/サイトで確認する。
3. オーナーがレビュー・マージを明示承認した後に限りマージする。**本PRから無断でマージ・本番配備しない。**
4. 旧 `WP_API_<SITE_ID>` Credential は、本番動作確認前に削除しない。
