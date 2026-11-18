"""本番 Workflow が、接続確認 Workflow と同じ Credential・組み立て関数で外部につなぐことを確かめる。

n8n 2.x の Code ノードは require を使えず、$env も読めない（N8N_BLOCK_ENV_ACCESS_IN_NODE=true）。
外部への接続は HTTP Request・Google Drive・Discord ノードで行い、認証は Credential が付ける。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import generate_workflows as gw  # noqa: E402
import prompts  # noqa: E402

SITES = {
    "example.com": {"WP_URL": "https://example.com/", "IMG_FOLDER": "example"},
    "blog-b": {"WP_URL": "https://b.example.net"},
}


@pytest.fixture(scope="module")
def prod():
    return gw.buildWorkflow()


@pytest.fixture(scope="module")
def test_wf():
    """プロンプト評価 Workflow（前の Test Workflow の ID を引き継ぐ）。"""
    import prompt_eval
    return prompt_eval.buildPromptEvalWorkflow()


def by_name(w):
    return {n["name"]: n for n in w["nodes"]}


def targets(w, name, output=0):
    return [c["node"] for c in w["connections"][name]["main"][output]]


def js(w, name):
    return by_name(w)[name]["parameters"]["jsCode"]


# ── 秘密・$env・require を使わない ─────────────────────────────────────────
@pytest.mark.parametrize("which", ["prod", "test_wf"])
def test_no_env_require_or_hand_made_auth(which, request):
    w = request.getfixturevalue(which)
    text = json.dumps(w, ensure_ascii=False)
    for banned in ("$env", "require(", "helpers.httpRequest", "Authorization", "x-api-key", "x-goog-api-key",
                   "wpAppPassword", "WP_APP_PASSWORD", "DISCORD_WEBHOOK_URL", "OPENAI_API_KEY"):
        assert banned not in text, banned


def test_every_external_node_uses_a_credential(prod):
    for n in prod["nodes"]:
        if n["type"] == "n8n-nodes-base.httpRequest":
            p = n["parameters"]
            assert p["authentication"] in ("predefinedCredentialType", "genericCredentialType"), n["name"]
            cred_type = p["nodeCredentialType"] if p["authentication"] == "predefinedCredentialType" else p["genericAuthType"]
            assert set(n["credentials"]) == {cred_type}, n["name"]
            assert n["credentials"][cred_type]["id"].startswith("__ENV:SHAMAN_"), n["name"]
        if n["type"] in ("n8n-nodes-base.googleDrive", "n8n-nodes-base.discord", "n8n-nodes-base.googleSheets"):
            assert n["credentials"], n["name"]


# ── P1〜P5：Gemini・Claude ───────────────────────────────────────────────
def test_gemini_and_claude_nodes_use_the_shared_credentials(prod):
    n = by_name(prod)
    for name in ("Gemini: Research", "Gemini: Generate Outline", "Gemini: Generate Content",
                 "Gemini: Generate Meta", "Gemini: Analyze Sections"):
        assert n[name]["credentials"] == {"googlePalmApi": gw.API_CREDENTIALS["gemini"][1]}, name
        assert n[name]["parameters"]["jsonBody"] == "={{ $json.requestBody }}"
        assert "$json.geminiModel" in n[name]["parameters"]["url"]
    claude = n["Claude: Strengthen Content"]
    assert claude["credentials"] == {"anthropicApi": gw.API_CREDENTIALS["anthropic"][1]}
    assert claude["parameters"]["url"] == "https://api.anthropic.com/v1/messages"
    headers = claude["parameters"]["headerParameters"]["parameters"]
    assert {"name": "anthropic-version", "value": gw.ANTHROPIC_VERSION} in headers
    assert claude["parameters"]["options"]["timeout"] == gw.P4_TIMEOUT_MS  # P4 は思考込みで最大 1800 秒（900 秒は実行 8 で時間切れ）


def test_map_variables_embeds_models_and_image_settings(prod):
    code = js(prod, "Map Variables")
    for key, value in (("MODEL_PRO", gw.MODEL_PRO), ("MODEL_FLASH_LITE", gw.MODEL_FLASH_LITE),
                       ("MODEL_IMAGE", gw.MODEL_IMAGE), ("MODEL_IMAGE_OPENAI", gw.MODEL_IMAGE_OPENAI),
                       ("MODEL_CLAUDE", gw.MODEL_CLAUDE), ("IMAGE_PROVIDER", gw.IMAGE_PROVIDER),
                       ("IMAGE_QUALITY", gw.IMAGE_QUALITY)):
        assert "  " + key + ": " + gw.js_str(value) + "," in code, key
    assert "imgFolder: String(sc.IMG_FOLDER || item.siteId || '')" in code
    assert "wpUser" not in code and "imgPath" not in code


def test_filter_rows_throws_and_leaves_discord_to_error_trigger(prod):
    code = js(prod, "Filter Rows")
    assert "throw new Error(msg)" in code
    assert targets(prod, "Error Trigger") == ["Discord: Notify Error"]


# ── Discord ─────────────────────────────────────────────────────────────
def test_discord_nodes_use_the_webhook_credential(prod):
    n = by_name(prod)
    for name in ("Discord: Notify Success", "Discord: Notify Error"):
        assert n[name]["type"] == "n8n-nodes-base.discord"
        assert n[name]["credentials"] == {"discordWebhookApi": gw.DISCORD_CREDENTIAL}
        assert n[name]["parameters"]["content"].startswith("={{")
        assert "JSON.stringify" not in n[name]["parameters"]["content"]


def test_discord_success_reports_every_error(prod):
    content = by_name(prod)["Discord: Notify Success"]["parameters"]["content"]
    for field in ("$json.p6Status", "$json.uploadError", "$json.wpError", "$json.p4QualityIssues"):
        assert field in content
    assert "!!s" in content and "⚠️" in content


def test_discord_error_uses_last_node_executed(prod):
    content = by_name(prod)["Discord: Notify Error"]["parameters"]["content"]
    assert "lastNodeExecuted" in content and "error.node.name" not in content
    assert ".substring(0, 1500)" in content  # Discord の 2000 文字の上限に収める


# ── P6：参考画像（Google Drive） ─────────────────────────────────────────
def test_reference_images_come_from_the_site_folder_on_drive(prod):
    n = by_name(prod)
    assert targets(prod, "Check P6 Mode") == ["P6: Find IMG Root"]
    assert targets(prod, "P6: Find IMG Root") == ["P6: Verify IMG Root"]
    assert targets(prod, "P6: Verify IMG Root") == ["P6: Find Image Folder"]
    assert targets(prod, "P6: Find Image Folder") == ["P6: Check Image Folder"]
    assert targets(prod, "P6: Check Image Folder") == ["P6: Has Image Folder?"]
    assert targets(prod, "P6: Has Image Folder?", 0) == ["P6: List Reference Images"]
    assert targets(prod, "P6: Has Image Folder?", 1) == ["Read Reference Images"]
    assert targets(prod, "P6: List Reference Images") == ["P6: Check Reference Images"]
    assert targets(prod, "P6: Check Reference Images") == ["P6: Has Reference Images?"]
    assert targets(prod, "P6: Has Reference Images?", 0) == ["P6: Download Reference Image"]
    assert targets(prod, "P6: Has Reference Images?", 1) == ["Read Reference Images"]
    assert targets(prod, "P6: Download Reference Image") == ["Read Reference Images"]
    assert targets(prod, "Read Reference Images") == ["Build Section Analysis Request"]
    folder_q = n["P6: Find Image Folder"]["parameters"]["queryString"]
    assert folder_q.startswith("='" + gw.IMG_FOLDER + "' in parents")
    assert "name = '{{ $json.imgFolder }}'" in folder_q
    list_node = n["P6: List Reference Images"]
    assert "'{{ $json.folderId }}' in parents" in list_node["parameters"]["queryString"]
    for mime in gw.REFERENCE_MIMES:
        assert mime in list_node["parameters"]["queryString"]
    assert list_node["parameters"]["limit"] == gw.MAX_REFERENCE_IMAGES == 16
    assert n["P6: Download Reference Image"]["parameters"]["fileId"]["value"] == "={{ $json.id }}"
    for name in ("P6: Find Image Folder", "P6: List Reference Images", "P6: Download Reference Image"):
        assert n[name]["credentials"] == {"googleApi": gw.SHEETS_CREDENTIAL}


def test_read_reference_images_uses_binary_helper(prod):
    code = js(prod, "Read Reference Images")
    assert "this.helpers.getBinaryDataBuffer(i, 'data')" in code
    assert "referenceImages" in code and "referenceImages.length === 0" in code


# ── P6：画像の生成（OpenAI、再試行のループ） ──────────────────────────────
def test_image_generation_chain_and_retry_loop(prod):
    assert targets(prod, "Parse Section Plan") == ["P6: Plan Error?"]
    assert targets(prod, "P6: Plan Error?", 0) == ["P6: Error Gate?"]
    assert targets(prod, "P6: Plan Error?", 1) == ["Build Image Requests"]
    # API使用量の記録をはさむ（Usage: Stamp P6 → P6: Generate Image → Usage: Row/Write/Pass P6）
    assert targets(prod, "Build Image Requests") == ["Usage: Stamp P6"]
    assert targets(prod, "Usage: Stamp P6") == ["P6: Generate Image"]
    assert targets(prod, "P6: Generate Image") == ["Usage: Row P6"]
    assert targets(prod, "Usage: Pass P6") == ["Collect Generated Images"]
    assert targets(prod, "Collect Generated Images") == ["P6: Retry Images?"]
    assert targets(prod, "P6: Retry Images?", 0) == ["P6: Retry Wait"]
    assert targets(prod, "P6: Retry Images?", 1) == ["P6: Images Ready"]
    assert targets(prod, "P6: Retry Wait") == ["Build Image Requests"]
    assert targets(prod, "P6: Images Ready") == ["P6: Error Gate?"]
    wait = by_name(prod)["P6: Retry Wait"]["parameters"]
    assert (wait["amount"], wait["unit"]) == (gw.IMAGE_RETRY_WAIT_SECONDS, "seconds")
    assert "var MAX_ATTEMPTS = 3;" in js(prod, "Collect Generated Images")


def test_openai_request_node(prod):
    node = by_name(prod)["P6: Generate Image"]
    p = node["parameters"]
    assert (p["method"], p["url"]) == ("POST", "={{ $json.imageApiPath }}")
    assert node["credentials"] == {"openAiApi": gw.API_CREDENTIALS["openai"][1]}
    # JSON の本文で送る。multipart のバイナリを渡すと、n8n 2.x が 1 バイトずつフォームの項目に作り直して止まる（実行 11）。
    assert (p["sendBody"], p["specifyBody"], p["jsonBody"]) == (True, "json", "={{ $json.requestBody }}")
    assert "contentType" not in p and "inputDataFieldName" not in p
    assert p["options"]["timeout"] == 180000
    assert p["options"]["response"] == {"response": {"neverError": True, "fullResponse": True}}
    assert node["onError"] == "continueRegularOutput"


def test_openai_request_body_is_built_in_code(prod):
    code = js(prod, "Build Image Requests")
    assert gw.js_str(prompts.P6_IMAGE_PROMPT_OPENAI) in code
    assert gw.js_str(prompts.P6_IMAGE_PROMPT_OPENAI_NO_REFERENCE) in code
    assert "multipart" not in code.replace("// multipart", "")
    assert "images = refImages.map(function(r)" in code
    assert "https://api.openai.com/v1/images/generations" in code
    assert "https://api.openai.com/v1/images/edits" in code
    assert "if (images.length) requestBody.images = images" in code
    assert "size: '1536x864'" in code and "output_format: 'jpeg'" in code and "output_compression: 80," in code
    assert "section.MODEL_IMAGE_OPENAI || " + gw.js_str(gw.MODEL_IMAGE_OPENAI) in code
    assert "section.IMAGE_QUALITY || " + gw.js_str(gw.IMAGE_QUALITY) in code
    assert "$('Read Reference Images').first().json.referenceImages" in code


def test_images_ready_requires_five_images(prod):
    code = js(prod, "P6: Images Ready")
    assert "successCount < 5" in code and "insufficient_images_" in code


def test_gemini_rollback_is_chosen_at_generate_time():
    nodes = {n["name"]: n for n in gw.p6ImageNodes(0, 0, provider="gemini")}
    http = nodes["P6: Generate Image"]
    assert http["credentials"] == {"googlePalmApi": gw.API_CREDENTIALS["gemini"][1]}
    assert ":generateContent" in http["parameters"]["url"]
    assert http["parameters"]["options"]["response"]["response"]["fullResponse"] is True
    code = nodes["Build Image Requests"]["parameters"]["jsCode"]
    assert gw.js_str(prompts.P6_IMAGE_PROMPT) in code and "maxOutputTokens: 65536" in code
    assert gw.js_str(prompts.P6_IMAGE_PROMPT_NO_REFERENCE) in code
    assert "var PROVIDER = \"gemini\";" in nodes["Collect Generated Images"]["parameters"]["jsCode"]
    with pytest.raises(ValueError, match="IMAGE_PROVIDER"):
        gw.p6ImageNodes(0, 0, provider="dalle")


# ── P6・P7：WordPress（サイトごとの Credential） ─────────────────────────
def wp(sites=SITES):
    nodes, conn = gw.wpSiteNodes(sites, 0, 0)
    return {n["name"]: n for n in nodes}, conn


def test_wordpress_nodes_are_shared_across_sites():
    for sites in (SITES, {**SITES, "third.example": {"WP_URL": "https://third.example"}}):
        n, conn = wp(sites)
        assert len(n) == 4
        assert len([x for x in n.values() if x["type"] == "n8n-nodes-base.httpRequest"]) == 2
        for name in ("WP: Upload Media", "WP: Create Post"):
            assert n[name]["credentials"] == {"httpBasicAuth": gw.WP_MULTISITE_CREDENTIAL}
            assert n[name]["parameters"]["genericAuthType"] == "httpBasicAuth"
            assert "$json.wpUrl" in n[name]["parameters"]["url"]
        assert conn["WP: Upload Media"]["main"][0][0]["node"] == "WP: Collect Uploads"
        assert conn["WP: Create Post"]["main"][0][0]["node"] == "Parse WP Response"


def test_media_upload_sends_the_image_as_binary():
    p = wp()[0]["WP: Upload Media"]["parameters"]
    assert (p["contentType"], p["inputDataFieldName"]) == ("binaryData", "media")
    header = p["headerParameters"]["parameters"][0]
    assert header["name"] == "Content-Disposition" and "$json.mediaFilename" in header["value"]
    assert p["options"]["response"] == {"response": {"neverError": True, "fullResponse": True}}


def test_create_post_sends_the_built_body():
    p = wp()[0]["WP: Create Post"]["parameters"]
    assert p["jsonBody"] == "={{ $json.wpPostBody }}"
    assert p["options"]["response"] == {"response": {"neverError": True}}


def test_invalid_site_id_stops_before_wordpress():
    code = js(gw.buildWorkflow(), "Filter Rows")
    assert "SITE_ID" in code and "siteConfig" in code
    assert "if (!row.siteConfig)" in code
    for name in ("WP: Validate Upload Target", "WP: Validate Post Target"):
        assert "WP_SITE_ID_INVALID" in js(gw.buildWorkflow(), name)


def test_production_wires_wordpress_through_shared_http_nodes(prod):
    assert targets(prod, "P6: Error Gate?", 1) == ["WP: Prepare Media"]
    assert targets(prod, "WP: Prepare Media") == ["WP: Validate Upload Target"]
    assert targets(prod, "WP: Validate Upload Target") == ["WP: Upload Media"]
    assert targets(prod, "WP: Collect Uploads") == ["WP: Upload Error?"]
    assert targets(prod, "Build WP Post") == ["WP: Validate Post Target"]
    assert targets(prod, "WP: Validate Post Target") == ["WP: Create Post"]
    assert "WP: Route Post" not in by_name(prod)


def test_build_wp_post_and_parse_response_carry_no_credentials(prod):
    build = js(prod, "Build WP Post")
    assert "authHeader" not in build and "Buffer.from" not in build and "wpPostBody: postBody" in build
    assert "delete config.wpPostBody" in js(prod, "Parse WP Response")


def test_prepare_media_and_collect_uploads(prod):
    prep = js(prod, "WP: Prepare Media")
    assert "replace(/[^a-zA-Z0-9._-]/g, '_')" in prep and "delete base.imageB64" in prep
    collect = js(prod, "WP: Collect Uploads")
    assert "'upload_failed [index=' + idx + ', status=' + status + ']: '" in collect
    assert "batchUploadError" in collect
    assert "$('P6: Images Ready').all()" in collect and "$('WP: Prepare Media').all()" in collect


# ── sites.json ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("site, message", [
    ({"WP_URL": "https://a.example", "IMG_FOLDER": "x' or name contains '"}, "IMG_FOLDER"),
    ({"WP_URL": "http://a.example"}, "must match SITE_ID"),
    ({"WP_URL": "https://a", "IMG_PATH": "/img"}, "unknown keys"),
])
def test_sites_json_rejects_unsafe_values(tmp_path, site, message):
    path = tmp_path / "sites.json"
    path.write_text(json.dumps({"a": site}), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        gw.load_sites(str(path))


# ── レビューの指摘への対応 ───────────────────────────────────────────────
def test_discord_success_never_stops_the_row_loop(prod):
    node = by_name(prod)["Discord: Notify Success"]
    assert node["onError"] == "continueRegularOutput"
    assert node["parameters"]["content"].endswith(".substring(0, 1900) }}")
    assert targets(prod, "Discord: Notify Success") == ["Loop Over Items"]


def test_discord_error_masks_api_keys(prod):
    content = by_name(prod)["Discord: Notify Error"]["parameters"]["content"]
    assert r'''.replace(/(key=|Bearer\s+)[^&\s"',}]+/gi, "$1***")''' in content


def test_reference_images_have_a_total_size_cap(prod):
    code = js(prod, "Read Reference Images")
    assert "if (totalBytes > " + str(gw.MAX_REFERENCE_BYTES) + ")" in code


def test_drive_nodes_use_the_sheets_service_account(prod):
    """Drive はシートと同じサービスアカウント（`Google / Sheets`、googleApi）で読む。OAuth2 はトークンが切れて本番の実行 45 が止まった（2026-11-10）。"""
    drive = [n for n in prod["nodes"] if n["type"] == "n8n-nodes-base.googleDrive"]
    assert drive
    for n in drive:
        assert n["parameters"]["authentication"] == "serviceAccount", n["name"]
        assert n["credentials"] == {"googleApi": gw.SHEETS_CREDENTIAL}, n["name"]
    assert not any("googleDriveOAuth2Api" in (n.get("credentials") or {}) for n in prod["nodes"])


def test_drive_auth_can_be_switched_and_rejects_unknown():
    node = gw.driveDownloadNode("X", [0, 0], "id", auth="serviceAccount")
    assert node["parameters"]["authentication"] == "serviceAccount"
    assert set(node["credentials"]) == {"googleApi"}
    with pytest.raises(ValueError):
        gw.driveSearchNode("X", [0, 0], "q", auth="apiKey")



def test_reference_free_generation_is_visible_in_quality_and_discord(prod):
    by = by_name(prod)
    source = by["Read Reference Images"]["parameters"]["jsCode"]
    assert "imageReferenceNote" in source
    build = by["Build Image Requests"]["parameters"]["jsCode"]
    assert "imageReferenceNote: refNote" in build
    cleanup = by["Cleanup"]["parameters"]["jsCode"]
    assert "data.imageReferenceNote" in cleanup
    notify = by["Discord: Notify Success"]["parameters"]["content"]
    assert "$json.imageReferenceNote" in notify


def test_drive_root_permission_check_precedes_site_folder_search(prod):
    by = by_name(prod)
    node = by["P6: Find IMG Root"]
    assert node["type"] == "n8n-nodes-base.googleDrive"
    assert node["parameters"]["operation"] == "search"
    assert "name = 'IMG'" in node["parameters"]["queryString"]
    guard = by["P6: Verify IMG Root"]["parameters"]["jsCode"]
    assert "SHAMAN_DRIVE_IMG_ACCESS_LOST" in guard
    assert gw.js_str(gw.IMG_FOLDER) in guard
