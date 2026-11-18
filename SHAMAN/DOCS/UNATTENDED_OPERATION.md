# SHAMAN 無人下書き生成・独立監視（2026-11-10）

## 目的と稼働条件

記事キュー登録後、人間が GitHub Actions を起動しなくても **毎日6:00（Asia/Tokyo）に最大1行**を処理し、WordPressへ**下書き**を保存する。公開は自動化しない。対象ゼロ件は正常な無処理とし、余分なAPI課金・Discordエラー通知を発生させない。

- 対象：本番Workflow `SHAMAN` の `Daily Schedule`（n8n Schedule Trigger、Cron `0 6 * * *`）。
- 定期実行だけは、キューに「待機中」「画像エラー」が複数あっても先頭の1行だけを処理。既存Webhookからの手動実行・`recover_execution` の扱いは変更しない。
- `Daily: Idle?` で処理対象ゼロ件を判別し、`Daily: No Work` で正常終了する。
- 本番の同時実行は **1件** に制限する必要がある。n8n の `N8N_CONCURRENCY_PRODUCTION_LIMIT=1` を既存LaunchDaemonの環境変数へ設定する。このn8nはSHAMANとASFNPSで共用しており、将来ARBWも短時間の受付に限り共用を計画しているため、制限は**全プロジェクト共通の本番実行**に適用される。既存の `com.shaman.n8n.plist` はこのPRで変更しない。設定手順と共存条件は [QUEUE_GOOGLE_SHEETS_DESIGN.md §4・§6.1](QUEUE_GOOGLE_SHEETS_DESIGN.md) を参照。**mainマージ後の配備ジョブは、この値がplistに無ければ本番を配備せず失敗する安全ゲートを備える。** 設定後はn8n本体も再起動して環境変数を反映させる。手動実行はこのproduction制限の対象外であり、別途排他が必要なため、本番Workflowの手動試験は禁止する。
- 旧Google Drive OAuth2 Credentialには依存しない。

## 監視

macOSの`launchd`が `shaman_watchdog.py` を **5分おき**に起動する。これはn8nのSchedule Triggerとは別プロセスなので、n8nが停止しても障害を検知できる。

- n8n `/healthz` はHTTP 200で正常判定し、応答本文の文字列には依存しない。ダウン時はDiscord通知し、n8n本体の再起動は既存LaunchDaemonに任せる。Discordが403等を返しても監視プロセスは落とさず、HTTPステータスのみ監視ログに記録する（Webhook URLは出力しない）。通知成功としては記録しないため次回監視でも送信を試みる。Discord 403はWebhook URLの有効性またはDiscord側のアクセス拒否を確認する。
- 本番実行が6時間以上継続：Discord通知。進行中の実行は強制停止しない。
- 定期実行が異常終了：**停止から30分以上24時間以内**の実行について、次の全条件を満たせば**一度だけ**復旧する。
  1. `Daily Schedule`を通った実行。手動Webhook、キャンセル、成功実行は対象外。
  2. 明確な一時的な障害（HTTP 429/500/502/503/504、接続リセット、タイムアウト等）。`SHAMAN_RUN_NG`（品質・情報源不足等）は再試行しない。
  3. 既存の `recover_check`（PR #90）が「本番実行・停止状態・実行データ・WordPress未送信・他の本番実行なし」をすべて検証。
  4. 対象キュー行が「処理中」・元の実行ID一致・`WP_POST_ID`空であることを本番Workflowが再確認。
- 復旧を試みる前に、実行IDと「試行済み」を状態ファイルへ原子的に保存。監視プロセスが落ちても同じ実行に2度目の復旧をしない。ファイルロックで監視処理自体も重複起動しない。
- WordPressへ送信した可能性がある異常、情報源0件、品質NG、履歴不完全は**自動復旧しない**。Discordへ対応要否を通知する。
- 監視はキューの投稿済み行を変更しない。成功した復旧の後にWordPressが受け取るのはあくまで下書き。

## 自動配備・秘密情報

人間がPRをmainへマージした場合のみ、既存の `publish-production` ジョブが本番Workflowを公開し、その後 `install_shaman_watchdog.py` が監視を配備する。通常のPR/CIで稼働系を操作しない。

- 監視コードと本番Workflow JSONを `~/.local/share/shaman-watchdog/runtime/SHAMAN/` にコピーする（Actions checkoutのワークスペース消去で動かなくならないため）。
- 専用Secret `SHAMAN_WATCHDOG_N8N_API_KEY` と既存の `SHAMAN_RUN_WEBHOOK_TOKEN` を、`~/.local/share/shaman-watchdog/config.json`（0600）へ渡す。**デプロイ用 `N8N_DEPLOY_API_KEY` を監視用に流用することは禁止**。値が同一なら配備を拒否する。Gitには保存しない。
- **n8n Community EditionではAPIキーをread-onlyに制限できない**（API key scopeはEnterprise機能）。専用キーへの分離は漏えい時の失効・影響把握を目的とするもので、権限を最小化したことを意味しない。真にread-onlyへ限定するにはEnterprise版のスコープ設定または別途制限付きの参照プロキシが必要。監視キーは最小限の保管先とOS権限0600で隔離する。
- 監視プログラムの配置と秘密設定は既存の配備runnerが一般ユーザーで行う。監視自体は`/Library/LaunchDaemons/com.shaman.watchdog.plist`（root所有、0644）で起動し、`UserName=luna-user` に降格する。**初回だけ管理者が下記コマンドでsystemドメインに登録する**。GUIログイン・自動ログインは不要。Homebrew不要。
- 初回登録（PR #93がマージされ、本番配備成功・`SHAMAN_WATCHDOG_RUNTIME_READY`を確認した後。luna上で実行）：
  ```sh
  sudo python3 /Users/luna-user/.local/share/shaman-watchdog/runtime/SHAMAN/tools/luna/setup_shaman_watchdog_daemon.py --user luna-user --python "$(command -v python3)"
  sudo launchctl print system/com.shaman.watchdog
  ```
  **注意：** 初回登録の実行はオーナーの承認が必要であり、PRをマージするだけでは完了しない。
- 次回以降の配備ではrunnerが監視コードと秘密設定を更新し、system LaunchDaemonは引き続き5分ごとに起動する。
- Discord通知には **任意のSecret `SHAMAN_WATCHDOG_DISCORD_WEBHOOK_URL`** を登録する。指定がない場合は障害を記録するがDiscordへ送れないため、無人監視を完成させるには登録が必要。
- 監視用秘密設定の配備に失敗した場合は配備ジョブを失敗にする。初回のLaunchDaemon登録が未完了でも、配備完了を監視稼働の保証とはしない。
- 監視には `workflow:list`、`execution:list`、`execution:read` の3つのGET APIが必要。ただしCommunity EditionのAPIキーそのもののread-only化はできない。

## 日次運用

新規記事を「記事キュー」に登録して`STATUS=待機中`とすれば、翌朝6:00に最大1記事を下書きとして生成する。途中保存の「画像エラー」も処理対象。正常完了時のDiscord通知は既存Workflowを利用する。下書きの内容確認・公開は利用者が行う。旧下書き1906は削除対象に含めない。

監視で `SHAMAN_RUN_NG` を検出した場合は通知するが、無限にAPIを呼び直さない。安全上、自動で再開できない停止実行は人間の判断を必要とする。

## 動作確認

- PRのCI：`python3 -m unittest discover -s SHAMAN/tools/luna -p 'test_*.py'`、`pytest`、`jest`、Workflow JSONの再生成差分確認。
- 配備後の監視コード配置ログ：`SHAMAN_WATCHDOG_RUNTIME_READY`。初回の管理者登録後：`SHAMAN_WATCHDOG_DAEMON_REGISTERED`。
- システム常駐確認：`sudo launchctl print system/com.shaman.watchdog`。画面ログインは不要。
- 監視ログ：`~/Library/Logs/shaman-watchdog-error.log`。障害時の通知はDiscordへ。
- 定期起動による下書き生成は本番のキュー行がある場合のみ発生し、ゼロ件のときは記事を作らない。

## リスクと制約

`N8N_CONCURRENCY_PRODUCTION_LIMIT=1` はSHAMAN・ASFNPS（将来のARBW受付を含む）の**共用n8nインスタンス全体**の本番実行を直列化する。ASFNPSがSHAMANの実行完了を待つことはオーナーが許容済み（2026-11-10）。他プロジェクトの本番実行でSHAMANの開始が遅れる可能性がある。ARBW受付1件によるSHAMANの追加待機10秒以内は設計上の受入条件であり、実測前には保証しない。逆に、ARBWの受付はSHAMANの長時間実行を待つため、受付の10秒以内完了は保証できない。受付応答方式・許容待機時間はARBW本番化前にARBW側の設計で決める（[QUEUE_GOOGLE_SHEETS_DESIGN.md §4](QUEUE_GOOGLE_SHEETS_DESIGN.md)）。手動実行は制限対象外であり、SHAMANの本番Workflowを手動で実行しない。

機械的なGoogle検索情報源チェックだけでは事実の正確性は保証できない。WordPressへの**公開**は引き続きオーナーが判断する。

監視対象の一時障害判定は、n8n実行履歴の `error.httpCode`・`statusCode`・`description`・`message` および入れ子の `cause` / `response` を参照する。HTTP 429/500/502/503/504、Bad gateway / Service unavailableなどを認識するが、`SHAMAN_RUN_NG`を含む場合は必ず自動復旧しない。
