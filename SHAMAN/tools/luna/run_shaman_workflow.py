#!/usr/bin/env python3
"""SHAMAN: Actions から luna の n8n の Workflow を実行する（DOCS/RUN_FROM_ACTIONS.md）。

Workflow の JSON から、合言葉付きの Webhook（Header Auth）の path を取り、127.0.0.1 の n8n に POST する。
Webhook は responseMode=lastNode なので、実行が終わるまで応答しない。最後のノード Run: Check は、結果（shamanRun）が
ok でなければ例外で止める（n8n の実行は Error。応答は中身の無い HTTP 500）。ok なら shamanRun を返す。
shamanRun.ok が true のときだけ成功にする。HTTP 500 や結果が返らないときは、n8n の API で、この呼び出しの実行
（nonce で特定）のエラーを読んで出す。ログに出す文は、API キーなどを伏せる（redact）。
"""
import argparse
import json
import os
import re
import time
import uuid
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request

from deploy_shaman_google_sheets import RUN_WEBHOOK_CREDENTIAL_ID, WORKFLOWS

WORKFLOW_DIR = Path(__file__).resolve().parents[2] / "workflows"
RUN_WEBHOOK_HEADER = "X-SHAMAN-Run-Token"
# 応答を待つ上限（秒）。本番はキューの待機中の行を全部処理するので長い。
TIMEOUTS = {"connection": 600, "test": 3600, "production": 5 * 3600}
# ログに出す応答の長さの上限（記事の本文などで長くなるため）。
MAX_OUTPUT = 20000  # プロンプト評価が結果ごとの測定値も返す（2026-11-09）


def base_url(env):
    base = env.get("N8N_BASE_URL", "http://127.0.0.1:5678")
    url = urllib.parse.urlsplit(base)
    if (url.scheme != "http" or url.hostname not in ("localhost", "127.0.0.1")
            or url.port != 5678 or url.path not in ("", "/")
            or url.username or url.password or url.query or url.fragment):
        raise RuntimeError("n8n URL must be local http://127.0.0.1:5678")
    return base.rstrip("/")


def run_webhook(mode, workflow_dir=WORKFLOW_DIR):
    """Workflow の JSON にある、合言葉付きの Webhook（1 つだけ）の (ノード名, path)。"""
    filename, _name = WORKFLOWS[mode]
    workflow = json.loads((Path(workflow_dir) / filename).read_text(encoding="utf-8"))
    hooks = [(n.get("name"), n["parameters"]["path"]) for n in workflow.get("nodes") or []
             if n.get("type") == "n8n-nodes-base.webhook"
             and (n.get("parameters") or {}).get("authentication") == "headerAuth"
             and (n.get("credentials") or {}).get("httpHeaderAuth", {}).get("id") == RUN_WEBHOOK_CREDENTIAL_ID]
    if len(hooks) != 1 or not hooks[0][1] or not all(c.isalnum() or c in "-_" for c in hooks[0][1]):
        raise RuntimeError("Workflow must have exactly one run webhook: " + filename)
    return hooks[0]


def webhook_path(mode, workflow_dir=WORKFLOW_DIR):
    return run_webhook(mode, workflow_dir)[1]


# ログに出す前に伏せる値（n8n の実行のエラー文には、外部 API の URL や認証ヘッダーが入ることがある）。
_SECRET_PATTERNS = [
    re.compile(r"((?:[?&]|\b)(?:key|api_?key|apikey|token|access_token|refresh_token|password|secret|signature)=)"
               r"[^&\s\"',}]+", re.I),
    re.compile(r"(\b(?:Bearer|Basic|Token)\s+)[^\s\"',}]+", re.I),
    re.compile(r"((?:authorization|x-api-key|x-n8n-api-key|x-shaman-run-token|x-goog-api-key|api[-_]?key|password|token)"
               r"[\"']?\s*[:=]\s*[\"']?)(?!\*\*\*)[^\s\"',}]+", re.I),
    re.compile(r"(?<![\w-])()(?:AIza[0-9A-Za-z_\-]{20,}|sk-[A-Za-z0-9_\-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,})"),
]
_SECRET_ENV = ("SHAMAN_RUN_WEBHOOK_TOKEN", "N8N_DEPLOY_API_KEY")


def redact(text, env=None):
    """API キー・合言葉・認証ヘッダーの値を *** にする（Actions のログに出す文に使う）。"""
    text = str(text)
    for name in _SECRET_ENV:
        value = (env or {}).get(name, "").strip()
        if len(value) >= 6:
            text = text.replace(value, "***")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + "***", text)
    return text


def _summary(raw):
    text = raw.decode("utf-8", "replace")
    try:
        text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
    except ValueError:
        pass
    return text if len(text) <= MAX_OUTPUT else text[:MAX_OUTPUT] + "\n…(" + str(len(text)) + " chars)"


# プロンプト評価（test）で、Actions の入力から「評価の実行」に足す行（DOCS/PROMPT_EVAL_DESIGN.md §10.2）。
# 環境変数 → Webhook の本文の evalRun の項目。値の確かめは Workflow の Eval: Pick Run がする。
EVAL_RUN_ENV = (("step", "SHAMAN_EVAL_STEP"), ("keyword", "SHAMAN_EVAL_KEYWORD"), ("option1", "SHAMAN_EVAL_OPTION1"),
                ("siteId", "SHAMAN_EVAL_SITE_ID"), ("inputId", "SHAMAN_EVAL_INPUT_ID"),
                ("baselineId", "SHAMAN_EVAL_BASELINE_ID"), ("model", "SHAMAN_EVAL_MODEL"),
                ("rounds", "SHAMAN_EVAL_ROUNDS"))


def eval_run(env):
    """Actions の入力から作る評価の行。ステップが空なら None（シートに手で書いた待機中の行を使う）。"""
    run = {key: env.get(name, "").strip() for key, name in EVAL_RUN_ENV}
    if not run["step"]:
        return None
    if run["step"] == "WP":
        # 同じHTTPノードをサイトの件数だけ逐次実行。最大5サイトはEval: Pick Runで検証する。
        run["rounds"] = str(len([v for v in run["siteId"].split(",") if v.strip()]))
    run["runId"] = env.get("GITHUB_RUN_ID", "").strip()
    return run


# 本番の実行から、評価の入力を取るノード（そのステップの Build ノードへつながる本番のノードと、その出力の番号）。
# 例：P4 の入力は、本番で Build P4 Request が受け取る Parse Content の出力（DOCS/PROMPT_EVAL_DESIGN.md §4.3）。
IMPORT_SOURCES = {"P2": ("Parse Research", 0), "P3": ("Parse Outline", 0), "P4": ("Parse Content", 0),
                  "P5": ("P4: Retry?", 1)}
IMPORT_ENV = "SHAMAN_EVAL_INPUT_EXECUTION"


def _api_get(env, open_url):
    key = env.get("N8N_DEPLOY_API_KEY", "").strip()
    if not key:
        raise RuntimeError("N8N_DEPLOY_API_KEY is missing: cannot read the n8n execution")

    def get(path):
        req = urllib.request.Request(base_url(env) + "/api/v1" + path,
                                     headers={"Accept": "application/json", "X-N8N-API-KEY": key})
        with open_url(req, timeout=25) as res:
            return json.loads(res.read().decode("utf-8"))
    return get


def import_input(env, open_url, run):
    """本番の n8n 実行から、run のステップの入力を取る（Webhook の本文の evalRun.importData にする）。

    本番の Workflow の実行であること、そのノードが動いて出力があることを確かめる。取れなければ理由を付けて止める。
    """
    execution_id = env.get(IMPORT_ENV, "").strip()
    if not execution_id:
        return None
    if not re.fullmatch(r"\d+", execution_id):
        raise RuntimeError("eval_input_execution must be an n8n execution ID (digits): " + execution_id)
    if run is None:
        raise RuntimeError("eval_input_execution needs eval_step")
    if run.get("inputId"):
        raise RuntimeError("use either eval_input_id or eval_input_execution, not both")
    if run["step"] not in IMPORT_SOURCES:
        raise RuntimeError("eval_input_execution supports steps " + ", ".join(IMPORT_SOURCES) + ": " + run["step"])
    node, output = IMPORT_SOURCES[run["step"]]
    get = _api_get(env, open_url)
    try:
        name = WORKFLOWS["production"][1]
        flows = [w for w in get("/workflows?limit=100&name=" + urllib.parse.quote(name)).get("data") or []
                 if w.get("name") == name]
        execution = get("/executions/" + execution_id + "?includeData=true")
    except urllib.error.HTTPError as exc:
        raise RuntimeError("cannot read n8n execution %s: API HTTP %d%s" % (
            execution_id, exc.code, ": the API key needs " + API_SCOPES if exc.code == 403 else "")) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError("cannot read n8n execution %s: %s" % (execution_id, type(exc).__name__)) from None
    if len(flows) != 1 or str(execution.get("workflowId")) != str(flows[0].get("id")):
        raise RuntimeError("n8n execution %s is not an execution of %s" % (execution_id, name))
    runs = (((execution.get("data") or {}).get("resultData") or {}).get("runData") or {}).get(node) or []
    try:
        # 最後に動いたときの出力（P4: Retry? は、やり直しのあと P5 へ進んだときの出力）。
        data = runs[-1]["data"]["main"][output][0]["json"]
    except (IndexError, KeyError, TypeError):
        data = None
    if not isinstance(data, dict) or not data:
        raise RuntimeError("n8n execution %s has no output of %s (output %d): the execution data may not be saved,"
                           " or it stopped before that node" % (execution_id, node, output))
    return {"executionId": execution_id, "node": node, "data": data}


# 止まった実行の行を再開する（本番だけ）。n8n の実行 ID。DOCS/RUN_FROM_ACTIONS.md「止まった行の再開」。
RECOVER_ENV = "SHAMAN_RECOVER_EXECUTION"
# 止まった実行として扱う状態（動いている・待っている実行は再開しない）。
RECOVER_STATUSES = ("crashed", "error", "canceled")


# 本番の Workflow で、キューの行を 1 つずつ処理するループのノード。
ROW_LOOP_NODE = "Loop Over Items"


def _wordpress_and_after(workflow):
    """WordPress のノード（名前に WP を含む）と、そこからつながる先のノード（送信中・送信済みの可能性がある工程）。

    行ごとのループ（Loop Over Items）に戻ったところで止める（その先は次の行の処理）。
    """
    conns = workflow.get("connections") or {}
    seen = {n.get("name") for n in workflow.get("nodes") or [] if "WP" in str(n.get("name"))}
    todo = list(seen)
    while todo:
        for outputs in ((conns.get(todo.pop()) or {}).get("main") or []):
            for c in outputs or []:
                if c.get("node") not in seen and c.get("node") != ROW_LOOP_NODE:
                    seen.add(c.get("node"))
                    todo.append(c.get("node"))
    return seen


def recover_check(env, open_url, mode, workflow_dir=WORKFLOW_DIR):
    """recover_execution の実行が、行を最初からやり直してよい状態かを n8n の API で確かめる。

    本番の実行であること、止まっていること（crashed・error・canceled）、WordPress のノード（名前に WP を含む）が
    1 つも動いていないこと（下書きも画像も送っていない）、本番の実行がほかに動いていないことを確かめる。
    よければ Webhook の本文に入れる {"executionId": ...} を返す。指定が無ければ None。
    """
    execution_id = env.get(RECOVER_ENV, "").strip()
    if not execution_id:
        return None
    if mode != "production":
        raise RuntimeError("recover_execution is only for workflow=production")
    if not re.fullmatch(r"\d+", execution_id):
        raise RuntimeError("recover_execution must be an n8n execution ID (digits): " + execution_id)
    get = _api_get(env, open_url)
    try:
        name = WORKFLOWS["production"][1]
        flows = [w for w in get("/workflows?limit=100&name=" + urllib.parse.quote(name)).get("data") or []
                 if w.get("name") == name]
        execution = get("/executions/" + execution_id + "?includeData=true")
        flow_id = str(flows[0].get("id")) if len(flows) == 1 else None
        running = []
        if flow_id is not None:
            for status in ("running", "waiting", "new"):
                running += get("/executions?limit=20&status=" + status + "&workflowId=" + urllib.parse.quote(flow_id)).get("data") or []
    except urllib.error.HTTPError as exc:
        raise RuntimeError("cannot read n8n execution %s: API HTTP %d%s" % (
            execution_id, exc.code, ": the API key needs " + API_SCOPES if exc.code == 403 else "")) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError("cannot read n8n execution %s: %s" % (execution_id, type(exc).__name__)) from None
    if flow_id is None or str(execution.get("workflowId")) != flow_id:
        raise RuntimeError("n8n execution %s is not an execution of %s" % (execution_id, name))
    if execution.get("status") not in RECOVER_STATUSES:
        raise RuntimeError("n8n execution %s is %s, not stopped (%s): its row is not recovered" % (
            execution_id, execution.get("status"), "/".join(RECOVER_STATUSES)))
    # 実行の記録が無い・壊れているときは、WordPress に送っていないと言えないので再開しない（PR #90 レビュー B2）。
    result = (execution.get("data") or {}).get("resultData") if isinstance(execution.get("data"), dict) else None
    run_data = result.get("runData") if isinstance(result, dict) else None
    last = result.get("lastNodeExecuted") if isinstance(result, dict) else None
    if not isinstance(run_data, dict) or not run_data or not isinstance(last, str) or last not in run_data:
        raise RuntimeError("n8n execution %s has no usable execution data (runData / lastNodeExecuted): cannot tell"
                           " whether it sent anything to WordPress; check WordPress and fix the row by hand" % execution_id)
    filename, _name = WORKFLOWS["production"]
    after = _wordpress_and_after(json.loads((Path(workflow_dir) / filename).read_text(encoding="utf-8")))
    wordpress = sorted(n for n in run_data if n in after or "WP" in n)
    if wordpress or last in after:
        raise RuntimeError("n8n execution %s may have sent to WordPress (last node %s; ran %s): check WordPress and fix"
                           " the row by hand" % (execution_id, last, ", ".join(wordpress) or "-"))
    if running:
        raise RuntimeError("another production execution is running (%s): try again after it finishes"
                           % ", ".join(str(x.get("id")) for x in running))
    return {"executionId": execution_id}


def run(mode, env, open_url=urllib.request.urlopen, workflow_dir=WORKFLOW_DIR):
    token = env.get("SHAMAN_RUN_WEBHOOK_TOKEN", "").strip()
    if not token:
        raise RuntimeError("SHAMAN_RUN_WEBHOOK_TOKEN is missing: register the GitHub Secret (DOCS/RUN_FROM_ACTIONS.md)")
    hook_node, path = run_webhook(mode, workflow_dir)
    url = base_url(env) + "/webhook/" + path
    # この呼び出しの印。失敗したとき、n8n の実行履歴から、この呼び出しの実行だけを探すのに使う。
    nonce = uuid.uuid4().hex
    body = {"source": "github-actions", "mode": mode, "runId": env.get("GITHUB_RUN_ID", ""), "nonce": nonce}
    recover = recover_check(env, open_url, mode, workflow_dir)
    if recover is not None:
        body["recover"] = recover
    if mode == "test":
        run_row = eval_run(env)
        imported = import_input(env, open_url, run_row)
        if imported is not None:
            run_row["importData"] = imported
        if run_row is not None:
            body["evalRun"] = run_row
    req = urllib.request.Request(url, method="POST", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Accept": "application/json",
                                          RUN_WEBHOOK_HEADER: token})
    started = time.time()
    try:
        with open_url(req, timeout=TIMEOUTS[mode]) as res:
            raw = res.read()
    except urllib.error.HTTPError as exc:
        detail = _summary(exc.read() or b"")
        hint = {
            403: "the run token does not match the n8n Credential (SHAMAN / Run Webhook)",
            404: "the webhook is not registered: the workflow is not published yet (merge to main deploys it)",
            500: "the workflow failed: see the n8n execution history",
        }.get(exc.code, "")
        if exc.code == 500:
            detail += "\n" + diagnose(mode, env, open_url, hook_node, nonce, started)
        raise RuntimeError(redact("n8n webhook HTTP %d%s\n%s" % (exc.code, (": " + hint) if hint else "", detail),
                                  env)) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        message = "Cannot reach local n8n webhook or it timed out: " + type(exc).__name__
        # 時間切れでも、n8n の実行がどこで止まっているかを出す（2026-11-08、本番の実行 11 が 5 時間応答しなかった）。
        if isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError):
            message += " after %ds\n" % (time.time() - started) + diagnose(mode, env, open_url, hook_node, nonce, started)
        raise RuntimeError(redact(message, env)) from None
    try:
        return redact(judge(mode, raw), env)
    except RuntimeError as exc:
        message = str(exc)
        # 結果が返らなかった（途中で止まった）ときも、n8n の実行のエラーを読む。
        if "no explicit run result" in message:
            message += "\n" + diagnose(mode, env, open_url, hook_node, nonce, started)
        raise RuntimeError(redact(message, env)) from None


# 実行の一覧から、この呼び出しの実行を探すとき、何件まで中身を読むか。
DIAGNOSE_CANDIDATES = 5
API_SCOPES = "workflow:list, execution:list and execution:read"


def _parse_time(value):
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _webhook_nonce(execution, hook_node):
    """実行のデータから、Webhook が受け取った nonce を取る。"""
    run_data = (((execution.get("data") or {}).get("resultData") or {}).get("runData") or {}).get(hook_node) or []
    try:
        return run_data[0]["data"]["main"][0][0]["json"]["body"].get("nonce")
    except (IndexError, KeyError, TypeError, AttributeError):
        return None


def _iso(ms):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _timing(execution, result):
    """実行と、止まったノードの時間（通信が切れた・時間切れの見分けに使う。2026-11-07、本番の実行 8 の P4）。"""
    parts = []
    if execution.get("startedAt") and execution.get("stoppedAt"):
        start, stop = _parse_time(execution["startedAt"]), _parse_time(execution["stoppedAt"])
        if start is not None and stop is not None:
            parts.append("execution %s -> %s (%ds)" % (execution["startedAt"], execution["stoppedAt"], stop - start))
    runs = (result.get("runData") or {}).get(result.get("lastNodeExecuted")) or []
    last = runs[-1] if runs and isinstance(runs[-1], dict) else {}
    if isinstance(last.get("startTime"), (int, float)) and isinstance(last.get("executionTime"), (int, float)):
        parts.append("last node started %s, ran %ds" % (_iso(last["startTime"]), last["executionTime"] / 1000))
    return ("\n  " + "; ".join(parts)) if parts else ""


def diagnose(mode, env, open_url, hook_node, nonce, started):
    """失敗したとき、n8n の API から、この呼び出しの実行のエラーを取る（取れなければ理由だけ返す）。

    Webhook（lastNode）は、途中で失敗すると中身の無い応答を返す。理由を Actions のログに出すため。
    同じ Workflow の別の実行（画面からの実行など）を取り違えないよう、Webhook が受け取った nonce が一致する
    実行だけを使う。見つからなければ「特定できない」と返す。API キー（N8N_DEPLOY_API_KEY）に
    workflow:list・execution:list・execution:read の権限が要る（n8n 2.35.7 の公開 API）。
    """
    if not env.get("N8N_DEPLOY_API_KEY", "").strip():
        return "(no N8N_DEPLOY_API_KEY: cannot read the n8n execution)"
    get = _api_get(env, open_url)
    try:
        name = WORKFLOWS[mode][1]
        flows = [w for w in get("/workflows?limit=100&name=" + urllib.parse.quote(name)).get("data") or []
                 if w.get("name") == name]
        if len(flows) != 1:
            return "(cannot find the n8n workflow: " + name + ")"
        runs = get("/executions?limit=20&workflowId=" + urllib.parse.quote(str(flows[0]["id"]))).get("data") or []
        # 呼び出しより前に始まった実行は、この呼び出しのものではない（同じ機械の時計。1 分の余裕を見る）。
        recent = [x for x in runs if (_parse_time(x.get("startedAt")) or 0) >= started - 60]
        for summary in recent[:DIAGNOSE_CANDIDATES]:
            execution = get("/executions/" + urllib.parse.quote(str(summary["id"])) + "?includeData=true")
            if _webhook_nonce(execution, hook_node) != nonce:
                continue
            result = (execution.get("data") or {}).get("resultData") or {}
            error = result.get("error") or {}
            text = "n8n execution %s: status=%s, last node=%s, error=%s" % (
                execution.get("id"), execution.get("status"), result.get("lastNodeExecuted"),
                str(error.get("message") or "")[:1000] + ((" / " + str(error.get("description"))[:500])
                                                         if error.get("description") else ""))
            text += _timing(execution, result)
            return redact(text, env)
        return "(cannot identify the n8n execution of this run: no execution received this request's nonce)"
    except urllib.error.HTTPError as exc:
        return "(cannot read the n8n execution: API HTTP %d%s)" % (
            exc.code, ": the API key needs " + API_SCOPES if exc.code == 403 else "")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
        return "(cannot read the n8n execution: " + type(exc).__name__ + ")"


def judge(mode, raw):
    """応答の shamanRun で成否を決める。ok が true でなければ、理由と応答を付けて失敗にする。"""
    output = _summary(raw)
    try:
        result = json.loads(raw.decode("utf-8"))
    except ValueError:
        result = None
    run = result.get("shamanRun") if isinstance(result, dict) else None
    if not isinstance(run, dict):
        raise RuntimeError("n8n returned no explicit run result (shamanRun): the workflow may have stopped early,"
                           " e.g. no waiting rows\n" + output)
    if run.get("workflow") != mode:
        raise RuntimeError("n8n run result is for another workflow: " + str(run.get("workflow")))
    if run.get("ok") is not True:
        raise RuntimeError("workflow finished with NG: " + str(run.get("reason") or "unknown") + "\n" + output)
    return output


# 実行の中身を読むとき、出すノードの数（最後に動いたものから）。
INSPECT_NODES = 25


def inspect(env, open_url, execution_id):
    """n8n の実行 1 件の状態と、ノードごとの開始時刻・所要時間・エラーを返す（Workflow は動かさない）。

    データの中身（記事の本文など）は出さない。止まった・遅いノードを Actions から見つけるため。
    """
    if not re.fullmatch(r"\d+", execution_id or ""):
        raise RuntimeError("inspect_execution must be an n8n execution ID (digits): " + str(execution_id))
    get = _api_get(env, open_url)
    try:
        execution = get("/executions/" + execution_id + "?includeData=true")
    except urllib.error.HTTPError as exc:
        raise RuntimeError("cannot read n8n execution %s: API HTTP %d%s" % (
            execution_id, exc.code, ": the API key needs " + API_SCOPES if exc.code == 403 else "")) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError("cannot read n8n execution %s: %s" % (execution_id, type(exc).__name__)) from None
    result = (execution.get("data") or {}).get("resultData") or {}
    error = result.get("error") or {}
    lines = ["n8n execution %s: workflow=%s, status=%s, mode=%s, started=%s, stopped=%s" % (
        execution.get("id"), execution.get("workflowId"), execution.get("status"), execution.get("mode"),
        execution.get("startedAt"), execution.get("stoppedAt") or "(not stopped)"),
        "last node executed: %s" % result.get("lastNodeExecuted")]
    if error:
        lines.append("error: %s / %s" % (str(error.get("message") or "")[:1000], str(error.get("description") or "")[:500]))
    runs = []
    for name, node_runs in (result.get("runData") or {}).items():
        for i, one in enumerate(node_runs or []):
            if isinstance(one, dict) and isinstance(one.get("startTime"), (int, float)):
                runs.append((one["startTime"], name, i, one))
    runs.sort(key=lambda x: x[0])
    lines.append("node runs: %d (last %d shown, oldest first)" % (len(runs), min(len(runs), INSPECT_NODES)))
    for start, name, i, one in runs[-INSPECT_NODES:]:
        took = one.get("executionTime")
        node_error = one.get("error") or {}
        lines.append("  %s %s%s %s%s" % (
            _iso(start), name, " #%d" % (i + 1) if i else "",
            "%.1fs" % (took / 1000) if isinstance(took, (int, float)) else "?s",
            (" ERROR " + str(node_error.get("message") or "")[:300]) if node_error else ""))
    # 動いている途中なら、最後のノードのあとに何が動いているかは runData に無い。今の時刻と比べられるよう出す。
    if execution.get("status") in ("running", "waiting", "new") and runs:
        last_start, last_name, _, last = runs[-1]
        end = last_start + (last.get("executionTime") or 0)
        lines.append("still %s: the next node after %s has been running since %s (%ds ago)" % (
            execution.get("status"), last_name, _iso(end), time.time() - end / 1000))
    return redact("\n".join(lines), env)


def main(argv=None, env=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=WORKFLOWS, required=True)
    parser.add_argument("--inspect", metavar="EXECUTION_ID",
                        help="read one n8n execution (status and node timings) instead of running the workflow")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    if args.inspect:
        print("SHAMAN_INSPECT_OK", args.inspect)
        print(inspect(env, urllib.request.urlopen, args.inspect.strip()))
        return
    output = run(args.mode, env)
    print("SHAMAN_RUN_OK", args.mode)
    print(output)


def cli(argv=None, env=None):
    """main を動かし、失敗なら伏せた理由を出して 1 を返す。"""
    env = os.environ if env is None else env
    try:
        main(argv, env)
        return 0
    except (RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print("SHAMAN_RUN_FAILED: " + redact(str(exc), env), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(cli())
