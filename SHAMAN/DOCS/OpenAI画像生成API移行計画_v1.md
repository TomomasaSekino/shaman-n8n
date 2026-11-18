# OpenAI 画像生成 API 移行計画 v1

**対象**: SEO Blog Auto Writing Workflow の P6 画像生成を Google Nano Banana（Gemini 画像モデル）から OpenAI 画像生成 API へ移行する
**ステータス**: 計画（未実装・レビュー待ち）
**作成日**: 2026-07-31

> **旧計画の記録**：本文中の `Docker_Environment/` は旧Docker前提の記述。`SHAMAN/Docker_Environment/` は2026-11に削除した。「変更ファイル一覧」と「Step 5: 環境変数の配備」の旧Docker手順は現在使わない。

---

## 1. 現状調査結果

### 1.1 画像生成に関係する実在ファイル・ノード・変数

| 種別 | 実体 |
|------|------|
| ワークフロー生成スクリプト | `SHAMAN/generate_workflows.py`（`buildWorkflow()` 内 `p6_nodes`、549〜833行） |
| プロンプト定義 | `SHAMAN/prompts.py` — `P6_ANALYZE_PROMPT`（244行〜）、`P6_IMAGE_PROMPT`（282行〜） |
| 生成物（n8n JSON） | `workflows/SHAMAN_Workflow.json`、`workflows/SHAMAN_Test_Workflow.json` |
| モデルレジストリ | `generate_workflows.py` 冒頭 `MODEL_IMAGE = 'gemini-3.1-flash-image-preview'`（12行）＝ Nano Banana 系モデル |
| 環境変数 | `GOOGLE_AI_API_KEY`（画像 API 認証）、`GEMINI_MODEL_IMAGE`（モデル上書き）、`IMG_PATH`（参照画像ディレクトリの既定）— `Docker_Environment/docker-compose.yaml` は `env_file: .env` で伝搬 |
| サイト別設定 | xlsx「サイト共通設定」シート → `siteConfigMap` → `Map Variables` ノードで `imgPath: sc.IMG_PATH`（373行） |
| Python テスト | `test_generate_workflows.py` — `TestBuildP6PromptAssignment`、`TestModelNames`、`TestModelRegistry`、`TestGenerateImagesPerSectionRefImages`、`TestGenerateImagesPerSectionMaxOutputTokens`、`TestParseSectionPlanDeletesReferenceImages`、`TestNoNestedImageLoop`、`TestUploadAllImagesGuards`、`TestP6SaveArticleOnMaxRetries`、`TestP6UploadAbort`、`TestImageUploadRequirements` |
| Node.js テスト | `node_tests/__tests__/generate-images-per-section.test.js`、`parse-section-plan.test.js`、`wp-upload-all-images.test.js`、`build-updated-html.test.js`、`p6-save-article.test.js`、`p6-upload-abort.test.js`（ハーネス: `node_tests/helpers/n8n-mock.js`） |
| ドキュメント | `DOCS/SEO記事自動生成_アーキテクチャ設計書_v8.md`（モデル表・env 表）、ルート `README.md` |

### 1.2 現在の画像生成フロー（P6）

```
Check P6 Mode (If: IMAGE_TRIGGER === 'TRUE')
 ├─ TRUE → Read Reference Images        … imgPath(既定 /home/node/img) から jpg/png/webp を全件 base64 化
 │         → Build Section Analysis Request … P6_ANALYZE_PROMPT + articleHtml で Gemini Pro 用 requestBody 構築
 │         → Gemini: Analyze Sections   … HTTP Request ノード (geminiHttp, timeout 120s)
 │         → Parse Section Plan         … JSON 抽出。insertionContext / imageDescription / altText を検証、
 │                                         有効 5 件未満なら p6Status=insufficient_sections_N、最大 7 件に slice
 │         → Generate Images Per Section … Code ノード内 for ループ。参照画像(inlineData) + P6_IMAGE_PROMPT を
 │                                         generativelanguage.googleapis.com/v1beta/models/{MODEL_IMAGE}:generateContent
 │                                         へ this.helpers.httpRequest で送信（タイムアウト指定なし・リトライなし）。
 │                                         成功 → imageB64/imageMime/imageFilename、失敗 → imageError、
 │                                         成功 5 枚未満 → 全 item に p6Status=insufficient_images_N
 │         → P6: Error Gate? ─ TRUE → P6: Save Article … 記事 JSON を保存し STATUS=画像エラー（後日再実行可能）
 │                           └ FALSE → WP: Upload All Images … native https で WP /wp-json/wp/v2/media へ
 │                                     バイナリ POST（5xx は指数バックオフで最大3リトライ、60s タイムアウト）
 │         → WP: Upload Error? ─ TRUE → P6: Upload Abort（ドラフト保存 + throw）
 │                              └ FALSE → Build Updated HTML … insertionContext の見出し直後に <figure> 挿入
 └─ FALSE → Build WP Post（画像なしで P7 へ）
```

再実行フロー: `STATUS=画像エラー` の行は `Filter Rows` を通過し、`Check Article Status` → `Load Saved Article` で本文を復元して P6 のみやり直す。

### 1.3 2つのプロンプトの役割

| 定数 | 役割 | 送信先 |
|------|------|--------|
| `P6_ANALYZE_PROMPT` | 記事 HTML を解析し、挿入位置（見出しタグ原文）・画像内容の指示文・ALT テキストを 5〜7 件の JSON で出力させる「**可変情報の生成**」 | Gemini Pro（`MODEL_PRO`、テキストのみ） |
| `P6_IMAGE_PROMPT` | 参照画像との画風・キャラクター同一性維持、16:9、テキスト禁止などの「**固定情報**」テンプレート。`# 以下に画像説明を入力してください #` を `imageDescription` で置換して 1 画像ずつ送信 | Gemini 画像モデル（`MODEL_IMAGE`、参照画像 inlineData 同梱） |

### 1.4 Nano Banana に依存している箇所（コード根拠）

1. `generate_workflows.py:12` — `MODEL_IMAGE = 'gemini-3.1-flash-image-preview'`
2. `generate_workflows.py:380` — `Map Variables` の `MODEL_IMAGE: $env.GEMINI_MODEL_IMAGE || …`
3. `generate_workflows.py:620-664` — `Generate Images Per Section` の Gemini `generateContent` 呼び出し（URL、`x-goog-api-key`、`contents/parts/inlineData`、`generationConfig.responseModalities`、レスポンスの `candidates[0].content.parts[].inlineData` 抽出）
4. `prompts.py` `P6_IMAGE_PROMPT` — Gemini 前提の記述（「思考プロセスの非表示」＝マルチモーダル応答対策、プロンプト内アスペクト比指定）
5. `node_tests/__tests__/generate-images-per-section.test.js` — Gemini レスポンス形状（`candidates/parts/inlineData`）のモック
6. `test_generate_workflows.py` — `TestModelNames`（`gemini-3.1-flash-image-preview` の文字列 assert）、`TestGenerateImagesPerSectionMaxOutputTokens` ほか

なお、`Gemini: Analyze Sections`（挿入位置解析）は**テキスト生成であり Nano Banana ではない**。今回の移行対象外（プロンプト拡張のみ）。

また `README.md`・`CLAUDE.md` に `FAL_KEY` / fal-ai の記載があるが、現行の `generate_workflows.py` に fal-ai 呼び出しは存在しない（ドキュメントの記載残り）。本計画では事実として記録するに留め、修正対象にはしない。

---

## 2. 影響範囲

### 変更対象

| ファイル / ノード | 変更の種類 |
|---|---|
| `generate_workflows.py` — モデルレジストリ | `MODEL_IMAGE_OPENAI = 'gpt-image-2'` 追加。既存 `MODEL_IMAGE`（Gemini）は切り戻し用に維持 |
| `generate_workflows.py` — `Map Variables` ノード | `IMAGE_PROVIDER`（`$env.IMAGE_PROVIDER || 'openai'`）と `MODEL_IMAGE_OPENAI`（`$env.OPENAI_MODEL_IMAGE || …`）、`OPENAI_IMAGE_QUALITY` の読み取り追加 |
| `generate_workflows.py` — `Generate Images Per Section` ノード | プロバイダー分岐＋OpenAI `/v1/images/edits` 呼び出し実装（本丸） |
| `generate_workflows.py` — `Parse Section Plan` ノード | 構造化可変情報（scene オブジェクト）の受け入れ（後方互換） |
| `prompts.py` | `P6_ANALYZE_PROMPT` の出力スキーマ拡張、`P6_IMAGE_PROMPT_OPENAI` 新設。既存 `P6_IMAGE_PROMPT` は Gemini 切り戻し用に維持 |
| `test_generate_workflows.py` | 上記に対応する test 追加・修正 |
| `node_tests/__tests__/generate-images-per-section.test.js` | OpenAI レスポンス形状のモックへ書き換え＋プロバイダー切替テスト追加 |
| `node_tests/__tests__/parse-section-plan.test.js` | scene 構造の正常/欠落ケース追加 |
| `Docker_Environment/docker-compose.yaml` | （任意）`OPENAI_API_KEY=${OPENAI_API_KEY}` の明示行追加。`env_file: .env` で伝搬するため `.env` への追記だけでも動作する |
| `DOCS/` | 本移行を反映した設計書追補（v8 への追記 or v9） |
| `workflows/*.json` | `python generate_workflows.py` による再生成（手編集しない） |

### 変更対象外（触らない）

- P1〜P5（リサーチ・構成・本文・長文化・メタ）と P7（WP 投稿）・Cleanup の全ノード
- `Read Reference Images`（参照画像読み込みは形式そのまま流用可能）
- `Gemini: Analyze Sections` HTTP ノード本体（解析は引き続き Gemini Pro）
- `WP: Upload All Images` / `Build Updated HTML` / `P6: Save Article` / `P6: Upload Abort` / 各 If ゲート — **インターフェース（`imageB64`/`imageMime`/`imageFilename`/`altText`/`insertionContext`）を維持するため無変更**（output_format を jpeg/png に限定することで既存の拡張子ロジック `mimeType === 'image/jpeg' ? 'jpg' : 'png'` がそのまま成立）
- ノード数・接続（connections）— **増減ゼロ**
- xlsx テンプレート・Excel 読み書きロジック

---

## 3. API 差分（Nano Banana vs OpenAI 画像生成）

> OpenAI 側仕様の根拠: 2026-04-21 に API へ投入された `gpt-image-2`（スナップショット `gpt-image-2-2026-04-21`）を OpenAI が現行推奨モデルとして案内している。本計画作成環境からは `developers.openai.com` / `platform.openai.com` への直接アクセスがプロキシで遮断されたため（HTTP 403）、以下は検索経由で確認した公開情報に基づく。**実装着手時に公式 API リファレンス（developers.openai.com/api/reference の images/edit）で必ず再確認すること**（→ §9 未確定事項）。

| 観点 | Nano Banana（現行） | OpenAI（移行先） |
|------|---------------------|------------------|
| エンドポイント | `POST generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` | `POST api.openai.com/v1/images/edits`（参照画像あり）。参照なし生成は `/v1/images/generations` だが今回は edits のみ使用 |
| モデル名 | `gemini-3.1-flash-image-preview` | 第一候補 `gpt-image-2`。代替: `gpt-image-1.5`（約20%安、`input_fidelity` 明示可）。※実装時に公式で正式名を確定 |
| リクエスト形式 | JSON（`contents[].parts[]`、参照画像は `inlineData {mimeType, data(base64)}`） | **multipart/form-data**（`image` フィールドにファイル添付、複数枚可・GPT image 系は最大16枚、PNG/WebP/JPG 各50MB 以下、`prompt` はテキストフィールド） |
| 認証 | ヘッダ `x-goog-api-key: $GOOGLE_AI_API_KEY` | ヘッダ `Authorization: Bearer $OPENAI_API_KEY` |
| プロンプトと画像の関係 | テキスト part と画像 part を同一 `parts` 配列に並べる | `prompt`（単一テキスト）と `image`（ファイル群）が別フィールド |
| 出力 | `candidates[0].content.parts[]` から `inlineData.data`（base64）を探索。テキスト part が混在し画像が返らないことがある（現行コードの `no_image_section_N` の由来） | `data[0].b64_json`（GPT image 系は**常に base64**、URL 返却なし）。画像は必ず1枚以上返るか、エラー応答 |
| サイズ / アスペクト比 | API パラメータなし → プロンプト内で「16:9」を指示（遵守は不確実） | `size` パラメータで指定。`gpt-image-2` は WIDTHxHEIGHT 任意指定（両辺16の倍数、比率 1:3〜3:1）→ **`1536x864` で 16:9 を API 保証**。`gpt-image-1.5` は 1024x1024 / 1536x1024 / 1024x1536 のみ（16:9 不可） |
| 品質 / コスト制御 | `generationConfig`（temperature 等）のみ | `quality`: `low` / `medium` / `high` / `auto`。トークン課金（目安: 16:9 medium ≈ $0.04/枚、high ≈ $0.17/枚） |
| 出力形式 | mimeType は応答依存（png/jpeg） | `output_format`: `png`(既定) / `jpeg` / `webp`、`output_compression`(0-100, jpeg/webp) |
| 参照画像の忠実度 | プロンプト指示のみ | `gpt-image-1.5`: `input_fidelity: high` 指定可。`gpt-image-2`: 常時高忠実度（パラメータ指定不可） |
| コンテンツ拒否 | 応答に画像 part が無い形で顕在化（メッセージ不明瞭） | HTTP 400 系エラー（moderation / content_policy_violation 系のエラーコード）として明示的に返る |
| 背景透過 | — | `gpt-image-2` は `background: transparent` 非対応（今回不要・opaque でよい） |
| レート/所要時間 | 数秒〜十数秒/枚 | quality に依存（medium で十数秒〜、high はさらに長い）。1画像ずつ直列生成のため、タイムアウトを明示設定する必要あり |

**コスト試算**: 7枚/記事 × medium(16:9 ≈ $0.041) ≈ **$0.29/記事、月30記事 ≈ $8.6** — 現行 README 記載の画像コスト（$8.40/月30記事）とほぼ同水準。high にすると ≈ $35/月。既定は `medium` とし `OPENAI_IMAGE_QUALITY` で上書き可能にする。

---

## 4. プロンプト変更方針

責務分離の設計: **固定情報（キャラ外見・衣装・画風・同一性条件・禁止事項・共通品質）は `prompts.py` の新テンプレートに、可変情報（場所・行動・ポーズ・表情・構図・カメラアングル・背景・小物・演出）は解析フェーズの JSON 出力に**置き、`Generate Images Per Section` が両者＋参照画像を合成して OpenAI へ渡す。

| 対象 | 方針 | 理由 |
|------|------|------|
| `P6_ANALYZE_PROMPT` | **修正（拡張）** | 役割（可変情報の生成）は正しいので維持。現在は `imageDescription` 1本の自由文だが、出力 JSON に構造化 `scene` オブジェクト（`location` / `action` / `pose` / `expression` / `composition` / `cameraAngle` / `background` / `props` / `mood`）を追加する。`insertionContext` / `altText` の絶対制約はそのまま。`imageDescription` は要約文として残し、scene 欠落時のフォールバックと WP キャプション用途に使う（後方互換） |
| `P6_IMAGE_PROMPT` | **維持（Gemini 切り戻し用に凍結）＋ OpenAI 用を新設** | 単純移植はしない。`P6_IMAGE_PROMPT_OPENAI` を新設し、(a) 参照画像が `image` フィールドで渡る前提の文言（「添付された参照画像と同一のキャラクター・画風」）、(b) 固定情報ブロック（キャラ同一性: 顔立ち・髪型・髪色・瞳の色・肌色・体型 / 衣装の基準 / アニメイラスト画風 / テキスト・ロゴ・ウォーターマーク禁止 / 品質条件）、(c) 可変情報プレースホルダ（scene を項目名付きで展開）で構成する。**Gemini 固有の記述は持ち込まない**: 「思考プロセスの非表示」（マルチモーダルテキスト応答対策）は不要、「16:9」のプロンプト内指示は `size=1536x864` パラメータへ移管して削除 |
| 統合 / 分離 | **しない** | 2段構成（解析→生成）は OpenAI 移行後も最適。解析は記事全体の文脈が必要で Gemini Pro のテキスト処理が適任、生成は画像特化 API。統合すると 1 リクエストの責務過多になり、分離追加はノード増で制約違反 |
| プロンプト管理方針 | **維持** | すべて `prompts.py` の Python 定数として保持（外部ファイル化しない）。`js_str()` 経由で JSON へ埋め込む現行方式のまま |

合成順序（Generate Images Per Section 内）: `固定ブロック（P6_IMAGE_PROMPT_OPENAI）` → `可変ブロック（scene を「場所: …」「行動: …」形式で展開、無ければ imageDescription）`。固定と可変で矛盾が出ないよう、固定側には「服装・ポーズ・背景はシーン指示に従って変更してよいが、キャラクターのアイデンティティ（顔立ち・髪型・髪色・瞳・肌色）は参照画像を維持する」という優先順位規則を明文化する。

---

## 5. 実装ステップ

CLAUDE.md の Strict TDD 4-Step（pytest → JSON 再生成 → Jest → 外部 LLM レビュー）を各ステップ内で回す。

### Step 1: モデルレジストリと env 配線（`generate_workflows.py`）

- **対象**: モデルレジストリ（冒頭定数）、`Map Variables` ノード
- **変更内容**: `MODEL_IMAGE_OPENAI = 'gpt-image-2'`（実装時に公式リファレンスで正式名を確定してから記入）を追加。`Map Variables` に `IMAGE_PROVIDER: $env.IMAGE_PROVIDER || 'openai'`、`MODEL_IMAGE_OPENAI: $env.OPENAI_MODEL_IMAGE || '<literal>'`、`IMAGE_QUALITY: $env.OPENAI_IMAGE_QUALITY || 'medium'` を追加。既存 `MODEL_IMAGE`（Gemini）行は削除しない
- **変更理由**: モデル名一元管理・`.env` 上書きという既存規約（`TestModelRegistry`）に従い、切替を設定値で制御可能にする
- **入出力**: 入力なし → `Map Variables` 出力 JSON に 3 フィールド追加
- **依存関係**: なし（最初に実施）
- **テスト方法**: `test_generate_workflows.py` に `TestModelRegistry` 相当の assert 追加（`$env.IMAGE_PROVIDER`、`$env.OPENAI_MODEL_IMAGE` が Map Variables の jsCode に含まれる、正式モデル名リテラルの一致）
- **完了条件**: pytest 全緑、再生成 JSON に新フィールドが出現

### Step 2: プロンプト改訂（`prompts.py`）

- **対象**: `P6_ANALYZE_PROMPT` 修正、`P6_IMAGE_PROMPT_OPENAI` 新設（`P6_IMAGE_PROMPT` は無変更）
- **変更内容**: §4 のとおり。`P6_ANALYZE_PROMPT` の出力フォーマット例に `scene` オブジェクトを追加し、「scene の各値は視覚的・具体的に、キャラクター名や固有名詞を含めない」等の制約を記載
- **変更理由**: 固定/可変の責務分離の明確化と OpenAI の単一 `prompt` フィールド形式への最適化
- **入出力**: `P6_ANALYZE_PROMPT` → Gemini Pro 出力 JSON に `scene` が追加。`P6_IMAGE_PROMPT_OPENAI` → Step 4 で合成されるテキスト
- **依存関係**: Step 1 の後（import 追加は Step 4 と同時でも可）
- **テスト方法**: `TestBuildP6PromptAssignment` を拡張（`gw.js_str(prompts.P6_IMAGE_PROMPT_OPENAI) in js_code`、ANALYZE に `"scene"` が含まれる）。プロンプト実文の品質は Step 7 の実機比較で検証
- **完了条件**: pytest 全緑

### Step 3: `Parse Section Plan` の scene 受け入れ

- **対象**: `Parse Section Plan` Code ノード（`generate_workflows.py:595-619`）
- **変更内容**: 検証条件は現行どおり `insertionContext && imageDescription && altText` を維持し、`s.scene` が object であれば出力 item に透過。scene 欠落は**エラーにしない**（imageDescription フォールバックで生成継続）
- **変更理由**: 解析 LLM の出力揺れで記事全体を落とさないため。最小変更で後方互換
- **入出力**: 入力: Gemini 解析 JSON → 出力: 各 section item に `scene?` 追加
- **依存関係**: Step 2
- **テスト方法**: `parse-section-plan.test.js` に scene あり/なし/型不正の 3 ケース追加
- **完了条件**: pytest + Jest 全緑

### Step 4: `Generate Images Per Section` の OpenAI 対応（本丸）

- **対象**: `Generate Images Per Section` Code ノード（`generate_workflows.py:620-664`）
- **変更内容**:
  1. `section.IMAGE_PROVIDER === 'gemini'` なら現行 Gemini パスをそのまま実行（コード温存）
  2. `openai`（既定）パス: 参照画像（`Read Reference Images` の `{b64, mime, file}`）を multipart パートに変換し、`prompt`（固定＋可変合成）、`model`、`size=1536x864`、`quality`、`output_format=jpeg`、`output_compression=80`、`n=1` とともに `https://api.openai.com/v1/images/edits` へ POST。認証は `Authorization: Bearer $env.OPENAI_API_KEY`
  3. **multipart は boundary を自前組み立てし Buffer 化して native `https` で送信**（`WP: Upload All Images` と同じ方式。同ノードのコメントに「`this.helpers.httpRequest` は Buffer ボディで不安定（500 化）」という実績記録があるため）。リクエストタイムアウト 180s/枚
  4. レスポンス `data[0].b64_json` → `imageB64`、`output_format` に対応する mime → `imageMime`（`image/jpeg`）、`imageFilename: 'article-image-N.jpg'`。**下流インターフェースは不変**
  5. リトライ: HTTP 429 / 5xx / タイムアウトのみ指数バックオフ（2s→4s）で最大2回再試行。HTTP 400 系（moderation・content policy・パラメータ不正）は**リトライせず** `imageError: 'policy_or_bad_request_section_N: <エラーコード>'` を記録して次セクションへ継続
  6. 部分失敗の扱いは現行踏襲: 成功 5 枚未満 → `p6Status: insufficient_images_N` → `P6: Error Gate?` → `P6: Save Article` → `STATUS=画像エラー`（既存の再実行フローに乗る）。5〜7枚成功なら失敗分は `mediaId: null` のまま先へ（現行と同じ）
- **変更理由**: API 移行の中核。既存 Code ノードへの集約・ノード数増ゼロの制約に適合
- **入出力**: 入力: section items + `Read Reference Images` 参照 → 出力: 現行と同一スキーマ（`imageB64`/`imageMime`/`imageFilename`/`imageIndex`/`imageError?`/`p6Status?`）
- **依存関係**: Step 1〜3
- **テスト方法**: `generate-images-per-section.test.js` を OpenAI モック（`{data:[{b64_json}]}`）へ書き換え。検証項目: multipart ボディに参照画像バイト列と boundary が含まれる / prompt に固定・可変両ブロックが含まれる / Authorization ヘッダ / 429→リトライ後成功 / 400→リトライなしで imageError / 5枚未満→insufficient / `IMAGE_PROVIDER=gemini` で旧パスが呼ばれる（Gemini 形状モックで従来テストが通る）
- **完了条件**: pytest + Jest 全緑、`n8n_llm_reviewer.py` で `【問題なし】`

### Step 5: 環境変数の配備（`.env` / `docker-compose.yaml`）

- **対象**: `Docker_Environment/docker-compose.yaml`（任意の明示行）、運用側 `.env`（リポジトリ外・コミットしない）
- **変更内容**: `.env` に `OPENAI_API_KEY=…` を追加（`env_file: .env` で伝搬）。可読性のため compose の `environment:` に `OPENAI_API_KEY=${OPENAI_API_KEY}` を明示追加してもよい。**キーをコード・ワークフロー JSON・リポジトリに書かない**
- **依存関係**: Step 4 と独立（実機検証前に必要）
- **テスト方法**: `docker compose config` で解決確認、n8n コンテナ内で `$env.OPENAI_API_KEY` 参照確認
- **完了条件**: 実機ワークフローが認証エラーなく API に到達

### Step 6: JSON 再生成と実機インポート

- **対象**: `workflows/SHAMAN_Workflow.json`、`SHAMAN_Test_Workflow.json`
- **変更内容**: `python generate_workflows.py` で再生成（手編集禁止）。ワークフロー `id`（`seo_blog_auto_writing`）は変更しない（CLAUDE.md の規約: id 変更は別ワークフロー化を引き起こす）。WF README sticky のバージョンを v1.2 に更新
- **テスト方法**: n8n UI へインポート → Test_Workflow（`SKIP_IMAGE`）で非画像経路の無風確認 → `IMAGE_TRIGGER=TRUE` の 1 行で実機 E2E
- **完了条件**: WP 下書きに 5〜7 枚の 16:9 画像が挿入され、xlsx へ「完了」が書き戻る

### Step 7: 品質比較（Nano Banana vs OpenAI）

- **対象**: 運用判断（コード変更なし）
- **変更内容**: 同一記事・同一参照画像・同一 scene 指示で、`IMAGE_PROVIDER=gemini` と `openai` を各1回実行し、§6.4 の観点表で比較評価。`quality: medium` と `high` も 1 記事ずつ比較
- **完了条件**: 比較表が埋まり、既定 quality と本採用可否をユーザーが判断できる状態

### Step 8: ドキュメント更新

- **対象**: `DOCS/`（設計書追補）、`README.md` の該当行（画像生成の技術選定・コスト・env 表）
- **変更内容**: P6 の API・env 変数（`OPENAI_API_KEY` / `IMAGE_PROVIDER` / `OPENAI_MODEL_IMAGE` / `OPENAI_IMAGE_QUALITY`）・切り戻し手順を記載
- **完了条件**: ドキュメントと実装の不一致がない

---

## 6. テスト計画

### 6.1 単体テスト（pytest — `test_generate_workflows.py`）

- モデルレジストリ: `MODEL_IMAGE_OPENAI` 正式名リテラル、`$env.OPENAI_MODEL_IMAGE` / `$env.IMAGE_PROVIDER` / `$env.OPENAI_IMAGE_QUALITY` の埋め込み
- プロンプト配線: `P6_IMAGE_PROMPT_OPENAI` が `Generate Images Per Section` に、scene 拡張済み `P6_ANALYZE_PROMPT` が `Build Section Analysis Request` に `js_str` 経由で埋め込まれている（実定数比較、fake 不可）
- 旧 Gemini パス温存: `generativelanguage.googleapis.com` と `api.openai.com/v1/images/edits` が同一ノード jsCode 内に共存
- 禁止事項: jsCode 内の OpenAI ボディ構築で `JSON.stringify` を HTTP Request ノードの jsonBody に対して使っていない（multipart は対象外だが、二重エンコード規約の回帰確認）
- 接続不変: P6 の connections・ノード数が変更前後で不変

### 6.2 ノードテスト（Jest — `node_tests/__tests__/`)

- `generate-images-per-section.test.js`（書き換え＋追加）: §5 Step 4 記載の 8 系統（正常5枚 / multipart 内容 / prompt 合成 / 429 リトライ / 400 非リトライ / 部分失敗→insufficient / 上流エラー pass-through / provider=gemini 旧経路）
- `parse-section-plan.test.js`（追加）: scene 透過・欠落フォールバック・型不正
- `wp-upload-all-images.test.js` / `build-updated-html.test.js`: **無変更で全緑のまま**であること自体を回帰確認（インターフェース不変の証明）

### 6.3 結合テスト（実機 n8n）

1. `SKIP_IMAGE` で P6 迂回経路の無影響確認
2. `IMAGE_TRIGGER=TRUE` × 1 記事 E2E（medium）: WP 下書き・画像枚数・`<figure>` 挿入位置・ALT・xlsx 書き戻し
3. `STATUS=画像エラー` 再実行フロー: 意図的に `OPENAI_API_KEY` を無効化して全滅 → `P6: Save Article` → キー復旧 → 再実行で復元完走
4. `IMAGE_PROVIDER=gemini` へ切替再起動 → 現行同等動作（切り戻しリハーサル）

### 6.4 画像品質比較（主観排除のための観点表）

同一条件（同一記事・同一参照画像・同一 scene）で Nano Banana / OpenAI 各 7 枚を生成し、以下を記録:

| 観点 | 測定方法 |
|------|----------|
| キャラクター同一性（顔立ち・髪型・髪色・瞳・肌色・衣装基準） | 7枚を参照画像と並べ、項目別 ○/× 判定（別人化 1 枚でも × ） |
| セクション本文との一致度 | scene 指示の要素（場所・行動・小物）が画像に存在するか要素別カウント |
| 固定/可変プロンプトの競合有無 | 衣装・画風が scene 指示に引きずられて崩れたケース数 |
| 構図の自然さ / 手指・身体の破綻 | 破綻画像の枚数 |
| 背景の整合性 | 記事テーマと無関係な背景の枚数 |
| 指示遵守率 | テキスト混入・16:9 逸脱・ロゴ出現の件数（OpenAI は size で 16:9 保証のため主にテキスト混入） |
| 連続生成時の安定性 | 7枚通しでの同一性維持（1枚目と7枚目の比較） |
| 再生成率 | 採用不可と判断し再実行した枚数 / 7 |
| 生成時間 | 1枚あたり所要秒（n8n 実行ログ） |
| 費用 | 1記事あたり API 費用（OpenAI usage ダッシュボード実測 vs 試算 $0.29） |

### 6.5 異常系テスト

- OpenAI 429（レート制限）→ バックオフ後成功（Jest モック）
- OpenAI 400 moderation → リトライなし・当該セクションのみ imageError・残り継続（Jest）
- タイムアウト（180s 超）→ imageError → 5 枚以上成功なら続行（Jest）
- 全滅 / 5枚未満 → `insufficient_images_N` → `P6: Save Article` → xlsx `画像エラー`（実機 6.3-3）
- 参照画像ディレクトリ空 → 既存の `Read Reference Images` throw（現行仕様のまま、回帰確認のみ）

---

## 7. 切り戻し計画

二段構えとする。

**レベル1: 設定値切り戻し（数分・コード変更なし）**
1. `.env` の `IMAGE_PROVIDER=gemini` を設定（または追記）
2. `docker compose restart n8n`（env 再読込）
3. `IMAGE_TRIGGER=TRUE` の 1 行で Gemini 経路の動作確認

`Generate Images Per Section` 内に Gemini パスと `P6_IMAGE_PROMPT`（旧プロンプト）を温存するのはこのため。`GOOGLE_AI_API_KEY` と `GEMINI_MODEL_IMAGE` の env・フォールバックリテラルも削除しない。

**レベル2: コード切り戻し（レベル1で解消しない場合）**
1. `git revert` で移行コミットを打ち消し → `python generate_workflows.py` で JSON 再生成
2. n8n へ再インポート（ワークフロー `id` 不変のため同一ワークフローが上書きされ、Webhook URL・実行履歴は維持される）

**切り戻し判断基準（例）**: キャラクター同一性の不合格率が Nano Banana 実績を上回る / 1記事あたり費用が想定の2倍超 / moderation 拒否が常態化して 5 枚を安定確保できない。

---

## 8. リスク

| リスク | 発生条件 | 影響 | 対策 |
|--------|----------|------|------|
| キャラクター同一性が参照画像方式でも不足する | gpt-image-2 の参照画像追従が期待未満 | 記事全体の画像品質低下・別人化 | Step 7 の比較検証を本採用のゲートにする。`gpt-image-1.5` + `input_fidelity: high` を代替候補として比較に含める。プロンプト側の同一性条項（§4）で補強 |
| moderation による生成拒否 | 記事テーマ（健康・法律・金融等）由来の scene 指示が引っかかる | セクション画像欠落 → 5 枚未満で記事保留 | 400 は即 imageError 化して継続（全体は落とさない）。scene 生成プロンプトに「医療行為・危険行為の直接描写を避け、比喩的・日常的シーンに置換する」規則を追加。頻発時は解析プロンプトの表現規則を調整 |
| multipart 実装の不具合 | boundary / Content-Length の組み立てミス | 400/500 で全滅 | WP アップロードで実績のある native https + Buffer 方式を踏襲。Jest でバイト列レベルの multipart 検証。実機 E2E を完了条件に含む |
| 生成時間の増大 | quality=high や API 混雑 | 7枚直列で P6 が長時間化、n8n 実行タイムアウト | 180s/枚のタイムアウト明示（現行は未設定）。既定 quality=medium。所要時間を Step 7 で実測して判断 |
| コスト超過 | high 常用・再生成多発 | 月額費用が現行比増 | 既定 medium（試算で現行同水準）。`OPENAI_IMAGE_QUALITY` で制御。Step 7 で実測費用を記録 |
| レート制限（429） | 組織 Tier が低い新規アカウント | 生成失敗の断続発生 | 指数バックオフ最大2回 + 失敗許容（5枚閾値）。Tier 確認を実装前チェックリストに含める |
| 公式仕様と本計画の齟齬 | 本計画の API 詳細は二次情報を含む | パラメータ名・制約の誤り | Step 4 着手前に公式リファレンスで §9 の全項目を確定してから実装（完了条件に含む） |
| テスト時の実 API 費用 | 実機 E2E・品質比較 | 比較 2 プロバイダー × 数記事分の費用 | 実機は最小行数（1〜2記事）で実施。Jest は全てモック |

---

## 9. 未確定事項（実装着手時に公式ドキュメント / 実環境で確定する）

本計画作成環境からは OpenAI 公式ドキュメント（developers.openai.com / platform.openai.com）への直接アクセスが遮断されていたため、以下は着手時の必須確認事項として残す。

1. **正式モデル名**: `gpt-image-2`（および snapshot `gpt-image-2-2026-04-21`）が `/v1/images/edits` で利用可能な正確な ID か。`gpt-image-1.5` の edits 対応と `input_fidelity` の正確な仕様
2. **multipart のフィールド名**: 複数参照画像を渡す際のフィールド名が `image` の繰り返しか `image[]` か
3. **`size=1536x864` の可否**: gpt-image-2 の「16 の倍数・1:3〜3:1」制約の一次情報確認（検索結果ベース）。不可の場合の 16:9 代替値
4. **moderation 拒否時のエラーコード**: エラー JSON の `error.code` / `error.type` の正確な値（非リトライ判定に使用）
5. **`moderation` パラメータ**: gpt-image-1 系にあった `moderation: low` が gpt-image-2 で指定可能か
6. **利用アカウントのレート制限 Tier** と gpt-image-2 の RPM/TPM 実値
7. **参照画像 16 枚上限の適用範囲**: 現在 `Read Reference Images` はディレクトリ内全件を読む。サイトの参照画像枚数が上限に収まるか（超える場合は先頭 N 枚に制限する処理を Step 4 に含める）
8. **キャラクター同一性の実力**: Nano Banana 比の品質は実測（Step 7）でのみ確定
9. **`quality` 既定値の最終決定**: medium/high の品質差と費用差の実測結果待ち
10. **n8n 2.16.1 の Code ノードで native `https` + 大きな multipart Buffer（参照画像合計サイズ依存）が問題なく送信できるか**: WP アップロード（1枚ずつ）では実績あり、複数枚同梱は実機で確認

---

## 実装前レビュー用チェックリスト

### 変更対象（コード）
- [ ] `generate_workflows.py`: モデルレジストリ + `Map Variables`（`IMAGE_PROVIDER` / `OPENAI_MODEL_IMAGE` / `OPENAI_IMAGE_QUALITY`）
- [ ] `generate_workflows.py`: `Generate Images Per Section` — OpenAI `/v1/images/edits`（multipart, native https, 180s timeout, 429/5xx リトライ, 400 非リトライ継続）＋ Gemini パス温存
- [ ] `generate_workflows.py`: `Parse Section Plan` — `scene` 透過（後方互換）
- [ ] `prompts.py`: `P6_ANALYZE_PROMPT` scene 拡張 / `P6_IMAGE_PROMPT_OPENAI` 新設 / `P6_IMAGE_PROMPT` 凍結維持
- [ ] `.env`: `OPENAI_API_KEY`（リポジトリにはコミットしない）
- [ ] `workflows/*.json`: スクリプト再生成のみ（手編集なし・ワークフロー id 不変）
- [ ] ノード数・接続: **増減ゼロ**
- [ ] 変更対象外: P1〜P5・P7・Cleanup・`WP: Upload All Images`・`Build Updated HTML`・xlsx ロジック

### テスト範囲
- [ ] pytest: レジストリ / プロンプト配線 / 両 API パス共存 / 接続不変
- [ ] Jest: OpenAI モック正常系・multipart 検証・リトライ/非リトライ・部分失敗・provider 切替
- [ ] Jest: `wp-upload-all-images` / `build-updated-html` が**無変更で**全緑（インターフェース不変の証明）
- [ ] 外部 LLM レビュー: `n8n_llm_reviewer.py` で `【問題なし】`
- [ ] 実機: SKIP_IMAGE 無風 / E2E 1記事 / 画像エラー再実行 / gemini 切替リハーサル
- [ ] 品質比較: §6.4 の観点表（同一条件・両プロバイダー・7枚×2）

### 切り戻し方法
- [ ] レベル1: `.env` の `IMAGE_PROVIDER=gemini` + `docker compose restart n8n`（コード変更なし・数分）
- [ ] レベル2: `git revert` → JSON 再生成 → 再インポート（id 不変で Webhook・履歴維持）
- [ ] 切り戻し判断基準を運用者と合意（同一性不合格率 / 費用 / moderation 拒否率）

### 実装開始前の確定事項（ブロッカー）
- [ ] §9 の 1〜5（公式 API リファレンスでモデル名・multipart 形式・size・エラーコード・moderation パラメータを確定）
- [ ] OpenAI アカウントの Tier / レート制限確認、`OPENAI_API_KEY` 発行
