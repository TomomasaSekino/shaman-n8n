# SEO記事自動生成システム 設計書 v8

**プロンプト内製化 / 命名統一 / モデルレジストリ集約 / 記事HTML整形**

- 版: v8（2026-07-20）
- 前版: [v7 (2026-04-24)](./SEO記事自動生成_アーキテクチャ設計書_v7.md)
- 対象実装（当時）: `generate_workflows.py` / `prompts.py` / `workflows/*.json` / n8n 2.17.5

> **過去の設計記録**：本書は2026-09時点までの構成を扱う。本文中のExcelJS・Docker・Windows・旧モデルの説明は当時の記録であり、現行の本番手順ではない。v7からの変更点はv7を参照。

## 現在の本番構成（2026-11-18 追記）

- lunaへの移行は2026-11-10完了。macOS版n8n 2.35.7（2026-11-01からLaunchDaemon稼働）。旧Windows・Docker・Lima・bindfs・iCloudは不使用。
- オーナーによるPRのmainマージ時のみ[配備Actions](../../.github/workflows/deploy-shaman-n8n.yml)が3 WorkflowをAPI配備・読み戻し・公開。手動実行は[RUN_FROM_ACTIONS.md](RUN_FROM_ACTIONS.md)、定期実行は毎日6:00 JST・最大1行の[UNATTENDED_OPERATION.md](UNATTENDED_OPERATION.md)。
- `com.shaman.watchdog`による5分間隔の監視とDiscord通知。
- キューはGoogle Sheets標準ノード（[設計](QUEUE_GOOGLE_SHEETS_DESIGN.md)）。WordPressは共通Credentialとサイト登録タブ（[詳細](WP_DYNAMIC_MULTISITE.md)）。未登録SITE_IDは受付拒否。
- モデルは[generate_workflows.py](../generate_workflows.py)の定数が正。P4は`claude-opus-5-5`、画像は`gpt-image-2`。
- **v9方針案（未実施）**：現行設計を独立したv9として再整理するかを別途判断する。本PRでは作成しない。

---

## 1. v7 → v8 の変更点

| # | 変更カテゴリ | 内容 | 背景 |
|---|-------------|------|------|
| 1 | **プロンプト内製化** | `TEXT_FILES/` を廃止し、全プロンプトを `prompts.py` の定数へ移行。ディレクトリごと削除 | 外部テキスト依存が5種の矛盾を生んでいた（§2） |
| 2 | **命名統一** | ファイル名からバージョンと "Single" を除去。正式名称 `SEO Blog Auto Writing Workflow` に準拠 | ファイル名 `..._v2.json` と中身 `v1.1` が乖離。複数WF連携時代の "Single" が実態と不一致 |
| 3 | **モデルレジストリ集約** | AIモデル名を `generate_workflows.py` 冒頭の定数に集約し、`.env` で上書き可能に | モデル改称のたびに複数箇所を修正していた |
| 4 | **Flash Lite 正式版化** | `gemini-3.1-flash-lite-preview` → `gemini-3.1-flash-lite` | preview 終了に伴う GA 移行 |
| 5 | **記事HTML整形** | タイトル重複除去・画像挿入位置修正・HTML文書殻の除去 | WP投稿タイトルと本文 `<h1>` の二重表示、`<figure>` が見出しタグ内に混入 |
| 6 | **旧世代コード削除** | `generate_workflows.js`（旧JS版生成器）を削除。あわせて `buildP1()`〜`buildP5()` / `buildP7()` / `buildOrchestrator()` を削除（416行） | 1ワークフロー運用の確定により、複数WF連携時代のビルダーは死蔵コード |
| 7 | **テスト接合部の是正** | プロンプトのモック注入を廃止し、実定数との照合に変更 | 空振りするテストが3件存在した（§3） |

---

## 2. プロンプト管理の再設計（v8 の中核）

### 2-1. 旧構成の問題

v7 では P1〜P5 を `TEXT_FILES/*.txt` から読み込み、P6 のみ Python 定数に直書きしていた。この二重管理は v7 §9 で `[issue: prompt management]` として課題登録されていたが、実際には単なる二重管理にとどまらず、以下5点の矛盾を生んでいた。

| # | 内容 | 影響 |
|---|------|------|
| 1 | プロンプトの正本が txt と py の2箇所に分裂 | 編集対象が Phase ごとに異なる |
| 2 | P1 のみ `#` 始まりの行を削除。一方 P6 は `#` 行が本文として残る前提 | 同じ記法が Phase により逆の意味を持つ |
| 3 | `P2_RAW.index('インプットした「')` でプロンプト本文を検索し2ターンに分割 | 文言を推敲すると `ValueError` で生成停止 |
| 4 | `.replace('(p1_prompt.txt で作成したデータベースを貼り付け)', '')` で但し書きを除去 | プロンプト本文にファイル名が混入。文言がずれても**例外を出さず**除去に失敗する |
| 5 | テストが `builtins.open` を差し替え偽プロンプトを注入 | Pythonテストが本番プロンプトを一度も通らない。#3 が起きてもテストは緑のまま生成だけ落ちる |

### 2-2. 新構成

```
prompts.py            ← 人間が編集する文言（正本）。加工処理を一切持たない
generate_workflows.py ← 機械が組む構造。プロンプトは import するのみ
```

設計原則は **「生成器はプロンプトに一切手を加えない」**。`prompts.py` の各定数は LLM に送られる最終形そのものであり、読んだ文字列がそのままモデルに渡る。

| 定数 | 用途 |
|------|------|
| `P1_PROMPT` | キーワードリサーチ（コメント行除去済み） |
| `P2_T1` / `P2_T2` | アウトライン生成（2ターン会話。**最初から別定数**として保持） |
| `P3_RAW` | HTML本文生成 |
| `P4_RAW` | 長文化・強化（Claude） |
| `P5_RAW` | メタディスクリプション |
| `P6_ANALYZE_PROMPT` / `P6_IMAGE_PROMPT` | 画像挿入位置分析 / 画像生成 |

これにより §2-1 の矛盾は加工処理ごと消滅した。#2 は除去処理の廃止、#3・#4 は分割・置換処理の廃止、#5 は `open()` 自体の消滅による。

### 2-3. 移行の等価性検証

移行は手作業のコピーを行わず、現行モジュールから加工後の値を機械的に書き出す方式を採った。検証は **生成される n8n ワークフロー JSON がバイト単位で一致すること** をゲートとした。

```
移行前 SEO_Blog_Single_Workflow_v2.json  MD5: d8e65bc3b0e165971cd123cbe4d0ad67
移行後 (改名前の同一条件で再生成)          MD5: d8e65bc3b0e165971cd123cbe4d0ad67  → IDENTICAL
```

n8n は `TEXT_FILES` を参照しない（プロンプトは生成時に JSON へ埋め込み済み）ため、この一致は **実行時の挙動が不変であることの証明** となる。命名変更（§4）はこの検証の後に、意図した差分のみを確認して適用した。

---

## 3. テスト方針の変更

### 3-1. モック注入の廃止

v7 のテストは `builtins.open` を差し替え、ファイル名をキーに偽プロンプト（`FAKE_PROMPTS`）を注入していた。`generate_workflows.py` から `open()` が消えたため、この接合部を撤去した。

### 3-2. 空振りテストの是正

移行時に、**通っているが何も検査していないテストが3件**発見された。

```python
# 旧: _P5_MARKER は偽プロンプト内にしか存在しない文字列
assert _P5_MARKER not in js          # モック撤去後は永久に成功する
```

これらは「P4 のノードに P5 のプロンプトが埋まる」という実在したバグ（v7以前に修正済み）への防御であり、失われれば同種のクロス配線が再発しうる。実定数との照合に置き換えた。

```python
# 新: js_str() で JSON エンコードされた本番プロンプトと全文照合
assert gw.js_str(prompts.P4_RAW) in js       # 正しいプロンプトが入っている
assert gw.js_str(prompts.P5_RAW) not in js   # 誤ったプロンプトが入っていない
```

検査対象が実在する文字列になったため空振りせず、かつ全文一致のためプロンプト推敲でテストが割れない。

同様に、本番ワークフロー名を旧リテラルと比較していたテストも動的比較へ修正した。

### 3-2-1. 旧ビルダー削除に伴うガードの移設

`buildP4()` / `buildP5()` の削除でクロス配線テストが消えるため、同じ検査を**本番ワークフロー
（`buildWorkflow()`）側に移設**した（`TestPromptCrossWiring`）。P4ノードにP5のプロンプトが
入っていないこと・P5ノードにP6のプロンプトが入っていないことを、正負両方で検査する。

さらに `test_no_sub_workflow_builders_remain` を追加し、削除したビルダーが復活していないことを
`hasattr` で検査する。テスト数は 282 → 262 に減少したが、これは旧ビルダー用テスト20件の削除に
よるもので、本番パスのカバレッジは減っていない。

### 3-3. カバレッジ（v8）

| レイヤ | v7 | v8 | 対象 |
|--------|----|----|------|
| Python (`test_generate_workflows.py`) | 267 | **262** | ノード生成、接続グラフ、プロンプト配線、命名規約、モデルレジストリ、記事HTML整形 他 |
| Node.js (`node_tests/`) | 54 | **70** | Cleanup（ExcelJS mock）、P6画像ロジック、WP投稿、Build Updated HTML、Parse Strengthened、Parse Meta + Extract Title 他 |
| LLM Review | 任意 | 任意 | n8n固有バグパターンの外部LLM検証 |

---

## 4. 命名規約（v8 新設）

### 4-1. 規約

- 正式名称は **SEO Blog Auto Writing Workflow**
- **ファイル名にバージョンを含めない**。バージョンは WF README 付箋（n8nキャンバス上および生成JSON内）に記載する
- 複数ワークフロー連携時代の名残である "Single" は使用しない

### 4-2. 変更対応表

| 対象 | v7 | v8 |
|------|----|----|
| 出力ディレクトリ | `workflows_v2/` | `workflows/` |
| 本番JSON | `SEO_Blog_Single_Workflow_v2.json` | `SEO_Blog_Auto_Writing_Workflow.json` |
| n8n表示名 | `SEO Blog Auto Writing Workflow v1.1` | `SEO Blog Auto Writing Workflow` |
| workflow id | `seo_blog_single_v11` | `seo_blog_auto_writing` |
| ビルダー関数 | `buildSingleWorkflow()` | `buildWorkflow()` |
| タグ | `single-workflow` | `seo-blog-auto-writing` |

### 4-3. workflow id の扱い（重要）

n8n の `id` は**稼働中ワークフローの同一性**を決める識別子であり、実行履歴・activate 状態・Webhook URL が紐づく。したがって：

- **ファイル名の整理を理由に id を変更してはならない**
- id を変更した場合、次回 CLI インポートで**別の新規ワークフローが作成され**、旧ワークフローはそのまま残る

v8 では命名統一の一環として id を変更したため、以下の移行を実施した（**2026-07-20 完了**）。

```bash
# 1. 生成JSONをマウント先へ配置（C:\n8n\data → /home/node/.n8n-files）
# 【過去のWindows/Docker手順。現在は実行しない】
cp workflows/SEO_Blog_Auto_Writing_Workflow.json C:/n8n/data/

# 2. インポート（JSON内の id をそのまま採用し、新規WFとして作成される）
docker exec n8n n8n import:workflow --input=/home/node/.n8n-files/SEO_Blog_Auto_Writing_Workflow.json

# 3. 旧WF停止 → 新WF有効化
docker exec n8n n8n update:workflow --id=seo_blog_single_v11 --active=false
docker exec n8n n8n update:workflow --id=seo_blog_auto_writing --active=true

# 4. 再起動（active 変更は再起動しないと反映されない）
docker compose restart n8n
```

> **Git Bash 実行時の注意**: `--input=/home/node/...` は Git Bash がパスを Windows 形式へ変換して
> `ENOENT: ... C:/Program Files/Git/home/node/...` で失敗する。`MSYS_NO_PATHCONV=1` を前置すること。

**移行結果**: 旧WF `seo_blog_single_v11` は **deactivate 済み・削除はしていない**（実行履歴が紐づくため保全）。
起動ログで `Activated workflow "SEO Blog Auto Writing Workflow" (ID: seo_blog_auto_writing)` を確認済み。

---

## 5. ワークフロー実体

| 環境 | n8n ID | Webhook Path | Name | 状態 |
|------|--------|--------------|------|------|
| テスト | `HGtIq1cMQN1zYy0w` | `/blog-pipeline-test` | SEO Blog: Test Workflowv0.3 | active |
| **本番** | **`seo_blog_auto_writing`** | `/blog-pipeline-upload` | **SEO Blog Auto Writing Workflow** | **active**（2026-07-20〜） |
| 旧本番 | `seo_blog_single_v11` | 同上 | SEO Blog Auto Writing Workflow v1.1 | inactive（履歴保全のため残置） |

n8n CLI の `import:workflow` は JSON 内の `id` をそのまま採用する（自動採番しない）。したがって
`generate_workflows.py` 側で id を変えると、実機に**新しい箱が増える**。

### 5-1. ワークフローの蓄積（要整理）

実機には 2026-07-20 時点で **21 ワークフロー**が存在し、大半が旧版の残骸である。

| 分類 | 例 | 件数 |
|------|----|----|
| 本番系の旧版 | `SEO Blog Auto Writing Workflow v0.7` 〜 `v1.1` | 14 |
| テスト系の旧版 | `SEO Blog: Test Workflowv0.1` 〜 `v0.3` | 3 |
| 検証用 | `P6 Loop Verification Test` / `Multi-Site Dry-Run Test` | 2 |
| 旧アーキテクチャ | `Orchestrator: AI Blog Pipeline 2026 Optimized` | 1 |
| **現行** | `SEO Blog Auto Writing Workflow` / `SEO Blog: Test Workflowv0.3` | 2 |

`SEO Blog Auto Writing Workflow v1.0` は **ID違いで2件**存在する（`CQvDZoFuGQxV8NUC` / `1DnXBfyEsgTc86Ur`）。
名前にバージョンを含める運用が、実機側でも重複と混乱を生んでいた実例であり、§4-1 の命名規約の根拠となる。
→ [issue: n8n workflow cleanup]

---

## 6. AIモデルレジストリ

### 6-1. 集中管理

モデル名は `generate_workflows.py` 冒頭の定数に集約されている。改称時の修正は1箇所で済む。

```python
MODEL_PRO        = 'gemini-3.1-pro-preview'
MODEL_FLASH_LITE = 'gemini-3.1-flash-lite'      # v8: preview → GA
MODEL_IMAGE      = 'gemini-3.1-flash-image-preview'
MODEL_CLAUDE     = 'claude-sonnet-4-6'
```

### 6-2. 実行時上書き

n8n 実行時に環境変数で上書きできる。JSONを再生成せずにモデルを差し替えられる（`.env` を編集し n8n を再起動）。

| 環境変数 | 対象 | 未設定時 |
|----------|------|----------|
| `GEMINI_MODEL_PRO` | P1 リサーチ / P3 本文 / 画像挿入位置分析 | `MODEL_PRO` |
| `GEMINI_MODEL_FLASH_LITE` | P2 アウトライン / P5 メタ | `MODEL_FLASH_LITE` |
| `GEMINI_MODEL_IMAGE` | P6 画像生成 | `MODEL_IMAGE` |
| `CLAUDE_MODEL_STRENGTHEN` | P4 長文化・強化 | `MODEL_CLAUDE` |

生成JSONには `$env.GEMINI_MODEL_PRO || '<定数>'` の形で埋め込まれるため、環境変数未設定の n8n でもそのまま動作する。

---

## 7. 記事HTML整形（v1.1 相当）

| 対象ノード | 変更内容 | 解決した事象 |
|-----------|---------|-------------|
| Parse Strengthened | Claude が返す完全HTML文書から `<body>` 内のみ抽出。doctype/head/meta/title/style/script を除去（class/id は保持） | 記事本文にHTML文書殻が混入 |
| Parse Meta + Extract Title | 投稿タイトルを `<h1>` から採取（無ければ h2 → KEYWORD）し、本文の `<h1>` を削除 | WP投稿タイトルと本文見出しの二重表示 |
| Build Updated HTML | 挿入位置が見出しの場合、`<figure>` を `</hN>` の外に挿入 | `<figure>` が見出しタグ内に入り表示崩れ |

---

## 8. 既知の制約・課題（v8）

- **Gemini MALFORMED_FUNCTION_CALL リスクは P1/P2/P3/P5 に潜在**: P4 のみ Claude 化で回避、他フェーズは Gemini のまま → [issue: Gemini fallback/retry]
- **当時の課題・現行では解消**: xlsxキューファイルの競合。現行はGoogle Sheets標準ノード（冒頭の現在構成参照）
- **HTTP リトライ無し**: 5xx/timeout で即失敗 → [issue: retry mechanism]
- ~~**旧サブワークフロー用ビルダーが残存**~~ → **v8 で削除**（§1-6、§3-2-1）。本番パスは `buildWorkflow()` のみ
- **n8n実機に旧版ワークフローが21件蓄積**: うち現行は2件のみ。同名異IDの重複も存在（§5-1）→ [issue: n8n workflow cleanup]
- **当時の課題**: v7記載2.16.1と当時の実機2.17.5のずれ。現行はmacOS版2.35.7。バージョン変化の自動検出は**要確認**。
- ~~**プロンプト管理**: TEXT_FILES/ 経由ファイル管理と JSON インライン化の二重管理~~ → **v8 で解消**（§2）

### 8-1. コスト試算

v7 §5 から変更なし（**約 $12.50/月**、月30記事想定）。Flash Lite の GA 移行に伴う単価変動は未反映のため、実績値での再検証が必要。

---

## 9. 関連リンク

- 実装: [`../generate_workflows.py`](../generate_workflows.py)
- プロンプト正本: [`../prompts.py`](../prompts.py)
- 成果物: [`../workflows/SEO_Blog_Auto_Writing_Workflow.json`](../workflows/SEO_Blog_Auto_Writing_Workflow.json) / [`../workflows/Test_Workflow.json`](../workflows/Test_Workflow.json)
- テスト: [`../test_generate_workflows.py`](../test_generate_workflows.py) / [`../node_tests/__tests__/`](../node_tests/__tests__/)
- LLM レビュアー: [`../tools/n8n_llm_reviewer.py`](../tools/n8n_llm_reviewer.py)
- 開発規約: [`../CLAUDE.md`](../CLAUDE.md)
- 旧版: [`./SEO記事自動生成_アーキテクチャ設計書_v7.md`](./SEO記事自動生成_アーキテクチャ設計書_v7.md)
