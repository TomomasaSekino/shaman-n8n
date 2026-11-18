#!/usr/bin/env python3
"""SHAMAN: merge-gated macOS n8n deploy over local API (Google Sheets queue)."""
import argparse
import copy
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

WORKFLOWS = {
    # どれも公開して配備する。入口は合言葉付きの Webhook だけで、Actions から実行する（DOCS/RUN_FROM_ACTIONS.md）。
    # プロンプト評価 Workflow（DOCS/PROMPT_EVAL_DESIGN.md）。前の Test Workflow を、同じ ID のまま置き換える。
    "test": ("SHAMAN_Prompt_Eval.json", "SHAMAN: Prompt Eval"),
    "production": ("SHAMAN_Workflow.json", "SHAMAN"),
    # 外部サービスへの接続だけを確かめる Workflow（DOCS/CONNECTION_CHECK.md）。
    "connection": ("SHAMAN_Connection_Check.json", "SHAMAN: Connection Check"),
}
WORKFLOW_IDS = {"test": "shaman_test", "production": "shaman", "connection": "shaman_connection_check"}
# Repository Variable で置き換える値。Credential の ID は入れない（n8n の一覧から取る。resolve_credentials）。
PLACEHOLDERS = frozenset({
    "SHAMAN_QUEUE_SPREADSHEET_ID", "SHAMAN_QUEUE_TEST_SPREADSHEET_ID", "SHAMAN_DRAFT_DIR",
})
# Webhook は、この Credential（Header Auth）で合言葉を確かめるものだけを許す。合言葉の無い入口を公開しない。
RUN_WEBHOOK_CREDENTIAL_ID = "__ENV:SHAMAN_RUN_WEBHOOK_CREDENTIAL_ID__"
# Credential の ID の置き場所。ノードの credentials に {id: __ENV:<変数>__, name: <n8n の名前>} で書く。
CREDENTIAL_PLACEHOLDER = re.compile(r"SHAMAN_[A-Z0-9_]+_CREDENTIAL_ID")
EXPRESSION = re.compile(r"__ENV:([A-Z][A-Z0-9_]*)__")
# ID は英数字と - _ だけ。Drive のクエリ文字列や JSON に埋め込むので、引用符などを通さない。
ID_VALUE = re.compile(r"[A-Za-z0-9_-]{1,128}")
# 前の名前。n8n にこの名前の Workflow があれば、新しい名前で同じ ID のまま上書きする（増やさない）。
LEGACY_BY_MODE = {
    "test": frozenset({"SHAMAN: Test Workflow", "SHAMAN / Blog / Pipeline Test"}),
    "production": frozenset({"SHAMAN / Blog / Pipeline"}),
    "connection": frozenset(),
}
SHEET_NODES = {
    # プロンプト評価は、試験用スプレッドシートの評価のシートだけを読み書きする（キューは読まない）。
    # シートと見出しが無ければ作る（Ensure Sheets・Header … Write）。Actions の入力から評価の行を足す（Add Run）。
    # 本番の実行から取り込んだ入力は、評価データに書く（Save Import）。
    "test": frozenset({"Eval: Ensure Sheets", "Eval: Header Runs Write", "Eval: Header Results Write",
                       "Eval: Header Data Write", "Eval: Header Log Write", "Eval: Save Import", "Eval: Add Run", "Eval: Read Runs", "Eval: Claim Run", "Eval: Log Start", "Eval: Read Input",
                       "Eval: Read Baseline", "Eval: Log Call", "Eval: Save Data", "Eval: Save Result",
                       "Eval: Mark Done", "Eval: Log End", "Eval: Mark Failed", "Eval: Log Stop"}),
    "production": frozenset({"Queue: Read", "Sites: Read Registry", "Queue: Recheck", "Queue: Claim", "Queue: Reread",
                             "Queue: Final Recheck", "Queue: Update",
                             # 実行結果のための読み直し（Run Result。DOCS/RUN_FROM_ACTIONS.md）
                             "Queue: Result Read",
                             # API使用量のシート（DOCS/API_USAGE_LOG.md）。無ければ作り、呼び出しごとに 1 行を書く。
                             "Usage: Ensure Sheet", "Usage: Header Write",
                             "Usage: Write P1", "Usage: Write P2", "Usage: Write P3", "Usage: Write P4",
                             "Usage: Write P5", "Usage: Write P6A", "Usage: Write P6"}),
    "connection": frozenset({"Conn: Sheets Read", "Conn: Sheets Write", "Conn: Sheets Reread"}),
}

def validate_env(env, mode, publish):
    base = env.get("N8N_BASE_URL", "http://127.0.0.1:5678")
    url = urllib.parse.urlsplit(base)
    if (url.scheme != "http" or url.hostname not in ("localhost", "127.0.0.1")
            or url.port != 5678 or url.path not in ("", "/")
            or url.username or url.password or url.query or url.fragment):
        raise RuntimeError("n8n API URL must be local http://127.0.0.1:5678")
    if not env.get("N8N_DEPLOY_API_KEY", "").strip():
        raise RuntimeError("N8N_DEPLOY_API_KEY is missing: create API key in macOS n8n, then register GitHub Secret")
    if not publish:
        raise RuntimeError("Every SHAMAN workflow is deployed with --publish (run from Actions via its webhook)")
    # 公開は、オーナーが main にマージした PR のイベントからだけ行う。
    if env.get("GITHUB_ACTIONS") != "true" or env.get("GITHUB_EVENT_NAME") != "pull_request":
        raise RuntimeError("Publish requires GitHub Actions merged PR event")
    if env.get("GITHUB_REF") != "refs/heads/main":
        raise RuntimeError("Publish requires main ref")
    repository = env.get("GITHUB_REPOSITORY", "")
    owner = repository.split("/", 1)[0] if repository.count("/") == 1 else ""
    event_path = env.get("GITHUB_EVENT_PATH", "")
    if not owner or not event_path:
        raise RuntimeError("Publish requires GitHub repository and event provenance")
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("Cannot read merged pull request event") from exc
    pr = event.get("pull_request") or {}
    merged_by = pr.get("merged_by") or {}
    sender = event.get("sender") or {}
    if (event.get("action") != "closed"
            or pr.get("merged") is not True
            or pr.get("base", {}).get("ref") != "main"
            or merged_by.get("login") != owner
            or merged_by.get("type") != "User"
            or sender.get("login") != owner
            or sender.get("type") != "User"
            or not env.get("SHAMAN_MERGED_COMMIT_SHA")
            or pr.get("merge_commit_sha") != env.get("SHAMAN_MERGED_COMMIT_SHA")
            or pr.get("head", {}).get("sha") != env.get("SHAMAN_MERGED_PR_HEAD_SHA")
            or env.get("GITHUB_SHA") != env.get("SHAMAN_MERGED_COMMIT_SHA")):
        raise RuntimeError("Publish requires a verified repository owner merge event and SHA")
    return base.rstrip("/")

def load_workflow(path, mode, env):
    expected_filename, expected_name = WORKFLOWS[mode]
    if Path(path).name != expected_filename:
        raise RuntimeError("Unexpected workflow filename")
    workflow = json.loads(Path(path).read_text(encoding="utf-8"))
    if (workflow.get("name") != expected_name
            or workflow.get("id") != WORKFLOW_IDS[mode]):
        raise RuntimeError("Workflow identity mismatch")
    if not isinstance(workflow.get("nodes"), list) or not workflow["nodes"]:
        raise RuntimeError("Workflow nodes are missing")
    names = [n.get("name") for n in workflow["nodes"]]
    if any(not isinstance(n, str) for n in names) or len(names) != len(set(names)):
        raise RuntimeError("Duplicate or invalid node name")
    if not isinstance(workflow.get("connections"), dict):
        raise RuntimeError("Workflow connections are missing")
    if not isinstance(workflow.get("settings"), dict):
        raise RuntimeError("Workflow settings are missing")

    # Validate the Google Sheets nodes and ensure production/test isolation.
    # The connection check writes only to the test spreadsheet.
    sheet_names = SHEET_NODES[mode]
    sheets = [node for node in workflow["nodes"]
              if node.get("type") == "n8n-nodes-base.googleSheets"]
    expected_sheet = ("__ENV:SHAMAN_QUEUE_SPREADSHEET_ID__" if mode == "production"
                      else "__ENV:SHAMAN_QUEUE_TEST_SPREADSHEET_ID__")
    if len(sheets) != len(sheet_names) or {node["name"] for node in sheets} != sheet_names:
        raise RuntimeError("Google Sheets queue nodes are missing or duplicated")
    if any(node.get("parameters", {}).get("documentId", {}).get("value") != expected_sheet
           for node in sheets):
        raise RuntimeError("Production and test Sheets IDs are not isolated")
    # 公開するので、Webhook は合言葉（Header Auth の Credential）を確かめるものだけにする。
    for node in workflow["nodes"]:
        if node.get("type") == "n8n-nodes-base.webhook":
            params = node.get("parameters") or {}
            creds = node.get("credentials") or {}
            if (params.get("authentication") != "headerAuth" or set(creds) != {"httpHeaderAuth"}
                    or creds["httpHeaderAuth"].get("id") != RUN_WEBHOOK_CREDENTIAL_ID):
                raise RuntimeError("Webhook must require the run token (Header Auth): " + str(node.get("name")))
    if mode == "test":
        # WordPress GET認証と、試験ドメインへの明示承認済み下書きPOSTだけを許す。
        # Google Drive は、P6（画像）の参考画像を読むだけ（探す・ダウンロードする）。OpenAI は P6: Generate Image だけ。
        # Webhook は、Actions から実行する入口（合言葉付き。上で確かめた）だけを許す。
        forbidden = {"n8n-nodes-base.discord", "n8n-nodes-base.wordpress"}
        for node in workflow["nodes"]:
            creds = set((node.get("credentials") or {}))
            params = node.get("parameters") or {}
            drive_read = (node.get("type") == "n8n-nodes-base.googleDrive"
                          and params.get("operation") in ("search", "download"))
            wp_auth_probe = (node.get("name") == "WP: Probe GET"
                             and node.get("type") == "n8n-nodes-base.httpRequest"
                             and creds == {"httpBasicAuth"}
                             and params.get("method") == "GET"
                             and params.get("genericAuthType") == "httpBasicAuth"
                             and "/wp-json/wp/v2/users/me?context=edit" in str(params.get("url", "")))
            wp_draft_probe = (node.get("name") == "WP: Test Draft POST"
                              and node.get("type") == "n8n-nodes-base.httpRequest"
                              and creds == {"httpBasicAuth"}
                              and params.get("method") == "POST"
                              and params.get("genericAuthType") == "httpBasicAuth"
                              and params.get("jsonBody") == "={{ $json.wpPostBody }}"
                              and "/wp-json/wp/v2/posts" in str(params.get("url", "")))
            if (node.get("type") in forbidden or "wordpressApi" in creds or "discordWebhookApi" in creds
                    or ("httpBasicAuth" in creds and not (wp_auth_probe or wp_draft_probe))
                    or (node.get("type") == "n8n-nodes-base.googleDrive" and not drive_read)
                    or ("openAiApi" in creds and node.get("name") != "P6: Generate Image")):
                raise RuntimeError("Prompt eval workflow must not touch WordPress, Discord, write to Drive"
                                   " or call OpenAI outside P6: " + str(node.get("name")))

    needed = set(EXPRESSION.findall(json.dumps(workflow, ensure_ascii=False)))
    unknown = {key for key in needed - PLACEHOLDERS if not CREDENTIAL_PLACEHOLDER.fullmatch(key)}
    if unknown:
        raise RuntimeError("Unknown workflow placeholders: " + ", ".join(sorted(unknown)))
    unresolved = [key for key in sorted(needed)
                  if CREDENTIAL_PLACEHOLDER.fullmatch(key) and not env.get(key, "").strip()]
    if unresolved:
        raise RuntimeError("Credential IDs were not resolved from n8n: " + ", ".join(unresolved))
    missing = [key for key in sorted(needed - set(unresolved)) if not env.get(key, "").strip()]
    if missing:
        raise RuntimeError("Missing Repository Variables: " + ", ".join(missing))
    bad_ids = [key for key in sorted(needed)
               if (key.endswith("_CREDENTIAL_ID") or key.endswith("_SPREADSHEET_ID"))
               and not ID_VALUE.fullmatch(env[key].strip())]
    if bad_ids:
        raise RuntimeError("Repository Variables must be plain IDs: " + ", ".join(bad_ids))
    # 試験用のシートが本番のシートと同じ ID なら、試験・接続確認が本番のキューに書いてしまう。
    test_sheet = env.get("SHAMAN_QUEUE_TEST_SPREADSHEET_ID", "").strip()
    if test_sheet and test_sheet == env.get("SHAMAN_QUEUE_SPREADSHEET_ID", "").strip():
        raise RuntimeError("Test and production spreadsheet IDs must differ")
    if "SHAMAN_DRAFT_DIR" in needed:
        # luna（macOS）のパスとして判定する。どの OS でテストしても同じ結果にする。
        directory = PurePosixPath(env["SHAMAN_DRAFT_DIR"])
        if (not directory.is_absolute() or ".." in directory.parts
                or not str(directory).startswith("/Users/")):
            raise RuntimeError("SHAMAN_DRAFT_DIR must be a fixed macOS directory under /Users/")

    def replace(value):
        if isinstance(value, dict):
            return {k: replace(v) for k, v in value.items()}
        if isinstance(value, list):
            return [replace(v) for v in value]
        if isinstance(value, str):
            return EXPRESSION.sub(lambda m: env[m.group(1)].strip(), value)
        return value

    payload = replace({k: copy.deepcopy(workflow[k])
                       for k in ("name", "nodes", "connections", "settings")})
    if EXPRESSION.search(json.dumps(payload, ensure_ascii=False)):
        raise RuntimeError("Unresolved workflow placeholder")
    if mode == "production":
        sites_path = Path(path).parent.parent / "config" / "sites.json"
        if not json.loads(sites_path.read_text(encoding="utf-8")):
            raise RuntimeError("sites.json is empty; production deployment refused")
        for node in payload["nodes"]:
            if node.get("type") == "n8n-nodes-base.code":
                script = node.get("parameters", {}).get("jsCode", "")
                if "require(" in script or "$env." in script:
                    raise RuntimeError("Production Code node still requires legacy runtime access: " + node["name"])
    return payload

class N8n:
    def __init__(self, base_url, key, open_url=None):
        self.url = base_url + "/api/v1"
        self.key = key
        self._open = open_url or urllib.request.urlopen

    def request(self, method, path, data=None):
        req = urllib.request.Request(self.url + path, method=method,
                                     headers={"Accept": "application/json", "X-N8N-API-KEY": self.key})
        if data is not None:
            req.add_header("Content-Type", "application/json")
            req.data = json.dumps(data, ensure_ascii=False).encode("utf-8")
        try:
            with self._open(req, timeout=25) as res:
                return json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError("n8n API HTTP %d at %s %s" % (exc.code, method, path.split("?")[0])) from None
        except (urllib.error.URLError, TimeoutError):
            raise RuntimeError("Cannot reach local n8n API") from None

    def list_workflows(self):
        return self._list("/workflows", "workflow")

    def list_credentials(self):
        # 名前・種類・ID だけが返る（Secret は返らない）。API キーに credential:list の権限が要る。
        try:
            return self._list("/credentials", "credential")
        except RuntimeError as exc:
            if "HTTP 403" in str(exc):
                raise RuntimeError("N8N_DEPLOY_API_KEY cannot list n8n Credentials (needs credential:list)") from None
            raise

    def _list(self, path, label):
        result, cursor, visited = [], None, set()
        while True:
            qs = "?limit=100" + ("&cursor=" + urllib.parse.quote(cursor) if cursor else "")
            obj = self.request("GET", path + qs)
            if not isinstance(obj.get("data"), list):
                raise RuntimeError("Invalid n8n " + label + " listing")
            result.extend(obj["data"])
            cursor = obj.get("nextCursor")
            if not cursor:
                return result
            if cursor in visited:
                raise RuntimeError("n8n pagination cursor loop")
            visited.add(cursor)

def credential_requirements(workflow):
    """Workflow が使う Credential。{変数: (種類, n8n の名前)}。同じ変数が別の Credential を指していれば止める。"""
    requirements = {}
    for node in workflow.get("nodes") or []:
        for cred_type, cred in (node.get("credentials") or {}).items():
            match = EXPRESSION.fullmatch(str((cred or {}).get("id", "")))
            if not match or not CREDENTIAL_PLACEHOLDER.fullmatch(match.group(1)):
                raise RuntimeError("Credential ID must be a placeholder: " + str(node.get("name")))
            name = (cred or {}).get("name")
            if not isinstance(name, str) or not name.strip():
                raise RuntimeError("Credential name is missing: " + str(node.get("name")))
            key = (cred_type, name)
            if requirements.setdefault(match.group(1), key) != key:
                raise RuntimeError("One placeholder points to different Credentials: " + match.group(1))
    return requirements

def _same_type_names(entries, cred_type):
    """見つからないとき、同じ種類の Credential の名前を示す（名前だけ。値は API も返さない）。"""
    names = sorted({str(e.get("name")) for e in entries if e.get("type") == cred_type})
    return "; same type in n8n: " + (", ".join(names) if names else "(none)")

def resolve_credentials(client, requirements, names=None):
    """n8n の Credential を 1 件に決める。

    名前は問わない（n8n が付けた既定の名前のままでよい）。その種類の Credential が n8n に 1 件だけなら、それを使う。
    同じ種類が 2 件以上あるときだけ、Workflow に書いた名前と一致する 1 件を使う。
    決まらないものが 1 つでもあれば、全部を並べて、n8n を変える前に止める。
    names に dict を渡すと、決まった Credential の {ID: n8n の名前} を入れる（apply_credential_names で使う）。
    """
    if not requirements:
        return {}
    entries = client.list_credentials()
    resolved = {}
    problems = []
    for variable, (cred_type, name) in sorted(requirements.items()):
        same_type = [e for e in entries if e.get("type") == cred_type]
        hits = same_type if len(same_type) == 1 else [e for e in same_type if e.get("name") == name]
        if len(hits) != 1:
            reason = "not found: " if not same_type else ("is ambiguous: " if hits else "is ambiguous (name not matched): ")
            problems.append(reason + name + " (" + cred_type + ")" + _same_type_names(entries, cred_type))
            continue
        credential_id = str(hits[0].get("id") or "")
        if not ID_VALUE.fullmatch(credential_id):
            raise RuntimeError("n8n returned an invalid Credential ID: " + name)
        resolved[variable] = credential_id
        if names is not None:
            names[credential_id] = hits[0].get("name")
    if problems:
        raise RuntimeError("n8n Credential " + " | ".join(problems))
    return resolved

def apply_credential_names(payload, names):
    """ノードの Credential の名前を、n8n にある実際の名前にする。

    n8n は保存のとき、Credential の名前を ID の Credential の名前に書き換える。Workflow に書いた名前
    （`Anthropic / API`）と n8n の名前（`Anthropic API`）が違うと、読み戻しが一致しない。
    """
    for node in payload["nodes"]:
        for cred in (node.get("credentials") or {}).values():
            name = names.get(str((cred or {}).get("id", "")))
            if name:
                cred["name"] = name
    return payload

def _readback_differences(saved, payload):
    """読み戻しで違っていた所（ノードの名前とキーだけ。値は出さない）。"""
    diffs = []
    if saved.get("name") != payload["name"]:
        diffs.append("name")
    if saved.get("connections") != payload["connections"]:
        diffs.append("connections")
    saved_nodes = {n.get("name"): n for n in saved.get("nodes") or []}
    sent_nodes = {n.get("name"): n for n in payload["nodes"]}
    for node_name in sorted(set(saved_nodes) | set(sent_nodes), key=str):
        a, b = saved_nodes.get(node_name), sent_nodes.get(node_name)
        if a is None or b is None:
            diffs.append("node " + ("missing" if a is None else "added") + ": " + str(node_name))
        elif a != b:
            keys = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
            diffs.append("node " + str(node_name) + " [" + ", ".join(keys) + "]")
    if not diffs and saved.get("nodes") != payload["nodes"]:
        diffs.append("node order")
    return diffs

def deploy(client, payload, mode, publish):
    entries = client.list_workflows()
    name = payload["name"]
    # 旧版を残したまま新規Workflowを増殖させない。既存の未公開旧版を同じIDで移行する。
    legacy_names = LEGACY_BY_MODE[mode]
    known = {name} | legacy_names
    matches = [entry for entry in entries if entry.get("name") in known]
    if len(matches) > 1:
        raise RuntimeError("Current and legacy SHAMAN workflows coexist; refuse ambiguous deployment")
    if matches:
        wf_id = str(matches[0]["id"])
        endpoint = "/workflows/" + urllib.parse.quote(wf_id, safe="")
        existing = client.request("GET", endpoint)
        if existing.get("name") not in known:
            raise RuntimeError("Workflow ID resolved to an unexpected name")
        if existing.get("active") and existing.get("name") in legacy_names:
            raise RuntimeError("Published legacy workflow must not be overwritten")
        client.request("PUT", endpoint + "?publishIfActive=false", payload)
    else:
        created = client.request("POST", "/workflows", payload)
        wf_id = str(created.get("id") or "")
        if not wf_id:
            raise RuntimeError("n8n did not return the created workflow ID")
    endpoint = "/workflows/" + urllib.parse.quote(wf_id, safe="")
    saved = client.request("GET", endpoint)
    if (saved.get("name") != name or saved.get("nodes") != payload["nodes"]
            or saved.get("connections") != payload["connections"]):
        raise RuntimeError("n8n workflow readback mismatch: " + "; ".join(_readback_differences(saved, payload)))
    if any(saved.get("settings", {}).get(k) != v for k, v in payload["settings"].items()):
        raise RuntimeError("n8n workflow settings readback mismatch")
    if publish:
        version = saved.get("versionId")
        if not isinstance(version, str) or not version:
            raise RuntimeError("n8n did not return a publishable versionId")
        client.request("POST", endpoint + "/publish", {"versionId": version})
        live = client.request("GET", endpoint)
        if not live.get("active") or live.get("activeVersionId") != version:
            raise RuntimeError("n8n published version does not match merged source")
        print("SHAMAN_PUBLISHED", mode, wf_id)
    else:
        print("SHAMAN_DRAFT_VERIFIED", mode, wf_id)

def main(argv=None, env=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=WORKFLOWS, required=True)
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    url = validate_env(env, args.mode, args.publish)
    client = N8n(url, env["N8N_DEPLOY_API_KEY"])
    # Credential の ID と名前は、n8n にある Credential から種類で取る（Repository Variable に登録しない）。
    raw = json.loads(Path(args.workflow).read_text(encoding="utf-8"))
    names = {}
    resolved = resolve_credentials(client, credential_requirements(raw), names)
    payload = apply_credential_names(load_workflow(args.workflow, args.mode, {**env, **resolved}), names)
    deploy(client, payload, args.mode, args.publish)

if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        print("SHAMAN_DEPLOY_FAILED: " + str(exc), file=sys.stderr)
        sys.exit(1)
