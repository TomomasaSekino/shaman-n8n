"""Actions から n8n の Workflow を実行するスクリプト（run_shaman_workflow.py）と、その Actions（run-shaman-n8n.yml）。"""
import io
import json
from pathlib import Path
import sys
import unittest
import urllib.error

import run_shaman_workflow as r

ROOT = Path(__file__).resolve().parents[3]
RUN_YML = ROOT / ".github" / "workflows" / "run-shaman-n8n.yml"
ENV = {"SHAMAN_RUN_WEBHOOK_TOKEN": "dummy-token", "GITHUB_RUN_ID": "42"}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class Recorder:
    def __init__(self, body=b'{"ok": true}', error=None):
        self.body, self.error, self.requests = body, error, []

    def __call__(self, req, timeout):
        self.requests.append((req, timeout))
        if self.error:
            raise self.error
        return FakeResponse(self.body)


class TestRunShamanWorkflow(unittest.TestCase):
    def test_every_workflow_has_one_run_webhook(self):
        sys.path.insert(0, str(ROOT / "SHAMAN"))
        import generate_workflows as gw
        paths = {mode: r.webhook_path(mode) for mode in r.WORKFLOWS}
        self.assertEqual(paths, {"test": "shaman-prompt-eval", "production": "blog-pipeline-upload",
                                 "connection": "shaman-connection-check"})
        self.assertEqual(r.RUN_WEBHOOK_HEADER, gw.RUN_WEBHOOK_HEADER)

    def test_posts_to_local_webhook_with_token_and_waits(self):
        opener = Recorder(json.dumps({"shamanRun": {"ok": True, "workflow": "connection"}, "summary": "all OK"}).encode())
        out = r.run("connection", ENV, opener)
        req, timeout = opener.requests[0]
        self.assertEqual(req.full_url, "http://127.0.0.1:5678/webhook/shaman-connection-check")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("X-shaman-run-token"), "dummy-token")
        self.assertEqual(json.loads(req.data)["runId"], "42")
        self.assertEqual(timeout, r.TIMEOUTS["connection"])
        self.assertIn("all OK", out)
        self.assertNotIn("dummy-token", out)

    def test_test_mode_sends_the_eval_run_from_actions_inputs(self):
        """プロンプト評価は、Actions の入力を evalRun として送る。Workflow がそれで評価の行を足す（2026-11-06）。"""
        env = {**ENV, "SHAMAN_EVAL_STEP": "P1", "SHAMAN_EVAL_KEYWORD": " アジング ジグヘッド ",
               "SHAMAN_EVAL_OPTION1": "研究者としてのスタンスで", "SHAMAN_EVAL_SITE_ID": "kuro-aji-lab.com",
               "SHAMAN_EVAL_ROUNDS": "1"}
        ok = json.dumps({"shamanRun": {"ok": True, "workflow": "test"}}).encode()
        opener = Recorder(ok)
        r.run("test", env, opener)
        sent = json.loads(opener.requests[0][0].data)["evalRun"]
        self.assertEqual(sent, {"step": "P1", "keyword": "アジング ジグヘッド", "option1": "研究者としてのスタンスで",
                                "siteId": "kuro-aji-lab.com", "inputId": "", "baselineId": "", "model": "",
                                "rounds": "1", "runId": "42"})
        # ステップが空なら送らない（シートに手で書いた待機中の行を使う）。test 以外では送らない。
        for mode, e in (("test", ENV), ("connection", env)):
            opener = Recorder(json.dumps({"shamanRun": {"ok": True, "workflow": mode}}).encode())
            r.run(mode, e, opener)
            self.assertNotIn("evalRun", json.loads(opener.requests[0][0].data), mode)

    def test_wp_auth_probe_counts_the_selected_sites(self):
        env = {**ENV, "SHAMAN_EVAL_STEP": "WP", "SHAMAN_EVAL_SITE_ID": "alpha.example,beta.example",
               "SHAMAN_EVAL_ROUNDS": "1"}
        run = r.eval_run(env)
        self.assertEqual(run["siteId"], "alpha.example,beta.example")
        self.assertEqual(run["rounds"], "2")

    def test_draft_post_is_opt_in_via_explicit_test_flag(self):
        text = RUN_YML.read_text(encoding="utf-8")
        self.assertIn("eval_wp_draft_confirm:", text)
        self.assertIn("TEST_DRAFT_DENIED", text)
        self.assertIn("eval_wp_test_site:", text)

    def test_live_wordpress_auth_is_required_before_production_publication(self):
        yml = (ROOT / ".github" / "workflows" / "deploy-shaman-n8n.yml").read_text(encoding="utf-8")
        assert "WordPressサイト別認証の実機ゲート" in yml
        assert "SHAMAN_EVAL_STEP: WP" in yml
        assert "SHAMAN_EVAL_SITE_ID: kuro-aji-lab.com,kuromimi.online" in yml
        stage = yml.index("      - name: プロンプト評価WorkflowをAPI配備・読戻し・公開")
        auth = yml.index("      - name: WordPressサイト別認証の実機ゲート")
        publish = yml.index("  publish-production:")
        assert stage < auth < publish
        assert "needs: stage-test" in yml[publish:publish + 120]

    def test_actions_passes_every_eval_input(self):
        text = RUN_YML.read_text(encoding="utf-8")
        for key, name in r.EVAL_RUN_ENV:
            self.assertIn(name + ": ${{ inputs.eval_", text, name)
        self.assertIn(r.IMPORT_ENV + ": ${{ inputs.eval_input_execution }}", text)

    @staticmethod
    def _production_api(execution, calls):
        def opener(req, timeout):
            calls.append(req)
            if "/webhook/" in req.full_url:
                return FakeResponse(json.dumps({"shamanRun": {"ok": True, "workflow": "test"}}).encode())
            assert req.get_header("X-n8n-api-key") == "dummy-api-key"
            if "/workflows?" in req.full_url:
                return FakeResponse(json.dumps({"data": [{"id": "prod", "name": r.WORKFLOWS["production"][1]}]}).encode())
            assert req.full_url.endswith("/api/v1/executions/8?includeData=true")
            return FakeResponse(json.dumps(execution).encode())
        return opener

    IMPORT_ENV = {**ENV, "N8N_DEPLOY_API_KEY": "dummy-api-key", "SHAMAN_EVAL_STEP": "P4",
                  "SHAMAN_EVAL_INPUT_EXECUTION": "8", "SHAMAN_EVAL_ROUNDS": "1"}

    def test_eval_input_from_a_production_execution(self):
        """P4 だけを、本番の実行 8 で Build P4 Request が受け取ったデータで試す（2026-11-07、実行 8 の時間切れ）。"""
        data = {"KEYWORD": "アジング", "htmlContent": "<h2>a</h2>", "p1Knowledge": "k"}
        execution = {"id": "8", "workflowId": "prod", "data": {"resultData": {"runData": {
            "Parse Content": [{"data": {"main": [[{"json": data}]]}}]}}}}
        calls = []
        r.run("test", self.IMPORT_ENV, self._production_api(execution, calls))
        sent = json.loads(calls[-1].data)["evalRun"]
        self.assertEqual(sent["step"], "P4")
        self.assertEqual(sent["inputId"], "")
        self.assertEqual(sent["importData"], {"executionId": "8", "node": "Parse Content", "data": data})

    def test_eval_input_from_execution_is_refused_when_it_cannot_be_used(self):
        good = {"id": "8", "workflowId": "prod", "data": {"resultData": {"runData": {
            "Parse Content": [{"data": {"main": [[{"json": {"KEYWORD": "k"}}]]}}]}}}}
        cases = [
            ({}, {**good, "workflowId": "other"}, "not an execution of"),
            ({}, {"id": "8", "workflowId": "prod", "data": {"resultData": {"runData": {}}}}, "no output of Parse Content"),
            ({"SHAMAN_EVAL_INPUT_ID": "R1-1"}, good, "not both"),
            ({"SHAMAN_EVAL_STEP": "P1"}, good, "supports steps"),
            ({"SHAMAN_EVAL_INPUT_EXECUTION": "8; x"}, good, "digits"),
            ({"N8N_DEPLOY_API_KEY": ""}, good, "N8N_DEPLOY_API_KEY"),
        ]
        for extra, execution, message in cases:
            calls = []
            with self.subTest(message=message), self.assertRaisesRegex(RuntimeError, message):
                r.run("test", {**self.IMPORT_ENV, **extra}, self._production_api(execution, calls))
            # 取れなければ、評価の Workflow を呼ばない（API を使わない）
            self.assertFalse(any("/webhook/" in c.full_url for c in calls), message)

    # ---- 止まった行の再開（recover_execution） ----
    RECOVER_ENV = {**ENV, "N8N_DEPLOY_API_KEY": "dummy-api-key", "SHAMAN_RECOVER_EXECUTION": "11"}
    # 本番の実行 11（2026-11-08）：P6: Generate Image で異常終了。WordPress のノードは動いていない。
    CRASHED_11 = {"id": "11", "workflowId": "prod", "status": "crashed", "data": {"resultData": {
        "lastNodeExecuted": "P6: Generate Image",
        "runData": {"Claude: Strengthen Content": [{}], "Build Image Requests": [{}], "P6: Generate Image": [{}]}}}}

    @staticmethod
    def _recover_api(execution, calls, running=()):
        def opener(req, timeout):
            calls.append(req)
            url = req.full_url
            if "/webhook/" in url:
                return FakeResponse(json.dumps({"shamanRun": {"ok": True, "workflow": "production"}}).encode())
            assert req.get_header("X-n8n-api-key") == "dummy-api-key"
            if "/workflows?" in url:
                return FakeResponse(json.dumps({"data": [{"id": "prod", "name": r.WORKFLOWS["production"][1]}]}).encode())
            if "/executions?" in url:
                assert "workflowId=prod" in url
                status = url.split("status=")[1].split("&")[0]
                return FakeResponse(json.dumps({"data": [x for x in running if x["status"] == status]}).encode())
            assert url.endswith("/api/v1/executions/11?includeData=true"), url
            return FakeResponse(json.dumps(execution).encode())
        return opener

    def test_recover_sends_the_stopped_execution_to_the_production_webhook(self):
        """実行 11 が P6 で異常終了し、行が「処理中」のまま残った。人がシートを戻さずに、Actions から再開する（2026-11-09）。"""
        calls = []
        r.run("production", self.RECOVER_ENV, self._recover_api(self.CRASHED_11, calls))
        self.assertEqual(json.loads(calls[-1].data)["recover"], {"executionId": "11"})
        # 指定が無ければ送らない
        calls = []
        r.run("production", {**ENV, "N8N_DEPLOY_API_KEY": "dummy-api-key"}, self._recover_api(self.CRASHED_11, calls))
        self.assertNotIn("recover", json.loads(calls[-1].data))

    def test_recover_is_refused_when_the_row_may_not_be_redone(self):
        ex = self.CRASHED_11
        posted = {**ex, "status": "error", "data": {"resultData": {"lastNodeExecuted": "WP 1: Upload Media", "runData": {
            "P6: Generate Image": [{}], "WP: Prepare Media": [{}], "WP 1: Upload Media": [{}]}}}}
        def stopped_at(last, ran):
            return {**ex, "status": "crashed", "data": {"resultData": {"lastNodeExecuted": last,
                                                                       "runData": {n: [{}] for n in ran}}}}
        cases = [
            # 実行の記録が無い・壊れている（PR #90 レビュー B2）
            ({}, {**ex, "data": None}, (), "no usable execution data"),
            ({}, {**ex, "data": {"resultData": {"runData": {}, "lastNodeExecuted": "P6: Generate Image"}}}, (), "no usable execution data"),
            ({}, {**ex, "data": {"resultData": {"runData": {"P6: Generate Image": [{}]}}}}, (), "no usable execution data"),
            ({}, {**ex, "data": {"resultData": {"runData": ["x"], "lastNodeExecuted": "x"}}}, (), "no usable execution data"),
            ({}, stopped_at("P6: Generate Image", ["Claude: Strengthen Content"]), (), "no usable execution data"),
            # WordPress へ送る途中・送った後で止まった（WP の名前の無いノードも、WP の先なら同じ）
            ({}, stopped_at("Build WP Post", ["P6: Generate Image", "Build WP Post"]), (), "may have sent to WordPress"),
            ({}, stopped_at("Parse WP Response", ["Parse WP Response"]), (), "may have sent to WordPress"),
            ({}, stopped_at("Queue: Update", ["Queue: Update"]), (), "may have sent to WordPress"),
            ({}, stopped_at("Discord: Notify Success", ["Discord: Notify Success"]), (), "may have sent to WordPress"),
            ({}, {**ex, "status": "running"}, (), "not stopped"),
            ({}, {**ex, "status": "success"}, (), "not stopped"),
            ({}, posted, (), "may have sent to WordPress .*WP 1: Upload Media"),
            ({}, {**ex, "workflowId": "eval"}, (), "not an execution of"),
            ({}, ex, ({"id": "40", "status": "running"},), "another production execution is running \\(40\\)"),
            ({}, ex, ({"id": "41", "status": "waiting"},), "another production execution is running \\(41\\)"),
            ({"SHAMAN_RECOVER_EXECUTION": "11 x"}, ex, (), "digits"),
            ({"N8N_DEPLOY_API_KEY": ""}, ex, (), "N8N_DEPLOY_API_KEY"),
        ]
        for extra, execution, running, message in cases:
            calls = []
            with self.subTest(message=message), self.assertRaisesRegex(RuntimeError, message):
                r.run("production", {**self.RECOVER_ENV, **extra}, self._recover_api(execution, calls, running))
            # 再開できなければ、本番の Workflow を呼ばない
            self.assertFalse(any("/webhook/" in c.full_url for c in calls), message)
        with self.assertRaisesRegex(RuntimeError, "only for workflow=production"):
            r.run("test", self.RECOVER_ENV, self._recover_api(ex, []))

    def test_actions_passes_the_recover_input(self):
        self.assertIn(r.RECOVER_ENV + ": ${{ inputs.recover_execution }}", RUN_YML.read_text(encoding="utf-8"))

    def test_diagnosis_shows_how_long_the_stopped_node_ran(self):
        """止まったノードの開始と所要時間を出す（通信が切れたのか、時間切れかを見分ける。2026-11-07、実行 8）。"""
        execution = {"startedAt": "2026-11-07T06:07:00.000Z", "stoppedAt": "2026-11-07T06:23:49.000Z"}
        result = {"lastNodeExecuted": "Claude: Strengthen Content", "runData": {"Claude: Strengthen Content": [
            {"startTime": 1794031860000, "executionTime": 600123}]}}
        self.assertEqual(r._timing(execution, result),
                         "\n  execution 2026-11-07T06:07:00.000Z -> 2026-11-07T06:23:49.000Z (1009s); "
                         "last node started 2026-11-07T06:11:00Z, ran 600s")
        self.assertEqual(r._timing({}, {"lastNodeExecuted": "x", "runData": {}}), "")

    def test_timeout_also_reads_the_n8n_execution(self):
        """Webhook が時間内に応答しなくても、この呼び出しの実行がどこで止まっているかを出す（2026-11-08、実行 11）。"""
        opener = self._n8n(TimeoutError("timed out"), "SHAMAN", {
            "11": (self.NOW, "running", "Claude: Strengthen Content", {}, "ours")}, "Webhook")
        with self.assertRaisesRegex(RuntimeError, r"timed out: TimeoutError after \d+s\n"
                                                  r"n8n execution 11: status=running, last node=Claude: Strengthen Content"):
            r.run("production", self.KEYED, opener)

    def test_inspect_shows_status_and_node_timings_without_data(self):
        """実行 ID を指定して、状態とノードごとの時間を読む（Workflow は動かさない。データの中身は出さない）。"""
        execution = {"id": "11", "workflowId": "prod", "status": "running", "mode": "webhook",
                     "startedAt": "2026-11-08T10:58:34.000Z", "stoppedAt": None, "data": {"resultData": {
                         "lastNodeExecuted": "Parse Content", "runData": {
                             "Webhook": [{"startTime": 1794135514000, "executionTime": 5,
                                          "data": {"main": [[{"json": {"body": {"secret-ish": "x"}}}]]}}],
                             "Parse Content": [{"startTime": 1794135600000, "executionTime": 1200,
                                                "data": {"main": [[{"json": {"htmlContent": "本文の中身"}}]]}}],
                             "Gemini: Research": [{"startTime": 1794135520000, "executionTime": 61000,
                                                   "error": {"message": "HTTP 503"}}]}}}}
        calls = []

        def opener(req, timeout):
            calls.append(req)
            assert req.get_header("X-n8n-api-key") == "dummy-api-key"
            assert req.full_url.endswith("/api/v1/executions/11?includeData=true")
            return FakeResponse(json.dumps(execution).encode())
        out = r.inspect(self.KEYED, opener, "11")
        self.assertIn("n8n execution 11: workflow=prod, status=running, mode=webhook", out)
        self.assertIn("stopped=(not stopped)", out)
        self.assertLess(out.index("Webhook"), out.index("Gemini: Research"))
        self.assertLess(out.index("Gemini: Research"), out.index("  2026-11-08T11:00:00Z Parse Content 1.2s"))
        self.assertIn("Gemini: Research 61.0s ERROR HTTP 503", out)
        self.assertIn("still running: the next node after Parse Content has been running since", out)
        self.assertNotIn("本文の中身", out)
        self.assertNotIn("secret-ish", out)
        self.assertEqual(len(calls), 1)
        with self.assertRaisesRegex(RuntimeError, "digits"):
            r.inspect(self.KEYED, opener, "11; rm")

    def test_actions_can_inspect_an_execution_without_running(self):
        text = RUN_YML.read_text(encoding="utf-8")
        self.assertIn("SHAMAN_INSPECT_EXECUTION: ${{ inputs.inspect_execution }}", text)
        self.assertIn('--inspect "$SHAMAN_INSPECT_EXECUTION"', text)

    def test_token_is_required(self):
        with self.assertRaisesRegex(RuntimeError, "SHAMAN_RUN_WEBHOOK_TOKEN"):
            r.run("test", {}, Recorder())

    def test_only_local_n8n(self):
        for url in ("http://evil.example:5678", "https://127.0.0.1:5678", "http://127.0.0.1:9999"):
            with self.subTest(url=url), self.assertRaisesRegex(RuntimeError, "local"):
                r.run("test", {**ENV, "N8N_BASE_URL": url}, Recorder())

    def test_http_errors_fail_with_a_hint(self):
        for code, hint in ((403, "run token"), (404, "not published"), (500, "workflow failed")):
            err = urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b'{"message": "Error in workflow"}'))
            with self.subTest(code=code), self.assertRaisesRegex(RuntimeError, "HTTP %d: .*%s" % (code, hint)):
                r.run("production", ENV, Recorder(error=err))

    @staticmethod
    def _n8n(webhook, workflow_name, executions, hook_node, calls=None, received=None):
        """偽の n8n。executions：{id: (startedAt, status, lastNode, error, nonce の決め方)}。

        nonce の決め方が "ours" なら、この呼び出しの nonce を受け取った実行にする。それ以外は別の値。
        """
        sent = {}

        def opener(req, timeout):
            if calls is not None:
                calls.append(req)
            if "/webhook/" in req.full_url:
                sent["nonce"] = json.loads(req.data)["nonce"]
                if isinstance(webhook, Exception):
                    raise webhook
                return FakeResponse(webhook)
            assert req.get_method() == "GET"
            assert req.get_header("X-n8n-api-key") == "dummy-api-key"
            if "/workflows?" in req.full_url:
                return FakeResponse(json.dumps({"data": [{"id": "wf1", "name": workflow_name}]}).encode())
            if "/executions?" in req.full_url:
                assert "includeData" not in req.full_url and "workflowId=wf1" in req.full_url
                return FakeResponse(json.dumps({"data": [
                    {"id": i, "startedAt": v[0], "status": v[1]} for i, v in executions.items()]}).encode())
            exec_id = req.full_url.split("/executions/")[1].split("?")[0]
            assert req.full_url.endswith("?includeData=true")
            started_at, status, last, error, who = executions[exec_id]
            nonce = sent["nonce"] if who == "ours" else "someone-else"
            if received is not None:
                received.append(exec_id)
            return FakeResponse(json.dumps({"id": exec_id, "status": status, "data": {"resultData": {
                "lastNodeExecuted": last, "error": error,
                "runData": {hook_node: [{"data": {"main": [[{"json": {"body": {"nonce": nonce}}}]]}}]}}}}).encode())
        return opener

    NOW = "2999-01-01T00:00:00.000Z"
    KEYED = {**ENV, "N8N_DEPLOY_API_KEY": "dummy-api-key"}

    def test_http_500_reads_the_error_of_this_runs_execution(self):
        """Webhook が中身の無い 500 を返したら、この呼び出しの実行（nonce が一致）のエラーを読み、ログに出す。"""
        err = urllib.error.HTTPError("u", 500, "x", {}, io.BytesIO(b'{"message": "Error in workflow"}'))
        received = []
        opener = self._n8n(err, "SHAMAN: Connection Check", {
            "10": (self.NOW, "error", "Conn: Gemini Models", {"message": "別の実行のエラー"}, "other"),  # 画面からの実行
            "9": (self.NOW, "error", "Run: Check", {"message": "SHAMAN_RUN_NG: Gemini（HTTP 403）"}, "ours"),
        }, "Run Webhook", received=received)
        with self.assertRaisesRegex(RuntimeError, r"n8n execution 9: status=error, last node=Run: Check, "
                                                  r"error=SHAMAN_RUN_NG: Gemini（HTTP 403）") as ctx:
            r.run("connection", self.KEYED, opener)
        self.assertNotIn("別の実行", str(ctx.exception))
        self.assertEqual(received, ["10", "9"])

    def test_execution_that_cannot_be_matched_is_not_reported(self):
        """nonce が一致する実行が無ければ、別の実行の原因を出さず「特定できない」とする（PR #73 のレビュー）。"""
        err = urllib.error.HTTPError("u", 500, "x", {}, io.BytesIO(b"{}"))
        opener = self._n8n(err, "SHAMAN", {
            "8": (self.NOW, "error", "Queue: Read", {"message": "別の実行のエラー"}, "other"),
            "7": ("2000-01-01T00:00:00Z", "error", "Queue: Read", {"message": "古い実行"}, "ours"),  # 呼び出しより前
        }, "Webhook")
        with self.assertRaisesRegex(RuntimeError, "cannot identify the n8n execution of this run") as ctx:
            r.run("production", self.KEYED, opener)
        self.assertNotIn("別の実行", str(ctx.exception))
        self.assertNotIn("古い実行", str(ctx.exception))

    def test_http_500_without_api_access_still_fails_with_a_note(self):
        err = urllib.error.HTTPError("u", 500, "x", {}, io.BytesIO(b"{}"))
        with self.assertRaisesRegex(RuntimeError, "no N8N_DEPLOY_API_KEY"):
            r.run("connection", ENV, Recorder(error=err))
        forbidden = urllib.error.HTTPError("u", 403, "x", {}, io.BytesIO(b"{}"))

        def opener(req, timeout):
            raise err if "/webhook/" in req.full_url else forbidden
        # GET /workflows は workflow:list、GET /executions は execution:list、GET /executions/{id} は execution:read。
        with self.assertRaisesRegex(RuntimeError, "workflow:list, execution:list and execution:read"):
            r.run("connection", {**ENV, "N8N_DEPLOY_API_KEY": "k"}, opener)

    def test_no_result_also_reads_the_n8n_execution(self):
        """結果が返らない（途中で止まった）ときも、この呼び出しの実行を読む。"""
        opener = self._n8n(b'{"message": "Workflow executed successfully but the last node did not return any data"}',
                           "SHAMAN: Prompt Eval", {"3": (self.NOW, "success", "Eval: Claim Run", None, "ours")},
                           "Run Webhook")
        with self.assertRaisesRegex(RuntimeError, r"(?s)no explicit run result.*last node=Eval: Claim Run"):
            r.run("test", self.KEYED, opener)

    def test_secrets_in_the_execution_error_are_masked(self):
        """n8n のエラー文に入った API キー・認証ヘッダー・合言葉は、ログに出す前に伏せる（PR #73 のレビュー）。"""
        google = "AIza" + "B" * 35
        message = ("400 https://generativelanguage.googleapis.com/v1/models?key=%s&pageSize=1 / "
                   "Authorization: Bearer abc.def-ghi / {\"x-api-key\": \"sk-ant-%s\"} / %s / dummy-token / dummy-api-key"
                   % (google, "C" * 24, google))
        err = urllib.error.HTTPError("u", 500, "x", {}, io.BytesIO(b'{"message": "token=xyz123"}'))
        opener = self._n8n(err, "SHAMAN: Connection Check",
                           {"9": (self.NOW, "error", "Conn: Gemini Models", {"message": message,
                                                                            "description": "api_key=zzz999"}, "ours")},
                           "Run Webhook")
        with self.assertRaises(RuntimeError) as ctx:
            r.run("connection", self.KEYED, opener)
        text = str(ctx.exception)
        for secret in (google, "abc.def-ghi", "C" * 24, "dummy-token", "dummy-api-key", "xyz123", "zzz999"):
            self.assertNotIn(secret, text)
        self.assertIn("key=***", text)
        self.assertIn("Conn: Gemini Models", text)

    def test_redact_keeps_ordinary_text(self):
        self.assertEqual(r.redact("Gemini（HTTP 403） / 行 5: STATUS「エラー」"), "Gemini（HTTP 403） / 行 5: STATUS「エラー」")

    def test_failure_message_is_masked(self):
        import contextlib
        from unittest import mock
        err = io.StringIO()
        with mock.patch.object(r, "run", side_effect=RuntimeError("failed ?key=SECRETVALUE123 / dummy-token")), \
                contextlib.redirect_stderr(err):
            self.assertEqual(r.cli(["--mode", "test"], ENV), 1)
        self.assertIn("SHAMAN_RUN_FAILED: failed ?key=***", err.getvalue())
        self.assertNotIn("SECRETVALUE123", err.getvalue())
        self.assertNotIn("dummy-token", err.getvalue())

    def test_unreachable_n8n_fails(self):
        with self.assertRaisesRegex(RuntimeError, "Cannot reach"):
            r.run("test", ENV, Recorder(error=urllib.error.URLError("refused")))

    def test_long_output_is_truncated(self):
        body = {"shamanRun": {"ok": True, "workflow": "production"}, "html": "x" * 10000}
        out = r.run("production", ENV, Recorder(json.dumps(body).encode()))
        self.assertLess(len(out), r.MAX_OUTPUT + 100)

    def test_http_200_with_ng_result_fails(self):
        """HTTP 200 でも、キュー・評価の行がエラーなら失敗にする（PR #71 のレビュー）。"""
        cases = {
            "production": {"ok": False, "workflow": "production", "reason": "1 / 2 行が完了していません",
                           "rows": [{"row": 5, "status": "画像エラー"}]},
            "test": {"ok": False, "workflow": "test", "reason": "評価の実行が「エラーあり」で終わりました"},
        }
        for mode, run in cases.items():
            with self.subTest(mode=mode), self.assertRaisesRegex(RuntimeError, "NG: " + run["reason"]):
                r.run(mode, ENV, Recorder(json.dumps({"shamanRun": run}).encode()))

    def test_http_200_without_explicit_result_fails(self):
        for body in (b'{"ok": true}', b'', b'[]', b'not json', b'{"shamanRun": {"ok": "true", "workflow": "test"}}'):
            with self.subTest(body=body), self.assertRaises(RuntimeError):
                r.run("test", ENV, Recorder(body))

    def test_result_for_another_workflow_fails(self):
        body = json.dumps({"shamanRun": {"ok": True, "workflow": "connection"}}).encode()
        with self.assertRaisesRegex(RuntimeError, "another workflow"):
            r.run("production", ENV, Recorder(body))

    def test_workflow_without_run_webhook_is_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "SHAMAN_Prompt_Eval.json").write_text(json.dumps(
                {"nodes": [{"type": "n8n-nodes-base.webhook", "parameters": {"path": "open"}}]}))
            with self.assertRaisesRegex(RuntimeError, "exactly one run webhook"):
                r.webhook_path("test", tmp)


class TestRunWorkflowYml(unittest.TestCase):
    def setUp(self):
        self.yml = RUN_YML.read_text(encoding="utf-8")

    def test_manual_dispatch_by_owner_on_main_only(self):
        self.assertIn("on:\n  workflow_dispatch:", self.yml)
        self.assertNotIn("pull_request", self.yml)
        self.assertNotIn("push:", self.yml)
        for gate in ("github.ref == 'refs/heads/main'", "github.actor == github.repository_owner",
                     "github.triggering_actor == github.repository_owner"):
            self.assertIn(gate, self.yml)

    def test_choices_are_every_deployed_workflow(self):
        for mode in r.WORKFLOWS:
            self.assertIn("          - " + mode + "\n", self.yml)

    def test_runs_on_luna_deploy_runner_and_does_not_deploy(self):
        self.assertIn("runs-on: [self-hosted, macOS, X64, shaman-deploy]", self.yml)
        self.assertIn('RUNNER_NAME:-}" != "luna-shaman-deploy"', self.yml)
        self.assertNotIn("deploy_shaman_google_sheets.py", self.yml)
        # API キーは、失敗のときに実行のエラーを読むためだけに、実行のステップに渡す。
        self.assertEqual(self.yml.count("N8N_DEPLOY_API_KEY: ${{ secrets.N8N_DEPLOY_API_KEY }}"), 1)
        self.assertIn("SHAMAN_RUN_WEBHOOK_TOKEN: ${{ secrets.SHAMAN_RUN_WEBHOOK_TOKEN }}", self.yml)
        self.assertIn('run_shaman_workflow.py --mode "$SHAMAN_RUN_MODE"', self.yml)
        self.assertIn("permissions:\n  contents: read", self.yml)


if __name__ == "__main__":
    unittest.main()
