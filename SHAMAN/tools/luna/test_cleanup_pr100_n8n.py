"""PR #100 の削除に関する安全性を n8n API モックで検証する。"""
import importlib.util
import pathlib
import unittest

SCRIPT = pathlib.Path(__file__).with_name("cleanup_pr100_n8n.py")
spec = importlib.util.spec_from_file_location("cleanup_pr100_n8n", SCRIPT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class FakeApi:
    def __init__(self, name=mod.TARGET_NAME, target=True, protected=True, sticky=False):
        self.workflows = {
            ident: {"id": ident, "name": ident, "active": protected, "updatedAt": "2026-11-15T00:00:00Z"}
            for ident in mod.PROTECTED_IDS
        }
        if target:
            self.workflows[mod.TARGET_ID] = {
                "id": mod.TARGET_ID, "name": name, "active": False,
                "nodes": [{"credentials": {"httpBasicAuth": {
                    "name": "SHAMAN / WordPress / MultiSite", "id": "credential-id"
                }}}],
            }
        self.executions = {mod.TARGET_EXECUTION_ID: {
            "id": mod.TARGET_EXECUTION_ID, "workflowId": mod.TARGET_ID
        }} if target else {}
        self.writes = []
        self.sticky = sticky

    def call(self, method, path):
        from urllib.parse import parse_qs
        base, _, query = path.partition("?")
        parts = base.strip("/").split("/")
        kind, store = parts[0], self.workflows if parts[0] == "workflows" else self.executions
        if method == "GET":
            if len(parts) == 2:
                value = store.get(parts[1])
                return (200, dict(value)) if value is not None else (404, None)
            values = list(store.values())
            if kind == "executions":
                w = parse_qs(query).get("workflowId", [None])[0]
                values = [x for x in values if x["workflowId"] == w]
            return 200, {"data": values, "nextCursor": None}
        self.writes.append((method, path))
        if not self.sticky:
            store.pop(parts[1], None)
        return 200, {}


class CleanupTests(unittest.TestCase):
    def test_success_and_idempotency(self):
        api = FakeApi()
        mod.cleanup(api)
        self.assertEqual(api.writes, [
            ("DELETE", "/executions/1293"),
            ("DELETE", "/workflows/" + mod.TARGET_ID),
        ])
        mod.cleanup(api)
        self.assertEqual(len(api.writes), 2)

    def test_wrong_name_no_writes(self):
        api = FakeApi(name="unrelated")
        with self.assertRaisesRegex(mod.CleanupError, "TARGET_ID_OR_NAME_MISMATCH"):
            mod.cleanup(api)
        self.assertFalse(api.writes)

    def test_protected_id_rejected(self):
        original = mod.TARGET_ID
        try:
            mod.TARGET_ID = "0hxaRjPczHWnBozO"
            api = FakeApi()
            with self.assertRaisesRegex(mod.CleanupError, "TARGET_IS_PROTECTED"):
                mod.cleanup(api)
            self.assertFalse(api.writes)
        finally:
            mod.TARGET_ID = original

    def test_absent_success(self):
        api = FakeApi(target=False)
        mod.cleanup(api)
        self.assertFalse(api.writes)

    def test_post_delete_still_exists_failure(self):
        api = FakeApi(sticky=True)
        with self.assertRaisesRegex(mod.CleanupError, "STILL_EXISTS"):
            mod.cleanup(api)

    def test_protected_not_active_no_writes(self):
        api = FakeApi(protected=False)
        with self.assertRaisesRegex(mod.CleanupError, "PROTECTED_WORKFLOW_NOT_ACTIVE"):
            mod.cleanup(api)
        self.assertFalse(api.writes)

    def test_execution_owner_mismatch_no_writes(self):
        api = FakeApi()
        api.executions["1293"]["workflowId"] = "other"
        with self.assertRaisesRegex(mod.CleanupError, "EXECUTION_OWNER_MISMATCH"):
            mod.cleanup(api)
        self.assertFalse(api.writes)

    def test_absent_workflow_with_execution_fails(self):
        api = FakeApi(target=False)
        api.executions["1293"] = {"id": "1293", "workflowId": mod.TARGET_ID}
        with self.assertRaisesRegex(mod.CleanupError, "TARGET_GONE_BUT_EXECUTION"):
            mod.cleanup(api)
        self.assertFalse(api.writes)


if __name__ == "__main__":
    unittest.main()
