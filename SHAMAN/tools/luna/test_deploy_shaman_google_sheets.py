"""SHAMAN n8n APIデプロイの副作用なし検証。外部接続・Secretsは不要。"""
import copy
import json
import sys
from pathlib import Path
import tempfile
import unittest

import deploy_shaman_google_sheets as d

# sites.json のサイトの WordPress の Credential：{変数: n8n の名前}。サイトを足しても試験が追いつくようにする。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import generate_workflows as gw  # noqa: E402
SITES = gw.load_sites()
WP_VARS = {"SHAMAN_WORDPRESS_MULTISITE_CREDENTIAL_ID": "SHAMAN / WordPress / MultiSite"}
WP_ENV = {"SHAMAN_WORDPRESS_MULTISITE_CREDENTIAL_ID": "wp-multisite-id"}
WP_CREDENTIALS = [{"id": "wp-multisite-id", "name": "SHAMAN / WordPress / MultiSite", "type": "httpBasicAuth"}]


class FakeClient:
    def __init__(self):
        self.entries = []
        self.store = {}
        self.calls = []

    def list_workflows(self):
        return self.entries

    def request(self, method, path, data=None):
        self.calls.append((method, path, data))
        if method == "POST" and path == "/workflows":
            self.entries.append({"name": data["name"], "id": "new-id", "active": False})
            self.store["new-id"] = {**copy.deepcopy(data), "id": "new-id",
                                    "active": False, "versionId": "version-123"}
            return {"id": "new-id"}
        if method == "PUT":
            self.store[path.split("?")[0].rsplit("/", 1)[-1]].update(copy.deepcopy(data))
            return {}
        if method == "GET":
            return self.store[path.rsplit("/", 1)[-1]]
        if method == "POST" and path.endswith("/publish"):
            self.store[path.split("/")[2]].update(active=True, activeVersionId="version-123")
            return {}
        raise AssertionError((method, path))


class TestDeployment(unittest.TestCase):
    def setUp(self):
        self.env = {
            "N8N_DEPLOY_API_KEY": "dummy-secret",
            "SHAMAN_DRAFT_DIR": "/Users/example/.local/share/shaman-n8n-files/drafts",
            "SHAMAN_GOOGLE_SHEETS_CREDENTIAL_ID": "credential-id",
            "SHAMAN_QUEUE_SPREADSHEET_ID": "prod-sheet",
            "SHAMAN_QUEUE_TEST_SPREADSHEET_ID": "test-sheet",
        }
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        # mode "test" はプロンプト評価 Workflow（DOCS/PROMPT_EVAL_DESIGN.md）。評価のシートだけを読み書きする。
        self.file = self.root / "SHAMAN_Prompt_Eval.json"
        self.workflow = {
            "name": "SHAMAN: Prompt Eval", "id": "shaman_test",
            "settings": {"executionOrder": "v1"},
            "nodes": self.sheet_nodes(sorted(d.SHEET_NODES["test"]), "__ENV:SHAMAN_QUEUE_TEST_SPREADSHEET_ID__"),
            "connections": {},
        }
        self.file.write_text(json.dumps(self.workflow), encoding="utf-8")

    def test_api_key_must_be_present(self):
        with self.assertRaisesRegex(RuntimeError, "N8N_DEPLOY_API_KEY"):
            d.validate_env({**self.env, "N8N_DEPLOY_API_KEY": ""}, "test", False)

    def test_api_must_be_local(self):
        for url in ("http://evil.example:5678", "http://127.0.0.1:9999", "https://127.0.0.1:5678"):
            with self.subTest(url=url), self.assertRaises(RuntimeError):
                d.validate_env({**self.env, "N8N_BASE_URL": url}, "test", False)

    def test_every_workflow_must_be_published(self):
        """Webhook は公開しないと受け付けない。どの Workflow も --publish で配備する（DOCS/RUN_FROM_ACTIONS.md）。"""
        for mode in d.WORKFLOWS:
            with self.subTest(mode=mode), self.assertRaisesRegex(RuntimeError, "--publish"):
                d.validate_env(self.env, mode, False)

    def test_production_requires_publish_gate(self):
        with self.assertRaises(RuntimeError):
            d.validate_env(self.env, "production", False)

    def production_event(self):
        return {
            "action": "closed",
            "sender": {"login": "repo-owner", "type": "User"},
            "pull_request": {
                "merged": True,
                "merged_by": {"login": "repo-owner", "type": "User"},
                "base": {"ref": "main"},
                "head": {"sha": "head"},
                "merge_commit_sha": "merge",
            },
        }

    def production_env(self):
        event_path = self.root / "merge-event.json"
        event_path.write_text(json.dumps(self.production_event()), encoding="utf-8")
        return {
            **self.env, "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REF": "refs/heads/main", "GITHUB_REPOSITORY": "repo-owner/SHAMAN",
            "GITHUB_EVENT_PATH": str(event_path),
            "SHAMAN_MERGED_COMMIT_SHA": "merge", "GITHUB_SHA": "merge",
            "SHAMAN_MERGED_PR_HEAD_SHA": "head",
        }

    def test_publish_gate_checks_real_merged_sha(self):
        """公開は、オーナーが main にマージした PR のイベントからだけ。E2E の Variable は要らない（実行は Actions から）。"""
        good = self.production_env()
        for mode in d.WORKFLOWS:
            self.assertEqual(d.validate_env(good, mode, True), "http://127.0.0.1:5678")
            for key in ("GITHUB_REF", "GITHUB_REPOSITORY", "GITHUB_EVENT_PATH", "GITHUB_SHA",
                        "SHAMAN_MERGED_PR_HEAD_SHA"):
                with self.subTest(mode=mode, key=key), self.assertRaises(RuntimeError):
                    d.validate_env({**good, key: "wrong"}, mode, True)

    def test_production_rejects_non_owner_merge_events(self):
        good = self.production_env()
        for field, value in (
                ("action", "opened"), ("merged", False),
                ("merged_by", {"login": "someone-else", "type": "User"}),
                ("sender", {"login": "someone-else", "type": "User"}),
                ("base", {"ref": "other"}),
                ("head", {"sha": "wrong"}),
                ("merge_commit_sha", "wrong")):
            with self.subTest(field=field):
                event = self.production_event()
                if field in ("action", "sender"):
                    event[field] = value
                else:
                    event["pull_request"][field] = value
                Path(good["GITHUB_EVENT_PATH"]).write_text(json.dumps(event), encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "verified repository owner"):
                    d.validate_env(good, "production", True)

    def test_placeholders_are_replaced_without_including_api_key(self):
        payload = d.load_workflow(self.file, "test", self.env)
        serialized = json.dumps(payload)
        self.assertNotIn("__ENV:", serialized)
        self.assertIn("test-sheet", serialized)
        self.assertNotIn("dummy-secret", serialized)

    def test_unknown_placeholder_is_rejected(self):
        self.workflow["nodes"][0]["parameters"]["extra"] = "__ENV:UNEXPECTED__"
        self.file.write_text(json.dumps(self.workflow))
        with self.assertRaisesRegex(RuntimeError, "Unknown"):
            d.load_workflow(self.file, "test", self.env)

    def test_actual_production_workflow_sheet_nodes_match_deploy_contract(self):
        """実際の生成JSONで配備前のGoogle Sheetsノード一覧を検証する。"""
        root = Path(__file__).resolve().parents[2]
        workflow = json.loads((root / "workflows" / "SHAMAN_Workflow.json").read_text(encoding="utf-8"))
        nodes = [n for n in workflow["nodes"] if n["type"] == "n8n-nodes-base.googleSheets"]
        self.assertEqual(len(nodes), len(d.SHEET_NODES["production"]))
        self.assertEqual({n["name"] for n in nodes}, d.SHEET_NODES["production"])
        registry = next(n for n in nodes if n["name"] == "Sites: Read Registry")
        self.assertIs(registry.get("executeOnce"), True)
        self.assertEqual(registry["parameters"]["sheetName"]["value"], "サイト登録")
        self.assertEqual(registry["parameters"]["operation"], "read")

    def test_production_registry_missing_is_rejected(self):
        """登録照合ノードが生成JSONから消えた場合は配備前に失敗する。"""
        path = self.production_file()
        data = json.loads(path.read_text(encoding="utf-8"))
        data["nodes"] = [n for n in data["nodes"] if n["name"] != "Sites: Read Registry"]
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "Google Sheets queue nodes"):
            d.load_workflow(path, "production", self.env)

    def test_missing_google_sheets_node_is_rejected(self):
        self.workflow["nodes"] = [
            n for n in self.workflow["nodes"] if n["name"] != "Eval: Claim Run"]
        self.file.write_text(json.dumps(self.workflow))
        with self.assertRaisesRegex(RuntimeError, "Google Sheets queue nodes"):
            d.load_workflow(self.file, "test", self.env)

    def test_test_workflow_cannot_point_at_production_sheet(self):
        self.workflow["nodes"][0]["parameters"]["documentId"]["value"] = "__ENV:SHAMAN_QUEUE_SPREADSHEET_ID__"
        self.file.write_text(json.dumps(self.workflow))
        with self.assertRaisesRegex(RuntimeError, "not isolated"):
            d.load_workflow(self.file, "test", self.env)

    @staticmethod
    def sheet_nodes(names, document):
        return [{"name": name, "type": "n8n-nodes-base.googleSheets",
                 "parameters": {"documentId": {"value": document}},
                 "credentials": {"googleApi": {"id": "__ENV:SHAMAN_GOOGLE_SHEETS_CREDENTIAL_ID__"}}}
                for name in names]

    def production_nodes(self):
        """本番のキューのノードと途中保存のノード（シートは試験用の ID のまま。各試験で付け替える）。"""
        return [*self.sheet_nodes(sorted(d.SHEET_NODES["production"]), "__ENV:SHAMAN_QUEUE_TEST_SPREADSHEET_ID__"),
                {"name": "Read Draft", "type": "n8n-nodes-base.readWriteFile",
                 "parameters": {"fileSelector": "=__ENV:SHAMAN_DRAFT_DIR__/article_draft_{{ $json.rowIndex }}.json"}}]

    def production_file(self):
        prod = {**self.workflow, "name": "SHAMAN", "id": "shaman", "nodes": self.production_nodes()}
        for node in prod["nodes"]:
            if node["type"] == "n8n-nodes-base.googleSheets":
                node["parameters"]["documentId"]["value"] = "__ENV:SHAMAN_QUEUE_SPREADSHEET_ID__"
        path = self.root / "SHAMAN_Workflow.json"
        path.write_text(json.dumps(prod))
        return path

    def test_prompt_eval_rejects_queue_sheet_nodes(self):
        """評価はキューを読まない。キューのノード（前の Test Workflow の形）があれば止める。"""
        self.workflow["nodes"] += self.sheet_nodes(["Queue: Read"], "__ENV:SHAMAN_QUEUE_TEST_SPREADSHEET_ID__")
        self.file.write_text(json.dumps(self.workflow))
        with self.assertRaisesRegex(RuntimeError, "Google Sheets queue nodes"):
            d.load_workflow(self.file, "test", self.env)

    def test_webhook_without_run_token_is_rejected_in_every_workflow(self):
        """公開するので、合言葉（Header Auth の Run Webhook の Credential）を確かめない Webhook は配備しない。"""
        bad = (
            {"name": "Open", "type": "n8n-nodes-base.webhook", "parameters": {"path": "x"}},
            {"name": "Other auth", "type": "n8n-nodes-base.webhook", "parameters": {"authentication": "headerAuth"},
             "credentials": {"httpHeaderAuth": {"id": "__ENV:SHAMAN_OTHER_CREDENTIAL_ID__"}}},
        )
        for extra in bad:
            with self.subTest(node=extra["name"]):
                self.file.write_text(json.dumps({**self.workflow, "nodes": self.workflow["nodes"] + [extra]}))
                with self.assertRaisesRegex(RuntimeError, "run token"):
                    d.load_workflow(self.file, "test", self.env)

    def test_prompt_eval_rejects_wordpress_discord_drive_and_images(self):
        extras = [
            {"name": "WP 1: Create Post", "type": "n8n-nodes-base.httpRequest",
             "credentials": {"httpBasicAuth": {"id": "x"}}},
            {"name": "Discord", "type": "n8n-nodes-base.discord", "credentials": {"discordWebhookApi": {"id": "x"}}},
            {"name": "Drive", "type": "n8n-nodes-base.googleDrive"},
            {"name": "Image", "type": "n8n-nodes-base.httpRequest", "credentials": {"openAiApi": {"id": "x"}}},
        ]
        for extra in extras:
            with self.subTest(node=extra["name"]):
                self.file.write_text(json.dumps({**self.workflow, "nodes": self.workflow["nodes"] + [extra]}))
                with self.assertRaisesRegex(RuntimeError, "must not touch"):
                    d.load_workflow(self.file, "test", self.env)

    def test_production_workflow_cannot_point_at_test_sheet(self):
        self.workflow["name"] = "SHAMAN"
        self.workflow["id"] = "shaman"
        self.workflow["nodes"] = self.production_nodes()
        production = self.root / "SHAMAN_Workflow.json"
        production.write_text(json.dumps(self.workflow))
        with self.assertRaisesRegex(RuntimeError, "not isolated"):
            d.load_workflow(production, "production", self.env)

    def test_missing_credential_variable_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "SHAMAN_GOOGLE_SHEETS_CREDENTIAL_ID"):
            d.load_workflow(self.file, "test", {**self.env, "SHAMAN_GOOGLE_SHEETS_CREDENTIAL_ID": ""})

    def test_invalid_draft_path_is_rejected(self):
        prod_file = self.production_file()
        for bad in ("/tmp/shaman", "relative", "/Users/example/../other"):
            with self.subTest(path=bad), self.assertRaisesRegex(RuntimeError, "SHAMAN_DRAFT_DIR"):
                d.load_workflow(prod_file, "production", {**self.env, "SHAMAN_DRAFT_DIR": bad})

    def test_create_test_draft_only(self):
        client = FakeClient()
        payload = d.load_workflow(self.file, "test", self.env)
        d.deploy(client, payload, "test", False)
        self.assertFalse(client.store["new-id"]["active"])
        self.assertFalse(any("/publish" in path for _, path, _ in client.calls))

    def test_inactive_legacy_test_is_reused_in_place(self):
        for legacy in ("SHAMAN: Test Workflow", "SHAMAN / Blog / Pipeline Test"):
            with self.subTest(legacy=legacy):
                client = FakeClient()
                client.entries.append({"name": legacy, "id": "old"})
                client.store["old"] = {"name": legacy, "active": False}
                d.deploy(client, d.load_workflow(self.file, "test", self.env), "test", False)
                # 前の Test Workflow を、同じ ID のままプロンプト評価に置き換える（Workflow を増やさない）。
                self.assertEqual(client.store["old"]["name"], "SHAMAN: Prompt Eval")
                self.assertTrue(any(method == "PUT" for method, _, _ in client.calls))
                self.assertFalse(any(method == "POST" for method, _, _ in client.calls))

    def test_current_and_legacy_test_coexistence_is_rejected(self):
        client = FakeClient()
        client.entries += [
            {"name": "SHAMAN: Prompt Eval", "id": "new"},
            {"name": "SHAMAN: Test Workflow", "id": "old"},
        ]
        with self.assertRaisesRegex(RuntimeError, "coexist"):
            d.deploy(client, d.load_workflow(self.file, "test", self.env), "test", False)

    def test_published_legacy_test_is_not_modified(self):
        client = FakeClient()
        client.entries.append({"name": "SHAMAN / Blog / Pipeline Test", "id": "old"})
        client.store["old"] = {"name": "SHAMAN / Blog / Pipeline Test", "active": True}
        with self.assertRaisesRegex(RuntimeError, "[Pp]ublished"):
            d.deploy(client, d.load_workflow(self.file, "test", self.env), "test", False)
        self.assertFalse(any(method in ("PUT", "POST") for method, _, _ in client.calls))

    def test_legacy_production_is_not_modified_if_active(self):
        client = FakeClient()
        client.entries.append({"name": "SHAMAN / Blog / Pipeline", "id": "old"})
        client.store["old"] = {"name": "SHAMAN / Blog / Pipeline", "active": True}
        with self.assertRaisesRegex(RuntimeError, "[Pp]ublished"):
            d.deploy(client, {"name": "SHAMAN"}, "production", True)
        self.assertFalse(any(method in ("PUT", "POST") for method, _, _ in client.calls))

    def test_published_test_workflow_is_updated_and_republished(self):
        client = FakeClient()
        client.entries.append({"name": "SHAMAN: Prompt Eval", "id": "abc"})
        client.store["abc"] = {"name": "SHAMAN: Prompt Eval", "active": True, "versionId": "version-123"}
        d.deploy(client, d.load_workflow(self.file, "test", self.env), "test", True)
        self.assertTrue(any(path.endswith("?publishIfActive=false") for _, path, _ in client.calls))
        self.assertIn(("POST", "/workflows/abc/publish", {"versionId": "version-123"}), client.calls)

    def test_existing_test_draft_uses_nonpublishing_update(self):
        client = FakeClient()
        client.entries.append({"name": "SHAMAN: Prompt Eval", "id": "abc"})
        client.store["abc"] = {"name": "SHAMAN: Prompt Eval", "active": False}
        d.deploy(client, d.load_workflow(self.file, "test", self.env), "test", False)
        self.assertTrue(any(path.endswith("?publishIfActive=false") for _, path, _ in client.calls))

    def test_empty_sites_json_refuses_production(self):
        prod = {**copy.deepcopy(self.workflow), "name": "SHAMAN", "id": "shaman",
                "nodes": copy.deepcopy(self.production_nodes())}
        for node in prod["nodes"]:
            if node["type"] == "n8n-nodes-base.googleSheets":
                node["parameters"]["documentId"]["value"] = "__ENV:SHAMAN_QUEUE_SPREADSHEET_ID__"
        wfdir = self.root / "SHAMAN" / "workflows"
        wfdir.mkdir(parents=True)
        prod_file = wfdir / "SHAMAN_Workflow.json"
        prod_file.write_text(json.dumps(prod))
        config = self.root / "SHAMAN" / "config"
        config.mkdir(parents=True)
        (config / "sites.json").write_text("{}")
        with self.assertRaisesRegex(RuntimeError, "sites.json is empty"):
            d.load_workflow(prod_file, "production", self.env)



class TestPromptEvalDeployment(unittest.TestCase):
    """プロンプト評価 Workflow（生成済みの JSON）の配備：未公開で配備し、LLM の Credential と試験用シートだけを使う。"""

    def setUp(self):
        self.env = {
            "N8N_DEPLOY_API_KEY": "dummy-secret",
            "SHAMAN_QUEUE_TEST_SPREADSHEET_ID": "test-sheet",
            "SHAMAN_GOOGLE_SHEETS_CREDENTIAL_ID": "sheets-id",
            "SHAMAN_GEMINI_CREDENTIAL_ID": "gemini-id",
            "SHAMAN_ANTHROPIC_CREDENTIAL_ID": "anthropic-id",
            # P6（画像を 1 枚作る）：参考画像を Drive から読み（シートと同じサービスアカウント）、OpenAI で作る
            "SHAMAN_OPENAI_CREDENTIAL_ID": "openai-id",
            "SHAMAN_RUN_WEBHOOK_CREDENTIAL_ID": "run-id",
            **WP_ENV,
        }
        self.file = Path(__file__).resolve().parents[2] / "workflows" / "SHAMAN_Prompt_Eval.json"
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_generated_workflow_is_published_with_only_llm_sheet_and_run_webhook_ids(self):
        payload = d.load_workflow(self.file, "test", self.env)
        text = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("__ENV:", text)
        self.assertNotIn("dummy-secret", text)
        cred_types = {t for n in payload["nodes"] for t in (n.get("credentials") or {})}
        self.assertEqual(cred_types,
                         {"googlePalmApi", "anthropicApi", "googleApi", "httpHeaderAuth", "openAiApi", "httpBasicAuth"})
        client = FakeClient()
        d.deploy(client, payload, "test", True)
        self.assertTrue(client.store["new-id"]["active"])
        self.assertTrue(any(path.endswith("/publish") for _m, path, _d in client.calls))

    def test_prompt_eval_publish_requires_merge_event(self):
        with self.assertRaisesRegex(RuntimeError, "merged PR event"):
            d.validate_env(self.env, "test", True)

    def test_prompt_eval_needs_no_production_ids(self):
        """本番のシート・途中保存・Discord の ID は使わない。WordPressは読取とテスト用下書きのみ。"""
        payload = d.load_workflow(self.file, "test", self.env)
        self.assertNotIn("prod-sheet", json.dumps(payload))

    def test_prompt_eval_refuses_drive_writes_and_openai_outside_p6(self):
        """Drive は読むだけ、OpenAI は P6: Generate Image だけ（2026-11-09）。"""
        base = json.loads(self.file.read_text(encoding="utf-8"))
        for change, label in (("upload", "drive"), ("openai", "openai")):
            wf = copy.deepcopy(base)
            if change == "upload":
                node = next(n for n in wf["nodes"] if n["type"] == "n8n-nodes-base.googleDrive")
                node["parameters"]["operation"] = "upload"
            else:
                node = next(n for n in wf["nodes"] if n["name"] == "Build P4 Request")
                node["credentials"] = {"openAiApi": {"id": "__ENV:SHAMAN_OPENAI_CREDENTIAL_ID__", "name": "x"}}
            folder = Path(self.tmp.name) / label
            folder.mkdir()
            path = folder / self.file.name
            path.write_text(json.dumps(wf), encoding="utf-8")
            with self.subTest(label), self.assertRaisesRegex(RuntimeError, "write to Drive or call OpenAI outside P6"):
                d.load_workflow(path, "test", self.env)

class TestConnectionCheckDeployment(unittest.TestCase):
    """接続確認 Workflow（生成済みの JSON）の配備：未公開で配備し、試験用シートだけを使う。"""

    def setUp(self):
        self.env = {
            "N8N_DEPLOY_API_KEY": "dummy-secret",
            "SHAMAN_QUEUE_TEST_SPREADSHEET_ID": "test-sheet",
            "SHAMAN_GOOGLE_SHEETS_CREDENTIAL_ID": "sheets-id",
            "SHAMAN_GEMINI_CREDENTIAL_ID": "gemini-id",
            "SHAMAN_ANTHROPIC_CREDENTIAL_ID": "anthropic-id",
            "SHAMAN_OPENAI_CREDENTIAL_ID": "openai-id",
            "SHAMAN_DISCORD_CREDENTIAL_ID": "discord-id",
            "SHAMAN_RUN_WEBHOOK_CREDENTIAL_ID": "run-id",
            **WP_ENV,
        }
        self.file = Path(__file__).resolve().parents[2] / "workflows" / "SHAMAN_Connection_Check.json"

    def test_generated_workflow_is_published_with_ids(self):
        payload = d.load_workflow(self.file, "connection", self.env)
        text = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("__ENV:", text)
        self.assertNotIn("dummy-secret", text)
        creds = {n["name"]: n.get("credentials") for n in payload["nodes"]}
        self.assertEqual(creds["Conn: Gemini Models"], {"googlePalmApi": {"id": "gemini-id", "name": "Google / Gemini"}})
        self.assertEqual(creds["Conn: Discord Notify"]["discordWebhookApi"]["id"], "discord-id")
        # SHAMAN/IMG の ID は config/drive.json から生成時に入る（Repository Variable ではない）。
        folder = json.loads((self.file.parents[1] / "config" / "drive.json").read_text(encoding="utf-8"))
        self.assertIn("'" + folder["IMG_FOLDER_ID"] + "' in parents", text)
        self.assertEqual(creds["Run Webhook"]["httpHeaderAuth"]["id"], "run-id")
        client = FakeClient()
        d.deploy(client, payload, "connection", True)
        self.assertTrue(client.store["new-id"]["active"])
        self.assertTrue(any(path.endswith("/publish") for _m, path, _d in client.calls))

    def test_connection_check_publish_requires_merge_event(self):
        with self.assertRaisesRegex(RuntimeError, "merged PR event"):
            d.validate_env(self.env, "connection", True)

    def test_missing_credential_variable_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "SHAMAN_OPENAI_CREDENTIAL_ID"):
            d.load_workflow(self.file, "connection", {**self.env, "SHAMAN_OPENAI_CREDENTIAL_ID": ""})

    def test_id_with_quote_is_rejected(self):
        """JSON に埋め込むので、引用符などを含む ID は配備しない。"""
        with self.assertRaisesRegex(RuntimeError, "plain IDs"):
            d.load_workflow(self.file, "connection", {**self.env, "SHAMAN_GEMINI_CREDENTIAL_ID": "a b"})

    def test_connection_check_cannot_point_at_production_sheet(self):
        workflow = json.loads(self.file.read_text(encoding="utf-8"))
        for n in workflow["nodes"]:
            if n["type"] == "n8n-nodes-base.googleSheets":
                n["parameters"]["documentId"]["value"] = "__ENV:SHAMAN_QUEUE_SPREADSHEET_ID__"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SHAMAN_Connection_Check.json"
            path.write_text(json.dumps(workflow), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "isolated"):
                d.load_workflow(path, "connection", {**self.env, "SHAMAN_QUEUE_SPREADSHEET_ID": "prod"})

    def test_wordpress_site_credential_placeholder_is_accepted(self):
        workflow = json.loads(self.file.read_text(encoding="utf-8"))
        workflow["nodes"].append({"name": "Conn: WordPress Extra", "type": "n8n-nodes-base.httpRequest", "parameters": {},
                                  "credentials": {"httpBasicAuth": {"id": "__ENV:SHAMAN_WORDPRESS_MULTISITE_CREDENTIAL_ID__", "name": "SHAMAN / WordPress / MultiSite"}}})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SHAMAN_Connection_Check.json"
            path.write_text(json.dumps(workflow), encoding="utf-8")
            missing = {key: value for key, value in self.env.items() if key != "SHAMAN_WORDPRESS_MULTISITE_CREDENTIAL_ID"}
            with self.assertRaisesRegex(RuntimeError, "SHAMAN_WORDPRESS_MULTISITE_CREDENTIAL_ID"):
                d.load_workflow(path, "connection", missing)
            payload = d.load_workflow(path, "connection", {**self.env, "SHAMAN_WORDPRESS_MULTISITE_CREDENTIAL_ID": "wp-id"})
        self.assertEqual(payload["nodes"][-1]["credentials"]["httpBasicAuth"]["id"], "wp-id")

    def test_same_test_and_production_sheet_is_rejected(self):
        env = {**self.env, "SHAMAN_QUEUE_SPREADSHEET_ID": "test-sheet"}
        with self.assertRaisesRegex(RuntimeError, "must differ"):
            d.load_workflow(self.file, "connection", env)

    def test_ci_passes_every_site_wordpress_credential_variable(self):
        """WordPress の Credential ID も n8n から取る。Repository Variable で渡さない。"""
        import sys
        sys.path.insert(0, str(self.file.parents[1]))
        import generate_workflows as gw
        yml = (self.file.parents[2] / ".github" / "workflows" / "deploy-shaman-n8n.yml").read_text(encoding="utf-8")
        for var in WP_VARS:
            self.assertNotIn("vars." + var, yml)
        self.assertNotRegex(yml, r"vars\.SHAMAN_[A-Z0-9_]*(?:CREDENTIAL_ID|IMG_FOLDER_ID)")

    def test_every_deploy_job_receives_the_ids_its_workflow_needs(self):
        """配備するジョブに、その Workflow の JSON が参照する値（__ENV:…__）が渡されている。Credential の ID は除く（n8n から取る）。"""
        import re
        root = self.file.parents[2]
        yml = (root / ".github" / "workflows" / "deploy-shaman-n8n.yml").read_text(encoding="utf-8")
        body = yml.split("\njobs:\n", 1)[1]
        pattern = r"^  ([A-Za-z0-9_-]+):\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)"
        checked = 0
        for job, text in re.findall(pattern, body, re.M | re.S):
            for wf_path in re.findall(r"--workflow (SHAMAN/workflows/\S+\.json)", text):
                needed = set(d.EXPRESSION.findall((root / wf_path).read_text(encoding="utf-8")))
                self.assertTrue(needed, wf_path)
                for var in sorted(needed):
                    with self.subTest(job=job, workflow=wf_path, var=var):
                        if d.CREDENTIAL_PLACEHOLDER.fullmatch(var):
                            self.assertNotIn("vars." + var, text)
                        else:
                            self.assertIn(var + ": ${{ vars." + var + " }}", text)
                checked += 1
        self.assertEqual(checked, 3)  # Test・接続確認・本番

    def test_inline_python_checks_in_the_deploy_pass_on_the_generated_workflows(self):
        """配備の yml の中で JSON を確かめる python3 -c を、今の生成物で実際に動かす（#83 の配備が、
        Sheets ノードの一覧を固定で比べる確認で止まった。2026-11-09）。"""
        import re, shlex, subprocess, sys
        root = self.file.parents[2]
        yml = (root / ".github" / "workflows" / "deploy-shaman-n8n.yml").read_text(encoding="utf-8")
        checks = re.findall(r"^\s*python3 -c '([^'\n]*)'\s*$", yml, re.M)
        self.assertTrue(checks)
        for src in checks:
            with self.subTest(check=src[:80]):
                done = subprocess.run([sys.executable, "-c", src], cwd=root, capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stderr[-1000:])

    def test_connection_check_deploys_in_its_own_job(self):
        """旧 Test Workflow の配備が失敗しても、接続確認 Workflow は配備される（PR #64 のレビュー）。"""
        import re
        yml = (self.file.parents[2] / ".github" / "workflows" / "deploy-shaman-n8n.yml").read_text(encoding="utf-8")
        body = yml.split("\njobs:\n", 1)[1]
        pattern = r"^  ([A-Za-z0-9_-]+):\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)"
        jobs = dict(re.findall(pattern, body, re.M | re.S))
        self.assertNotIn("--mode connection", jobs["stage-test"])
        job = jobs["deploy-connection-check"]
        self.assertIn("--mode connection", job)
        self.assertNotIn("needs:", job)
        self.assertIn("--publish", job)
        self.assertNotIn("SHAMAN_CONNECTION_CHECK_READY", job)
        for gate in ("github.event.pull_request.merged == true",
                     "github.event.pull_request.base.ref == 'main'",
                     "github.event.pull_request.merged_by.login == github.repository_owner",
                     "github.event.sender.login == github.repository_owner"):
            self.assertIn(gate, job)
        self.assertIn('RUNNER_NAME:-}" != "luna-shaman-deploy"', job)
        self.assertIn("git diff --exit-code -- SHAMAN/workflows/SHAMAN_Connection_Check.json", job)

    def test_every_deploy_job_publishes_after_an_owner_merge_without_manual_gates(self):
        """3 つとも公開して配備する。手で設定する Variable（E2E 済みの SHA・READY）を条件にしない。"""
        yml = (self.file.parents[2] / ".github" / "workflows" / "deploy-shaman-n8n.yml").read_text(encoding="utf-8")
        self.assertEqual(yml.count("--publish"), 3)
        for gone in ("SHAMAN_E2E_PASSED_SHA", "SHAMAN_PRODUCTION_READY", "SHAMAN_CONNECTION_CHECK_READY"):
            self.assertNotIn(gone, yml)
        self.assertEqual(yml.count("SHAMAN_MERGED_COMMIT_SHA: ${{ github.event.pull_request.merge_commit_sha }}"), 3)
        self.assertEqual(yml.count("github.event.pull_request.merged_by.login == github.repository_owner"), 3)

    def test_published_connection_check_is_updated_and_republished(self):
        payload = d.load_workflow(self.file, "connection", self.env)
        client = FakeClient()
        client.entries = [{"name": "SHAMAN: Connection Check", "id": "c1"}]
        client.store["c1"] = {"name": "SHAMAN: Connection Check", "active": True, "versionId": "version-123"}
        d.deploy(client, payload, "connection", True)
        self.assertIn(("POST", "/workflows/c1/publish", {"versionId": "version-123"}), client.calls)
        self.assertTrue(client.store["c1"]["active"])


if __name__ == "__main__":
    unittest.main()


class CredentialClient(FakeClient):
    def __init__(self, credentials, status=None):
        super().__init__()
        self.credentials = credentials
        self.status = status

    def list_credentials(self):
        if self.status:
            raise RuntimeError(self.status)
        return self.credentials


class TestCredentialsFromN8n(unittest.TestCase):
    """Credential の ID は、n8n の Credential の一覧から、種類で 1 件に決める（同じ種類が 2 件以上のときだけ名前で選ぶ。Repository Variable に登録しない）。"""

    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        self.workflow = json.loads((root / "workflows" / "SHAMAN_Connection_Check.json").read_text(encoding="utf-8"))
        self.all = [
            {"id": "sheets-id", "name": "Google / Sheets", "type": "googleApi"},
            # 使わなくなった OAuth2 の Drive の Credential が n8n に残っていても使わない（Drive は Google / Sheets で読む）。
            {"id": "drive-id", "name": "SHAMAN / Google Drive", "type": "googleDriveOAuth2Api"},
            {"id": "gemini-id", "name": "Google / Gemini", "type": "googlePalmApi"},
            {"id": "anthropic-id", "name": "Anthropic / API", "type": "anthropicApi"},
            {"id": "openai-id", "name": "OpenAI / API", "type": "openAiApi"},
            {"id": "discord-id", "name": "SHAMAN / Discord / Notify", "type": "discordWebhookApi"},
            {"id": "run-id", "name": "SHAMAN / Run Webhook", "type": "httpHeaderAuth"},
            {"id": "other-id", "name": "ARBW / Sheets", "type": "googleApi"},
            *WP_CREDENTIALS,
        ]

    def test_requirements_come_from_node_credentials(self):
        req = d.credential_requirements(self.workflow)
        self.assertEqual(req["SHAMAN_GEMINI_CREDENTIAL_ID"], ("googlePalmApi", "Google / Gemini"))
        self.assertEqual(req["SHAMAN_GOOGLE_SHEETS_CREDENTIAL_ID"], ("googleApi", "Google / Sheets"))
        self.assertNotIn("SHAMAN_GOOGLE_DRIVE_CREDENTIAL_ID", req)
        self.assertEqual(req["SHAMAN_RUN_WEBHOOK_CREDENTIAL_ID"], ("httpHeaderAuth", "SHAMAN / Run Webhook"))
        self.assertEqual(len(req), 6 + len(WP_VARS))

    def test_resolved_by_type_and_name(self):
        resolved = d.resolve_credentials(CredentialClient(self.all), d.credential_requirements(self.workflow))
        self.assertEqual(resolved["SHAMAN_GOOGLE_SHEETS_CREDENTIAL_ID"], "sheets-id")
        self.assertEqual(resolved["SHAMAN_DISCORD_CREDENTIAL_ID"], "discord-id")
        self.assertNotIn("drive-id", resolved.values())
        self.assertNotIn("other-id", resolved.values())

    def test_missing_or_ambiguous_credential_stops_before_n8n_changes(self):
        req = d.credential_requirements(self.workflow)
        same_name_other_type = [c if c["name"] != "Google / Gemini" else {**c, "type": "googleApi"} for c in self.all]
        cases = (
            ("not found", [c for c in self.all if c["name"] != "OpenAI / API"]),
            ("not found", same_name_other_type),
            ("ambiguous", self.all + [{"id": "dup", "name": "Google / Sheets", "type": "googleApi"}]),
        )
        for message, credentials in cases:
            client = CredentialClient(credentials)
            with self.subTest(message=message), self.assertRaisesRegex(RuntimeError, message):
                d.resolve_credentials(client, req)
            self.assertEqual(client.calls, [])

    def test_default_names_are_used_when_the_type_is_unique(self):
        """名前は問わない。n8n が付けた既定の名前のままでも、その種類が 1 件だけならそれを使う。"""
        defaults = [
            {"id": "sheets-id", "name": "Google Service Account account", "type": "googleApi"},
            {"id": "gemini-id", "name": "Google Gemini(PaLM) Api account", "type": "googlePalmApi"},
            {"id": "anthropic-id", "name": "Anthropic API", "type": "anthropicApi"},
            {"id": "openai-id", "name": "OpenAi account", "type": "openAiApi"},
            {"id": "discord-id", "name": "Discord Webhook account", "type": "discordWebhookApi"},
            {"id": "run-id", "name": "Header Auth account", "type": "httpHeaderAuth"},
            # WordPress は全サイト共通で 1 つ。
            *(WP_CREDENTIALS if len(WP_CREDENTIALS) > 1
              else [{**c, "name": "Wordpress account"} for c in WP_CREDENTIALS]),
        ]
        resolved = d.resolve_credentials(CredentialClient(defaults), d.credential_requirements(self.workflow))
        self.assertEqual(resolved["SHAMAN_GOOGLE_SHEETS_CREDENTIAL_ID"], "sheets-id")
        self.assertEqual(resolved["SHAMAN_ANTHROPIC_CREDENTIAL_ID"], "anthropic-id")
        self.assertEqual(len(resolved), 6 + len(WP_VARS))

    def test_same_type_without_name_match_stops_and_lists_names(self):
        """同じ種類が 2 件以上で、名前も一致しなければ、どれを使うか決めずに止める。"""
        renamed = [c if c["id"] != "sheets-id" else {**c, "name": "Google Service Account account"} for c in self.all]
        with self.assertRaisesRegex(RuntimeError, r"name not matched\): Google / Sheets \(googleApi\); same type in n8n: "
                                                  r"ARBW / Sheets, Google Service Account account$"):
            d.resolve_credentials(CredentialClient(renamed), d.credential_requirements(self.workflow))

    def test_all_problems_are_reported_at_once(self):
        """1 つ目で止めず、決まらない Credential を全部並べる（直すたびに配備を回し直さなくてよい）。"""
        missing = [c for c in self.all if c["type"] not in ("anthropicApi", "googlePalmApi")]
        with self.assertRaises(RuntimeError) as ctx:
            d.resolve_credentials(CredentialClient(missing), d.credential_requirements(self.workflow))
        self.assertIn("not found: Anthropic / API (anthropicApi); same type in n8n: (none)", str(ctx.exception))
        self.assertIn("not found: Google / Gemini (googlePalmApi); same type in n8n: (none)", str(ctx.exception))

    def test_invalid_id_from_n8n_is_rejected(self):
        bad = [c if c["name"] != "Anthropic / API" else {**c, "id": "a'b"} for c in self.all]
        with self.assertRaisesRegex(RuntimeError, "invalid Credential ID"):
            d.resolve_credentials(CredentialClient(bad), d.credential_requirements(self.workflow))

    def test_literal_credential_id_in_workflow_is_rejected(self):
        self.workflow["nodes"][0]["credentials"] = {"googleApi": {"id": "hard-coded", "name": "Google / Sheets"}}
        with self.assertRaisesRegex(RuntimeError, "must be a placeholder"):
            d.credential_requirements(self.workflow)

    def test_one_placeholder_for_two_credentials_is_rejected(self):
        node = next(n for n in self.workflow["nodes"] if n.get("credentials"))
        self.workflow["nodes"].append({"name": "X", "credentials": {
            "openAiApi": {"id": next(iter(node["credentials"].values()))["id"], "name": "OpenAI / API"}}})
        with self.assertRaisesRegex(RuntimeError, "different Credentials"):
            d.credential_requirements(self.workflow)

    def test_403_explains_missing_scope(self):
        client = d.N8n("http://127.0.0.1:5678", "k")
        client.request = lambda method, path, data=None: (_ for _ in ()).throw(
            RuntimeError("n8n API HTTP 403 at GET /credentials"))
        with self.assertRaisesRegex(RuntimeError, "credential:list"):
            client.list_credentials()

    def test_credential_listing_is_paginated(self):
        pages = {"": {"data": [self.all[0]], "nextCursor": "c2"}, "c2": {"data": [self.all[1]], "nextCursor": None}}
        client = d.N8n("http://127.0.0.1:5678", "k")
        seen = []

        def request(method, path, data=None):
            seen.append(path)
            cursor = path.split("cursor=")[1] if "cursor=" in path else ""
            return pages[cursor]
        client.request = request
        self.assertEqual([c["id"] for c in client.list_credentials()], ["sheets-id", "drive-id"])
        self.assertTrue(all(p.startswith("/credentials?limit=100") for p in seen))

    def test_main_deploys_with_ids_from_n8n_without_repository_variables(self):
        client = CredentialClient(self.all)
        env = {"N8N_DEPLOY_API_KEY": "dummy-secret", "SHAMAN_QUEUE_TEST_SPREADSHEET_ID": "test-sheet"}
        original, original_validate = d.N8n, d.validate_env
        d.N8n = lambda url, key: client
        d.validate_env = lambda env, mode, publish: "http://127.0.0.1:5678"
        try:
            path = Path(__file__).resolve().parents[2] / "workflows" / "SHAMAN_Connection_Check.json"
            d.main(["--mode", "connection", "--workflow", str(path), "--publish"], env)
        finally:
            d.N8n, d.validate_env = original, original_validate
        saved = client.store["new-id"]
        creds = {n["name"]: n.get("credentials") for n in saved["nodes"]}
        self.assertEqual(creds["Conn: Gemini Models"]["googlePalmApi"]["id"], "gemini-id")
        self.assertNotIn("__ENV:", json.dumps(saved, ensure_ascii=False))

    def test_main_uses_n8n_credential_names_so_readback_matches(self):
        """n8n は保存のとき Credential の名前を実際の名前に書き換える。既定の名前のままでも読み戻しが一致する。"""
        defaults = [{**c, "name": c["name"].replace(" / ", " ")} for c in self.all
                    if c["id"] != "other-id"]
        by_id = {c["id"]: c["name"] for c in defaults}

        class RenamingClient(CredentialClient):
            def request(self, method, path, data=None):
                if data and "nodes" in data:
                    data = copy.deepcopy(data)
                    for node in data["nodes"]:
                        for cred in (node.get("credentials") or {}).values():
                            cred["name"] = by_id[cred["id"]]
                return super().request(method, path, data)

        client = RenamingClient(defaults)
        env = {"N8N_DEPLOY_API_KEY": "dummy-secret", "SHAMAN_QUEUE_TEST_SPREADSHEET_ID": "test-sheet"}
        original, original_validate = d.N8n, d.validate_env
        d.N8n = lambda url, key: client
        d.validate_env = lambda env, mode, publish: "http://127.0.0.1:5678"
        try:
            path = Path(__file__).resolve().parents[2] / "workflows" / "SHAMAN_Connection_Check.json"
            d.main(["--mode", "connection", "--workflow", str(path), "--publish"], env)
        finally:
            d.N8n, d.validate_env = original, original_validate
        creds = {n["name"]: n.get("credentials") for n in client.store["new-id"]["nodes"]}
        self.assertEqual(creds["Conn: Gemini Models"]["googlePalmApi"]["name"], "Google Gemini")

    def test_readback_mismatch_names_the_node_and_keys(self):
        saved = {"name": "W", "connections": {}, "nodes": [{"name": "A", "parameters": {"x": "secret-value"}}]}
        sent = {"name": "W", "connections": {}, "nodes": [{"name": "A", "parameters": {"x": "other"}}]}
        diffs = d._readback_differences(saved, sent)
        self.assertEqual(diffs, ["node A [parameters]"])
        self.assertNotIn("secret-value", "; ".join(diffs))
