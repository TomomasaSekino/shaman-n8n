# SEO記事自動生成システム 設計書 v7

**マルチサイト対応 / Gemini + Claude ハイブリッド / ExcelJS書式保持 / 単一ワークフロー / WordPress自動投稿**

- 版: v7（2026-04-24）
- 前版: [v6 (docx)](./SEO記事自動生成_アーキテクチャ設計書_v6.docx)
- 対象実装: `generate_workflows.py` / `workflows_v2/*.json` / n8n 2.16.1

---

## 1. 設計方針と v6 からの変更点

### 1-1. v6 → v7 の主な変更

| 変更カテゴリ | 内容 | 背景 |
|-------------|------|------|
| **LLM ハイブリッド化** | P4（長文化・強化）を Gemini 3.1 Pro → **Claude Sonnet 4.6** に切替 | Gemini Pro が `MALFORMED_FUNCTION_CALL` を返す事象が長文SEOプロンプトで散発（存在しないツール呼び出しを幻覚） |
| **xlsx 書式保持エンジン** | Cleanup ノード内の xlsx R/W を SheetJS (xlsx) → **ExcelJS** に全面切替 | SheetJS Community Edition は `cellStyles:true` でも cell font/fill/border を write 時に剥がす仕様（Pro限定機能）。書式保持＋肥大化防止の両立が必要 |
| **errorWorkflow 設定削除** | `settings.errorWorkflow` の固定ID参照をやめ、Error Trigger 内蔵による n8n デフォルト自己参照挙動に統一 | 旧 Orchestrator 時代の固定ID（`TEST_WF_2026`/`SINGLE_WF_v2_2026`）が実機インポート時 n8n 自動採番と不一致となり、エラー発生時に `Could not find workflow` が発生・通知不達 |
| **テスト/本番ワークフロー分離** | v0.3 (Test) と v1.0 (Production) を別インスタンスとして明示的に運用 | Webhook path は `/blog-pipeline-test` / `/blog-pipeline-upload` で分離。動作検証はv0.3、本番投稿はv1.0 |
| **TDD 強化** | Python 267 / Node.js 54 テストで回帰保証 | ExcelJS / Claude P4 / errorWorkflow 削除の各修正で RED→GREEN サイクルを適用 |
| **LLM 自動レビュー** | `tools/n8n_llm_reviewer.py` で JSON 品質を外部LLMで確認 | splitInBatches v3 の port 順序や httpRequest v4.2 の jsonBody 仕様など n8n 特有の誤判定パターンをプロンプトで明示 |

### 1-2. LLM 使い分け方針（v7）

| Phase | モデル | 理由 |
|-------|--------|------|
| P1 リサーチ | `gemini-3.1-pro-preview` (+Google Search) | Google検索統合の一次情報収集に最適 |
| P2 アウトライン | `gemini-3.1-flash-lite-preview` | 構造生成は軽量モデルで十分 |
| P3 HTML本文生成 | `gemini-3.1-pro-preview` | 初稿の質を重視 |
| **P4 長文化・強化** | **`claude-sonnet-4-6`** | 長文指示追従・function-call 幻覚なし・E-E-A-T 強化品質が向上 |
| P5 メタ/タイトル | `gemini-3.1-flash-lite-preview` | 短文処理は軽量モデル |
| P6 画像生成 | `gemini-3.1-flash-image-preview` + fal-ai | 画像生成APIに依存 |
| P7 WP投稿 | WordPress REST API | 投稿処理 |

---

## 2. 全体ワークフロー（v7 単一ワークフロー構成）

```
Webhook (POST) → Spreadsheet File (Excel読込) → Filter Rows (待機中のみ)
  → Loop Over Items → Map Variables
    → P1 Gemini Research
    → P2 Gemini Outline
    → P3 Gemini Content
    → P4 Claude Strengthen      ← v7 でLLM切替
    → P5 Gemini Meta
    → Check P6 Mode (IMAGE_TRIGGER分岐)
      → P6 Gemini Image Gen + WP Media Upload + Build Updated HTML
      → P6 Skip
    → Build WP Post → WordPress Create Post → Parse WP Response
    → Cleanup (ExcelJS で xlsx に STATUS/WP_ID/QUALITY/日時 書込)
    → Discord: Notify Success
  → Pipeline Complete

Error Trigger → Discord: Notify Error  (内蔵 Error Handler)
```

### 2-1. ワークフロー実体

| 環境 | n8n ID | Webhook Path | Name | 用途 |
|------|--------|--------------|------|------|
| テスト | `HGtIq1cMQN1zYy0w` | `/blog-pipeline-test` | SEO Blog: Test Workflowv0.3 | TEST_MODE gate あり（DRY_RUN/SKIP_IMAGE/FULL 分岐） |
| **本番** | `1DnXBfyEsgTc86Ur` | `/blog-pipeline-upload` | **SEO Blog Auto Writing Workflow v1.0** | TEST_MODE gate なし、常に FULL 実行 |

両方 active。xlsx キューファイルは同一（`/home/node/.n8n-files/SEO記事キュー.xlsx`）を共有。webhook path で呼び分け。

### 2-2. TEST_MODE (テスト環境専用)

テスト版 v0.3 は `TEST_MODE` 列で処理分岐:

| TEST_MODE | 動作 |
|-----------|------|
| `DRY_RUN` | LLM/WP/画像すべてスキップ、Cleanup のみ実行（Excel書込経路のみ検証） |
| `SKIP_IMAGE` または空欄 | P6 画像生成のみスキップ、その他 full |
| その他（例: `FULL`） | P1〜P7 全実行 |

本番 v1.0 には `TEST_MODE` gate なし。xlsx の TEST_MODE 列値は無視される。

---

## 3. Phase 別詳細設計（v6 からの変更箇所のみ記載）

### 3-5. Phase 4: 長文化・強化 — **Claude Sonnet 4.6** に変更

**旧 (v6)**: Gemini 3.1 Pro で強化。4000-tokens超プロンプト + `site:BASEURL` 指示で `MALFORMED_FUNCTION_CALL` 事象。

**新 (v7)**:

- エンドポイント: `https://api.anthropic.com/v1/messages`
- 認証: `x-api-key: {{ $env.ANTHROPIC_API_KEY }}` + `anthropic-version: 2023-06-01`
- リクエスト形式:
  ```json
  {
    "model": "claude-sonnet-4-6",
    "max_tokens": 32000,
    "temperature": 0.7,
    "messages": [{"role": "user", "content": "...prompt..."}]
  }
  ```
- 応答パース: `response.content[].text` (SheetJS風の `candidates[].content.parts` ではない)

**実測効果（2026-04-23/24）**:

| 指標 | Gemini Pro (旧) | Claude Sonnet (新) |
|------|-----------------|---------------------|
| 平均文字数 | 11,359字 | **15,666字 (+38%)** |
| QUALITY平均 | 96% | **100%** |
| MALFORMED_FUNCTION_CALL | 発生 | **ゼロ** |
| 処理時間/記事 | 7-10分 | 9-11分 (+25%) |
| API単価 | $0.03/記事 | $0.10/記事 |

### 3-8. Phase 7 後処理: Cleanup — **ExcelJS** に変更

**旧 (v6)**: SheetJS (`require('xlsx')`) で xlsx の STATUS/WP_ID/QUALITY 列を更新。

**問題点**:
- `cellStyles: true` 指定で 302MB 肥大化（未圧縮ZIPコンテナ）
- `cellStyles` 削除で肥大化は解決するが、**cell font/fill/border が全消失** (SheetJS Community Edition の制約: col/row dimensions のみ保持、fill/font は Pro 限定機能)

**新 (v7)**: ExcelJS (`require('exceljs')`) に切替。

```javascript
var ExcelJS = require('exceljs');
var wb = new ExcelJS.Workbook();
await wb.xlsx.readFile(queuePath);
var ws = wb.getWorksheet('記事キュー');
// セル更新
ws.getRow(rowIndex + 1).getCell(colIndex + 1).value = value;
await wb.xlsx.writeFile(queuePath);
```

**インフラ要件**:
- Docker image に `npm install -g exceljs` を追加（`C:/n8n/Dockerfile`）
- `NODE_FUNCTION_ALLOW_EXTERNAL=xlsx,moment,lodash,exceljs` に exceljs 追加

**検証結果**: フルパイプライン（LLM+画像+WP）を経ても、列幅・行高・塗りつぶし（FF4472C4/FFFFF2CC/FFE2EFDA）・フォント（ＭＳ Ｐゴシック）・Bold・結合セルが **完全保持**。ファイルサイズは 13KB → 15KB（+14%、肥大化なし）。

---

## 4. エラーハンドリング（v7 簡素化）

### 4-1. 仕組み

**v6**: `settings.errorWorkflow = SINGLE_WF_v2_2026` を固定指定 → インポート時の自動採番IDと乖離 → 発火失敗。

**v7**: n8n docs 準拠。

> If a workflow contains the Error Trigger node, by default, the workflow uses itself as the error workflow.

- `settings.errorWorkflow` は **未指定**
- 同ワークフロー内の **Error Trigger ノード**が n8n のデフォルト挙動で自動的に self-handler として発火
- Error Trigger → Discord: Notify Error で Discord 通知

### 4-2. エラー通知の2経路

| 経路 | 発火条件 | 内容 |
|------|----------|------|
| Discord: Notify Success | ワークフローが正常完走 | `p6Status` / `uploadError` / `wpError` を拾って silent failure も可視化 |
| Discord: Notify Error | ワークフローが致命エラー（Parse throw 等） | Error Trigger が発火、`execution.lastNodeExecuted` を通知 |

---

## 5. コスト試算（v7）

### 5-1. 使用モデルと料金単価（2026-04時点）

| モデル | 入力 $/M tokens | 出力 $/M tokens |
|--------|----------------|----------------|
| gemini-3.1-pro-preview | 1.25 | 5.00 |
| gemini-3.1-flash-lite-preview | 0.10 | 0.40 |
| **claude-sonnet-4-6** | **3.00** | **15.00** |
| fal-ai (画像) | — | 約 $0.04/枚 |

### 5-2. 月30記事の試算

- Gemini系 (P1/P2/P3/P5): 約 $1.10
- **Claude系 (P4)**: 約 $3.00 (v6からの増分 +$2.50)
- 画像 (7枚×30記事): 約 $8.40
- **合計: 約 $12.50/月**

v6の約$11.50 から +$1 程度の増加で、MALFORMED_FUNCTION_CALL ゼロ化・品質+38% を獲得。

---

## 6. Docker 環境

### 6-1. カスタムイメージ構成（`C:/n8n/Dockerfile`）

```dockerfile
FROM docker.n8n.io/n8nio/n8n:latest
USER root
RUN npm install -g xlsx exceljs    # ExcelJS 追加
USER node
```

### 6-2. docker-compose（環境変数）

```yaml
environment:
  - GOOGLE_AI_API_KEY=${GOOGLE_AI_API_KEY}
  - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}      # v7 新規
  - NODE_FUNCTION_ALLOW_EXTERNAL=xlsx,moment,lodash,exceljs  # exceljs 追加
```

---

## 7. テスト方針

### 7-1. 4-step TDD サイクル（CLAUDE.md）

1. Python Update & Test (`pytest test_generate_workflows.py`)
2. Regenerate JSON (`python generate_workflows.py`)
3. Node.js Test (`cd node_tests && npm test`)
4. External LLM Review (`python tools/n8n_llm_reviewer.py <path>`)

### 7-2. テストカバレッジ（v7）

| レイヤ | テスト数 | 対象 |
|--------|---------|------|
| Python (`test_generate_workflows.py`) | **267** | ノード生成、接続グラフ、プロンプト埋込、P4 Claude移行、ExcelJS、errorWorkflow削除、本番名、他 |
| Node.js (`node_tests/`) | **54** | Cleanup（ExcelJS mock）、P6画像ロジック、WP投稿、Build Updated HTML 他 |
| LLM Review | 任意 | n8n固有バグパターンの外部LLM検証（splitInBatches v3・httpRequest v4.2等） |

---

## 8. 本番運用手順（v1.0）

1. `C:/n8n/data/SEO記事キューテンプレート.xlsx` から作業用コピー作成
2. 待機中行（STATUS=`待機中`、`KEYWORD`/`OPTION1`/`AUTHOR`/`SITE_ID` 必須）を記入
3. `C:/n8n/data/SEO記事キュー.xlsx` として配置
4. Webhook 起動:
   ```bash
   curl -X POST http://localhost:5678/webhook/blog-pipeline-upload
   ```
5. Discord 通知 or xlsx 直接確認で進捗を監視
6. WordPress 下書き（`status: draft`）として投稿される → 管理画面で目視確認 → 公開
7. xlsx には STATUS/WP_POST_ID/QUALITY/開始日時/完了日時/エラー内容が記録される → そのまま実行結果ログとして保存

---

## 9. 既知の制約・課題

- **Gemini MALFORMED_FUNCTION_CALL リスクは P1/P2/P3/P5 にも潜在**: 現状 P4 のみ Claude 化で回避、他フェーズは Gemini のまま → [issue: Gemini fallback/retry]
- **xlsx キューファイルを v0.3 と v1.0 が共有**: 同時起動すると競合の可能性 → [issue: xlsx isolation]
- **HTTP リトライ無し**: 5xx/timeout で即失敗 → [issue: retry mechanism]
- **プロンプト管理**: TEXT_FILES/ 経由ファイル管理と JSON インライン化の二重管理 → [issue: prompt management]

---

## 10. 関連リンク

- 実装: [`../generate_workflows.py`](../generate_workflows.py)
- 成果物: [`../workflows_v2/SEO_Blog_Single_Workflow_v2.json`](../workflows_v2/SEO_Blog_Single_Workflow_v2.json) / [`../workflows_v2/Test_Workflow.json`](../workflows_v2/Test_Workflow.json)
- テスト: [`../test_generate_workflows.py`](../test_generate_workflows.py) / [`../node_tests/__tests__/`](../node_tests/__tests__/)
- LLM レビュアー: [`../tools/n8n_llm_reviewer.py`](../tools/n8n_llm_reviewer.py)
- 旧版（参考）: [`./SEO記事自動生成_アーキテクチャ設計書_v6.docx`](./SEO記事自動生成_アーキテクチャ設計書_v6.docx)
