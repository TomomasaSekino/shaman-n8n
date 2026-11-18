"""プロンプト評価 Workflow（prompt_eval.py、DOCS/PROMPT_EVAL_DESIGN.md）の生成を確かめる。外部接続はしない。"""
import copy
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import generate_workflows as gw  # noqa: E402
import prompt_eval as pe  # noqa: E402


@pytest.fixture(scope="module")
def ev():
    return pe.buildPromptEvalWorkflow()


@pytest.fixture(scope="module")
def prod():
    return gw.buildWorkflow()


def by_name(w):
    return {n["name"]: n for n in w["nodes"]}


def targets(w, name, output=0):
    return [c["node"] for c in w["connections"][name]["main"][output]]


def strip(node):
    n = copy.deepcopy(node)
    for k in ("id", "position", "onError"):
        n.pop(k, None)
    n["parameters"].get("options", {}).pop("response", None)
    return n


# ── 本番の処理を共用する（評価のために複製しない） ────────────────────────
@pytest.mark.parametrize("step", pe.STEP_ORDER)
def test_build_and_parse_are_the_production_nodes(ev, prod, step):
    if step in ("WP", "WPD"):
        pytest.skip("WPは認証GETのみの新しいテスト工程")
    spec = pe._steps()[step]
    e, p = by_name(ev), by_name(prod)
    for name in (spec["build"], spec["parse"]):
        assert e[name]["parameters"] == p[name]["parameters"], name
        assert e[name]["type"] == p[name]["type"]


@pytest.mark.parametrize("step", pe.STEP_ORDER)
def test_api_node_is_the_production_node_with_full_response(ev, prod, step):
    """URL・本文・認証・タイムアウトは本番と同じ。違うのは、状態コードとエラーをログに残すための応答の設定だけ。"""
    if step in ("WP", "WPD"):
        pytest.skip("WP認証GETテストのHTTPは本番の投稿ノードと別用途")
    name = pe._steps()[step]["http"]
    e, p = by_name(ev)[name], by_name(prod)[name]
    assert strip(e) == strip(p)
    assert e["parameters"]["options"]["response"] == {"response": {"neverError": True, "fullResponse": True}}
    assert e["onError"] == "continueRegularOutput"


def test_map_variables_is_the_production_node(ev, prod):
    assert by_name(ev)["Map Variables"]["parameters"] == by_name(prod)["Map Variables"]["parameters"]


def test_p4_quality_gate_retry_is_kept(ev, prod):
    assert by_name(ev)["P4: Retry?"]["parameters"] == by_name(prod)["P4: Retry?"]["parameters"]
    # 作り直しは 1 回だけ（P4 の呼び出しは 1 回の評価で最大 2 回）。設計書にも書く（PR #79 のレビュー B1）。
    assert "if (issues.length > 0 && attempt < 2) {" in by_name(ev)["Parse Strengthened"]["parameters"]["jsCode"]
    assert "1 回の評価（回数 1）で最大 2 回" in (Path(__file__).parent / "DOCS" / "PROMPT_EVAL_DESIGN.md").read_text(encoding="utf-8")
    assert targets(ev, "Parse Strengthened") == ["P4: Retry?"]
    assert targets(ev, "P4: Retry?", 0) == ["Build P4 Request"]
    assert targets(ev, "P4: Retry?", 1) == ["Eval: Record"]


def test_production_is_unchanged_by_the_step_builders(prod):
    """本番は、切り出したステップの関数を前と同じ順に呼ぶ。生成済みの本番の JSON と一致する。"""
    committed = json.loads((Path(gw.OUT) / "SHAMAN_Workflow.json").read_text(encoding="utf-8"))
    gw._c[0] = 0
    assert gw.buildWorkflow() == committed


# ── 範囲：LLM と試験用のシートだけ ───────────────────────────────────────
def test_identity_and_triggers(ev):
    """入口は、画面の Manual Trigger と、Actions から実行する合言葉付きの Webhook（DOCS/RUN_FROM_ACTIONS.md）。"""
    assert (ev["name"], ev["id"]) == (pe.NAME, "shaman_test")
    triggers = [n["type"] for n in ev["nodes"] if "trigger" in n["type"].lower() or n["type"].endswith(".webhook")]
    assert triggers == ["n8n-nodes-base.manualTrigger", "n8n-nodes-base.webhook"]
    hook = next(n for n in ev["nodes"] if n["type"] == "n8n-nodes-base.webhook")
    assert hook == gw.runWebhookNode(pe.RUN_WEBHOOK_PATH, hook["position"]) | {"id": hook["id"]}
    assert ev["settings"]["saveDataSuccessExecution"] == "all"
    assert ev["settings"]["saveDataErrorExecution"] == "all"


def test_touches_only_llm_apis_read_only_wordpress_and_test_spreadsheet(ev):
    types = {n["type"] for n in ev["nodes"]}
    assert not types & {"n8n-nodes-base.discord", "n8n-nodes-base.wordpress",
                        "n8n-nodes-base.readWriteFile", "n8n-nodes-base.convertToFile"}
    creds = {t for n in ev["nodes"] for t in (n.get("credentials") or {})}
    assert creds == {"googlePalmApi", "anthropicApi", "googleApi", "httpHeaderAuth", "openAiApi", "httpBasicAuth"} | {
        t for n in ev["nodes"] if n["type"] == "n8n-nodes-base.googleDrive" for t in n["credentials"]}
    # Google Drive は P6 の参考画像を読むだけ。OpenAI は P6: Generate Image だけ（2026-11-09、画像 1 枚の評価）。
    drive = [n for n in ev["nodes"] if n["type"] == "n8n-nodes-base.googleDrive"]
    assert sorted(n["name"] for n in drive) == ["P6: Download Reference Image", "P6: Find IMG Root",
                                                 "P6: Find Image Folder", "P6: List Reference Images"]
    assert all(n["parameters"]["operation"] in ("search", "download") for n in drive)
    assert [n["name"] for n in ev["nodes"] if "openAiApi" in (n.get("credentials") or {})] == ["P6: Generate Image"]
    sheets = [n for n in ev["nodes"] if n["type"] == "n8n-nodes-base.googleSheets"]
    assert sheets
    for n in sheets:
        assert n["parameters"]["documentId"]["value"] == gw.QUEUE_TEST_SPREADSHEET, n["name"]
        if n["parameters"]["operation"] == "create":
            continue
        assert n["parameters"]["sheetName"]["value"] in (pe.SHEET_RUNS, pe.SHEET_RESULTS, pe.SHEET_DATA, pe.SHEET_LOG)


def test_no_old_test_modes(ev):
    text = json.dumps(ev, ensure_ascii=False)
    for word in ("TEST_MODE", "DRY_RUN", "SKIP_IMAGE", "__SHAMAN_AUTO_E2E__", "test1.invalid", "$execution.mode",
                 "記事キュー", "$env", "require("):
        assert word not in text, word


def test_deploy_script_lists_the_same_sheet_nodes(ev):
    sys.path.insert(0, str(Path(__file__).parent / "tools" / "luna"))
    import deploy_shaman_google_sheets as d
    names = {n["name"] for n in ev["nodes"] if n["type"] == "n8n-nodes-base.googleSheets"}
    assert names == d.SHEET_NODES["test"]


def test_writes_are_raw_and_appends_do_not_match_rows(ev):
    for n in ev["nodes"]:
        if n["type"] != "n8n-nodes-base.googleSheets" or n["parameters"]["operation"] in ("read", "create"):
            continue
        assert n["parameters"]["options"]["cellFormat"] == "RAW", n["name"]
        expected = ["row_number"] if n["parameters"]["operation"] == "update" else []
        assert n["parameters"]["columns"]["matchingColumns"] == expected, n["name"]


def test_sheets_and_headers_are_made_before_reading(ev):
    """評価のシートと見出しは、Workflow が自分で用意する（オーナーが作らない。2026-11-06）。
    見出しは、その実行で作ったシートにだけ書く。前からあるシートには書かない（PR #75 のレビュー B1）。"""
    nodes = by_name(ev)
    ensure = nodes["Eval: Ensure Sheets"]
    assert ensure["parameters"]["operation"] == "create"
    assert ensure["parameters"]["title"] == "={{ $json.title }}"
    assert ensure["alwaysOutputData"] is True  # 4 つともあれば 0 件。それでも次へ進む
    assert targets(ev, "Eval: Sheet Setup") == ["Eval: Ensure Sheets"]
    assert targets(ev, "Eval: Ensure Sheets") == ["Eval: Sheets Ready"]
    assert targets(ev, "Eval: Sheets Ready") == ["Eval: New Runs?"]
    keys = [k for k, _, _ in pe.SETUP_SHEETS]
    for i, (key, sheet, columns) in enumerate(pe.SETUP_SHEETS):
        after = "Eval: New " + keys[i + 1] + "?" if i + 1 < len(keys) else "Eval: New Run?"
        cond = nodes["Eval: New " + key + "?"]["parameters"]["conditions"]["conditions"][0]["leftValue"]
        assert cond == "={{ $('Eval: Sheets Ready').first().json.created.includes(" + gw.js_str(sheet) + ") }}"
        # 作ったシート：見出しを書いてから次へ。前からあるシート：何も書かずに次へ。
        assert targets(ev, "Eval: New " + key + "?") == ["Eval: Header " + key]
        assert targets(ev, "Eval: New " + key + "?", 1) == [after]
        assert targets(ev, "Eval: Header " + key) == ["Eval: Header " + key + " Write"]
        assert targets(ev, "Eval: Header " + key + " Write") == [after]
        write = nodes["Eval: Header " + key + " Write"]["parameters"]
        assert write["operation"] == "append"
        assert write["sheetName"]["value"] == sheet
        assert write["columns"]["mappingMode"] == "autoMapInputData"  # 空のシートなら、項目名を 1 行目に書く
        assert nodes["Eval: Header " + key].get("executeOnce") is True
        for c in columns:
            assert gw.js_str(c) in nodes["Eval: Header " + key]["parameters"]["jsCode"], (key, c)
    # 見出しを書くノードに着くのは、New …? の true の出力だけ
    for key in keys:
        sources = [src for src, c in ev["connections"].items() for out in c["main"] for t in out
                   if t["node"] == "Eval: Header " + key]
        assert sources == ["Eval: New " + key + "?"], key
    # Actions の入力があれば評価の行を足し、無ければそのまま読む
    assert targets(ev, "Eval: New Run?") == ["Eval: Import?"]
    assert targets(ev, "Eval: New Run?", 1) == ["Eval: Read Runs"]
    # 本番の実行から取り込んだ入力があれば、評価データに書いてから行を足す（2026-11-07）
    assert targets(ev, "Eval: Import?") == ["Eval: Import Rows"]
    assert targets(ev, "Eval: Import?", 1) == ["Eval: New Run Row"]
    assert targets(ev, "Eval: Import Rows") == ["Eval: Save Import"]
    assert targets(ev, "Eval: Save Import") == ["Eval: New Run Row"]
    save = nodes["Eval: Save Import"]["parameters"]
    assert save["operation"] == "append" and save["sheetName"]["value"] == pe.SHEET_DATA
    assert set(save["columns"]["value"]) == set(pe.DATA_COLUMNS)
    assert targets(ev, "Eval: New Run Row") == ["Eval: Add Run"]
    assert targets(ev, "Eval: Add Run") == ["Eval: Read Runs"]
    assert set(nodes["Eval: Add Run"]["parameters"]["columns"]["value"]) == set(pe.RUN_COLUMNS)
    assert nodes["Eval: Add Run"]["parameters"]["sheetName"]["value"] == pe.SHEET_RUNS


def test_a_stopped_added_row_is_marked_failed(ev):
    """Actions から足した行で止めたときは、その行を「エラー」にしてから止める（待機中のまま残さない。B2）。"""
    assert targets(ev, "Eval: Run OK?", 1) == ["Eval: Stop Has Row?"]
    assert targets(ev, "Eval: Stop Has Row?") == ["Eval: Mark Failed"]
    assert targets(ev, "Eval: Stop Has Row?", 1) == ["Eval: Stop Rows"]
    assert targets(ev, "Eval: Mark Failed Check") == ["Eval: Stop Rows"]


def test_sheet_columns(ev):
    nodes = by_name(ev)
    assert set(nodes["Eval: Save Data"]["parameters"]["columns"]["value"]) == set(pe.DATA_COLUMNS)
    assert set(nodes["Eval: Save Result"]["parameters"]["columns"]["value"]) == set(pe.RESULT_COLUMNS)
    for name in ("Eval: Log Call", "Eval: Log Start", "Eval: Log End", "Eval: Log Stop"):
        assert set(nodes[name]["parameters"]["columns"]["value"]) == set(pe.LOG_COLUMNS), name


def test_inputs_are_read_by_result_id(ev):
    nodes = by_name(ev)
    for name, sheet in (("Eval: Read Input", pe.SHEET_DATA), ("Eval: Read Baseline", pe.SHEET_RESULTS)):
        p = nodes[name]["parameters"]
        assert p["sheetName"]["value"] == sheet
        assert p["filtersUI"]["values"][0]["lookupColumn"] == "結果ID"
        assert nodes[name]["executeOnce"] is True
        assert nodes[name]["alwaysOutputData"] is True


# ── つながり ──────────────────────────────────────────────────────────
def test_one_step_per_run_through_switches(ev):
    steps = pe._steps()
    assert targets(ev, "Manual Trigger") == ["Eval: Sheet Setup"]
    assert targets(ev, "Run Webhook") == ["Eval: Sheet Setup"]
    for switch, key in (("Eval: Route Step", "build"), ("Eval: Route Call", "http"), ("Eval: Route Parse", "parse")):
        outs = [[c["node"] for c in o] for o in ev["connections"][switch]["main"]]
        # どの工程にも当たらなければ、最後の出力（Eval: Unknown Step）で止める。
        # P6 は、参考画像を読むノード（Check P6 Mode から）に入る
        first = [steps[s].get("entry", steps[s][key]) if key == "build" else steps[s][key] for s in pe.STEP_ORDER]
        assert outs == [[n] for n in first] + [["Eval: Unknown Step"]], switch
        assert by_name(ev)[switch]["parameters"]["options"]["fallbackOutput"] == "extra"
        rules = by_name(ev)[switch]["parameters"]["rules"]["values"]
        assert [r["conditions"]["conditions"][0]["rightValue"] for r in rules] == list(pe.STEP_ORDER)
    for step in pe.STEP_ORDER:
        spec = steps[step]
        assert targets(ev, spec["build"]) == ["Eval: Stamp"]
        assert targets(ev, spec["http"]) == ["Eval: Call Log Rows"]
        assert targets(ev, spec["parse"], 1) == ["Eval: Parse Failed"]
        assert by_name(ev)[spec["parse"]]["onError"] == "continueErrorOutput"


def test_every_call_is_logged_before_parsing(ev):
    assert targets(ev, "Eval: Call Log Rows") == ["Eval: Log Call"]
    assert targets(ev, "Eval: Log Call") == ["Eval: Response"]
    assert targets(ev, "Eval: Response") == ["Eval: Route Parse"]


def test_rounds_loop_and_finish(ev):
    assert targets(ev, "Eval: Input OK?", 0) == ["Eval: Repeat"]
    assert targets(ev, "Eval: Repeat", 0) == ["Eval: Finish"]
    assert targets(ev, "Eval: Repeat", 1) == ["Eval: P1?"]
    assert targets(ev, "Eval: P1?", 0) == ["Map Variables"]
    assert targets(ev, "Eval: P1?", 1) == ["Eval: Apply Model"]
    chain = ["Eval: Record", "Eval: Data Rows", "Eval: Save Data", "Eval: Result Row", "Eval: Save Result", "Eval: Next"]
    for a, b in zip(chain, chain[1:]):
        assert targets(ev, a) == [b]
    assert targets(ev, "Eval: Next") == ["Eval: Repeat"]
    assert targets(ev, "Eval: Finish") == ["Eval: Mark Done"]


def test_stops_are_logged(ev):
    assert targets(ev, "Eval: Run OK?", 1) == ["Eval: Stop Has Row?"]  # 足した行なら「エラー」にしてから
    assert targets(ev, "Eval: Input OK?", 1) == ["Eval: Mark Failed"]
    assert targets(ev, "Eval: Mark Failed") == ["Eval: Mark Failed Check"]
    assert targets(ev, "Eval: Mark Failed Check") == ["Eval: Stop Rows"]
    assert targets(ev, "Eval: Stop Rows") == ["Eval: Log Stop"]
    assert targets(ev, "Eval: Log Stop") == ["Eval: Stop"]
    assert "'SHAMAN_PROMPT_EVAL_STOPPED: ' + reason" in by_name(ev)["Eval: Stop"]["parameters"]["jsCode"]


def test_all_connections_point_at_existing_nodes(ev):
    names = {n["name"] for n in ev["nodes"]}
    for src, v in ev["connections"].items():
        assert src in names, src
        for out in v["main"]:
            for c in out:
                assert c["node"] in names, (src, c["node"])


# ── プロンプトの版 ─────────────────────────────────────────────────────
def test_prompt_hash_changes_with_the_prompt(monkeypatch):
    before = pe._step_table(pe._step_nodes())["P3"]
    monkeypatch.setattr(gw, "P3_RAW", gw.P3_RAW + "\n追加の指示")
    after = pe._step_table(pe._step_nodes())["P3"]
    assert before["promptHash"] != after["promptHash"]
    assert before["codeHash"] != after["codeHash"]


def test_prompt_hash_is_embedded(ev):
    table = pe._step_table(pe._step_nodes())
    code = by_name(ev)["Eval: Record"]["parameters"]["jsCode"]
    for step in pe.STEP_ORDER:
        assert table[step]["promptHash"] in code, step


def test_committed_json_matches_generator(ev):
    committed = json.loads((Path(gw.OUT) / pe.FILENAME).read_text(encoding="utf-8"))
    assert ev == committed


def test_unreplaced_placeholder_is_refused():
    with pytest.raises(ValueError, match="__X__"):
        pe._js("var a = __X__;")


def test_p1_input_uses_the_columns_map_variables_reads(ev):
    """P1 の入力は、本番の Map Variables が読むキューの列名で渡す（列名が変わったら気づく）。"""
    code = by_name(ev)["Map Variables"]["parameters"]["jsCode"]
    for col in (pe.QUEUE_KEYWORD_COL, pe.QUEUE_OPTION1_COL):
        assert "item[" + gw.js_str(col).replace('"', "'") + "]" in code, col


def test_round_failures_become_error_results_and_the_loop_continues(ev):
    """回の途中のノードが失敗しても、行を「処理中」のまま止めない。その回をエラーの結果にして、次の回へ進む。"""
    nodes = by_name(ev)
    names = list(pe.ROUND_NODES) + [pe._steps()[s]["build"] for s in pe.STEP_ORDER]
    for name in names:
        assert nodes[name]["onError"] == "continueErrorOutput", name
        assert targets(ev, name, 1) == ["Eval: Parse Failed"], name
    assert targets(ev, "Eval: Parse Failed") == ["Eval: Record"]


def test_sheet_nodes_retry(ev):
    for n in ev["nodes"]:
        if n["type"] == "n8n-nodes-base.googleSheets":
            assert (n["retryOnFail"], n["maxTries"], n["waitBetweenTries"]) == (True, 3, 5000), n["name"]
            assert "onError" not in n, n["name"]  # onError があると retryOnFail が効かない


def _n8n_connection_index(w, node_name, parent_name):
    """n8n 2.35.7 の Workflow.getNodeConnectionIndexes と同じ探し方（上流へ幅優先、最初に見つかったつながり）。"""
    by_dest = {}
    for src, c in w["connections"].items():
        for out_index, outs in enumerate(c.get("main", [])):
            for t in outs or []:
                by_dest.setdefault(t["node"], {}).setdefault(t["index"], []).append({"node": src, "index": out_index})
    queue, seen = [node_name], set()
    while queue:
        cur = queue.pop(0)
        if cur in seen:
            continue
        seen.add(cur)
        for _, conns in sorted(by_dest.get(cur, {}).items()):
            for conn in conns:
                if conn["node"] == parent_name:
                    return conn["index"]
                if conn["node"] not in seen:
                    queue.append(conn["node"])
    return None


def test_references_to_two_output_nodes_read_the_data_output(ev):
    """出力が 2 つあるノードを $('…').first() で読むときは、データのある出力を読む（2026-11-07、実行 6）。
    番号を省くと n8n は上流へたどって最初に見つかったつながりの出力を使い、Eval: Record は Eval: Parse Failed
    経由で Eval: Response のエラー出力（空）を選んで落ちた。"""
    nodes = by_name(ev)
    two = {src for src, c in ev["connections"].items() if len(c["main"]) > 1}
    # 読むべき出力：Loop は 1（loop）、ほかは 0（成功・true）。
    want = {name: (1 if nodes[name]["type"] == "n8n-nodes-base.splitInBatches" else 0) for name in two}
    checked = 0
    for n in ev["nodes"]:
        if n["type"] != "n8n-nodes-base.code":
            continue
        for m in re.finditer(r"\$\('([^']+)'\)\.(first|last|all)\((\d*)", n["parameters"]["jsCode"]):
            ref, explicit = m.group(1), m.group(3)
            if ref not in two:
                continue
            got = int(explicit) if explicit else _n8n_connection_index(ev, n["name"], ref)
            assert got == want[ref], (n["name"], m.group(0), got)
            checked += 1
    assert checked >= 10


def test_p2_heading_gate_retry_is_kept(ev, prod):
    """P2 の見出しの数が条件を満たさないとき、1 回だけ出し直す（R27-1：H2 2 + H3 9 = 11 個）。評価でも本番と同じにする。"""
    assert by_name(ev)["P2: Retry?"]["parameters"] == by_name(prod)["P2: Retry?"]["parameters"]
    assert by_name(ev)["Parse Outline"]["parameters"] == by_name(prod)["Parse Outline"]["parameters"]
    assert "if (issues.length && attempt < 2) {" in by_name(ev)["Parse Outline"]["parameters"]["jsCode"]
    assert "P2 の呼び出しは、1 回の評価（回数 1）で最大 2 回" in (Path(__file__).parent / "DOCS" / "PROMPT_EVAL_DESIGN.md").read_text(encoding="utf-8")
    assert targets(ev, "Parse Outline") == ["P2: Retry?"]
    assert targets(ev, "P2: Retry?", 0) == ["Build P2 Request"]
    assert targets(ev, "P2: Retry?", 1) == ["Eval: Record"]
    assert targets(prod, "Parse Outline") == ["P2: Retry?"]
    assert targets(prod, "P2: Retry?", 0) == ["Build P2 Request"]
    assert targets(prod, "P2: Retry?", 1) == ["Build P3 Request"]


def test_nodes_that_run_many_times_are_read_at_the_latest_run(ev, prod):
    """出し直しと評価の回で、同じ実行の中で何度も動くノードは、最後の実行（-1）を明示して読む。runIndex を省いたときの
    扱いが n8n のドキュメント（最初の実行）とソース（最後の実行）で食い違うため（PR #89 レビュー B1・B3）。"""
    for w, name, ref in ((prod, "Parse Outline", "Build P2 Request"), (prod, "Parse Strengthened", "Build P4 Request"),
                         (ev, "Parse Outline", "Build P2 Request"), (ev, "Parse Strengthened", "Build P4 Request")):
        code = by_name(w)[name]["parameters"]["jsCode"]
        assert "$('" + ref + "').first(0, -1).json" in code, name
        assert "$('" + ref + "').first()" not in code, name
    for name in ("Eval: Call Log Rows", "Eval: Response", "Eval: Record"):
        code = by_name(ev)[name]["parameters"]["jsCode"]
        assert re.search(r"\$\((?:spec\.http|'Eval: (?:Stamp|Repeat)'|name)\)\.first\((?:\d)?\)", code) is None, name
    log = by_name(ev)["Eval: Call Log Rows"]["parameters"]["jsCode"]
    assert "run.step === 'P2' ? (stamp.p2Attempt || 1)" in log


def test_p1_retries_once_when_there_are_no_sources(ev, prod):
    """本番の実行 43（2026-11-09）：P1 の情報源が 0 件。1 回だけ呼び直す。評価でも本番と同じにする。"""
    assert by_name(ev)["P1: Retry?"]["parameters"] == by_name(prod)["P1: Retry?"]["parameters"]
    assert by_name(ev)["Parse Research"]["parameters"] == by_name(prod)["Parse Research"]["parameters"]
    code = by_name(prod)["Parse Research"]["parameters"]["jsCode"]
    assert "$('Build P1 Request').first(0, -1).json" in code
    assert "if (!p1Sources.sources.length && attempt < 2)" in code
    assert targets(prod, "Parse Research") == ["P1: Retry?"]
    assert targets(prod, "P1: Retry?", 0) == ["Build P1 Request"]
    assert targets(prod, "P1: Retry?", 1) == ["Build P2 Request"]
    assert targets(ev, "Parse Research") == ["P1: Retry?"]
    assert targets(ev, "P1: Retry?", 0) == ["Build P1 Request"]
    assert targets(ev, "P1: Retry?", 1) == ["Eval: Record"]
    assert "run.step === 'P1' ? (stamp.p1Attempt || 1)" in by_name(ev)["Eval: Call Log Rows"]["parameters"]["jsCode"]


def test_wp_auth_probe_is_read_only_and_uses_shared_credential(ev):
    nodes = by_name(ev)
    probe = nodes["WP: Probe GET"]
    assert probe["parameters"]["method"] == "GET"
    assert "/wp-json/wp/v2/users/me?context=edit" in probe["parameters"]["url"]
    assert probe["credentials"] == {"httpBasicAuth": gw.WP_MULTISITE_CREDENTIAL}
    assert probe["parameters"]["genericAuthType"] == "httpBasicAuth"
    assert nodes["WP: Build Auth Probe"]["type"] == "n8n-nodes-base.code"
    assert "WP_TEST_INVALID_SITE_ID" in nodes["WP: Build Auth Probe"]["parameters"]["jsCode"]
    assert "WP_AUTH_TEST_FAILED" in nodes["WP: Parse Auth Probe"]["parameters"]["jsCode"]


def test_wp_auth_probe_routes_through_existing_test_logging(ev):
    assert targets(ev, "WP: Build Auth Probe", 0) == ["Eval: Stamp"]
    assert targets(ev, "WP: Probe GET") == ["Eval: Call Log Rows"]
    assert targets(ev, "WP: Parse Auth Probe", 0) == ["Eval: Record"]
    assert targets(ev, "WP: Parse Auth Probe", 1) == ["Eval: Parse Failed"]
    pick = by_name(ev)["Eval: Pick Run"]["parameters"]["jsCode"]
    assert "重複しない2〜5サイト" in pick
    assert "run.authSites = domains" in pick
    prepare = by_name(ev)["Eval: Prepare Input"]["parameters"]["jsCode"]
    assert "run.authSites[i - 1]" in prepare
    assert "one.siteId" in prepare


def test_wp_auth_probe_and_draft_write_are_isolated(ev):
    wp_nodes = [n for n in ev["nodes"] if "httpBasicAuth" in n.get("credentials", {})]
    assert len(wp_nodes) == 2
    assert {n["name"]: n["parameters"]["method"] for n in wp_nodes} == {
        "WP: Probe GET": "GET",
        "WP: Test Draft POST": "POST",
    }
    assert "TEST_DRAFT_ALLOWED" in by_name(ev)["WP: Build Test Draft"]["parameters"]["jsCode"]
    assert "WP_TEST_DRAFT_DENIED" in by_name(ev)["WP: Build Test Draft"]["parameters"]["jsCode"]


def test_draft_test_post_is_draft_only_and_explicitly_opted_in(ev):
    nodes = by_name(ev)
    build = nodes["WP: Build Test Draft"]["parameters"]["jsCode"]
    assert "TEST_DRAFT_ALLOWED" in build
    assert "WP_TEST_DRAFT_DENIED" in build
    assert "production.indexOf(domain) !== -1" in build
    assert "status: 'draft'" in build
    assert "comment_status: 'closed'" in build
    assert targets(ev, "WP: Build Test Draft") == ["Eval: Stamp"]
    assert targets(ev, "WP: Test Draft POST") == ["Eval: Call Log Rows"]
    assert targets(ev, "WP: Parse Test Draft", 0) == ["Eval: Record"]
    assert targets(ev, "WP: Parse Test Draft", 1) == ["Eval: Parse Failed"]
    post = nodes["WP: Test Draft POST"]
    assert post["parameters"]["method"] == "POST"
    assert post["parameters"]["jsonBody"] == "={{ $json.wpPostBody }}"
    assert post["credentials"] == {"httpBasicAuth": gw.WP_MULTISITE_CREDENTIAL}


def test_draft_test_destinations_are_deny_by_default(tmp_path):
    import json
    config = tmp_path / "test_sites.json"
    config.write_text("[]", encoding="utf-8")
    assert pe.approved_wp_test_sites(config) == []
    config.write_text(json.dumps(["test.example.com"]), encoding="utf-8")
    assert pe.approved_wp_test_sites(config) == ["test.example.com"]
    for site in ["test-example.com", "staging-example.com", "kuro-aji-lab.com"]:
        config.write_text(json.dumps([site]), encoding="utf-8")
        with pytest.raises(ValueError, match="unsafe"):
            pe.approved_wp_test_sites(config)


def test_eval_wpd_hosts_must_have_separate_explicit_allowlist(ev):
    js = by_name(ev)["WP: Build Test Draft"]["parameters"]["jsCode"]
    pick = by_name(ev)["Eval: Pick Run"]["parameters"]["jsCode"]
    assert "var approved = [];" in js
    assert "if (approved.indexOf(domain) === -1)" in js
    assert "APPROVED_TEST_DOMAINS" not in pick
    assert "[]" in pick
