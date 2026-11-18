"""
Tests for generate_workflows.py

Run: python -m pytest test_generate_workflows.py -v
"""
import copy
import json
import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import prompts

# ---------------------------------------------------------------------------
# Prompt-wiring assertions
# ---------------------------------------------------------------------------
# Prompts are plain constants in prompts.py, so tests check the *real* text
# rather than injected fakes. js_str() JSON-encodes a prompt before embedding
# it, so the encoded form appears verbatim in the node's jsCode — an exact
# full-text match, which stays valid when prompt wording is revised.
#
# Deliberately not marker-based: markers only existed inside mocked prompt
# files, so any "must NOT contain" check would pass vacuously once the mock
# was gone, silently dropping the guard against cross-wired prompts.


# ---------------------------------------------------------------------------
# Module fixture — load once per test session
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def gw():
    """Import generate_workflows (no file I/O at import time)."""
    mod_name = "generate_workflows"
    sys.path.insert(0, str(Path(__file__).parent))
    sys.modules.pop(mod_name, None)

    import generate_workflows as module

    return module


@pytest.fixture(autouse=True)
def reset_uid_counter(gw):
    """Reset the global UID counter before every test for determinism."""
    gw._c[0] = 0


# ===========================================================================
# uid()
# ===========================================================================
class TestUid:
    def test_returns_string(self, gw):
        assert isinstance(gw.uid(), str)

    def test_format_matches_expected_pattern(self, gw):
        uid = gw.uid()
        assert re.match(r"^[0-9a-f]{8}-0000-4000-a000-[0-9a-f]{12}$", uid), uid

    def test_sequential_calls_differ(self, gw):
        assert gw.uid() != gw.uid()

    def test_increments_counter(self, gw):
        gw.uid()
        assert gw._c[0] == 1
        gw.uid()
        assert gw._c[0] == 2


# ===========================================================================
# sticky()
# ===========================================================================
class TestSticky:
    def test_type(self, gw):
        node = gw.sticky("name", "content", [0, 0])
        assert node["type"] == "n8n-nodes-base.stickyNote"

    def test_name(self, gw):
        node = gw.sticky("My Note", "content", [0, 0])
        assert node["name"] == "My Note"

    def test_default_color(self, gw):
        node = gw.sticky("n", "c", [0, 0])
        assert node["parameters"]["color"] == 7

    def test_custom_color(self, gw):
        node = gw.sticky("n", "c", [0, 0], color=3)
        assert node["parameters"]["color"] == 3

    def test_default_dimensions(self, gw):
        node = gw.sticky("n", "c", [0, 0])
        assert node["parameters"]["width"] == 520
        assert node["parameters"]["height"] == 300

    def test_custom_dimensions(self, gw):
        node = gw.sticky("n", "c", [0, 0], w=400, h=200)
        assert node["parameters"]["width"] == 400
        assert node["parameters"]["height"] == 200

    def test_has_id(self, gw):
        assert "id" in gw.sticky("n", "c", [0, 0])

    def test_position(self, gw):
        node = gw.sticky("n", "c", [100, 200])
        assert node["position"] == [100, 200]

    def test_content(self, gw):
        node = gw.sticky("n", "hello world", [0, 0])
        assert node["parameters"]["content"] == "hello world"


# ===========================================================================
# trigger()
# ===========================================================================
class TestTrigger:
    def test_name(self, gw):
        assert gw.trigger([0, 0])["name"] == "Execute Workflow Trigger"

    def test_type(self, gw):
        assert gw.trigger([0, 0])["type"] == "n8n-nodes-base.executeWorkflowTrigger"

    def test_type_version(self, gw):
        assert gw.trigger([0, 0])["typeVersion"] == 1

    def test_position(self, gw):
        assert gw.trigger([42, 99])["position"] == [42, 99]

    def test_has_id(self, gw):
        assert "id" in gw.trigger([0, 0])


# ===========================================================================
# setN()
# ===========================================================================
class TestSetN:
    def test_type(self, gw):
        assert gw.setN("MySet", [], [0, 0])["type"] == "n8n-nodes-base.set"

    def test_type_version(self, gw):
        assert gw.setN("n", [], [0, 0])["typeVersion"] == 3.4

    def test_assignment_ids_prefixed_a(self, gw):
        assignments = [
            {"name": "foo", "value": "bar", "type": "string"},
            {"name": "baz", "value": "qux", "type": "string"},
        ]
        node = gw.setN("n", assignments, [0, 0])
        ids = [a["id"] for a in node["parameters"]["assignments"]["assignments"]]
        assert ids == ["a0", "a1"]

    def test_incl_other_false_by_default(self, gw):
        node = gw.setN("n", [], [0, 0])
        assert "includeOtherFields" not in node["parameters"]

    def test_incl_other_true(self, gw):
        node = gw.setN("n", [], [0, 0], inclOther=True)
        assert node["parameters"].get("includeOtherFields") is True

    def test_assignment_values_preserved(self, gw):
        node = gw.setN("n", [{"name": "x", "value": "v", "type": "string"}], [0, 0])
        stored = node["parameters"]["assignments"]["assignments"][0]
        assert stored["name"] == "x"
        assert stored["value"] == "v"


# ===========================================================================
# code()
# ===========================================================================
class TestCode:
    def test_type(self, gw):
        assert gw.code("n", "return [];", [0, 0])["type"] == "n8n-nodes-base.code"

    def test_type_version(self, gw):
        assert gw.code("n", "x", [0, 0])["typeVersion"] == 2

    def test_js_code_in_parameters(self, gw):
        js = "var x = 1; return [{ json: { x } }];"
        assert gw.code_body(gw.code("n", js, [0, 0])["parameters"]["jsCode"]) == js

    def test_name(self, gw):
        assert gw.code("MyCode", "", [0, 0])["name"] == "MyCode"


# ===========================================================================
# geminiHttp()
# ===========================================================================
class TestGeminiHttp:
    def test_type(self, gw):
        assert gw.geminiHttp("n", [0, 0])["type"] == "n8n-nodes-base.httpRequest"

    def test_method_is_post(self, gw):
        assert gw.geminiHttp("n", [0, 0])["parameters"]["method"] == "POST"

    def test_url_uses_gemini_model_expression(self, gw):
        assert "$json.geminiModel" in gw.geminiHttp("n", [0, 0])["parameters"]["url"]


    def test_default_timeout(self, gw):
        assert gw.geminiHttp("n", [0, 0])["parameters"]["options"]["timeout"] == 180000

    def test_custom_timeout(self, gw):
        assert gw.geminiHttp("n", [0, 0], timeout=300000)["parameters"]["options"]["timeout"] == 300000

    def test_json_body_expression(self, gw):
        assert "$json.requestBody" in gw.geminiHttp("n", [0, 0])["parameters"]["jsonBody"]


# ===========================================================================
# cn()
# ===========================================================================
class TestCn:
    def test_structure(self, gw):
        assert gw.cn("NextNode") == {"node": "NextNode", "type": "main", "index": 0}

    def test_node_name(self, gw):
        assert gw.cn("FooBar")["node"] == "FooBar"


# ===========================================================================
# wf()
# ===========================================================================
class TestWf:
    def test_required_keys(self, gw):
        w = gw.wf("Test WF", [], {}, "wf-id-001")
        for key in ("name", "nodes", "connections", "active", "settings", "meta", "id"):
            assert key in w, f"Missing key: {key}"

    def test_name(self, gw):
        assert gw.wf("My Workflow", [], {}, "abc")["name"] == "My Workflow"

    def test_default_inactive(self, gw):
        assert gw.wf("n", [], {}, "id")["active"] is False

    def test_active_true(self, gw):
        assert gw.wf("n", [], {}, "id", active=True)["active"] is True

    def test_id(self, gw):
        assert gw.wf("n", [], {}, "my-workflow-id-123")["id"] == "my-workflow-id-123"

    def test_default_empty_tags(self, gw):
        assert gw.wf("n", [], {}, "id")["tags"] == []

    def test_custom_tags(self, gw):
        tags = [{"id": "t1", "name": "blog"}]
        assert gw.wf("n", [], {}, "id", tags=tags)["tags"] == tags

    def test_meta_has_instance_id(self, gw):
        assert "instanceId" in gw.wf("n", [], {}, "id")["meta"]

    def test_pindata_empty(self, gw):
        assert gw.wf("n", [], {}, "id")["pinData"] == {}


# ===========================================================================
# parseTextCode()
# ===========================================================================
class TestParseTextCode:
    def test_references_build_node(self, gw):
        assert "BuildMyNode" in gw.parseTextCode("BuildMyNode", "myProp")

    def test_references_output_prop(self, gw):
        assert "outputField" in gw.parseTextCode("Node", "outputField")

    def test_returns_string(self, gw):
        assert isinstance(gw.parseTextCode("Node", "prop"), str)

    def test_extra_validation_appended(self, gw):
        validation = "if (text.indexOf('## ') === -1) throw new Error('No H2');"
        assert validation in gw.parseTextCode("Node", "prop", validation)

    def test_no_extra_validation_does_not_raise(self, gw):
        code = gw.parseTextCode("Node", "prop")
        assert "throw new Error" in code

    def test_checks_candidates(self, gw):
        assert "candidates" in gw.parseTextCode("Node", "prop")


# ===========================================================================
# parseHtmlCode()
# ===========================================================================
class TestParseHtmlCode:
    def test_references_build_node(self, gw):
        assert "BuildHTMLNode" in gw.parseHtmlCode("BuildHTMLNode", "htmlProp")

    def test_references_output_prop(self, gw):
        assert "articleHtml" in gw.parseHtmlCode("Node", "articleHtml")

    def test_strips_code_block_backticks(self, gw):
        assert "```" in gw.parseHtmlCode("Node", "prop")

    def test_validates_h2_or_h3_presence(self, gw):
        code = gw.parseHtmlCode("Node", "prop")
        assert "<h2" in code or "h2" in code
        assert "<h3" in code or "h3" in code

    def test_returns_string(self, gw):
        assert isinstance(gw.parseHtmlCode("Node", "prop"), str)


# ===========================================================================
# js_str()
# ===========================================================================
class TestJsStr:
    def test_basic_string(self, gw):
        assert gw.js_str("hello") == '"hello"'

    def test_japanese_not_escaped(self, gw):
        result = gw.js_str("日本語テスト")
        assert "日本語テスト" in result

    def test_double_quotes_escaped(self, gw):
        result = gw.js_str('say "hi"')
        assert '\\"' in result

    def test_backslash_escaped(self, gw):
        assert "\\\\" in gw.js_str("path\\to\\file")

    def test_newline_escaped(self, gw):
        assert "\\n" in gw.js_str("line1\nline2")

    def test_output_is_valid_json(self, gw):
        result = gw.js_str("test value")
        assert json.loads(result) == "test value"


# ===========================================================================
# Prompt cross-wiring — each Build node must carry its own prompt and no other
# ===========================================================================
class TestPromptCrossWiring:
    """
    Guards a bug class that has occurred twice: a Build node embedding the
    neighbouring phase's prompt (P4 carrying P5's, P5 carrying P6's). Both the
    positive and the negative assertion are required — the positive alone
    passes even when a second, wrong prompt is also present.
    """

    def _js(self, gw, node_name):
        nodes = gw.buildWorkflow()["nodes"]
        return next(n for n in nodes if n["name"] == node_name)["parameters"]["jsCode"]

    def test_p4_embeds_p4_prompt(self, gw):
        assert gw.js_str(prompts.P4_RAW) in self._js(gw, "Build P4 Request")

    def test_p4_does_not_embed_meta_description_prompt(self, gw):
        assert gw.js_str(prompts.P5_RAW) not in self._js(gw, "Build P4 Request"), (
            "Build P4 Request embeds the P5 meta description prompt."
        )

    def test_p5_embeds_p5_prompt(self, gw):
        assert gw.js_str(prompts.P5_RAW) in self._js(gw, "Build P5 Request")

    def test_p5_does_not_embed_image_generation_prompt(self, gw):
        assert gw.js_str(prompts.P6_IMAGE_PROMPT) not in self._js(gw, "Build P5 Request"), (
            "Build P5 Request embeds the P6 image generation prompt."
        )





# ===========================================================================
# JSON serialization — every builder must produce valid JSON
# ===========================================================================
class TestJsonSerializable:
    @pytest.mark.parametrize("builder_name", ["buildWorkflow"])
    def test_output_is_json_serializable(self, gw, builder_name):
        result = getattr(gw, builder_name)()
        try:
            json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            pytest.fail(f"{builder_name} produced non-serializable output: {e}")

    def test_all_workflow_ids_are_unique(self, gw):
        import prompt_eval
        import connection_check
        ids = [gw.buildWorkflow()["id"], prompt_eval.buildPromptEvalWorkflow()["id"],
               connection_check.buildConnectionCheckWorkflow()["id"]]
        assert len(ids) == len(set(ids)), f"Duplicate workflow IDs: {ids}"

    def test_no_sub_workflow_builders_remain(self, gw):
        """Single-workflow operation is the standing policy — the sub-workflow
        era builders were removed and must not come back."""
        removed = [
            "buildP1", "buildP2", "buildP3", "buildP4",
            "buildP5", "buildP6", "buildP7", "buildOrchestrator",
        ]
        present = [name for name in removed if hasattr(gw, name)]
        assert not present, f"Retired sub-workflow builders reintroduced: {present}"


# ===========================================================================
# Single Workflow tests
# ===========================================================================
class TestBuildSingleWorkflow:
    def test_returns_workflow_dict(self, gw):
        result = gw.buildWorkflow()
        assert isinstance(result, dict)
        assert "nodes" in result
        assert "connections" in result

    def test_is_active(self, gw):
        result = gw.buildWorkflow()
        assert result.get("active") is True

    def test_has_no_execute_workflow_nodes(self, gw):
        nodes = gw.buildWorkflow()["nodes"]
        exec_types = [n for n in nodes if n.get("type") == "n8n-nodes-base.executeWorkflow"]
        assert exec_types == [], f"Found Execute Workflow nodes: {exec_types}"


    def test_p6_conditional_structure(self, gw):
        nodes = gw.buildWorkflow()["nodes"]
        node_names = {n["name"] for n in nodes}
        assert "Check P6 Mode" in node_names

    def test_p1_embeds_p1_prompt(self, gw):
        nodes = gw.buildWorkflow()["nodes"]
        build_p1 = next(n for n in nodes if n["name"] == "Build P1 Request")
        code = build_p1["parameters"]["jsCode"]
        assert gw.js_str(prompts.P1_PROMPT) in code

    def test_p4_embeds_p4_prompt(self, gw):
        nodes = gw.buildWorkflow()["nodes"]
        build_p4 = next(n for n in nodes if n["name"] == "Build P4 Request")
        code = build_p4["parameters"]["jsCode"]
        assert gw.js_str(prompts.P4_RAW) in code



    def test_webhook_is_entry_point(self, gw):
        nodes = gw.buildWorkflow()["nodes"]
        node_types = {n["name"]: n["type"] for n in nodes}
        assert "Webhook" in node_types
        assert node_types["Webhook"] == "n8n-nodes-base.webhook"

    def test_webhook_requires_run_token_and_waits_for_the_result(self, gw):
        """Actions（run-shaman-n8n.yml）から実行する。合言葉（Header Auth）が無ければ n8n が拒む。path は変えない。"""
        hook = next(n for n in gw.buildWorkflow()["nodes"] if n["name"] == "Webhook")
        assert hook["parameters"]["path"] == "blog-pipeline-upload"
        assert hook["parameters"]["authentication"] == "headerAuth"
        assert hook["parameters"]["responseMode"] == "lastNode"
        assert hook["credentials"] == {"httpHeaderAuth": gw.RUN_WEBHOOK_CREDENTIAL}
        assert gw.RUN_WEBHOOK_CREDENTIAL["id"] == "__ENV:SHAMAN_RUN_WEBHOOK_CREDENTIAL_ID__"

    def test_loop_connects_back(self, gw):
        conn = gw.buildWorkflow()["connections"]
        # Discord: Notify Success should loop back to Loop Over Items
        assert "Discord: Notify Success" in conn
        targets = [c["node"] for c in conn["Discord: Notify Success"]["main"][0]]
        assert "Loop Over Items" in targets

    def test_firstperson_substituted_in_p3(self, gw):
        nodes = gw.buildWorkflow()["nodes"]
        build_p3 = next(n for n in nodes if n["name"] == "Build P3 Request")
        code = build_p3["parameters"]["jsCode"]
        assert "FIRSTPERSON" in code

    def test_firstperson_substituted_in_p4(self, gw):
        nodes = gw.buildWorkflow()["nodes"]
        build_p4 = next(n for n in nodes if n["name"] == "Build P4 Request")
        code = build_p4["parameters"]["jsCode"]
        assert "FIRSTPERSON" in code


# ===========================================================================
# Regression: nested SplitInBatches image loop (2nd article images broken)
# ===========================================================================
class TestNoNestedImageLoop:
    """
    Loop Over Insertions (inner SplitInBatches) did not reset between outer
    loop iterations, causing images to be skipped from the 2nd article onwards.
    Fix: replace the inner loop with a single WP: Upload All Images code node.
    """

    def test_no_loop_over_insertions_node(self, gw):
        """Inner SplitInBatches must not exist in single workflow."""
        nodes = gw.buildWorkflow()["nodes"]
        names = [n["name"] for n in nodes]
        assert "Loop Over Insertions" not in names, (
            "Loop Over Insertions (SplitInBatches) still present. "
            "It causes nested-loop reset failures for 2nd article onwards."
        )





    def test_error_gate_true_goes_to_save_article(self, gw):
        """P6: Error Gate? TRUE branch must go to P6: Save Article."""
        conn = gw.buildWorkflow()["connections"]
        assert "P6: Error Gate?" in conn
        true_targets = [c["node"] for c in conn["P6: Error Gate?"]["main"][0]]
        assert "P6: Save Article" in true_targets



    def test_upload_error_gate_true_goes_to_upload_abort(self, gw):
        """WP: Upload Error? TRUE must go to P6: Upload Abort (save draft + hard stop)."""
        conn = gw.buildWorkflow()["connections"]
        true_targets = [c["node"] for c in conn["WP: Upload Error?"]["main"][0]]
        assert "P6: Upload Abort" in true_targets, (
            f"WP: Upload Error? TRUE connects to {true_targets}, expected P6: Upload Abort. "
            "Upload errors must hard-stop via P6: Upload Abort (saves draft + throws) "
            "so n8n marks execution FAILED and Error Trigger fires Discord notification."
        )

    def test_upload_error_gate_false_goes_to_build_updated_html(self, gw):
        """WP: Upload Error? FALSE must go to Build Updated HTML (normal flow)."""
        conn = gw.buildWorkflow()["connections"]
        false_targets = [c["node"] for c in conn["WP: Upload Error?"]["main"][1]]
        assert "Build Updated HTML" in false_targets

    # --- If-node operators must be ones n8n actually implements ---------------
    # n8n's filter engine silently evaluates an unknown operation as false.
    # 'isNotEmpty' (not an n8n operation) left both P6 gates permanently FALSE:
    # a run with 0 images posted an image-less article and marked the row 完了.

    @pytest.mark.parametrize("gate,field", [
        ("P6: Error Gate?", "p6Status"),
        ("WP: Upload Error?", "batchUploadError"),
    ])
    def test_error_gates_use_n8n_not_empty_operator(self, gw, gate, field):
        node = {n["name"]: n for n in gw.buildWorkflow()["nodes"]}[gate]
        cond = node["parameters"]["conditions"]["conditions"][0]
        assert field in cond["leftValue"]
        assert cond["operator"] == {"type": "string", "operation": "notEmpty", "singleValue": True}

    def test_no_prepare_image_binary_node(self, gw):
        """Prepare Image Binary (per-image node) must be removed."""
        nodes = gw.buildWorkflow()["nodes"]
        names = [n["name"] for n in nodes]
        assert "Prepare Image Binary" not in names

    def test_no_collect_image_info_node(self, gw):
        """Collect Image Info (per-image node) must be removed."""
        nodes = gw.buildWorkflow()["nodes"]
        names = [n["name"] for n in nodes]
        assert "Collect Image Info" not in names



# ===========================================================================
# Regression: metaDescription lost in Build Updated HTML (P6 active path)
# ===========================================================================
class TestP3NoFabricatedLinks:
    """v1.4 1.4 (real run 1295). P3 runs Gemini without any search tool but its prompt told it to
    run a site: search and add internal links, so it invented three URLs that all return 404.
    The P4 gate only rejected links P4 added, so the draft's fake links reached WordPress."""

    def test_p3_prompt_has_no_link_building_instructions(self):
        assert "ブラウジング機能" not in prompts.P3_RAW
        assert "site:" not in prompts.P3_RAW
        assert "内部リンク戦略" not in prompts.P3_RAW


    def test_parse_content_unwraps_links(self, gw):
        node = {n["name"]: n for n in gw.buildWorkflow()["nodes"]}["Parse Content"]
        assert "p3LinksRemoved" in node["parameters"]["jsCode"]






class TestMetaDescriptionPreserved:
    """
    Build Updated HTML must not use $('NodeName') for baseConfig — this returns stale
    data on loop iterations 2+. Instead it uses items[0].json which is carried forward
    by WP: Upload All Images (which spreads the full pipeline config into each result).
    metaDescription comes from Parse Meta + Extract Title (Phase-5) and must be present.
    """

    def test_build_updated_html_uses_items_not_node_ref(self, gw):
        """Build Updated HTML must use items[0].json to avoid stale $() refs in loops."""
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == "Build Updated HTML")
        code = node["parameters"]["jsCode"]
        assert "items[0].json" in code, \
            "Build Updated HTML must use items[0].json for baseConfig (loop-safe)"
        assert "$('Parse Meta + Extract Title')" not in code, \
            "Build Updated HTML must not use $() ref that can return stale data in loops"

    def test_build_updated_html_does_not_reference_parse_strengthened(self, gw):
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == "Build Updated HTML")
        code = node["parameters"]["jsCode"]
        assert "$('Parse Strengthened')" not in code

    def test_build_wp_post_uses_st_description(self, gw):
        """Build WP Post must pass metaDescription as st_description in meta."""
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == "Build WP Post")
        code = node["parameters"]["jsCode"]
        assert "st_description" in code
        assert "metaDescription" in code


# ===========================================================================
# Regression: model name gemini-3.1-flash-preview (should be flash-lite)
# ===========================================================================
class TestModelNames:
    """gemini-3.1-flash-preview was set instead of gemini-3.1-flash-lite-preview."""

    def _get_all_js(self, gw) -> str:
        """Collect all jsCode strings from single workflow nodes."""
        nodes = gw.buildWorkflow()["nodes"]
        return "\n".join(
            n["parameters"].get("jsCode", "")
            for n in nodes
            if "jsCode" in n.get("parameters", {})
        )

    def _get_all_set_values(self, gw) -> list[str]:
        nodes = gw.buildWorkflow()["nodes"]
        values = []
        for n in nodes:
            for a in n.get("parameters", {}).get("assignments", {}).get("assignments", []):
                values.append(str(a.get("value", "")))
        return values

    def test_no_bare_flash_preview_in_code(self, gw):
        """No node must reference gemini-3.1-flash-preview (non-lite, non-image)."""
        js = self._get_all_js(gw)
        # Allow flash-lite-preview and flash-image-preview, but not bare flash-preview
        bad = [line for line in js.splitlines()
               if "flash-preview" in line
               and "flash-lite-preview" not in line
               and "flash-image-preview" not in line]
        assert bad == [], f"Bare gemini-3.1-flash-preview found:\n" + "\n".join(bad)

    def test_no_bare_flash_preview_in_set_nodes(self, gw):
        values = self._get_all_set_values(gw)
        bad = [v for v in values
               if "flash-preview" in v
               and "flash-lite-preview" not in v
               and "flash-image-preview" not in v]
        assert bad == [], f"Bare flash-preview in Set node values: {bad}"


# ===========================================================================
# Error notification: Error Trigger + Discord: Notify Error
# ===========================================================================
class TestErrorNotification:
    """Workflow must capture errors and send Discord notifications."""

    def test_error_workflow_not_set_explicitly(self, gw):
        """
        n8n docs: when a workflow contains an Error Trigger node, it is
        automatically used as its own error workflow. Previously we set
        settings.errorWorkflow to the hardcoded workflow ID, but on import
        n8n assigns its own ID, so the hardcoded reference broke silently.
        Rely on n8n's default behavior instead.
        """
        wf = gw.buildWorkflow()
        assert "errorWorkflow" not in wf.get("settings", {}), (
            "settings.errorWorkflow must not be set — n8n auto-uses self when Error Trigger exists."
        )

    def test_error_trigger_node_exists(self, gw):
        nodes = gw.buildWorkflow()["nodes"]
        names = [n["name"] for n in nodes]
        assert "Error Trigger" in names

    def test_error_trigger_is_correct_type(self, gw):
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == "Error Trigger")
        assert node["type"] == "n8n-nodes-base.errorTrigger"

    def test_discord_notify_error_node_exists(self, gw):
        nodes = gw.buildWorkflow()["nodes"]
        names = [n["name"] for n in nodes]
        assert "Discord: Notify Error" in names

    def test_error_trigger_connects_to_discord_error(self, gw):
        conn = gw.buildWorkflow()["connections"]
        assert "Error Trigger" in conn
        targets = [c["node"] for c in conn["Error Trigger"]["main"][0]]
        assert "Discord: Notify Error" in targets





# ===========================================================================
# parseTextCode / parseHtmlCode — Gemini parts null-safety
# ===========================================================================
class TestGeminiParseGuards:
    """parseTextCode and parseHtmlCode must guard against missing content.parts.
    Gemini returns content without parts when safety filters block the response."""

    def test_parse_text_guard_includes_parts_check(self, gw):
        """parts can be undefined when Gemini blocks output → TypeError on parts.length."""
        code = gw.parseTextCode('SomeNode', 'result')
        assert '!response.candidates[0].content.parts' in code, (
            "parseTextCode guard must check content.parts exists before iterating."
        )

    def test_parse_html_guard_includes_parts_check(self, gw):
        """Same guard required for HTML parse path."""
        code = gw.parseHtmlCode('SomeNode', 'result')
        assert '!response.candidates[0].content.parts' in code, (
            "parseHtmlCode guard must check content.parts exists before iterating."
        )

    def _get_node_code(self, gw, node_name: str) -> str:
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == node_name)
        return node["parameters"]["jsCode"]

    def test_single_wf_parse_research_guard_includes_parts_check(self, gw):
        """Inline Parse Research in buildWorkflow must guard content.parts."""
        code = self._get_node_code(gw, "Parse Research")
        assert '!response.candidates[0].content.parts' in code, (
            "Parse Research inline node must check content.parts before iterating parts."
        )

    def test_single_wf_parse_meta_guard_includes_parts_check(self, gw):
        """Inline Parse Meta + Extract Title in buildWorkflow must guard content.parts."""
        code = self._get_node_code(gw, "Parse Meta + Extract Title")
        assert '!response.candidates[0].content.parts' in code, (
            "Parse Meta + Extract Title inline node must check content.parts before iterating parts."
        )


# ===========================================================================
# Production workflow naming
# ===========================================================================
class TestProductionWorkflowNaming:
    """
    Production workflow is deployed to n8n under the official name 'SHAMAN'
    (Synthetic Hack of Akashic Memory & Authoring Node). The version lives in
    the WF README sticky, not in the name or the id.

    The id is the identity of the workflow inside n8n (history, activation,
    webhooks), so it must stay stable and must never fall back to a retired id:
    SINGLE_WF_v2_2026 collided with an archived workflow and made every CLI
    import silently update that hidden box; seo_blog_single_v11 was its
    replacement; seo_blog_auto_writing was the pre-SHAMAN id, re-imported as a
    separate workflow rather than renamed in place.
    """

    def test_workflow_has_production_name(self, gw):
        wf = gw.buildWorkflow()
        assert wf["name"] == "SHAMAN", (
            f"Production workflow must use the official name, got: {wf['name']!r}"
        )

    def test_workflow_name_carries_no_version(self, gw):
        """Version belongs in the README sticky — never in the workflow name."""
        assert not re.search(r"v\d+(\.\d+)?", gw.buildWorkflow()["name"])

    def test_workflow_has_unique_id(self, gw):
        """Must NOT reuse an id that collides with an archived workflow."""
        wf = gw.buildWorkflow()
        assert wf["id"] == "shaman"
        assert wf["id"] not in (
            "SINGLE_WF_v2_2026", "seo_blog_single_v11", "seo_blog_auto_writing"
        )

    def test_test_workflow_name_differs_from_production(self, gw):
        """Test WF name must NOT collide with production — compared dynamically
        so the check cannot go stale when the production name changes."""
        import prompt_eval
        assert prompt_eval.buildPromptEvalWorkflow()["name"] != gw.buildWorkflow()["name"]


# ===========================================================================
# Error handling — Error Trigger self-contained (no explicit errorWorkflow)
# ===========================================================================
class TestErrorHandling:
    """
    n8n docs: 'If a workflow contains the Error Trigger node, by default,
    the workflow uses itself as the error workflow.'
    Therefore settings.errorWorkflow should NOT be set — the previous value
    (TEST_WF_2026 / SINGLE_WF_v2_2026) was a leftover from the old
    sub-workflow architecture and referenced a non-existent ID on the live
    n8n instance (v0.3 is imported with auto-generated ID, not the hardcoded
    TEST_WF_2026). This caused every error to log 'Could not find workflow'
    and suppressed the Discord error notification.
    """

    def test_single_wf_settings_has_no_errorworkflow(self, gw):
        wf = gw.buildWorkflow()
        assert "errorWorkflow" not in wf["settings"], (
            "Do not set settings.errorWorkflow — n8n auto-uses self when Error Trigger is present."
        )

    def test_single_wf_error_trigger_connects_to_discord(self, gw):
        wf = gw.buildWorkflow()
        names = [n["name"] for n in wf["nodes"]]
        assert "Error Trigger" in names
        assert "Discord: Notify Error" in names
        assert "Error Trigger" in wf["connections"]
        targets = [c["node"] for c in wf["connections"]["Error Trigger"]["main"][0]]
        assert "Discord: Notify Error" in targets, (
            "Error Trigger must be wired to the Discord notify node."
        )

# ===========================================================================
# P4 Strengthen Content — migrated from Gemini to Claude Sonnet 4.6
# ===========================================================================
class TestClaudeP4Strengthen:
    """
    P4 (Strengthen Content) uses Claude Sonnet 4.6 instead of Gemini Pro.
    Rationale: Gemini 3.1 Pro emits MALFORMED_FUNCTION_CALL on long prompts
    with search-related keywords (hallucinates non-existent tool calls).
    Claude does not exhibit this behavior.
    """

    def _p4_nodes_map(self, gw):
        wf = gw.buildWorkflow()
        return {n["name"]: n for n in wf["nodes"]}, wf["connections"]

    def test_p4_replaces_gemini_with_claude_node(self, gw):
        nodes, _ = self._p4_nodes_map(gw)
        assert "Gemini: Strengthen Content" not in nodes, (
            "Gemini node must be removed from P4."
        )
        assert "Claude: Strengthen Content" in nodes, (
            "Claude node must replace Gemini in P4."
        )

    def test_p4_http_hits_anthropic_endpoint(self, gw):
        nodes, _ = self._p4_nodes_map(gw)
        http = nodes["Claude: Strengthen Content"]
        url = http["parameters"]["url"]
        assert "api.anthropic.com" in url
        assert "/v1/messages" in url


    def test_p4_request_uses_claude_opus_model(self, gw):
        """P4 は Claude Opus 5.5（2026-11-07 オーナーの指定）。"""
        nodes, _ = self._p4_nodes_map(gw)
        build = nodes["Build P4 Request"]
        js = build["parameters"]["jsCode"]
        assert "claude-opus-5-5" in js
        assert "sonnet" not in js

    def test_p4_request_uses_messages_format_with_max_tokens(self, gw):
        nodes, _ = self._p4_nodes_map(gw)
        build = nodes["Build P4 Request"]
        js = build["parameters"]["jsCode"]
        assert "messages:" in js, "Claude API requires 'messages' field."
        assert "max_tokens:" in js, "Claude API requires 'max_tokens' field."

    # --- Claude Sonnet 5 compatibility (adaptive thinking, no sampling params) ---
    # Sonnet 5 rejects non-default temperature/top_p/top_k with HTTP 400 and runs
    # adaptive thinking by default; max_tokens caps thinking + text combined.

    def _p4_build_js(self, gw):
        nodes, _ = self._p4_nodes_map(gw)
        return nodes["Build P4 Request"]["parameters"]["jsCode"]

    def test_p4_request_sends_no_sampling_params(self, gw):
        js = self._p4_build_js(gw)
        for param in ("temperature", "top_p", "top_k"):
            assert param not in js, (
                f"P4 must not send '{param}': Claude Sonnet 5 returns 400 for non-default sampling params."
            )

    def test_p4_request_uses_adaptive_thinking(self, gw):
        js = self._p4_build_js(gw)
        assert "thinking: { type: 'adaptive' }" in js
        assert "budget_tokens" not in js, "Manual extended thinking returns 400 on Sonnet 5."

    def test_p4_request_sets_high_effort(self, gw):
        js = self._p4_build_js(gw)
        assert "output_config: { effort: 'high' }" in js

    def test_p4_request_max_tokens_covers_thinking_and_article(self, gw):
        """Offline run with the current prompt used 35.9k output tokens (20.2k thinking)."""
        js = self._p4_build_js(gw)
        assert "max_tokens: 96000" in js

    def test_p4_http_timeout_allows_adaptive_thinking(self, gw):
        nodes, _ = self._p4_nodes_map(gw)
        timeout = nodes["Claude: Strengthen Content"]["parameters"]["options"]["timeout"]
        # Offline run with the current prompt took 470s; keep headroom over it.
        assert timeout >= 1800000, f"P4 HTTP timeout too short for thinking + long article: {timeout}"  # 900s は実行 8 で時間切れ

    # --- P4 prompt: purpose-driven strengthening grounded in P1 research -------
    # Sonnet 5 follows instructions literally: "700文字以上" became a ceiling and
    # "構成は一切変えない" suppressed h4/tables/lists. P4 must state its purpose,
    # apply the rubric to every <h3>, and ground new facts in the P1 research.


    def test_p4_prompt_drops_unexecutable_link_instructions(self):
        """P4 has no browsing tool and P1 strips source URLs — link-building
        instructions can only produce invented URLs."""
        assert "ブラウジング機能" not in prompts.P4_RAW
        assert "発リンク" not in prompts.P4_RAW


    @pytest.mark.parametrize("prompt_name", ["P4_RAW", "P4_REPAIR_RAW"])
    def test_p4_build_substitutes_all_prompt_placeholders(self, gw, prompt_name):
        js = self._p4_build_js(gw)
        names = set(re.findall(r"[$][{]([A-Z0-9_]+)[}]", getattr(prompts, prompt_name)))
        assert names, f"{prompt_name} has no placeholders"
        for name in names:
            assert "split('${" + name + "}')" in js, f"Build P4 Request never fills ${{{name}}}"

    # --- P4 retry = repair of attempt 1 (real run 1293) ---------------------
    # Regenerating from the P3 draft replaced a 11,940-char attempt 1 (only lists
    # missing) with an 8,393-char attempt 2. The retry must edit attempt 1 in place.



    def test_p4_build_uses_repair_prompt_with_previous_article(self, gw):
        js = self._p4_build_js(gw)
        assert gw.js_str(prompts.P4_REPAIR_RAW) in js
        assert "data.p4PrevHtml" in js


    def test_p4_parse_checks_stop_reason(self, gw):
        nodes, _ = self._p4_nodes_map(gw)
        js = nodes["Parse Strengthened"]["parameters"]["jsCode"]
        assert "stop_reason" in js, "Parse must reject truncated or refused Claude output."

    # --- P4 quality gate: regenerate once, then record what is still missing ---
    # Parse Strengthened checks the article against P3 (headings unchanged, every
    # <h3> >= 700 chars with an ol/ul/table, no new links). On the first attempt a
    # failure loops back to Build P4 Request with feedback; on the second the
    # article continues and the shortfall is written to the queue and Discord.

    @pytest.mark.parametrize("builder", ["buildWorkflow"])
    def test_p4_retry_gate_node_uses_boolean_true(self, gw, builder):
        nodes = {n["name"]: n for n in getattr(gw, builder)()["nodes"]}
        assert "P4: Retry?" in nodes
        cond = nodes["P4: Retry?"]["parameters"]["conditions"]["conditions"][0]
        assert "p4Retry" in cond["leftValue"]
        assert cond["operator"] == {"type": "boolean", "operation": "true", "singleValue": True}

    @pytest.mark.parametrize("builder", ["buildWorkflow"])
    def test_p4_retry_gate_wiring(self, gw, builder):
        conn = getattr(gw, builder)()["connections"]
        assert [c["node"] for c in conn["Parse Strengthened"]["main"][0]] == ["P4: Retry?"]
        assert [c["node"] for c in conn["P4: Retry?"]["main"][0]] == ["Build P4 Request"]
        assert [c["node"] for c in conn["P4: Retry?"]["main"][1]] == ["Build P5 Request"]

    def test_p4_build_tracks_attempt_and_feedback(self, gw):
        js = self._p4_build_js(gw)
        assert "p4Attempt" in js
        assert "p4QualityFeedback" in js

    def test_p4_parse_runs_quality_gate(self, gw):
        nodes, _ = self._p4_nodes_map(gw)
        js = nodes["Parse Strengthened"]["parameters"]["jsCode"]
        for token in ("p4Retry", "p4QualityIssues", "p4QualityFeedback"):
            assert token in js

    def test_cleanup_records_p4_quality_issues(self, gw):
        nodes, _ = self._p4_nodes_map(gw)
        assert "data.p4QualityIssues" in nodes["Cleanup"]["parameters"]["jsCode"]


    def test_p4_parse_reads_claude_content_not_gemini_candidates(self, gw):
        nodes, _ = self._p4_nodes_map(gw)
        parse = nodes["Parse Strengthened"]
        js = parse["parameters"]["jsCode"]
        assert "response.content" in js, (
            "Parse must read Claude's content[].text, not Gemini's candidates[].content.parts."
        )
        assert "response.candidates" not in js, (
            "Parse must not assume Gemini response format."
        )

    def test_p4_connections_route_through_claude_node(self, gw):
        _, conn = self._p4_nodes_map(gw)
        one = lambda name: [c["node"] for c in conn.get(name, {}).get("main", [[]])[0]]
        # API使用量の記録をはさむ：Build → Usage: Stamp → Claude → Usage: Row → Write → Pass → Parse
        assert one("Build P4 Request") == ["Usage: Stamp P4"]
        assert one("Usage: Stamp P4") == ["Claude: Strengthen Content"]
        assert one("Claude: Strengthen Content") == ["Usage: Row P4"]
        assert one("Usage: Pass P4") == ["Parse Strengthened"]
        assert "Gemini: Strengthen Content" not in conn

    def test_p4_parse_strips_style_blocks(self, gw):
        """Parse Strengthened must remove <style> blocks so site-wide CSS is not overridden."""
        nodes, _ = self._p4_nodes_map(gw)
        js = nodes["Parse Strengthened"]["parameters"]["jsCode"]
        assert r"<style[\s\S]*?<\/style>" in js, (
            "Parse Strengthened must strip <style> blocks Claude may inject."
        )

    def test_p4_parse_strips_inline_style_attrs(self, gw):
        """Parse Strengthened must remove inline style= attributes."""
        nodes, _ = self._p4_nodes_map(gw)
        js = nodes["Parse Strengthened"]["parameters"]["jsCode"]
        assert r"\sstyle\s*=\s*" in js, (
            "Parse Strengthened must strip inline style= attributes."
        )

    def test_p4_parse_keeps_class_attrs(self, gw):
        """class/id must be preserved — site CSS targets WP classes (e.g. wp-block-paragraph)."""
        nodes, _ = self._p4_nodes_map(gw)
        js = nodes["Parse Strengthened"]["parameters"]["jsCode"]
        # must not contain a regex that strips ` class="..."` attributes wholesale
        assert r"\sclass\s*=" not in js, (
            "Parse Strengthened must NOT strip class attributes — removing them breaks site CSS."
        )

    def test_p4_parse_extracts_body_from_full_document(self, gw):
        """Claude returns a full HTML doc — keep only <body> inner, drop scaffolding."""
        nodes, _ = self._p4_nodes_map(gw)
        js = nodes["Parse Strengthened"]["parameters"]["jsCode"]
        assert "<body" in js, "Must extract <body> inner content."
        assert "DOCTYPE" in js, "Must strip <!DOCTYPE>."
        assert "<head" in js and "<meta" in js, "Must strip head/meta scaffolding."


# ===========================================================================
# Gemini Flash Lite — GA model id (preview suffix removed)
# ===========================================================================
class TestFlashLiteModelGA:
    """
    Gemini 3.1 Flash Lite left preview; the GA model id is 'gemini-3.1-flash-lite'.
    Build P2 (Outline) and Build P5 (Meta) must use the GA id, not the -preview alias.
    """

    def _build_node_code(self, gw, name) -> str:
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == name)
        return node["parameters"]["jsCode"]

    def test_p2_uses_gemini_3_5_flash_lite(self, gw):
        """2026-11-09 オーナーの指定で、P2（構成案）は gemini-3.5-flash-lite。P5 は 3.1 Flash-Lite のまま。"""
        js = self._build_node_code(gw, "Build P2 Request")
        assert "'gemini-3.5-flash-lite'" in js
        assert "gemini-3.1-flash-lite" not in js

    def test_p5_uses_flash_lite_ga(self, gw):
        js = self._build_node_code(gw, "Build P5 Request")
        assert "gemini-3.1-flash-lite-preview" not in js, "Must use GA id, not -preview."
        assert "gemini-3.1-flash-lite" in js


# ===========================================================================
# Model registry — centralized model names with $env override + literal fallback
# ===========================================================================
class TestModelRegistry:
    """
    Model names are defined once as Python constants, injected into Map Variables
    as `$env.X || literal`, and referenced by each Build node via data.MODEL_*.
    A rename is a single edit to the Python constant.
    """

    def _node_code(self, gw, name) -> str:
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == name)
        return node["parameters"]["jsCode"]

    def test_constants_exist(self, gw):
        assert gw.MODEL_PRO == "gemini-3.1-pro-preview"
        assert gw.MODEL_FLASH_LITE == "gemini-3.1-flash-lite"
        assert gw.MODEL_OUTLINE == "gemini-3.5-flash-lite"
        assert gw.MODEL_SECTION_ANALYSIS == "gemini-3.8-flash"
        assert gw.MODEL_IMAGE == "gemini-3.1-flash-image-preview"
        assert gw.MODEL_CLAUDE == "claude-opus-5-5"


    def test_build_nodes_reference_registry_not_hardcoded(self, gw):
        assert "data.MODEL_PRO" in self._node_code(gw, "Build P1 Request")
        assert "data.MODEL_OUTLINE" in self._node_code(gw, "Build P2 Request")
        assert "data.MODEL_PRO" in self._node_code(gw, "Build P3 Request")
        assert "data.MODEL_CLAUDE" in self._node_code(gw, "Build P4 Request")
        assert "data.MODEL_FLASH_LITE" in self._node_code(gw, "Build P5 Request")
        assert "data.MODEL_SECTION_ANALYSIS" in self._node_code(gw, "Build Section Analysis Request")
        mv = self._node_code(gw, "Map Variables")
        for name, value in (("MODEL_OUTLINE", "gemini-3.5-flash-lite"), ("MODEL_SECTION_ANALYSIS", "gemini-3.8-flash")):
            assert f'{name}: "{value}"' in mv, name



# ===========================================================================
# Title from <h1> + image placed outside heading
# ===========================================================================
class TestTitleAndImagePlacement:
    """
    Claude emits a full HTML document whose real title is <h1> and whose
    sections are <h2>. The post title must come from <h1> (not the first <h2>
    section), and the <h1> must be removed from the body to avoid duplicating
    the WordPress post title. Images must be inserted AFTER the heading block,
    not inside the <hN> element.
    """

    def _node_code(self, gw, name) -> str:
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == name)
        return node["parameters"]["jsCode"]

    def test_title_extracted_from_h1(self, gw):
        js = self._node_code(gw, "Parse Meta + Extract Title")
        assert "<h1" in js, "Title must be extracted from <h1>."
        assert "h1Match" in js

    def test_h1_removed_from_body(self, gw):
        js = self._node_code(gw, "Parse Meta + Extract Title")
        assert "cleanedHtml" in js, "Must strip <h1> from body to avoid duplicate title."
        assert "articleHtml: cleanedHtml" in js, "Cleaned HTML must be written back."

    def test_image_insertion_is_heading_aware(self, gw):
        js = self._node_code(gw, "Build Updated HTML")
        assert "headPat" in js, "Image insertion must place images after the heading block."
        assert "h[1-6]" in js


# ===========================================================================
# Queue on Google Sheets — read, claim and write back with the standard node
# ===========================================================================
class TestQueueSheets:
    """
    The queue lives in a Google spreadsheet and is read and written with the
    standard Google Sheets node (DOCS/QUEUE_GOOGLE_SHEETS_DESIGN.md). Each row is
    claimed (STATUS=処理中 + 実行ID) before work starts and written back only if the
    claim is still ours, so a row is never processed twice. Code nodes must not
    touch any file.
    """

    SHEETS = "n8n-nodes-base.googleSheets"
    READS = ("Queue: Read", "Queue: Recheck", "Queue: Reread", "Queue: Final Recheck")
    UPDATES = ("Queue: Claim", "Queue: Update")
    CODES = ("Queue: Parse", "Queue: Check Claim", "Queue: Confirm Claim", "Cleanup",
             "Queue: Check Final", "Queue: Claim Check", "Queue: Update Check", "Queue: Done")

    @staticmethod
    def _nodes(workflow):
        return {n["name"]: n for n in workflow["nodes"]}

    @staticmethod
    def _targets(workflow, name):
        return [c["node"] for c in workflow["connections"][name]["main"][0]]

    @pytest.mark.parametrize("builder, doc", [
        ("buildWorkflow", "__ENV:SHAMAN_QUEUE_SPREADSHEET_ID__"),
    ])
    def test_sheets_nodes_use_the_queue_spreadsheet(self, gw, builder, doc):
        nodes = self._nodes(getattr(gw, builder)())
        for name in self.READS + self.UPDATES:
            p = nodes[name]["parameters"]
            assert nodes[name]["type"] == self.SHEETS, name
            assert p["documentId"] == {"__rl": True, "value": doc, "mode": "id"}, name
            assert p["sheetName"] == {"__rl": True, "value": "記事キュー", "mode": "name"}, name

    def test_reads_use_header_row_3_and_always_output(self, gw):
        nodes = self._nodes(gw.buildWorkflow())
        for name in self.READS:
            p = nodes[name]["parameters"]
            assert p["operation"] == "read", name
            assert p["options"]["dataLocationOnSheet"]["values"] == {
                "rangeDefinition": "specifyRange", "headerRow": 3, "firstDataRow": 4}, name
            assert nodes[name]["alwaysOutputData"] is True, name

    def test_updates_match_on_row_number_only(self, gw):
        nodes = self._nodes(gw.buildWorkflow())
        claim = nodes["Queue: Claim"]["parameters"]["columns"]
        assert claim["matchingColumns"] == ["row_number"]
        assert set(claim["value"]) == {"row_number", "STATUS\n【自動】", "実行ID\n【自動】", "開始日時\n【自動】"}
        assert claim["value"]["STATUS\n【自動】"] == "処理中"
        update = nodes["Queue: Update"]["parameters"]["columns"]
        assert update["matchingColumns"] == ["row_number"]
        assert set(update["value"]) == {"row_number"} | {f"{c}\n【自動】" for c in gw.QUEUE_WRITABLE}
        for name in self.UPDATES:
            assert nodes[name]["parameters"]["operation"] == "update", name
            assert nodes[name]["parameters"]["options"]["cellFormat"] == "RAW", name

    def test_failed_or_empty_update_stops_at_the_check(self, gw):
        # 失敗はエラーの item にして、行が見つからない（0 件）ときと同じく Queue: Update Check で止める。
        # エラー出力にしないのは、alwaysOutputData が出力 0 に空の item を足し、両方に流れてしまうため。
        w = gw.buildWorkflow()
        nodes = self._nodes(w)
        assert nodes["Queue: Update"]["onError"] == "continueRegularOutput"
        for name in self.UPDATES:
            assert nodes[name]["alwaysOutputData"] is True, name
            outputs = w["connections"][name]["main"]
            assert [[c["node"] for c in o] for o in outputs] == [[name + " Check"]], name
            assert gw.SHEETS_UPDATE_CHECK_MARK in nodes[name + " Check"]["parameters"]["jsCode"]
        assert "Queue: Update Failed" not in nodes

    @pytest.mark.parametrize("auth, cred_type", [("serviceAccount", "googleApi"), ("oAuth2", "googleSheetsOAuth2Api")])
    def test_auth_method_is_switchable(self, gw, auth, cred_type):
        node = gw.sheetsNode("Queue: Read", "read", [0, 0], "__ENV:X__", auth=auth)
        assert node["parameters"]["authentication"] == auth
        assert node["credentials"] == {cred_type: {"id": "__ENV:SHAMAN_GOOGLE_SHEETS_CREDENTIAL_ID__",
                                                   "name": "Google / Sheets"}}

    def test_unknown_auth_method_is_refused(self, gw):
        with pytest.raises(ValueError):
            gw.sheetsNode("Queue: Read", "read", [0, 0], "__ENV:X__", auth="apiKey")

    @pytest.mark.parametrize("builder", ["buildWorkflow"])
    def test_queue_path_claims_before_work_and_checks_before_write(self, gw, builder):
        w = getattr(gw, builder)()
        # 最初に API使用量のシートを用意してから、キューを読む
        assert self._targets(w, "Webhook") == (["Usage: Ensure Sheet"] if builder == "buildWorkflow" else ["Queue: Read"])
        assert self._targets(w, "Queue: Read") == ["Sites: Read Registry"]
        assert self._targets(w, "Sites: Read Registry") == ["Queue: Parse"]
        assert self._targets(w, "Queue: Parse") == ["Filter Rows"]
        assert self._targets(w, "Map Variables") == ["Queue: Recheck"]
        assert self._targets(w, "Queue: Recheck") == ["Queue: Check Claim"]
        assert self._targets(w, "Queue: Check Claim") == ["Queue: Claim"]
        assert self._targets(w, "Queue: Claim") == ["Queue: Claim Check"]
        assert self._targets(w, "Queue: Claim Check") == ["Queue: Reread"]
        assert self._targets(w, "Queue: Reread") == ["Queue: Confirm Claim"]
        assert self._targets(w, "Cleanup") == ["Queue: Final Recheck"]
        assert self._targets(w, "Queue: Final Recheck") == ["Queue: Check Final"]
        assert self._targets(w, "Queue: Check Final") == ["Queue: Update"]
        assert self._targets(w, "Queue: Update") == ["Queue: Update Check"]
        assert self._targets(w, "Queue: Update Check") == ["Queue: Done"]
        assert "Queue: Update Failed" not in w["connections"]
        assert self._targets(w, "Queue: Done") == ["Discord: Notify Success"]

    @pytest.mark.parametrize("builder", ["buildWorkflow"])
    def test_no_code_node_touches_files(self, gw, builder):
        workflow = getattr(gw, builder)()
        nodes = self._nodes(workflow)
        assert "Spreadsheet File" not in nodes
        for name in self.CODES:
            js = nodes[name]["parameters"]["jsCode"]
            assert "require(" not in js, name
            assert "SEO記事キュー" not in js, name

    def test_claim_check_uses_execution_id(self, gw):
        nodes = self._nodes(gw.buildWorkflow())
        assert "String($execution.id)" in nodes["Queue: Check Claim"]["parameters"]["jsCode"]
        for name in ("Queue: Confirm Claim", "Queue: Check Final"):
            assert "String($execution.id)" in nodes[name]["parameters"]["jsCode"], name

    def test_site_config_is_embedded_from_sites_json(self, gw):
        js = self._nodes(gw.buildWorkflow())["Queue: Parse"]["parameters"]["jsCode"]
        assert "var SITES = " + gw.js_str(gw.load_sites()) + ";" in js


# ===========================================================================
# sites.json — per-site settings and WordPress credential names
# ===========================================================================
class TestSitesConfig:
    """サイト別の公開設定を保存。パスワードを持たず、Credential は全サイト共通。"""

    def _write(self, tmp_path, data):
        p = tmp_path / "sites.json"
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p

    def _site(self, site_id="site-a", **kw):
        base = {"SITENAME": "サイトA", "BASEURL": "https://a.example", "WP_URL": "https://" + site_id,
}
        base.update(kw)
        return base

    def test_repo_sites_json_is_valid(self, gw):
        assert isinstance(gw.load_sites(), dict)

    def test_valid_sites_load(self, gw, tmp_path):
        p = self._write(tmp_path, {"site-a": self._site(), "site-b": self._site("site-b")})
        sites = gw.load_sites(p)
        assert sites["site-b"]["WP_URL"] == "https://site-b"

    def test_domain_site_id_is_allowed_in_metadata(self, gw, tmp_path):
        site_id = "example.com"
        p = self._write(tmp_path, {site_id: self._site(site_id)})
        assert gw.load_sites(p)[site_id]["WP_URL"] == "https://example.com"

    @pytest.mark.parametrize("data, message", [
        ({"site-a": {**{"SITENAME": "A", "WP_URL": "https://site-a", },
                     "WP_APP_PASSWORD": "FAKESECRET"}}, "unknown"),
        

        ({"bad id": {"SITENAME": "A", "WP_URL": "https://a.example", }}, "SITE_ID"),
        ({"site-a": {"SITENAME": 1, "WP_URL": "https://site-a", }}, "string"),
        ({"site-a": {"WP_URL": "https://other.example"}}, "must match SITE_ID"),
        (["site-a"], "object"),
    ])
    def test_invalid_sites_are_refused(self, gw, tmp_path, data, message):
        with pytest.raises(ValueError, match=message):
            gw.load_sites(self._write(tmp_path, data))


# ===========================================================================
# Discord: Notify Success — must surface silent errors in notification
# ===========================================================================


# ===========================================================================
# Security: authHeader must not leak into item JSON
# ===========================================================================
class TestAuthHeaderNotLeaked:
    """authHeader (Basic auth token) must not be spread into node output JSON."""

    def _get_all_js(self, gw) -> str:
        nodes = gw.buildWorkflow()["nodes"]
        return "\n".join(
            n["parameters"].get("jsCode", "")
            for n in nodes
            if "jsCode" in n.get("parameters", {})
        )

    def _get_all_http_headers(self, gw) -> list[dict]:
        nodes = gw.buildWorkflow()["nodes"]
        headers = []
        for n in nodes:
            params = n.get("parameters", {})
            for h in params.get("headerParameters", {}).get("parameters", []):
                headers.append(h)
        return headers




    def test_parse_wp_response_no_dead_auth_variable(self, gw):
        """Parse WP Response must not assign authHeader to a local variable (dead code)."""
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == "Parse WP Response")
        code = node["parameters"]["jsCode"]
        assert "var authHeader = _ref.authHeader" not in code, \
            "Dead variable var authHeader in Parse WP Response — misleads readers"


# ===========================================================================
# Security: mediaUrl must be sanitized before HTML insertion
# ===========================================================================
class TestMediaUrlSanitized:
    """img.mediaUrl must be sanitized before inserting into <img src>."""

    def test_build_updated_html_sanitizes_media_url(self, gw):
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == "Build Updated HTML")
        code = node["parameters"]["jsCode"]
        assert "safeMediaUrl" in code, "Build Updated HTML must sanitize mediaUrl"
        # imgTag must use safeMediaUrl, not the raw img.mediaUrl
        img_tag_line = next(
            (l for l in code.splitlines() if "imgTag" in l and "img src" in l), ""
        )
        assert "safeMediaUrl" in img_tag_line, "imgTag must use safeMediaUrl, not raw mediaUrl"
        assert "img.mediaUrl" not in img_tag_line, "imgTag must not use raw img.mediaUrl"

    def test_build_updated_html_strips_quotes_from_url(self, gw):
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == "Build Updated HTML")
        code = node["parameters"]["jsCode"]
        assert 'replace(/[' in code or "replace(/" in code

    def test_build_updated_html_validates_url_scheme(self, gw):
        """mediaUrl must be validated as http(s) to block javascript: URIs."""
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == "Build Updated HTML")
        code = node["parameters"]["jsCode"]
        # In generated JS the regex is /^https?:\/\// — check for the pattern
        assert "https?" in code and "test(safeMediaUrl)" in code, \
            "Build Updated HTML must reject non-http(s) URLs to prevent javascript: injection"

    def test_build_updated_html_guards_empty_items(self, gw):
        """Build Updated HTML must throw if items is empty to fail fast."""
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == "Build Updated HTML")
        code = node["parameters"]["jsCode"]
        assert "allItems.length === 0" in code, \
            "Build Updated HTML must guard against empty items array"


# ===========================================================================
# WP: Upload All Images — reliability guards
# ===========================================================================


# ===========================================================================
# Regression: Parse Image Response does not handle Gemini API error responses
# ===========================================================================
class TestParseSectionPlanHandlesApiErrors:
    """
    Parse Section Plan must handle API errors gracefully.
    When Gemini: Analyze Sections returns an error JSON or empty candidates,
    the node must return p6Status error items (not throw).
    """

    def _get_parse_code(self, gw) -> str:
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == "Parse Section Plan")
        return node["parameters"]["jsCode"]

    def test_parse_detects_response_error_field(self, gw):
        """Parse Section Plan must check response.error and return section_analysis_error."""
        code = self._get_parse_code(gw)
        assert "response.error" in code, (
            "Parse Section Plan must check response.error first and return "
            "p6Status='section_analysis_error' instead of throwing."
        )

    def test_parse_no_candidates_returns_error_not_throws(self, gw):
        """Parse Section Plan must return p6Status on missing candidates, not throw."""
        code = self._get_parse_code(gw)
        assert "section_analysis_error" in code, (
            "Parse Section Plan must return p6Status='section_analysis_error' "
            "when candidates are missing."
        )



# ===========================================================================
# Regression: Parse Image Response output size exceeds n8n 12 MB pin limit
# ===========================================================================
class TestParseSectionPlanOutputSize:
    """
    Parse Section Plan must strip large fields (requestBody, referenceImages)
    from the config before spreading into per-section items to avoid n8n pin limit.
    """

    def _get_parse_code(self, gw) -> str:
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == "Parse Section Plan")
        return node["parameters"]["jsCode"]

    def test_parse_deletes_request_body_from_output(self, gw):
        """Parse Section Plan must delete requestBody from cfg before spreading."""
        code = self._get_parse_code(gw)
        assert "delete cfg.requestBody" in code, (
            "Parse Section Plan must delete requestBody from cfg before spreading into "
            "per-section items. requestBody can be large and is not needed downstream."
        )

    def test_parse_deletes_reference_images_from_output(self, gw):
        """Parse Section Plan must delete referenceImages from cfg before spreading."""
        code = self._get_parse_code(gw)
        assert "delete cfg.referenceImages" in code, (
            "Parse Section Plan must delete referenceImages from cfg before spreading. "
            "referenceImages contains base64-encoded photos and is not needed by "
            "downstream nodes beyond Generate Images Per Section."
        )

    def test_parse_uses_cfg_not_config_in_return(self, gw):
        """Parse Section Plan must return using cfg (stripped copy), not raw config."""
        code = self._get_parse_code(gw)
        assert "cfg" in code, (
            "Parse Section Plan must use cfg (stripped copy of config) in its return, "
            "not the raw config variable."
        )


# ===========================================================================
# CLICK-PATH-001: Parse Section Plan must delete referenceImages
# ===========================================================================
class TestParseSectionPlanDeletesReferenceImages:
    """
    referenceImages (base64 array of reference photos) set by Read Reference Images
    must be deleted by Parse Section Plan before spreading cfg into per-section items.
    Generate Images Per Section accesses referenceImages via $('Read Reference Images')
    directly, so downstream nodes don't need it in every item.
    """

    def _get_parse_code(self, gw) -> str:
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == "Parse Section Plan")
        return node["parameters"]["jsCode"]

    def test_parse_deletes_reference_images_from_cfg(self, gw):
        """Parse Section Plan must delete cfg.referenceImages before spreading."""
        code = self._get_parse_code(gw)
        assert "delete cfg.referenceImages" in code, (
            "Parse Section Plan must delete cfg.referenceImages before spreading cfg "
            "into per-section items. referenceImages contains base64-encoded reference "
            "photos and is accessed directly by Generate Images Per Section via $()."
        )


# ===========================================================================
# CLICK-PATH-002: Parse WP Response must set wpError; Discord must surface it
# ===========================================================================
class TestWpPostFailureSurfaced:
    """
    WordPress: Create Post has neverError:True — HTTP 401/404/500 errors become
    regular output. Parse WP Response logs console.warn but never sets an error
    field. Discord: Notify Success shows ✅ with 'URL: 未設定' on WP failure,
    indistinguishable from a normal draft. This must be fixed.
    """

    def _get_parse_wp_code(self, gw) -> str:
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == "Parse WP Response")
        return node["parameters"]["jsCode"]

    def _get_notify_body(self, gw) -> str:
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == "Discord: Notify Success")
        return node["parameters"].get("jsonBody", "")

    def test_parse_wp_response_sets_wp_error_on_failure(self, gw):
        """
        When postId is null (WP rejected the request), wpError must be set
        so downstream nodes can detect the failure.
        """
        code = self._get_parse_wp_code(gw)
        assert "wpError" in code, (
            "Parse WP Response must set wpError when postId is null. "
            "Currently WP failures log console.warn but no field is set, so "
            "Discord: Notify Success cannot distinguish success from failure."
        )

    def test_parse_wp_response_wp_error_conditioned_on_post_id(self, gw):
        """wpError should only be set when postId is falsy."""
        code = self._get_parse_wp_code(gw)
        assert "postId" in code and "wpError" in code, (
            "wpError assignment must be conditional on postId being null/falsy."
        )




# ===========================================================================
# CLICK-PATH-003: Generate Images Per Section must use ref images via $()
# ===========================================================================


# ===========================================================================
# CLICK-PATH-003b: Generate Images Per Section must include maxOutputTokens
# ===========================================================================


# ===========================================================================
# CLICK-PATH-003c: Build Updated HTML must not silently skip unmatched images
# ===========================================================================
class TestBuildUpdatedHtmlFallback:
    """
    Build Updated HTML silently skips images whose insertionContext cannot be found
    in the article HTML (even though they are uploaded to WP server). Root cause:
    Unicode/whitespace differences between Gemini output and article HTML cause the
    RegExp to miss.

    Fix: (1) try normalized match, (2) append to article end as fallback.
    All uploaded images must appear in the article.
    """

    def _get_code(self, gw) -> str:
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == "Build Updated HTML")
        return node["parameters"]["jsCode"]

    def test_normalize_function_present(self, gw):
        """Build Updated HTML must define a text normalization function."""
        code = self._get_code(gw)
        assert "normalize" in code, (
            "Build Updated HTML must include a normalizeStr (or equivalent) function "
            "to handle Unicode/whitespace differences between Gemini output and article HTML."
        )

    def test_fallback_append_when_no_match(self, gw):
        """Build Updated HTML must append image to end when no context match found."""
        code = self._get_code(gw)
        assert "matched" in code, (
            "Build Updated HTML must track whether each image was matched and "
            "fall back to appending unmatched images to the article end."
        )


# ===========================================================================
# CLICK-PATH-004: Build Updated HTML must count actually inserted images
# ===========================================================================
class TestBuildUpdatedHtmlInsertionCount:
    """
    imagesInserted is set to allItems.length (total input items) rather than
    the actual number of images successfully inserted into the HTML. This means
    imagesInserted=5 even when all 5 uploads failed and 0 images appear in the article.
    """

    def _get_build_html_code(self, gw) -> str:
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == "Build Updated HTML")
        return node["parameters"]["jsCode"]

    def test_images_inserted_counts_actual_insertions_not_input_items(self, gw):
        """imagesInserted must count HTML insertions, not input item count."""
        code = self._get_build_html_code(gw)
        assert "allItems.length" not in code.split("imagesInserted")[1].split("\n")[0], (
            "Build Updated HTML sets imagesInserted: allItems.length which counts "
            "input items, not images actually inserted into HTML. If all uploads fail, "
            "imagesInserted=5 even though 0 images appear in the article."
        )

    def test_images_inserted_uses_counter_variable(self, gw):
        """A counter variable must be incremented on each successful insertion."""
        code = self._get_build_html_code(gw)
        assert "insertedCount" in code, (
            "Build Updated HTML must use an insertedCount variable that increments "
            "only when an image is actually inserted into the HTML."
        )


# ===========================================================================
# CLICK-PATH-005: Parse Strengthened must delete dead large fields
# ===========================================================================
class TestParseStrengthenedDeletesDeadFields:
    """
    After Parse Strengthened (P4), htmlContent, outlineText, and researchText are
    never used again by any downstream node. They are large (20-50KB each) and
    propagate through the entire remaining pipeline, bloating n8n execution data.
    Parse Strengthened must clean them up.
    """

    def _get_parse_strengthened_code(self, gw) -> str:
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == "Parse Strengthened")
        return node["parameters"]["jsCode"]

    def test_parse_strengthened_deletes_html_content(self, gw):
        """htmlContent (P3 raw output, ~20-50KB) must be deleted after P4."""
        code = self._get_parse_strengthened_code(gw)
        assert "delete config" in code and "htmlContent" in code, (
            "Parse Strengthened must delete htmlContent — it was used by Build P4 Request "
            "but is never needed after that. It propagates wastefully through P5→P7→P8."
        )

    def test_parse_strengthened_deletes_outline_text(self, gw):
        """outlineText (P2 output) must be deleted — consumed by P3, dead after P4."""
        code = self._get_parse_strengthened_code(gw)
        assert "outlineText" in code and "delete" in code, (
            "Parse Strengthened must delete outlineText. "
            "It was consumed by Build P3 Request and is dead after that."
        )

    def test_parse_strengthened_deletes_research_text(self, gw):
        """researchText (P1 raw output) must be deleted — embedded in p1Knowledge."""
        code = self._get_parse_strengthened_code(gw)
        assert "researchText" in code and "delete" in code, (
            "Parse Strengthened must delete researchText. "
            "Its content is already embedded in p1Knowledge and not needed raw."
        )


# ===========================================================================
# CLICK-PATH-006: Cleanup must write 'エラー' status when WP post failed
# ===========================================================================
class TestCleanupExcelStatusOnFailure:
    """
    Cleanup always writes '完了' to the Excel queue regardless of whether
    WordPress post creation succeeded. When WP fails (wpPostId=null), the keyword
    is silently marked as completed and never retried.
    """

    def _get_cleanup_code(self, gw) -> str:
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == "Cleanup")
        return node["parameters"]["jsCode"]

    def test_cleanup_uses_conditional_status_not_hardcoded_done(self, gw):
        """Cleanup must not hardcode '完了' — must check wpPostId first."""
        code = self._get_cleanup_code(gw)
        # Both occurrences of '完了' are replaced by a variable
        done_lines = [l for l in code.splitlines() if "'完了'" in l and "v:" in l]
        assert len(done_lines) == 0, (
            "Cleanup hardcodes '完了' status regardless of whether WP post succeeded. "
            "When wpPostId is null (WP failed), the keyword is silently abandoned. "
            "Use a conditional: var excelStatus = data.wpPostId ? '完了' : 'エラー';"
        )

    def test_cleanup_sets_error_status_on_wp_failure(self, gw):
        """'エラー' must be written to Excel when WP post creation failed."""
        code = self._get_cleanup_code(gw)
        assert "エラー" in code, (
            "Cleanup must write 'エラー' to the STATUS column when wpPostId is null, "
            "so failed articles can be retried on the next run."
        )

    def test_cleanup_status_conditioned_on_wp_post_id(self, gw):
        """The Excel status must depend on wpPostId."""
        code = self._get_cleanup_code(gw)
        assert "wpPostId" in code, (
            "Cleanup must check data.wpPostId to determine the Excel status. "
            "If wpPostId is truthy → '完了', else → 'エラー'."
        )

    def test_cleanup_sets_image_error_status_on_image_error(self, gw):
        """p6Status=image_error must write '画像エラー' so the row can be retried later."""
        code = self._get_cleanup_code(gw)
        assert "画像エラー" in code, (
            "Cleanup must write '画像エラー' when p6Status is 'image_error'. "
            "This allows the retry-later path to pick up the row on the next run."
        )
        assert "image_error" in code


# ===========================================================================
# P6: Save Article on image error
# ===========================================================================
class TestP6SaveArticleOnMaxRetries:
    """
    When max retries are exceeded, article data must be saved to disk so it
    can be reused for image-only retry later without re-running P1-P5.
    Excel row must be marked '画像エラー'. WP post must NOT be created.
    """

    def _get_node(self, gw, name):
        nodes = gw.buildWorkflow()["nodes"]
        return next((n for n in nodes if n["name"] == name), None)

    def _get_save_article_code(self, gw):
        return self._get_node(gw, "P6: Save Article")["parameters"]["jsCode"]

    def _get_conn(self, gw):
        return gw.buildWorkflow()["connections"]

    def test_save_article_node_exists(self, gw):
        assert self._get_node(gw, "P6: Save Article") is not None

    def test_save_article_builds_draft_without_fs(self, gw):
        """n8n 2.x の Code ノードは require('fs') できない。中身とパスだけを作り、書き込みは標準ノードが行う。"""
        code = self._get_save_article_code(gw)
        assert "require(" not in code and "writeFileSync" not in code
        assert "draftJson" in code
        assert gw.js_str(gw.DRAFT_PREFIX) in code, \
            "P6: Save Article must use a predictable filename like article_draft_{rowIndex}.json"
        assert "/home/node" not in code

    def test_draft_is_written_with_standard_nodes(self, gw):
        nodes = {n["name"]: n for n in gw.buildWorkflow()["nodes"]}
        conv = nodes["P6: Draft To File"]
        assert conv["type"] == "n8n-nodes-base.convertToFile"
        assert conv["parameters"]["operation"] == "toText"
        assert conv["parameters"]["sourceProperty"] == "draftJson"
        write = nodes["P6: Write Draft"]
        assert write["type"] == "n8n-nodes-base.readWriteFile"
        assert write["parameters"]["operation"] == "write"
        assert "$('P6: Save Article').first().json.draftFile" in write["parameters"]["fileName"]
        assert write["onError"] == "continueErrorOutput"

    def test_image_error_only_after_draft_saved(self, gw):
        """保存できたときだけ「画像エラー」。失敗したら「エラー」で書き戻す（処理中のまま残さない）。"""
        w = gw.buildWorkflow()
        conn = w["connections"]
        assert [c["node"] for c in conn["P6: Save Article"]["main"][0]] == ["P6: Draft To File"]
        assert [c["node"] for c in conn["P6: Draft To File"]["main"][0]] == ["P6: Write Draft"]
        assert [c["node"] for c in conn["P6: Draft To File"]["main"][1]] == ["P6: Draft Save Failed"]
        ok, err = conn["P6: Write Draft"]["main"]
        assert [c["node"] for c in ok] == ["P6: Draft Saved"]
        assert [c["node"] for c in err] == ["P6: Draft Save Failed"]
        for name in ("P6: Draft Saved", "P6: Draft Save Failed"):
            assert [c["node"] for c in conn[name]["main"][0]] == ["Cleanup"]
        nodes = {n["name"]: n for n in w["nodes"]}
        assert "'image_error'" in nodes["P6: Draft Saved"]["parameters"]["jsCode"]
        assert "image_error" not in nodes["P6: Save Article"]["parameters"]["jsCode"]
        assert "'draft_save_error'" in nodes["P6: Draft Save Failed"]["parameters"]["jsCode"]

    def test_save_article_includes_article_html(self, gw):
        code = self._get_save_article_code(gw)
        assert "articleHtml" in code

    def test_save_article_includes_meta(self, gw):
        code = self._get_save_article_code(gw)
        assert "articleTitle" in code
        assert "metaDescription" in code
        assert "slug" in code

    def test_draft_saved_nullifies_wp_post_id(self, gw):
        """wpPostId must be null so Cleanup writes '画像エラー' not '完了'."""
        code = self._get_node(gw, "P6: Draft Saved")["parameters"]["jsCode"]
        assert "wpPostId: null" in code, \
            "P6: Draft Saved must ensure wpPostId is null (article was never posted)"


# ===========================================================================
# Article retry-later path: 画像エラー rows skip P1-P5
# ===========================================================================
class TestArticleRetryLaterPath:
    """
    Rows marked '画像エラー' in Excel have saved article data on disk.
    The workflow must pick them up, skip P1-P5, load the saved JSON,
    and run P6 (image generation) + P7 (WP post) directly.
    """

    def _get_node(self, gw, name):
        nodes = gw.buildWorkflow()["nodes"]
        return next((n for n in nodes if n["name"] == name), None)

    def _get_conn(self, gw):
        return gw.buildWorkflow()["connections"]

    # ── Filter Rows must include 画像エラー ───────────────────────────

    def test_filter_rows_includes_image_error_status(self, gw):
        """Filter Rows must pass '画像エラー' rows, not just '待機中'."""
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == "Filter Rows")
        # Works for both filter node and code node
        node_str = str(node)
        assert "画像エラー" in node_str, (
            "Filter Rows must also pass rows with STATUS='画像エラー' "
            "so saved articles can be retried for image generation."
        )

    # ── Map Variables must output STATUS ─────────────────────────────

    def test_map_variables_outputs_status(self, gw):
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == "Map Variables")
        code = node["parameters"]["jsCode"]
        assert "STATUS" in code, \
            "Map Variables must output STATUS field for Check Article Status branching"

    # ── Check Article Status node ─────────────────────────────────────

    def test_check_article_status_node_exists(self, gw):
        assert self._get_node(gw, "Check Article Status") is not None, \
            "Check Article Status IF node must exist to branch 画像エラー rows"

    def test_map_variables_connects_to_check_article_status(self, gw):
        conn = self._get_conn(gw)
        targets = [c["node"] for c in conn["Queue: Confirm Claim"]["main"][0]]
        assert "Check Article Status" in targets, \
            "The claimed row must go to Check Article Status (not directly to Build P1 Request)"

    def test_check_article_status_true_reads_the_draft(self, gw):
        conn = self._get_conn(gw)
        assert "Check Article Status" in conn
        true_targets = [c["node"] for c in conn["Check Article Status"]["main"][0]]
        assert true_targets == ["Read Draft"]
        assert [c["node"] for c in conn["Read Draft"]["main"][0]] == ["Draft From File"]
        assert [c["node"] for c in conn["Draft From File"]["main"][0]] == ["Load Saved Article"]

    def test_check_article_status_false_connects_to_build_p1(self, gw):
        conn = self._get_conn(gw)
        false_targets = [c["node"] for c in conn["Check Article Status"]["main"][1]]
        assert "Build P1 Request" in false_targets, \
            "Normal rows (not 画像エラー) must still go to Build P1 Request"

    # ── Load Saved Article node ───────────────────────────────────────

    def test_load_saved_article_node_exists(self, gw):
        assert self._get_node(gw, "Load Saved Article") is not None

    def test_draft_is_read_with_standard_nodes(self, gw):
        read = self._get_node(gw, "Read Draft")
        assert read["type"] == "n8n-nodes-base.readWriteFile"
        assert read["parameters"]["fileSelector"] == "=" + gw.DRAFT_PREFIX + "{{ $json.rowIndex }}.json"
        # ファイルがないと 0 件になる。空の item を渡して Draft From File のエラー出力にする。
        assert read["alwaysOutputData"] is True
        extract = self._get_node(gw, "Draft From File")
        assert extract["type"] == "n8n-nodes-base.extractFromFile"
        assert extract["parameters"]["operation"] == "text"
        code = self._get_node(gw, "Load Saved Article")["parameters"]["jsCode"]
        assert "require(" not in code and "/home/node" not in code

    def test_unusable_draft_writes_error_status(self, gw):
        """読めない・ない・別の行の下書きは、Draft Unusable → Cleanup で「エラー」にする。"""
        conn = self._get_conn(gw)
        for name in ("Read Draft", "Draft From File", "Load Saved Article"):
            assert self._get_node(gw, name)["onError"] == "continueErrorOutput", name
            assert [c["node"] for c in conn[name]["main"][1]] == ["Draft Unusable"], name
        assert [c["node"] for c in conn["Draft Unusable"]["main"][0]] == ["Cleanup"]

    def test_load_saved_article_sets_image_trigger_true(self, gw):
        """Retry path always needs image generation."""
        node = self._get_node(gw, "Load Saved Article")
        code = node["parameters"]["jsCode"]
        assert "IMAGE_TRIGGER" in code and "TRUE" in code, \
            "Load Saved Article must set IMAGE_TRIGGER=TRUE so P6 runs"

    def test_load_saved_article_connects_to_check_p6_mode(self, gw):
        conn = self._get_conn(gw)
        assert "Load Saved Article" in conn
        targets = [c["node"] for c in conn["Load Saved Article"]["main"][0]]
        assert "Check P6 Mode" in targets, \
            "Load Saved Article must skip P1-P5 and go directly to Check P6 Mode"

    # ── Discord notification for max_retries_exceeded ─────────────────



# ===========================================================================
# P6: Upload Abort — hard stop on WP upload failure
# ===========================================================================
class TestP6UploadAbort:
    """
    P6: Upload Abort is the terminal node for WP upload failures.
    It saves the article draft to disk, then throws to hard-stop the workflow.
    n8n marks the execution FAILED → Error Trigger fires → Discord notification.
    """

    def _get_node(self, gw, name):
        nodes = gw.buildWorkflow()["nodes"]
        return next((n for n in nodes if n["name"] == name), None)

    def _get_abort_code(self, gw) -> str:
        node = self._get_node(gw, "P6: Upload Abort")
        assert node is not None, "P6: Upload Abort node must exist"
        return node["parameters"]["jsCode"]

    def _get_conn(self, gw):
        return gw.buildWorkflow()["connections"]

    def test_upload_abort_node_exists(self, gw):
        assert self._get_node(gw, "P6: Upload Abort") is not None, \
            "P6: Upload Abort node must be present in the workflow"

    def test_upload_abort_is_code_node(self, gw):
        node = self._get_node(gw, "P6: Upload Abort")
        assert node["type"] == "n8n-nodes-base.code"

    def test_upload_abort_throws_after_saving(self, gw):
        """保存できてもできなくても、最後のノードが throw してハードストップする。"""
        conn = self._get_conn(gw)
        assert [c["node"] for c in conn["P6: Upload Abort"]["main"][0]] == ["P6: Abort Draft To File"]
        assert [c["node"] for c in conn["P6: Abort Draft To File"]["main"][0]] == ["P6: Abort Write Draft"]
        assert [c["node"] for c in conn["P6: Abort Draft To File"]["main"][1]] == ["P6: Abort Draft Failed"]
        ok, err = conn["P6: Abort Write Draft"]["main"]
        assert [c["node"] for c in ok] == ["P6: Abort Draft Saved"]
        assert [c["node"] for c in err] == ["P6: Abort Draft Failed"]
        for name in ("P6: Abort Draft Saved", "P6: Abort Draft Failed"):
            assert "throw new Error" in self._get_node(gw, name)["parameters"]["jsCode"], name
            assert name not in conn, f"{name} must be a terminal node"

    def test_upload_abort_saves_draft(self, gw):
        code = self._get_abort_code(gw)
        assert "require(" not in code and "writeFileSync" not in code
        assert gw.js_str(gw.DRAFT_PREFIX) in code, \
            "P6: Upload Abort must use article_draft_ filename pattern"
        write = self._get_node(gw, "P6: Abort Write Draft")
        assert "$('P6: Upload Abort').first().json.draftFile" in write["parameters"]["fileName"]

    def test_upload_abort_sets_upload_error_status(self, gw):
        code = self._get_abort_code(gw)
        assert "upload_error" in code, \
            "P6: Upload Abort must set p6Status='upload_error' in saved draft"

    def test_upload_abort_error_message_includes_keyword(self, gw):
        code = self._get_abort_code(gw)
        assert "KEYWORD" in code, \
            "P6: Upload Abort error message must include KEYWORD for diagnosability"

    def test_upload_abort_error_message_includes_upload_error(self, gw):
        code = self._get_abort_code(gw)
        assert "uploadError" in code, \
            "P6: Upload Abort error message must include uploadError detail"

    def test_upload_error_gate_true_connects_to_abort(self, gw):
        conn = self._get_conn(gw)
        true_targets = [c["node"] for c in conn["WP: Upload Error?"]["main"][0]]
        assert "P6: Upload Abort" in true_targets, \
            "WP: Upload Error? TRUE must connect to P6: Upload Abort"

    def test_upload_abort_not_connected_to_downstream(self, gw):
        """The abort path never reaches Cleanup or the WP post."""
        conn = self._get_conn(gw)
        reached, todo = set(), ["P6: Upload Abort"]
        while todo:
            name = todo.pop()
            for out in conn.get(name, {}).get("main", []):
                for c in out:
                    if c["node"] not in reached:
                        reached.add(c["node"])
                        todo.append(c["node"])
        assert "Cleanup" not in reached and "WordPress: Create Post" not in reached


# ===========================================================================
# Cleanup: dead-end nodes and redundant pass-through nodes removed
# ===========================================================================
class TestDeadEndNodesRemoved:
    """
    Pipeline Complete は Loop Over Items の output[0] に繋がる Dead-end Set ノード。
    p6Status='skipped' をセットするだけの P6: Skip も下流で参照されない。
    両ノードを削除し、接続を直結に変更する。
    """

    def test_pipeline_complete_removed_from_single_workflow(self, gw):
        """Pipeline Complete は dead-end ノード。削除済みであること。"""
        names = [n["name"] for n in gw.buildWorkflow()["nodes"]]
        assert "Pipeline Complete" not in names, (
            "Pipeline Complete is a dead-end Set node with no outgoing connections. "
            "SplitInBatches terminates naturally; this node adds no value."
        )

    def test_loop_over_items_output0_goes_only_to_the_run_result(self, gw):
        """Loop Over Items の output[0]（全行の処理後）は、実行結果（Queue: Result Read → Run Result）だけへ進む。"""
        w = gw.buildWorkflow()
        conn = w["connections"]
        assert [c["node"] for c in conn["Loop Over Items"]["main"][0]] == ["Queue: Result Read"]
        assert [c["node"] for c in conn["Queue: Result Read"]["main"][0]] == ["Run Result"]
        assert [c["node"] for c in conn["Run Result"]["main"][0]] == ["Run: Check"]
        assert "Run: Check" not in conn
        read = next(n for n in w["nodes"] if n["name"] == "Queue: Result Read")
        assert read["executeOnce"] is True
        assert read["parameters"]["documentId"]["value"] == gw.QUEUE_SPREADSHEET

    def test_p6_skip_removed_from_single_workflow(self, gw):
        """P6: Skip は p6Status='skipped' のみ。Cleanup も Discord も参照しない。削除済みであること。"""
        names = [n["name"] for n in gw.buildWorkflow()["nodes"]]
        assert "P6: Skip" not in names, (
            "P6: Skip only sets p6Status='skipped' which is never checked downstream. "
            "Check P6 Mode FALSE should connect directly to Build WP Post."
        )

    def test_check_p6_mode_false_connects_directly_to_build_wp_post(self, gw):
        """P6: Skip 削除後、Check P6 Mode FALSE パスが Build WP Post に直結されること。"""
        conn = gw.buildWorkflow()["connections"]
        false_targets = [c["node"] for c in conn["Check P6 Mode"]["main"][1]]
        assert "Build WP Post" in false_targets, (
            f"Check P6 Mode FALSE branch connects to {false_targets}, "
            "expected direct connection to Build WP Post."
        )



# ===========================================================================
# Req 1: 画像枚数保証 (5〜6枚)
# Req 2: 500エラー撲滅 (リトライ)
# Req 3: アップロード失敗 → ワークフロー停止 + エラー発報
# ===========================================================================
class TestImageUploadRequirements:
    """3要件の厳密保証テスト。"""

    # ── Req 1: 最低5枚 ──────────────────────────────────────────────────────




    # ── Req 2: 500エラー撲滅 ──────────────────────────────────────────────────





    # ── Req 3: 停止 + エラー発報 ──────────────────────────────────────────────

    def test_error_workflow_not_set(self, gw):
        """settings.errorWorkflow は未設定（Error Trigger 内蔵で n8n default 挙動が self 参照）。"""
        wf = gw.buildWorkflow()
        assert 'errorWorkflow' not in wf.get('settings', {}), (
            "Do not set errorWorkflow — n8n auto-uses self when Error Trigger is present."
        )

    def test_upload_abort_ends_in_terminal_throw_nodes(self, gw):
        """途中保存のあと、P6: Abort Draft Saved / Failed が terminal で throw する。"""
        conn = gw.buildWorkflow()["connections"]
        for name in ("P6: Abort Draft Saved", "P6: Abort Draft Failed"):
            assert name not in conn, (
                f"{name} must be a terminal node. After throwing, Error Trigger takes over."
            )

    def test_error_trigger_connects_to_discord_notify_error(self, gw):
        """Error Trigger → Discord: Notify Error が接続されていること。"""
        conn = gw.buildWorkflow()["connections"]
        assert "Error Trigger" in conn, "Error Trigger must have outgoing connections."
        targets = [c["node"] for c in conn["Error Trigger"]["main"][0]]
        assert "Discord: Notify Error" in targets, (
            f"Error Trigger connects to {targets}, expected Discord: Notify Error."
        )

    def test_upload_error_gate_true_goes_to_abort(self, gw):
        """WP: Upload Error? TRUE → P6: Upload Abort であること。"""
        conn = gw.buildWorkflow()["connections"]
        true_targets = [c["node"] for c in conn["WP: Upload Error?"]["main"][0]]
        assert "P6: Upload Abort" in true_targets, (
            f"WP: Upload Error? TRUE connects to {true_targets}, expected P6: Upload Abort."
        )


# ===========================================================================
class TestOpenAIImageMigration:
    """P6 image generation runs on OpenAI images/edits by default, with the
    frozen Gemini path kept inside the same node for IMAGE_PROVIDER=gemini
    rollback. Node count and connections must not change."""

    def _node_code(self, gw, name) -> str:
        nodes = gw.buildWorkflow()["nodes"]
        node = next(n for n in nodes if n["name"] == name)
        return node["parameters"]["jsCode"]

    # --- registry / env wiring -------------------------------------------
    def test_openai_model_constant_exists(self, gw):
        assert gw.MODEL_IMAGE_OPENAI == "gpt-image-2"



    # --- prompt wiring ----------------------------------------------------


    def test_openai_prompt_has_no_aspect_ratio_instruction(self):
        """16:9 is enforced via the API size parameter, not prompt text."""
        assert "16:9" not in prompts.P6_IMAGE_PROMPT_OPENAI


    # --- API call ---------------------------------------------------------





    # --- scene passthrough ------------------------------------------------
    def test_parse_section_plan_passes_scene_through(self, gw):
        js = self._node_code(gw, "Parse Section Plan")
        assert "scene: scene" in js
        # scene must stay optional — validation still keys off the original trio
        assert "s.insertionContext && s.imageDescription && s.altText" in js

    # --- structural invariants -------------------------------------------


# ===========================================================================
# n8n filter operators — every If/Filter condition must use a real operation
# ===========================================================================
# Source: n8n-workflow filter-parameter.js (n8n 2.17.5). Unknown operations do
# not raise; they fall through the switch and evaluate as false.
N8N_FILTER_OPERATIONS = {
    "string": {"exists", "notExists", "empty", "notEmpty", "equals", "notEquals",
               "contains", "notContains", "startsWith", "notStartsWith",
               "endsWith", "notEndsWith", "regex", "notRegex"},
    "boolean": {"exists", "notExists", "empty", "notEmpty", "true", "false",
                "equals", "notEquals"},
}
N8N_SINGLE_VALUE_OPERATIONS = {"exists", "notExists", "empty", "notEmpty", "true", "false"}


def _filter_conditions(workflow):
    for node in workflow["nodes"]:
        conds = (node.get("parameters", {}).get("conditions") or {})
        for cond in (conds.get("conditions") or []) if isinstance(conds, dict) else []:
            yield node["name"], cond["operator"]


class TestN8nFilterOperators:
    @pytest.mark.parametrize("builder", ["buildWorkflow"])
    def test_all_filter_operations_exist_in_n8n(self, gw, builder):
        bad = []
        for name, op in _filter_conditions(getattr(gw, builder)()):
            valid = N8N_FILTER_OPERATIONS.get(op["type"])
            if valid is None or op["operation"] not in valid:
                bad.append(f"{name}: {op}")
        assert bad == [], "Unknown n8n filter operation (evaluates as false):\n" + "\n".join(bad)

    @pytest.mark.parametrize("builder", ["buildWorkflow"])
    def test_unary_operations_are_marked_single_value(self, gw, builder):
        bad = [f"{name}: {op}" for name, op in _filter_conditions(getattr(gw, builder)())
               if op["operation"] in N8N_SINGLE_VALUE_OPERATIONS and op.get("singleValue") is not True]
        assert bad == [], "Unary operations need singleValue: True:\n" + "\n".join(bad)


# ===========================================================================
# 途中保存（macOS 版 n8n）：fs を使わず、試験用は別のファイル名
# ===========================================================================
class TestDraftFiles:
    def test_no_linux_path_or_fs_in_draft_nodes(self, gw):
        names = ("Load Saved Article", "P6: Save Article", "P6: Upload Abort", "P6: Draft Saved",
                 "P6: Draft Save Failed", "P6: Abort Draft Saved", "P6: Abort Draft Failed", "Draft Unusable")
        for w in (gw.buildWorkflow(),):
            nodes = {n["name"]: n for n in w["nodes"]}
            for name in names:
                code = nodes[name]["parameters"]["jsCode"]
                assert "require(" not in code and "/home/node" not in code, name

    def test_draft_dir_is_a_deploy_placeholder(self, gw):
        assert gw.DRAFT_DIR == "__ENV:SHAMAN_DRAFT_DIR__"

class TestDraftSaveFailurePaths:
    """変換（Convert to File）と書き込み（Read/Write Files）のどちらで失敗しても、決めた終わり方になる（本番・試験の両方）。"""

    @staticmethod
    def _follow(conn, start):
        reached, todo = set(), [start]
        while todo:
            name = todo.pop()
            for out in conn.get(name, {}).get("main", []):
                for c in out:
                    if c["node"] not in reached:
                        reached.add(c["node"])
                        todo.append(c["node"])
        return reached

    @pytest.mark.parametrize("build", ["buildWorkflow"])
    @pytest.mark.parametrize("step", ["P6: Draft To File", "P6: Write Draft"])
    def test_image_error_save_failure_writes_error(self, gw, build, step):
        """画像エラーの保存に失敗 → P6: Draft Save Failed → Cleanup → Queue: Update（STATUS=エラー）。"""
        w = getattr(gw, build)()
        nodes = {n["name"]: n for n in w["nodes"]}
        conn = w["connections"]
        assert nodes[step]["onError"] == "continueErrorOutput"
        err = [c["node"] for c in conn[step]["main"][1]]
        assert err == ["P6: Draft Save Failed"]
        reached = self._follow(conn, "P6: Draft Save Failed")
        assert {"Cleanup", "Queue: Update"} <= reached

    @pytest.mark.parametrize("build", ["buildWorkflow"])
    @pytest.mark.parametrize("step", ["P6: Abort Draft To File", "P6: Abort Write Draft"])
    def test_upload_abort_save_failure_keeps_upload_reason(self, gw, build, step):
        """アップロード失敗の保存に失敗 → P6: Abort Draft Failed が元の理由（abortMessage）を付けて throw する。"""
        w = getattr(gw, build)()
        nodes = {n["name"]: n for n in w["nodes"]}
        conn = w["connections"]
        assert nodes[step]["onError"] == "continueErrorOutput"
        assert [c["node"] for c in conn[step]["main"][1]] == ["P6: Abort Draft Failed"]
        code = nodes["P6: Abort Draft Failed"]["parameters"]["jsCode"]
        assert "abortMessage" in code and "throw new Error" in code
        assert "P6: Abort Draft Failed" not in conn

    @pytest.mark.parametrize("build", ["buildWorkflow"])
    def test_every_draft_file_node_has_an_error_output(self, gw, build):
        """途中保存のファイルを扱う標準ノードは、すべてエラー出力をつないでいる。"""
        w = getattr(gw, build)()
        conn = w["connections"]
        file_types = {"n8n-nodes-base.convertToFile", "n8n-nodes-base.readWriteFile", "n8n-nodes-base.extractFromFile"}
        names = [n["name"] for n in w["nodes"] if n["type"] in file_types]
        assert len(names) == 6, names
        for n in w["nodes"]:
            if n["type"] in file_types:
                assert n["onError"] == "continueErrorOutput", n["name"]
                assert len(conn[n["name"]]["main"]) == 2 and conn[n["name"]]["main"][1], n["name"]


class TestSiteIds:
    @pytest.mark.parametrize("site_id", [".a", "a.", "a..b", "a b", "a/b"])
    def test_malformed_dotted_site_ids_are_refused(self, gw, tmp_path, site_id):
        p = tmp_path / "sites.json"
        p.write_text(json.dumps({site_id: {"WP_URL": "https://a.example",
                                           }}), encoding="utf-8")
        with pytest.raises(ValueError, match="SITE_ID"):
            gw.load_sites(p)


# n8n 2.35.7 の Code ノードは、入力を $input で受け取る。生成した全 Workflow で、暗黙の items に頼らない（PR #66 のレビュー指摘）。
_WORKFLOW_DIR = Path(__file__).parent / 'workflows'
_ITEMS_TOKEN = re.compile(r'(?<![\w$.])items\b')


def _code_nodes():
    for path in sorted(_WORKFLOW_DIR.glob('*.json')):
        wf = json.loads(path.read_text(encoding='utf-8'))
        for node in wf['nodes']:
            if node['type'] == 'n8n-nodes-base.code':
                yield path.name, node


@pytest.mark.parametrize('file,node', list(_code_nodes()), ids=lambda v: v if isinstance(v, str) else v['name'])
def test_code_nodes_take_items_from_input(file, node):
    js = node['parameters']['jsCode']
    first_use = _ITEMS_TOKEN.search(js)
    if first_use is None:
        return
    decl = re.search(r'\b(?:let|const|var)\s+items\s*=\s*\$input\.all\(\)', js)
    assert decl is not None, f'{file} / {node["name"]}: items を $input.all() で宣言していない'
    assert first_use.start() >= decl.start(), f'{file} / {node["name"]}: items を宣言より前に使っている'
    assert node['parameters'].get('mode', 'runOnceForAllItems') == 'runOnceForAllItems'


def test_sheet_writes_use_the_header_row_of_their_sheet():
    """update・append は見出しの行を指定する。指定しないと n8n は 1 行目を見出しとして扱い、
    キュー（見出しは 3 行目）の列が見つからない（2026-11-06、接続確認の Conn: Sheets Write で発覚）。"""
    import generate_workflows as gw
    import connection_check as cc
    import prompt_eval as pe
    checked = 0
    for wf in (gw.buildWorkflow(), cc.buildConnectionCheckWorkflow(), pe.buildPromptEvalWorkflow()):
        for n in wf["nodes"]:
            if n["type"] != "n8n-nodes-base.googleSheets":
                continue
            p = n["parameters"]
            if p["operation"] == "read":
                header = p["options"]["dataLocationOnSheet"]["values"]["headerRow"]
                continue
            if p["operation"] == "create":  # シートを作るだけ（プロンプト評価の Eval: Ensure Sheets）
                continue
            location = p["options"]["locationDefine"]["values"]
            expected = gw.QUEUE_HEADER_ROW if p["sheetName"]["value"] == gw.QUEUE_SHEET else pe.HEADER_ROW
            assert location["headerRow"] == expected, n["name"]
            if p["operation"] == "update":
                assert location["firstDataRow"] == expected + 1, n["name"]
            else:
                assert "firstDataRow" not in location, n["name"]
            checked += 1
    assert checked >= 5
    names = {n["name"]: n for n in gw.buildWorkflow()["nodes"]}
    for name in ("Queue: Claim", "Queue: Update"):
        assert names[name]["parameters"]["options"]["locationDefine"] == {"values": {"headerRow": 3, "firstDataRow": 4}}


def test_every_workflow_ends_in_run_check_that_fails_the_execution_on_ng():
    """NG・結果なしのときは例外で止め、n8n の実行を Error にする（Succeeded にしない。2026-11-06）。"""
    import generate_workflows as gw
    import connection_check as cc
    import prompt_eval as pe
    for wf, sources in ((gw.buildWorkflow(), ["Run Result"]), (cc.buildConnectionCheckWorkflow(), ["Conn: Result"]),
                        (pe.buildPromptEvalWorkflow(), ["Eval: Run Result", "Eval: Stop"])):
        check = next(n for n in wf["nodes"] if n["name"] == "Run: Check")
        assert gw.code_body(check["parameters"]["jsCode"]) == gw.ITEMS_PRELUDE + gw.RUN_CHECK_JS
        assert "Run: Check" not in wf["connections"], wf["name"]
        for src in sources:
            assert [c["node"] for c in wf["connections"][src]["main"][0]] == ["Run: Check"], (wf["name"], src)


def test_connection_check_never_stops_silently():
    import connection_check as cc
    for n in cc.buildConnectionCheckWorkflow()["nodes"]:
        if n["type"] not in ("n8n-nodes-base.stickyNote", "n8n-nodes-base.manualTrigger", "n8n-nodes-base.webhook"):
            assert n.get("alwaysOutputData") is True, n["name"]


def test_no_node_can_stop_the_run_silently():
    """どのノードも、出力が 0 件のまま黙って止まらない（n8n の実行を Succeeded にしない。2026-11-06 オーナーの指示）。

    n8n は、ノードの出力が 0 件だと、その先を動かさずに実行を Succeeded で終える。そのため：
    - Code ノードは、出力が 0 件なら例外にする（_no_silent_stop で包む）。
    - Google Sheets の read は alwaysOutputData（次の Code が「行が無い」で止める）。update は 0 件を返し得るので
      alwaysOutputData にし、次の sheetsUpdateCheckNode が「1 行を書いた」ことを確かめる。append は入力ごとに 1 行。
    - IF・Switch・Loop・エラー出力は、全部の出力をつなぐ。Switch は、どれにも当たらない item を最後の出力へ出す。
    - そのほかは、入力 1 件ごとに 1 件を出すノード（HTTP・Discord・ファイル・Wait・Drive のダウンロード）だけ。
    - 先の無いノードは、最後の Run: Check と、必ず例外にする Code だけ。
    """
    import generate_workflows as gw
    import connection_check as cc
    import prompt_eval as pe
    one_per_input = {"n8n-nodes-base." + t for t in (
        "httpRequest", "discord", "readWriteFile", "convertToFile", "extractFromFile", "wait")}
    error_path = {"Error Trigger", "Discord: Notify Error"}
    for wf in (gw.buildWorkflow(), pe.buildPromptEvalWorkflow(), cc.buildConnectionCheckWorkflow()):
        nodes = {n["name"]: n for n in wf["nodes"]}
        for name, n in nodes.items():
            t = n["type"]
            if t == "n8n-nodes-base.stickyNote" or name in error_path:
                continue
            where = (wf["name"], name)
            outs = [[c["node"] for c in o] for o in wf["connections"].get(name, {}).get("main", [])]
            if t == "n8n-nodes-base.code":
                js = n["parameters"]["jsCode"]
                assert js.startswith(gw.NO_SILENT_STOP_MARK), where
                if not any(outs):
                    body = gw.code_body(js)
                    assert name == "Run: Check" or ("throw new Error" in body and "return" not in body), where
                    continue
            elif t == "n8n-nodes-base.googleSheets":
                op = n["parameters"]["operation"]
                if op in ("read", "update"):
                    assert n.get("alwaysOutputData") is True, where
                if op == "update" and wf["name"] != "SHAMAN: Connection Check":
                    assert len(outs) == 1 and len(outs[0]) == 1, where
                    assert gw.SHEETS_UPDATE_CHECK_MARK in nodes[outs[0][0]]["parameters"]["jsCode"], where
            elif t == "n8n-nodes-base.if":
                assert len(outs) == 2, where
            elif t == "n8n-nodes-base.switch":
                assert n["parameters"]["options"].get("fallbackOutput") == "extra", where
                assert len(outs) == len(n["parameters"]["rules"]["values"]) + 1, where
            elif t == "n8n-nodes-base.splitInBatches":
                assert len(outs) == 2, where
            elif t == "n8n-nodes-base.googleDrive":
                assert n.get("alwaysOutputData") is True or n["parameters"].get("operation") == "download", where
            elif t in ("n8n-nodes-base.webhook", "n8n-nodes-base.manualTrigger", "n8n-nodes-base.scheduleTrigger"):
                # 定時トリガーもWebHook同様に1件の起動アイテムを出す。
                pass
            elif t == "n8n-nodes-base.noOp" and name == "Daily: No Work":
                # 対象0件を確認したときだけ、意図的に正常な無処理として終了。
                assert not any(outs), where
                continue
            else:
                assert t in one_per_input, where
            if n.get("onError") == "continueErrorOutput":
                assert len(outs) == 2, where
            assert outs and all(outs), ("出力の先が無い", where)


def test_production_keeps_execution_data():
    """本番も実行データを残す（n8n 全体の既定に任せない。2026-11-07、実行 8 のデータが見当たらなかった）。"""
    import generate_workflows as gw
    settings = gw.buildWorkflow()["settings"]
    assert settings["saveDataSuccessExecution"] == "all"
    assert settings["saveDataErrorExecution"] == "all"


# ── API使用量（2026-11-09、オーナーの依頼。DOCS/API_USAGE_LOG.md） ─────────────────
def _usage_wf():
    import generate_workflows as gw
    return gw, gw.buildWorkflow()


def test_api_usage_sheet_is_made_before_reading_the_queue():
    gw, w = _usage_wf()
    nodes = {n["name"]: n for n in w["nodes"]}
    t = lambda name, i=0: [c["node"] for c in w["connections"][name]["main"][i]]
    assert t("Webhook") == ["Usage: Ensure Sheet"]
    ensure = nodes["Usage: Ensure Sheet"]
    assert ensure["parameters"]["operation"] == "create" and ensure["parameters"]["title"] == gw.USAGE_SHEET
    assert ensure["parameters"]["documentId"]["value"] == gw.QUEUE_SPREADSHEET
    assert ensure["alwaysOutputData"] is True
    assert t("Usage: Ensure Sheet") == ["Usage: Sheet Ready"]
    assert t("Usage: Sheet Ready") == ["Usage: New Sheet?"]
    # 作ったときだけ見出しを書く。前からあれば何も書かずにキューへ
    assert t("Usage: New Sheet?", 0) == ["Usage: Header"] and t("Usage: New Sheet?", 1) == ["Queue: Read"]
    assert t("Usage: Header") == ["Usage: Header Write"] and t("Usage: Header Write") == ["Queue: Read"]
    assert nodes["Usage: Header Write"]["parameters"]["columns"]["mappingMode"] == "autoMapInputData"
    # シートの作成・見出しの書き込みに失敗しても、記事の処理は止めない（PR #82 レビュー）
    for name in ("Usage: Ensure Sheet", "Usage: Header Write"):
        assert nodes[name]["onError"] == "continueRegularOutput", name
        assert nodes[name]["retryOnFail"] is True, name
    # どのノードも、失敗でキューへの道を断たない（Usage: で始まるノードは、エラーで止まる設定を持たない）
    for name, n in nodes.items():
        if name.startswith("Usage: ") and n["type"] == "n8n-nodes-base.googleSheets":
            assert n.get("onError") == "continueRegularOutput", name
    for c in gw.USAGE_COLUMNS:
        assert gw.js_str(c) in nodes["Usage: Header"]["parameters"]["jsCode"], c


def test_every_api_call_is_recorded_in_line():
    gw, w = _usage_wf()
    nodes = {n["name"]: n for n in w["nodes"]}
    t = lambda name: [c["node"] for c in w["connections"][name]["main"][0]]
    apis = [n["name"] for n in w["nodes"] if n["type"] == "n8n-nodes-base.httpRequest"
            and set(n.get("credentials") or {}) & {"googlePalmApi", "anthropicApi", "openAiApi"}]
    assert sorted(apis) == sorted(api for _, _, api in gw.USAGE_CALLS)
    for step, build, api in gw.USAGE_CALLS:
        assert t(build) == ["Usage: Stamp " + step]
        assert t("Usage: Stamp " + step) == [api]
        assert t(api) == ["Usage: Row " + step]
        assert t("Usage: Row " + step) == ["Usage: Write " + step]
        assert t("Usage: Write " + step) == ["Usage: Pass " + step]
        write = nodes["Usage: Write " + step]
        assert write["parameters"]["sheetName"]["value"] == gw.USAGE_SHEET
        assert set(write["parameters"]["columns"]["value"]) == set(gw.USAGE_COLUMNS)
        # 記録の失敗で記事を止めない
        assert write["onError"] == "continueRegularOutput" and write["retryOnFail"] is True
        assert "return $(" + gw.js_str(api) + ").all();" in nodes["Usage: Pass " + step]["parameters"]["jsCode"]
    assert t("Usage: Pass P4") == ["Parse Strengthened"]
    assert t("Usage: Pass P6") == ["Collect Generated Images"]


# ===========================================================================
# 2026-11-09 ハルシネーション対策（DOCS/P1_KNOWLEDGE.md）
# P1 だけが検索する。P2〜P4 は P1 のナレッジだけを根拠にし、検索・事実・体験・内部リンクを作らない。
# ===========================================================================
class TestGroundedKnowledge:
    def _code(self, gw, name):
        return {n["name"]: n for n in gw.buildWorkflow()["nodes"]}[name]["parameters"]["jsCode"]

    def test_only_p1_has_the_search_tool(self, gw):
        assert "googleSearch" in self._code(gw, "Build P1 Request")
        for name in ("Build P2 Request", "Build P3 Request", "Build P4 Request", "Build Section Analysis Request"):
            assert "googleSearch" not in self._code(gw, name), name
            assert "tools" not in self._code(gw, name), name

    @pytest.mark.parametrize("name", ["P2_T2", "P3_RAW", "P4_RAW", "P4_REPAIR_RAW"])
    def test_later_steps_are_not_told_to_search(self, name):
        text = getattr(prompts, name)
        for phrase in ("WEB検索したとき", "自律的にWEB", "WEBで情報を収集", "WEB検索して", "検索してください"):
            assert phrase not in text, (name, phrase)



    def test_p1_knowledge_reaches_p2_p3_p4(self, gw):
        assert "data.p1Knowledge" in self._code(gw, "Build P2 Request")
        for name, prompt in (("Build P3 Request", "P3_RAW"), ("Build P4 Request", "P4_RAW"), ("Build P4 Request", "P4_REPAIR_RAW")):
            assert "${P1_KNOWLEDGE}" in getattr(prompts, prompt), prompt
            assert "split('${P1_KNOWLEDGE}').join(data.p1Knowledge" in self._code(gw, name), name
        # P2・P3 の Parse はナレッジを消さない（P4 まで届く）
        for name in ("Parse Outline", "Parse Content"):
            assert "p1Knowledge" not in self._code(gw, name), name


    def test_p1_sources_come_from_grounding_metadata(self, gw):
        code = self._code(gw, "Parse Research")
        for key in ("groundingMetadata", "groundingChunks", "webSearchQueries", "groundingSupports", "p1Sources", "p1ResearchStatus"):
            assert key in code, key






    def test_p2_heading_gate_in_production(self, gw):
        w = gw.buildWorkflow()
        conn = w["connections"]
        assert [c["node"] for c in conn["Parse Outline"]["main"][0]] == ["P2: Retry?"]
        assert [c["node"] for c in conn["P2: Retry?"]["main"][0]] == ["Build P2 Request"]
        assert [c["node"] for c in conn["P2: Retry?"]["main"][1]] == ["Build P3 Request"]
        code = {n["name"]: n for n in w["nodes"]}["Parse Outline"]["parameters"]["jsCode"]
        assert "SHAMAN_RUN_NG: P2 を " in code
        assert prompts.P2_RETRY_PREFIX in {n["name"]: n for n in w["nodes"]}["Build P2 Request"]["parameters"]["jsCode"]



    def test_p4_model_and_gate_are_unchanged(self, gw):
        assert gw.MODEL_CLAUDE == "claude-opus-5-5"
        code = self._code(gw, "Build P4 Request")
        assert "max_tokens: 96000" in code and "effort: 'high'" in code
        gate = self._code(gw, "Parse Strengthened")
        assert "attempt < 2" in gate and "700" in gate
        assert "siteRefsIn(html)" in gate and "stripUrls(html)" in gate

    def test_p1_status_reaches_queue_and_discord(self, gw):
        assert "data.p1ResearchStatus" in self._code(gw, "Cleanup")
        nodes = {n["name"]: n for n in gw.buildWorkflow()["nodes"]}
        assert "$json.p1ResearchStatus" in json.dumps(nodes["Discord: Notify Success"]["parameters"], ensure_ascii=False)

