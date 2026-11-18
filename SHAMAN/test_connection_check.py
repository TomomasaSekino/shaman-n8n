"""接続確認 Workflow（connection_check.py）の生成を確かめる。外部接続はしない。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import connection_check as cc  # noqa: E402
import generate_workflows as gw  # noqa: E402

SITES = {
    "example.com": {"WP_URL": "https://example.com"},
    "blog-b": {"WP_URL": "https://blog-b"},
}


def build(tmp_path, sites=None):
    path = tmp_path / "sites.json"
    path.write_text(json.dumps(sites if sites is not None else {}), encoding="utf-8")
    return cc.buildConnectionCheckWorkflow(str(path))


def nodes_by_name(w):
    return {n["name"]: n for n in w["nodes"]}


def test_committed_json_matches_generator():
    committed = json.loads((Path(gw.OUT) / cc.FILENAME).read_text(encoding="utf-8"))
    assert cc.buildConnectionCheckWorkflow() == committed


def test_identity_and_triggers(tmp_path):
    """入口は、画面の Manual Trigger と、Actions から実行する合言葉付きの Webhook（DOCS/RUN_FROM_ACTIONS.md）。"""
    w = build(tmp_path)
    assert (w["name"], w["id"]) == (cc.NAME, cc.WORKFLOW_ID)
    triggers = [n for n in w["nodes"] if "trigger" in n["type"].lower() or n["type"].endswith(".webhook")]
    assert [n["type"] for n in triggers] == ["n8n-nodes-base.manualTrigger", "n8n-nodes-base.webhook"]
    hook = triggers[1]
    assert hook == gw.runWebhookNode(cc.RUN_WEBHOOK_PATH, hook["position"]) | {"id": hook["id"]}
    assert w["connections"]["Run Webhook"] == w["connections"]["Manual Trigger"]
    assert w["settings"]["saveDataSuccessExecution"] == "all"


def test_nodes_form_one_chain_ending_in_result(tmp_path):
    w = build(tmp_path, SITES)
    c = w["connections"]
    assert c["Conn: WP Sites"]["main"][0][0]["node"] == "Conn: WP Loop"
    assert [o[0]["node"] for o in c["Conn: WP Loop"]["main"]] == [
        "Conn: Drive Folder 1", "Conn: WordPress"]
    assert c["Conn: WordPress"]["main"][0][0]["node"] == "Conn: WP Loop"
    assert c["Conn: Result"]["main"][0][0]["node"] == "Run: Check"
    # ループ以外の連結ノードも、全て到達可能であること。
    visited, queue = set(), ["Manual Trigger"]
    while queue:
        name = queue.pop(0)
        if name in visited:
            continue
        visited.add(name)
        for output in c.get(name, {}).get("main", []):
            queue.extend(x["node"] for x in output)
    actual = {n["name"] for n in w["nodes"] if n["type"] != "n8n-nodes-base.stickyNote"}
    assert visited | {"Run Webhook"} == actual


def test_sheets_write_runs_only_when_reserved_row_is_found(tmp_path):
    """予約行を特定できない（0 件・複数件・読み込みの失敗）ときは書き込まず、判定へ進んで残りの診断を続ける。"""
    w = build(tmp_path)
    n = nodes_by_name(w)
    c = w["connections"]
    assert c["Conn: Sheets Pick Row"]["main"] == [[{"node": cc.ROW_FOUND, "type": "main", "index": 0}]]
    gate = n[cc.ROW_FOUND]
    assert gate["type"] == "n8n-nodes-base.if"
    cond = gate["parameters"]["conditions"]["conditions"]
    assert [(x["leftValue"], x["operator"]["type"], x["operator"]["operation"]) for x in cond]         == [("={{ $json.ok }}", "boolean", "true")]
    true_out, false_out = c[cc.ROW_FOUND]["main"]
    assert [t["node"] for t in true_out] == ["Conn: Sheets Write"]
    assert [t["node"] for t in false_out] == ["Conn: Sheets Check"]
    # Write に入るのは、IF の true だけ。
    into_write = [src for src, v in c.items() for outs in v["main"] for t in outs if t["node"] == "Conn: Sheets Write"]
    assert into_write == [cc.ROW_FOUND]
    # Check のあとは、ほかのサービスの診断へ続く。
    assert c["Conn: Sheets Check"]["main"][0][0]["node"] == "Conn: Gemini Models"
    # シートが空でも、判定まで進める。
    assert n["Conn: Sheets Read"]["alwaysOutputData"] is True
    assert n["Conn: Sheets Reread"]["alwaysOutputData"] is True


def test_no_secrets_env_or_require_in_workflow(tmp_path):
    # Conn: Summarize は、エラー文から key= や x-api-key の値を伏せる処理なので、その名前を含む。
    w = build(tmp_path, SITES)
    text = json.dumps([n for n in w["nodes"] if n["name"] != "Conn: Summarize"], ensure_ascii=False)
    assert "$env" not in json.dumps(w) and "require(" not in json.dumps(w)
    for banned in ("$env", "require(", "x-goog-api-key", "x-api-key", "Authorization", "Bearer"):
        assert banned not in text, banned


def test_external_nodes_use_the_shared_credentials(tmp_path):
    n = nodes_by_name(build(tmp_path))
    for name, service in (("Conn: Gemini Models", "gemini"), ("Conn: Anthropic Model", "anthropic"),
                          ("Conn: OpenAI Model", "openai")):
        cred_type, cred = gw.API_CREDENTIALS[service]
        assert n[name]["parameters"]["authentication"] == "predefinedCredentialType"
        assert n[name]["parameters"]["nodeCredentialType"] == cred_type
        assert n[name]["credentials"] == {cred_type: cred}
    assert n["Conn: Discord Notify"]["credentials"] == {"discordWebhookApi": gw.DISCORD_CREDENTIAL}
    drive = [x for x in build(tmp_path, SITES)["nodes"] if x["type"] == "n8n-nodes-base.googleDrive"]
    assert len(drive) == 3 * len(SITES)
    for node in drive:
        assert node["credentials"] == {"googleApi": gw.SHEETS_CREDENTIAL}
    headers = n["Conn: Anthropic Model"]["parameters"]["headerParameters"]["parameters"]
    assert {"name": "anthropic-version", "value": gw.ANTHROPIC_VERSION} in headers


def test_checks_the_models_production_uses(tmp_path):
    w = build(tmp_path)
    n = nodes_by_name(w)
    assert n["Conn: Anthropic Model"]["parameters"]["url"].endswith("/v1/models/" + gw.MODEL_CLAUDE)
    assert n["Conn: OpenAI Model"]["parameters"]["url"].endswith("/v1/models/" + gw.MODEL_IMAGE_OPENAI)
    summary = n["Conn: Summarize"]["parameters"]["jsCode"]
    for model in (gw.MODEL_PRO, gw.MODEL_FLASH_LITE, gw.MODEL_IMAGE):
        assert json.dumps(model) in summary


def test_only_reads_except_test_sheet_run_id_and_discord(tmp_path):
    w = build(tmp_path, SITES)
    for node in w["nodes"]:
        if node["type"] == "n8n-nodes-base.httpRequest":
            assert node["parameters"]["method"] == "GET", node["name"]
            assert "sendBody" not in node["parameters"], node["name"]
        if node["type"] == "n8n-nodes-base.googleSheets":
            assert node["parameters"]["documentId"]["value"] == gw.QUEUE_TEST_SPREADSHEET
            assert node["credentials"] == {"googleApi": gw.SHEETS_CREDENTIAL}
            if node["parameters"]["operation"] != "read":
                assert node["name"] == "Conn: Sheets Write"
                assert set(node["parameters"]["columns"]["value"]) == {"row_number", cc.RUN_COL}
        if node["type"] == "n8n-nodes-base.googleDrive":
            assert node["parameters"]["operation"] in ("search", "download")
    assert "QUEUE_SPREADSHEET_ID__" not in json.dumps(w).replace("QUEUE_TEST_SPREADSHEET_ID__", "")


def test_shared_wordpress_node_reads_users_one_site_at_a_time(tmp_path):
    w = build(tmp_path, SITES)
    n = nodes_by_name(w)
    wp = [node for node in w["nodes"] if node["name"] == "Conn: WordPress"]
    assert len(wp) == 1
    assert wp[0]["credentials"] == {"httpBasicAuth": gw.WP_MULTISITE_CREDENTIAL}
    assert wp[0]["parameters"]["genericAuthType"] == "httpBasicAuth"
    assert "$json.wpUrl" in wp[0]["parameters"]["url"]
    assert n["Conn: WP Loop"]["parameters"]["batchSize"] == 1
    js = n["Conn: WP Sites"]["parameters"]["jsCode"]
    assert '"siteId": "blog-b"' in js and '"siteId": "example.com"' in js

def test_no_sites_means_wordpress_is_reported_unchecked(tmp_path):
    n = nodes_by_name(build(tmp_path, {}))
    assert "var WP_SITES = [];" in n["Conn: Summarize"]["parameters"]["jsCode"]

def test_site_addition_keeps_one_wordpress_node(tmp_path):
    for sites in ({}, SITES, {**SITES, "third.example": {"WP_URL": "https://third.example"}}):
        w = build(tmp_path, sites)
        assert sum(n["name"] == "Conn: WordPress" for n in w["nodes"]) == 1

DRIVE_SITES = {"b.example": {"WP_URL": "https://b.example"},
               "a.example": {"WP_URL": "https://a.example", "IMG_FOLDER": "a-images"}}


def test_drive_checks_each_sites_folder_like_production(tmp_path):
    """本番の P6 と同じく、SHAMAN/IMG の中の、名前が IMG_FOLDER（無ければ SITE_ID）のフォルダーをサイトごとに確かめる。

    前は IMG の中の最初のフォルダーを使っていたため、サイトのフォルダーが無くても OK になり得た（2026-11-06）。
    """
    n = nodes_by_name(build(tmp_path, DRIVE_SITES))
    for i, folder in ((1, "a-images"), (2, "b.example")):  # サイトは SITE_ID の順
        assert n["Conn: Drive Folder %d" % i]["parameters"]["queryString"] == (
            "'" + cc.IMG_FOLDER + "' in parents and name = '" + folder + "' and mimeType = '"
            + cc.DRIVE_FOLDER_MIME + "' and trashed = false")
        assert "{{ $json.id }}" in n["Conn: Drive Image %d" % i]["parameters"]["queryString"]
        assert n["Conn: Drive Download %d" % i]["parameters"]["fileId"]["value"] == "={{ $json.id }}"
    # 本番の P6 も、同じ親フォルダーの中を名前で探す。
    p6 = nodes_by_name(gw.buildWorkflow())["P6: Find Image Folder"]["parameters"]["queryString"]
    assert p6.startswith("='" + cc.IMG_FOLDER + "' in parents and name = '{{ $json.imgFolder }}'")
    assert "Conn: Drive Find Folder" not in n


def test_discord_nodes_have_a_fixed_webhook_id(tmp_path):
    """n8n は Discord のノードに webhookId を足す。生成で入れておかないと、配備の読み戻しが一致しない（2026-11-06）。"""
    w = build(tmp_path)
    again = build(tmp_path)
    for wf in (w, gw.buildWorkflow()):
        discord = [n for n in wf["nodes"] if n["type"] == "n8n-nodes-base.discord"]
        assert discord
        ids = [n["webhookId"] for n in discord]
        assert all(ids) and len(set(ids)) == len(ids)
    assert [n.get("webhookId") for n in w["nodes"]] == [n.get("webhookId") for n in again["nodes"]]
