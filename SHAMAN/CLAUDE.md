# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This repo contains **SHAMAN** (Synthetic Hack of Akashic Memory & Authoring Node), an SEO blog auto-generation system built on n8n. The core idea: Python scripts programmatically generate n8n workflow JSON files, which are then imported into a local n8n instance. The workflows call Gemini AI through multiple sequential phases to research, write, enhance, and publish SEO blog articles to WordPress.

## Commands

### 1. Generate n8n Workflow JSON
```bash
cd SHAMAN
python generate_workflows.py
# Output: workflows/*.json (SHAMAN_Workflow.json, SHAMAN_Prompt_Eval.json, SHAMAN_Connection_Check.json)
```

### 2. Python Tests
```bash
cd SHAMAN
python -m pytest test_generate_workflows.py -v
# Run a single test:
python -m pytest test_generate_workflows.py::TestBuildP1::test_name -v
```

### 3. Node.js Tests (n8n Code node logic)
```bash
cd SHAMAN/node_tests
npm ci                  # first run after a clone — node_modules is not committed
npm test
npm run test:watch      # watch mode
```

### 4. External LLM Review (Final Validation)
```bash
# <path_to_target_json> should be replaced with the actual JSON file generated in the current task.
cd SHAMAN
python tools/n8n_llm_reviewer.py <path_to_target_json>
```

## Development Workflow (Strict TDD & Review Loop)

タスクを実行する際は、**必ず以下の順序（ステップ1〜4）で作業を進めてください。** 手順をスキップしたり、順序を入れ替えたりしないでください。

1. **Python Update & Test**:
   Pythonコード（またはテスト）を修正し、`pytest` を実行してすべてパスさせる。
2. **Regenerate JSON**:
   Pythonスクリプト（`generate_workflows.py` 等）を実行し、対象となるJSONファイル（例: `SHAMAN_Prompt_Eval.json` など）を必ず再生成する（Node.jsテストに反映させるため）。
3. **Node.js Test**:
   `npm test` を実行し、Codeノード内のJSロジックが正しく動くか確認する。失敗した場合はステップ1に戻る。
4. **External LLM Validation (実行条件あり)**:
   ステップ1〜3がすべて成功し、あなたが「これで実装は完璧だ、タスク完了だ」と判断した場合の**最終確認としてのみ**、ステップ4のコマンド（`n8n_llm_reviewer.py`）を生成されたJSONファイルに対して実行してください。
   * 指摘（エラー終了）があった場合：あなたの前提や仕様理解が間違っている可能性があります。指摘事項を最優先で読み込み、ステップ1からやり直してください。
   * `【問題なし】`（正常終了）が出た場合：タスクを完了とし、ユーザーに報告してください。
   * **警告**: 開発途中の軽微な修正のたびにステップ4を実行しないでください。APIコスト削減のため、ローカルテスト（Python/Node.js）が両方グリーンになった時のみ呼び出すこと。

## Architecture

### Two-Layer Test Strategy + Final Validation

The system has an unusual two-language test architecture, followed by a strict AI validation phase:

1. **Python tests** (`test_generate_workflows.py`) — validate that python scripts correctly embed prompts, build node graphs, and wire connections in the output JSON.
2. **Node.js tests** (`node_tests/__tests__/`) — validate the *JavaScript logic inside* n8n Code nodes. The helper `node_tests/helpers/n8n-mock.js` loads the generated workflow JSON, extracts the `jsCode` of a named node, and runs it in a Node.js `vm` context with mocked `$input`, `$env`, `$()`, and `this.helpers.httpRequest`. **This means Python tests must pass and the target workflow JSON must be regenerated before Node.js tests will reflect code changes.**
3. **External LLM Review** (`tools/n8n_llm_reviewer.py`) — A strict validation script that uses an external LLM to check the final JSON for n8n-specific parsing errors, missing parameters, and schema violations before importing to the real n8n instance.

### Workflow Pipeline

The workflows execute phases such as the following sequence:

| Phase | Purpose |
|-------|---------|
| P1 | Keyword research via Gemini + Google Search |
| P2 | Article outline generation (2-turn conversation) |
| P3 | HTML article body generation |
| P4 | Content strengthening/enrichment |
| P5 | Meta description generation |
| P6 | Image generation with retry loop |
| P7 | WordPress media upload + article publish |

### Prompt Templates

Prompts are Python constants in `SHAMAN/prompts.py` — the single source of truth. **There is no external prompt file**; the former `TEXT_FILES/` directory was removed, along with the comment-stripping and turn-splitting logic that used to derive prompts from it.

Each constant is the *final* text sent to the LLM: `generate_workflows.py` performs no stripping, splitting or substitution, and embeds each one verbatim as a string literal in the workflow JSON via `js_str()` (which calls `json.dumps`). P2 is a two-turn conversation and is stored as two independent constants (`P2_T1`, `P2_T2`). Lines starting with `#` are prompt body and are sent as-is.

Template variables like `${KEYWORD}`, `${SITENAME}`, `${AUTHOR}`, `${BASEURL}` are substituted at n8n runtime via `.split('${VAR}').join(value)` in the Code nodes.

Prompt-wiring tests compare against the real constants (`gw.js_str(prompts.P4_RAW) in js_code`), never against injected fakes — a mocked prompt file would make "must NOT contain" assertions pass vacuously.

### Key Files

- `SHAMAN/generate_workflows.py` — single source of truth for all workflow node definitions; functions return node lists. `buildWorkflow()` builds the production workflow
- `SHAMAN/prompts.py` — all LLM prompts; edit prompt wording here
- `SHAMAN/node_tests/helpers/n8n-mock.js` — VM harness; `runNode(nodeName, items, options)` is the core test utility
- `SHAMAN/workflows/*.json` — generated artifacts; committed for reference but regenerated from Python source
- `SHAMAN/prompt_eval.py` — the prompt evaluation workflow (`SHAMAN: Prompt Eval`, id `shaman_test`; `DOCS/PROMPT_EVAL_DESIGN.md`). It runs one LLM step (P1–P5, P6 section analysis) per execution on a saved input, built from the **same step builders as production** (`p1Nodes()` … `p5Nodes()`, `p6AnalysisNodes()` in `generate_workflows.py`). Do not copy prompts or parse logic into it; change the step builder instead. It replaced the old Test Workflow (DRY_RUN / SKIP_IMAGE / autonomous DRY_RUN, removed 2026-11-04)

### Naming

The official workflow name is **SHAMAN** (Synthetic Hack of Akashic Memory & Authoring Node). Filenames carry no version — the version lives in the WF README sticky note (visible in the n8n canvas and in the generated JSON).

The n8n workflow `id` (`shaman`) is the identity of the live workflow — its execution history, activation state and webhook URLs are bound to it. Changing the id makes the next CLI import create a *separate new workflow*, leaving the old one running. Never change it as part of a cosmetic rename.

The SHAMAN rename deliberately took that hit: the id moved from `seo_blog_auto_writing` to `shaman`, so importing the generated JSON registers a new workflow instead of updating the old one. The pre-rename workflow keeps the same webhook path (`blog-pipeline-upload`) and must be deleted in the n8n UI, or the two collide on that path.

### Environment Variables (n8n runtime)

**None.** luna runs n8n 2.x with `N8N_BLOCK_ENV_ACCESS_IN_NODE=true`, so nodes cannot read `$env`, and Code nodes
cannot `require` modules. External calls go through HTTP Request / Google Drive / Discord nodes, and n8n
Credentials add the authentication (`apiHttp`, `wordpressHttp`, `discordNode`, `driveSearchNode`,
`driveDownloadNode` in `generate_workflows.py`; the same builders as the connection check workflow,
`DOCS/CONNECTION_CHECK.md`). Credential IDs are `__ENV:SHAMAN_*_CREDENTIAL_ID__` placeholders. The deploy script lists n8n Credentials
(`GET /api/v1/credentials`) and replaces each with the only Credential of that type; names do not matter (n8n default names are fine).
Only when 2+ Credentials share the type is the node's name used to pick one (not Repository Variables;
anything unresolved stops the deploy, listing every unresolved Credential at once). LLM Credentials are shared
with other projects, so their names carry no project name. Never put API keys, passwords or webhook URLs in nodes.

- Run from Actions (`DOCS/RUN_FROM_ACTIONS.md`): every workflow has a Header Auth webhook (`runWebhookNode`,
  Credential `SHAMAN / Run Webhook`, `httpHeaderAuth`, `responseMode: lastNode`) and is **published** on deploy.
  `.github/workflows/run-shaman-n8n.yml` (owner `workflow_dispatch`) POSTs to it from the luna runner via
  `tools/luna/run_shaman_workflow.py`. The deploy script rejects any webhook without that Header Auth.
- Gemini (`googlePalmApi`), Anthropic (`anthropicApi`), OpenAI (`openAiApi`), Discord (`discordWebhookApi`),
  Google Drive and Sheets (`googleApi`, service account), WordPress (one shared `httpBasicAuth` Credential
  `SHAMAN / WordPress / MultiSite`; its User/Password expressions pick the values by `$json.siteId`).
- WordPress calls go through one shared node each (`WP: Upload Media` / `WP: Create Post`) to `https://<SITE_ID>`;
  node count does not grow with sites. A SITE_ID that is not in the 「サイト登録」 tab of the queue spreadsheet is
  rejected at `Filter Rows` before P1 (PR #101). Adding a site is manual: `DOCS/WP_DYNAMIC_MULTISITE.md`.
- Reference images come from Google Drive: `SHAMAN/IMG/<IMG_FOLDER or SITE_ID>/` (folder ID in `config/drive.json`).
- P6 image requests: `Build Image Requests` builds the OpenAI multipart body (the HTTP Request node cannot repeat
  `image[]` for a variable number of files) and `P6: Generate Image` sends it as binary. 429 / 5xx / network errors
  are retried per section by the `P6: Retry Images?` loop (3 attempts in total); other 4xx are not retried.

**Models and image settings** are constants in `generate_workflows.py`, embedded into Map Variables at generate time
(change them there and regenerate):
- `MODEL_PRO` — Pro model (Research / Content)
- `MODEL_OUTLINE` — P2 Outline: `gemini-3.5-flash-lite` (owner's choice, 2026-11-09)
- `MODEL_FLASH_LITE` — P5 Meta: `gemini-3.1-flash-lite`
- `MODEL_SECTION_ANALYSIS` — P6A section analysis: `gemini-3.8-flash` (owner's choice, 2026-11-09; its price doubles on 2027-01-01)
- `MODEL_IMAGE` — Gemini image model (rollback path only)
- `MODEL_IMAGE_OPENAI` — OpenAI image model for P6
- `IMAGE_PROVIDER` — `openai` (default) or `gemini` (rollback: regenerate; the P6 HTTP node is chosen at generate time)
- `IMAGE_QUALITY` — `low` / `medium` (default) / `high` for OpenAI images
- `MODEL_CLAUDE` — Claude model for P4 strengthen: `claude-opus-5-5` (owner's choice, 2026-11-07; Sonnet had too many
  problems). P4 sends adaptive thinking with `output_config.effort: 'high'` (Opus 5.5 defaults to `medium`) and **no
  sampling params** — Opus 5.5 returns 400 for `temperature`/`top_p`/`top_k`, and thinking cannot be disabled. `max_tokens` (96000) caps thinking + article combined
  (HTTP timeout 1800s; 900s timed out on luna with claude-sonnet-4-6, production execution 8, 2026-11-07), and Parse Strengthened throws on `stop_reason` `max_tokens` / `refusal` instead of passing a
  truncated article on.

### P4 prompt (`P4_RAW`)

P4 exists to turn the P3 draft into the article that fully resolves the searcher's problem — not to add characters.
P4 currently uses Claude Opus 5.5 (`claude-opus-5-5`). Keep these explicit `P4_RAW` requirements to avoid ambiguous or overly minimal output (each is covered by a test):
- State the purpose and apply the per-section rubric to **every `<h3>`** (h4 answers, one of ol/table/ul, a typical
  usage scene written as an example, never as the author's own experience (no experiences are provided), boxes, 900–1,300 chars as a guide, plus a self-review before output). A bare minimum such as "700文字以上" gets
  treated as the target, and "構成を変えない" without scope suppresses h4/tables/lists.
- Receive the P1 research via `${P1_KNOWLEDGE}` (placed before `### 以下記事本文 ###`) and ground new facts in it.
- Contain no link-building instructions: P4 has no browsing tool and P1 strips source URLs, so they only yield invented URLs.
- Compare wording candidates in a Claude chat against the real P3 output (`htmlContent`) from a past n8n Execution, using
  only the minimal inputs listed in WBS Phase 2, before changing `P4_RAW`. Do not iterate through API calls or full
  pipeline runs; the real API is used once, after one candidate is chosen.

**P4 phrasing (v1.4 Phase 1.2).** `P4_RAW` caps each stock opener (「具体的には、」「初心者がやりがちなのが、」…) at two uses per
article and puts the pitfall element in only one `<h4>` per `<h3>`, with the other `<h4>`s carrying concrete usage and
situational judgement instead — dropping the pitfall without that replacement shrank the article from 13,661 to 9,435
chars in offline validation. Both `P4_RAW` and `P4_REPAIR_RAW` forbid describing research, experiments, surveys or data
analysis that were never done.

### Grounded knowledge (2026-11-09, `DOCS/P1_KNOWLEDGE.md`)

P1 is the only step that searches (Google Search grounding). P2–P4 have no search tool, so their prompts must never
tell them to search or to fill gaps from the model's own knowledge; their facts (numbers, product specs, proper nouns)
come only from `p1Knowledge`, and nothing listed under P1's 「確認できなかった情報」 is filled in. P3's persona is voice
only — no invented experiences. Source URLs are never written by the model: Parse Research takes them from
`groundingMetadata` into `p1Sources` (internal data); `p1Knowledge` carries only the site names. A run with no sources
or with unconfirmed items sets `p1ResearchStatus` (queue エラー内容 + Discord; the run continues). The P4 gate also flags
mentions of other articles on the site (「別の記事」「関連記事」…) and URL text, and the final article has every `<a>`
unwrapped and URL text removed (no link is verified yet). If the selected attempt still mentions another article after
both P4 calls, Parse Strengthened throws `SHAMAN_RUN_NG` instead of posting it (PR #85 review B1).

### P2 headings and the queue stance (v1.4 Phase 1.3)

Every queue row sets OPTION1 to「研究者としてのスタンスで」. With no guidance P2 read that as "research was done" and
produced fictional headings (「研究データが示す…」「実証実験から導く…」) in 5/5 runs. `P2_T2` now states that the stance is a
writing attitude, bans 研究・実験・実証・検証・調査・分析・データ・科学・統計 in headings, and limits the full keyword to the two
H2s and the closing H3. A stricter per-level heading count was tried and lowered full-constraint compliance from 3/5 to 0/5,
so it was not adopted. Instead, Parse Outline checks the outline mechanically (2 H2s, 12–13 headings in total, ≤25 chars
without symbols, no banned words). If attempt 1 fails, `P2: Retry?` sends the outline back once with the unmet rules as an
extra turn (`P2_RETRY_PREFIX`); if attempt 2 still fails it throws `SHAMAN_RUN_NG` (R27-1 had 11 headings, 2026-11-09).

### No links from P3 (v1.4 1.4)

P3 calls Gemini **without** a search tool, yet its prompt used to say "run a site: search and add internal links", so it
invented URLs (real run 1295: three internal links, all 404, reached the WordPress draft). `P3_RAW` now forbids links, and
Parse Content (`parseHtmlCode(..., unwrapLinks=True)`) unwraps any `<a>` it still emits, keeping the anchor text and
reporting `p3LinksRemoved`. Because the P3 draft is now link-free, the P4 gate's "no links added" check covers every link
in the article. Real links are added only from verified candidates (WBS Phase 3).

`P3_RAW` wording candidates are compared in a Gemini chat with search tools off, against the minimal inputs from a past
n8n Execution (WBS Phase 2), not by repeated API runs.

### Meta description (v1.4 Phase 1.1)

`P5_RAW` asks for the description text only. Parse Meta + Extract Title also strips a trailing character-count note
(「（133文字）」「(133文字)」「【133文字】」「（文字数：133）」「 133文字」), which real run 1294 had published.

**P4 quality gate.** Parse Strengthened compares the article with the P3 draft (`htmlContent`): h2/h3 headings unchanged,
every `<h3>` section at least 700 chars with an ol/ul/table, and no added `<a>` links. On attempt 1 a failure sets
`p4Retry` (carrying `p4PrevHtml` / `p4PrevIssues`) and `P4: Retry?` loops back to Build P4 Request.

The retry is a **repair, not a regeneration**: Build P4 Request switches to `P4_REPAIR_RAW`, sends attempt 1 as the
body and the shortfall as `${P4_FEEDBACK}`, and asks Claude to add to the flagged sections only, never shrinking
anything. Regenerating from P3 was tried in real run 1293 and replaced an 11,940-char attempt 1 (only lists missing)
with an 8,393-char attempt 2. After attempt 2 the gate keeps the better attempt (fewer issues, ties to the longer
article) as `p4Selected`; any remaining shortfall goes to `p4QualityIssues`, the queue's エラー内容 column and the
Discord success message.

## n8n Specific Anti-Patterns & Rules (Crucial)

ClaudeCode（あなた）がコードを生成する際、以下のn8n特有の仕様に強く注意してください。これらはPythonの単体テストをパスしても、実機で必ず実行時エラー（400 Bad Request等）を引き起こします。

### 1. DO NOT double-encode JSON (`JSON.stringify` の禁止)
- HTTP Requestノードで `specifyBody: "json"` を設定し `jsonBody` を渡す場合、n8nは送信時に自動でシリアライズを行います。
- 事前のCodeノード内や、エクスプレッション (`={{ }}`) 内で **`JSON.stringify()` を使用して文字列化しないでください。**
- データは必ず生のJavaScriptオブジェクト（Object）または配列（Array）のまま、後続のHTTP Requestノードへ渡してください。
