# SHAMAN

**S**ynthetic **H**ack of **A**kashic **M**emory & **A**uthoring **N**ode

n8n × Gemini/Claude/OpenAI × WordPress のマルチサイト対応 SEO 記事自動生成パイプライン。

---

## リポジトリ構成

```
SHAMAN/
├── SHAMAN/                         ← プロジェクト本体
│   ├── generate_workflows.py       # n8n ワークフロー JSON の生成スクリプト（単一 source of truth）
│   ├── prompts.py                  # 全 LLM プロンプト定数（外部テンプレートは廃止）
│   ├── test_generate_workflows.py  # Python TDD
│   ├── workflows/                  # 生成された n8n ワークフロー JSON
│   │   ├── SHAMAN_Workflow.json        # 本番
│   │   ├── SHAMAN_Prompt_Eval.json     # プロンプト評価（ステップごと。DOCS/PROMPT_EVAL_DESIGN.md）
│   │   └── SHAMAN_Connection_Check.json # 接続確認（DOCS/CONNECTION_CHECK.md）
│   ├── node_tests/                 # Code ノード JS ロジック検証（Jest）
│   ├── tools/
│   │   └── n8n_llm_reviewer.py     # 外部LLMによる JSON 品質レビュー
│   ├── DOCS/
│   │   ├── SEO記事自動生成_アーキテクチャ設計書_v8.md   ← 過去の設計記録
│   │   └── SEO記事自動生成_アーキテクチャ設計書_v7.md   (前版)
│   └── CLAUDE.md                   # Claude Code 向けプロジェクト規約
└── README.md
```

---

## 概要

### 何ができるか

Google スプレッドシート「記事キュー」に keyword と SITE_ID を登録して、ActionsまたはDaily Scheduleで起動すると、

1. **Gemini リサーチ** (Google検索グラウンディングで一次情報収集)
2. **記事構成案 → HTML 本文 → 長文化・E-E-A-T強化** (Gemini + Claude Opus 5.5)
3. **E-E-A-T 向上イラスト画像 5〜7枚生成** (OpenAI images/edits、サイト別キャラクター一貫性あり)
4. **WordPress 下書き投稿** (共通 Credential 1件を利用し、未登録 SITE_ID は受付時に除外)
5. **Google スプレッドシートへ処理結果を書き戻し** (n8n 標準 Google Sheets ノード)

を全自動で実行します。

### 主な技術選定

| レイヤ | 採用 | 理由 |
|--------|------|------|
| ワークフローエンジン | **n8n 2.35.7** (luna/macOS LaunchDaemon) | ノードベース、HTTP/Code/Loop/Branch が揃う |
| LLM (P1・P3) | **Gemini `gemini-3.1-pro-preview`** (`MODEL_PRO`) | `generate_workflows.py` の定数が正本 |
| LLM (P2 構成案) | **Gemini `gemini-3.5-flash-lite`** (`MODEL_OUTLINE`) | `generate_workflows.py` の定数が正本 |
| LLM (P5 メタ) | **Gemini `gemini-3.1-flash-lite`** (`MODEL_FLASH_LITE`) | `generate_workflows.py` の定数が正本 |
| LLM (P6A 画像挿入位置分析) | **Gemini `gemini-3.8-flash`** (`MODEL_SECTION_ANALYSIS`) | `generate_workflows.py` の定数が正本 |
| LLM (P4 長文化) | **Claude Opus 5.5** (`claude-opus-5-5`) | `generate_workflows.py` の定数が正本 |
| 画像生成 | **OpenAI gpt-image-2** (images/edits、参照画像同梱) | `IMAGE_PROVIDER=gemini` で旧 Gemini 画像モデルへ切り戻し可 |
| 記事キュー | **Google Sheets 標準ノード** | 記事キュー・サイト登録を管理 |
| 投稿先 | **WordPress REST API** | マルチサイト認証、メタキー動的対応 |

### 実行コスト

**要確認**：旧モデル構成の月30記事 $12.7 試算は現行 Opus 5.5・gpt-image-2 の料金として未検証。

---

## セットアップ

### 開発（pytest / npm test / JSON 再生成）

```bash
cd SHAMAN
python -m pytest test_generate_workflows.py -v
python generate_workflows.py
cd node_tests
npm ci
npm test
```

必要に応じ `tools/n8n_llm_reviewer.py` を実行する。n8nコンテナの起動や画面からのインポートは行わない。

### 配備・実行（Actions）

- **配備**：オーナーがPRをmainへマージした場合のみ、[deploy-shaman-n8n.yml](.github/workflows/deploy-shaman-n8n.yml) がlunaのself-hosted runnerからn8n APIで本番・SHAMAN_Prompt_Eval・接続確認の3 Workflowを配備、読み戻し、公開する。
- **実行**：[run-shaman-n8n.yml](.github/workflows/run-shaman-n8n.yml)、[RUN_FROM_ACTIONS.md](SHAMAN/DOCS/RUN_FROM_ACTIONS.md)。
- **定期実行**：本番のDaily Scheduleは毎日6:00 JST、最大1行。[UNATTENDED_OPERATION.md](SHAMAN/DOCS/UNATTENDED_OPERATION.md)。
- **監視**：launchdの`com.shaman.watchdog`が5分おきに監視。Discord通知・常駐・空キュー日の定期実行は確認済み。
- **キュー**：[QUEUE_GOOGLE_SHEETS_DESIGN.md](SHAMAN/DOCS/QUEUE_GOOGLE_SHEETS_DESIGN.md)。
- **WordPress**：共通Credential 1件とサイト登録タブ（[WP_DYNAMIC_MULTISITE.md](SHAMAN/DOCS/WP_DYNAMIC_MULTISITE.md)）。

2026-11-10にlunaへの移行完了。n8nは2026-11-01からmacOS版2.35.7をLaunchDaemonで常駐運用。Windows・Docker・Lima・bindfs・iCloudは旧構成。

---

## 開発ワークフロー（Strict TDD 4-Step）

`SHAMAN/CLAUDE.md` で定義されたサイクルを厳守:

1. **Python Update & Test**: 仕様変更やバグ修正は `generate_workflows.py` / `prompts.py` / `test_generate_workflows.py` を修正し `pytest` を全通過させる
2. **Regenerate JSON**: `python generate_workflows.py` で `workflows/*.json` を再生成（Node.jsテストの前提）
3. **Node.js Test**: `npm test` で Code ノード内の JS ロジックを検証
4. **External LLM Validation**: ローカルテスト両緑時のみ `n8n_llm_reviewer.py` で JSON 品質を外部LLMに判定させる（コスト対策）

---

## ワークフロー運用

| 環境 | Webhook Path | 用途 |
|------|--------------|------|
| **本番** | `/webhook/blog-pipeline-upload`（合言葉付き。Actions から実行） | 実サイトへの下書き投稿 |
| プロンプト評価 | `/webhook/shaman-prompt-eval`（合言葉付き。Actions から実行） | LLM のモデルやプロンプトを変えたときの出力を、ステップごとに評価する（`DOCS/PROMPT_EVAL_DESIGN.md`） |
| 接続確認 | `/webhook/shaman-connection-check`（合言葉付き。Actions から実行） | 外部サービスへの接続だけを確かめる（`DOCS/CONNECTION_CHECK.md`） |

3 つとも Actions から手動実行でき、本番は別に Daily Schedule（6:00 JST、最大1行）で実行する（`SHAMAN/DOCS/UNATTENDED_OPERATION.md`）。

運用時はGoogleスプレッドシート「SHAMAN本番用」の「サイト登録」タブにSITE_IDを登録し、「記事キュー」に行を追加する。未登録SITE_IDは受付時に拒否する。WordPress送信先は `https://<SITE_ID>`。

### 改名履歴（過去の記録）

旧「SEO Blog Auto Writing Workflow」から「SHAMAN」に改名し、本番JSONは`SHAMAN_Workflow.json`となった。2026-11-04には旧テストWorkflowが`SHAMAN_Prompt_Eval.json`へ変更された。旧IDや手動インポートは現在の配備手順ではない。

---

## 設計・運用文書

[SEO記事自動生成_アーキテクチャ設計書_v8.md](./SHAMAN/DOCS/SEO記事自動生成_アーキテクチャ設計書_v8.md)

- v8は過去の設計記録。現行の構成は上記のActions・キュー・WordPress運用文書を参照。
- v8 の主な変更: プロンプトを `prompts.py` へ内製化、ファイル名の命名統一、モデルレジストリ集約、記事HTML整形

---

## ライセンス

Private repository. 配布・再利用は作者の許可に従うこと。
