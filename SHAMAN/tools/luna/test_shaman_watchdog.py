"""SHAMAN の無人運用における復旧判断の単体試験。外部API・WordPressには接続しない。"""
from datetime import datetime, timezone
import tempfile
from pathlib import Path
import unittest

import shaman_watchdog as w
import install_shaman_watchdog as installer


NOW = datetime(2026, 11, 10, 6, 30, tzinfo=timezone.utc).timestamp()
STOPPED = "2026-11-10T06:00:00Z"
STARTED = "2026-11-10T05:00:00Z"
CFG = {"n8n_api_key": "dummy", "webhook_token": "dummy"}


def execution(status="error", text="HTTP 503", scheduled=True):
    runs = {"Gemini: Research": [{"data": {}}]}
    if scheduled:
        runs["Daily Schedule"] = [{"data": {}}]
    return {"id": "123", "workflowId": "shaman", "status": status,
            "startedAt": STARTED, "stoppedAt": STOPPED,
            "data": {"resultData": {"runData": runs, "lastNodeExecuted": "Gemini: Research",
                                    "error": {"message": text}}}}


def simulate(entry, state=None):
    state = state or {"attempted": {}, "alerts": {}}
    recover = []
    persisted = []

    def send(url, data=None, headers=None, timeout=15):
        if "/workflows?" in url:
            return {"data": [{"name": "SHAMAN", "id": "shaman"}]}
        if "/executions?" in url:
            return {"data": [{k: v for k, v in entry.items() if k != "data"}]}
        if "/executions/123?" in url:
            return entry
        raise AssertionError(url)

    def run(mode, env):
        recover.append((mode, env["SHAMAN_RECOVER_EXECUTION"]))

    w.scan(CFG, state, NOW, send=send, run_recovery=run,
           check_health=lambda: None, save=lambda s: persisted.append({k: v.copy() for k, v in s["attempted"].items()}))
    return state, recover, persisted


class WatchdogTests(unittest.TestCase):
    def test_transient_scheduled_failure_recovers_at_most_once(self):
        state, recover, persisted = simulate(execution())
        self.assertEqual(recover, [("production", "123")])
        self.assertEqual(state["attempted"]["123"]["outcome"], "completed")
        self.assertEqual(persisted[0]["123"]["outcome"], "started")
        _, again, _ = simulate(execution(), state)
        self.assertEqual(again, [])

    def test_quality_gate_is_not_retried(self):
        state, recover, _ = simulate(execution(text="SHAMAN_RUN_NG: P1 は情報源0件"))
        self.assertEqual(recover, [])
        self.assertIn("manual-123", state["alerts"])

    def test_non_transient_failure_not_retried(self):
        _, recover, _ = simulate(execution(text="Validation Error"))
        self.assertEqual(recover, [])

    def test_manual_and_webhook_runs_are_not_retried(self):
        _, recover, _ = simulate(execution(scheduled=False))
        self.assertEqual(recover, [])

    def test_successful_and_canceled_runs_are_not_retried(self):
        for status in ("success", "canceled"):
            with self.subTest(status=status):
                _, recover, _ = simulate(execution(status=status))
                self.assertEqual(recover, [])

    def test_recovery_failure_still_counts_as_one_attempt(self):
        state = {"attempted": {}, "alerts": {}}
        def send(url, data=None, headers=None, timeout=15):
            if "/workflows?" in url:
                return {"data": [{"name": "SHAMAN", "id": "shaman"}]}
            if "/executions?" in url:
                return {"data": [{k: v for k, v in execution().items() if k != "data"}]}
            if "/executions/123?" in url:
                return execution()
            raise AssertionError(url)

        w.scan(CFG, state, NOW, send=send,
               run_recovery=lambda *_: (_ for _ in ()).throw(RuntimeError("unsafe")),
               check_health=lambda: None, save=lambda _: None)
        self.assertEqual(state["attempted"]["123"]["outcome"], "failed")

    def test_config_is_private(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            path.write_text('{"n8n_api_key":"a","webhook_token":"b"}')
            path.chmod(0o644)
            with self.assertRaisesRegex(RuntimeError, "0600"):
                w.load_config(path)
            path.chmod(0o600)
            self.assertEqual(w.load_config(path)["n8n_api_key"], "a")

    def test_system_launchdaemon_survives_without_user_login(self):
        import setup_shaman_watchdog_daemon as setup
        home = Path("/Users/luna-user")
        p = setup.daemon_plist("/opt/local/bin/python3", "/tmp/shaman_watchdog.py", home, "luna-user")
        self.assertEqual(p["StartInterval"], 300)
        self.assertTrue(p["RunAtLoad"])
        self.assertEqual(p["UserName"], "luna-user")
        self.assertTrue(str(setup.DAEMON).startswith("/Library/LaunchDaemons/"))
        self.assertNotIn("KeepAlive", p)

    def test_transient_structured_n8n_http_errors(self):
        for error in (
            {"message": "Bad gateway", "httpCode": 502},
            {"message": "HTTP Request Failed", "description": "Service unavailable", "httpCode": "503"},
            {"message": "Request rejected", "cause": {"statusCode": 429}},
            {"message": "Gateway Timeout", "httpCode": 504},
        ):
            with self.subTest(error=error):
                self.assertTrue(w.transient_error(error))
        for error in (
            {"message": "Invalid request", "httpCode": 400},
            {"message": "SHAMAN_RUN_NG: timeout in quality validation", "httpCode": 503},
            {"message": "Unknown failure"},
        ):
            with self.subTest(error=error):
                self.assertFalse(w.transient_error(error))

    def test_healthz_accepts_http_200_with_non_ok_body(self):
        import unittest.mock as mock
        import io

        class Response:
            status = 200
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass
            def read(self):
                return b'{"status":"ok"}'

        with mock.patch.object(w.urllib.request, "urlopen", return_value=Response()):
            w.health()  # 200 なら本文の形式を問わず正常

    def test_healthz_non_200_is_rejected(self):
        import unittest.mock as mock

        class Response:
            status = 503
            def __enter__(self):
                return self
            def __exit__(self, *_):
                pass

        with mock.patch.object(w.urllib.request, "urlopen", return_value=Response()):
            with self.assertRaisesRegex(OSError, "HTTP 503"):
                w.health()

    def test_discord_http_403_does_not_kill_watchdog_or_mark_sent(self):
        from urllib.error import HTTPError
        import io
        import unittest.mock as mock
        state = {"alerts": {}, "attempted": {}}
        cfg = dict(CFG, discord_webhook_url="https://discord.com/api/webhooks/secret")
        def fail(*_a, **_kw):
            raise HTTPError("https://discord.com/api/webhooks/secret", 403, "Forbidden", {}, None)
        with mock.patch("sys.stderr", new_callable=io.StringIO) as logs:
            w.notify("n8nエラー", cfg, state, "health", NOW, send=fail)
            self.assertIn("Discord HTTP 403", logs.getvalue())
            self.assertNotIn("https://discord.com", logs.getvalue())
        self.assertNotIn("health", state["alerts"])

    def test_discord_http_403_scan_finishes_normally(self):
        from urllib.error import HTTPError
        import io
        import unittest.mock as mock
        state = {"alerts": {}, "attempted": {}}
        cfg = dict(CFG, discord_webhook_url="https://discord.com/api/webhooks/secret")
        def fail(*_a, **_kw):
            raise HTTPError("https://discord.com/api/webhooks/secret", 403, "Forbidden", {}, None)
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            w.scan(cfg, state, NOW, send=fail, check_health=lambda: (_ for _ in ()).throw(OSError("failed")))
        self.assertEqual(state["attempted"], {})

    def test_installer_requires_dedicated_credential_without_overwriting_deploy_key(self):
        import unittest.mock as mock
        with mock.patch.object(installer.sys, "platform", "darwin"), mock.patch.dict(
            installer.os.environ, {
                "SHAMAN_WATCHDOG_N8N_API_KEY": "same",
                "N8N_DEPLOY_API_KEY": "same",
                "SHAMAN_RUN_WEBHOOK_TOKEN": "token",
            }):
            with self.assertRaisesRegex(RuntimeError, "流用"):
                installer.install()


if __name__ == "__main__":
    unittest.main()
