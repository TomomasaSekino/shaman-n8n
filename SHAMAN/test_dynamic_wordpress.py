"""PR #97: マルチサイトループ・共通 WordPress 認証の回帰検証。"""
import generate_workflows as gw

def test_wordpress_nodes_are_constant_for_site_count():
    sites_a = {"a.example": {"WP_URL": "https://a.example"}}
    sites_b = {**sites_a, "b.example": {"WP_URL": "https://b.example"}}
    for sites in (sites_a, sites_b):
        nodes, conn = gw.wpSiteNodes(sites, 500, 200)
        assert [n["name"] for n in nodes] == [
            "WP: Validate Upload Target", "WP: Upload Media",
            "WP: Validate Post Target", "WP: Create Post"]
        assert len(nodes) == 4
        http_nodes = [n for n in nodes if n["type"] == "n8n-nodes-base.httpRequest"]
        assert len(http_nodes) == 2
        assert set(conn) == set(n["name"] for n in nodes)
        for node in http_nodes:
            p = node["parameters"]
            assert p["authentication"] == "genericCredentialType"
            assert p["genericAuthType"] == "httpBasicAuth"
            assert "$json.wpUrl" in p["url"]
            assert node["credentials"]["httpBasicAuth"]["id"] == "__ENV:SHAMAN_WORDPRESS_MULTISITE_CREDENTIAL_ID__"

def test_existing_sequential_loop_is_preserved():
    wf = gw.buildWorkflow()
    by_name = {n["name"]: n for n in wf["nodes"]}
    loop = by_name["Loop Over Items"]
    assert loop["type"] == "n8n-nodes-base.splitInBatches"
    assert loop["parameters"].get("batchSize", 1) == 1
    assert wf["connections"]["WP: Prepare Media"]["main"][0][0]["node"] == "WP: Validate Upload Target"
    assert wf["connections"]["Build WP Post"]["main"][0][0]["node"] == "WP: Validate Post Target"
    assert wf["connections"]["Discord: Notify Success"]["main"][0][0]["node"] == "Loop Over Items"
    assert "WP: Route Post" not in by_name
    assert "WP: Route Upload" not in by_name

def test_sites_configuration_contains_no_secret_or_per_site_credential():
    sites = gw.load_sites()
    for site in sites.values():
        assert "wpCredential" not in site
        assert "WP_APP_PASSWORD" not in site

def test_shared_credential_is_attached_to_both_wordpress_http_nodes():
    wf = gw.buildWorkflow()
    names = ["WP: Upload Media", "WP: Create Post"]
    matched = [n for n in wf["nodes"] if n["name"] in names]
    assert len(matched) == 2
    assert all(n["credentials"] == {"httpBasicAuth": gw.WP_MULTISITE_CREDENTIAL} for n in matched)

def test_site_id_must_exist_in_registry_and_url_is_derived_from_site_id():
    workflow = gw.buildWorkflow()
    code = next(n["parameters"]["jsCode"] for n in workflow["nodes"] if n["name"] == "Queue: Parse")
    assert "https://' + siteId" in code
    assert "var isDomain = " in code
    assert "registry.has(siteId)" in code
    assert "WP_SITE_REGISTRY_EMPTY" in code
    assert "Object.assign({}, overrides" in code
    assert "siteId && Object.prototype.hasOwnProperty.call(SITES, siteId)" not in code

def test_both_wordpress_destinations_fail_before_http_when_invalid():
    wf = gw.buildWorkflow()
    nodes = {n["name"]: n for n in wf["nodes"]}
    for name in ("WP: Validate Upload Target", "WP: Validate Post Target"):
        assert "WP_SITE_ID_INVALID" in nodes[name]["parameters"]["jsCode"]
        assert "target.wpUrl !== 'https://' + id" in nodes[name]["parameters"]["jsCode"]
        assert nodes[name]["type"] == "n8n-nodes-base.code"

def test_registry_is_read_before_filter_and_processing():
    wf = gw.buildWorkflow()
    nodes = {n["name"]: n for n in wf["nodes"]}
    registry = nodes["Sites: Read Registry"]
    assert registry["type"] == "n8n-nodes-base.googleSheets"
    assert registry["executeOnce"] is True
    assert registry["parameters"]["sheetName"]["value"] == "サイト登録"
    assert registry["parameters"]["options"]["dataLocationOnSheet"]["values"]["headerRow"] == 1
    assert wf["connections"]["Queue: Read"]["main"][0][0]["node"] == "Sites: Read Registry"
    assert wf["connections"]["Sites: Read Registry"]["main"][0][0]["node"] == "Queue: Parse"
    assert wf["connections"]["Queue: Parse"]["main"][0][0]["node"] == "Filter Rows"
    filter_code = nodes["Filter Rows"]["parameters"]["jsCode"]
    assert "サイト登録シートに未登録" in filter_code
    assert "WP: Create Post" in nodes
