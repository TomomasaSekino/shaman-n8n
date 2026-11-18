#!/usr/bin/env python3
"""PR #100 の検証用 n8n Workflow と、その実行記録だけを削除する。"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "http://127.0.0.1:5678/api/v1"
TARGET_ID = "8nhou1L3SzjhauvE"
TARGET_NAME = "SHAMAN: WP Credential Check"
TARGET_EXECUTION_ID = "1293"
PROTECTED_IDS = frozenset({
    "0hxaRjPczHWnBozO",  # 本番
    "5M8ojV1u5wpDmxRk",  # 接続確認
    "LAzmmWKkS0ih5FFz",  # プロンプト評価
})
PRODUCTION_ID = "0hxaRjPczHWnBozO"
MAX_PAGES = 1000


class CleanupError(Exception):
    pass


class Api:
    def __init__(self, key):
        if not key:
            raise CleanupError("N8N_DEPLOY_API_KEY_MISSING")
        self.key = key

    def call(self, method, path):
        if method not in ("GET", "DELETE") or not re.fullmatch(
            r"/(?:workflows|executions)(?:/[a-zA-Z0-9]+)?(?:\?[a-zA-Z0-9=&%_-]+)?", path
        ):
            raise CleanupError("INVALID_API_REQUEST")
        request = urllib.request.Request(
            BASE_URL + path,
            headers={"X-N8N-API-KEY": self.key, "Accept": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read()
                return response.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            # エラー応答の本文には機微情報があり得るため絶対に出力しない。
            if error.code == 404:
                return 404, None
            raise CleanupError("N8N_API_%s_HTTP_%d" % (method, error.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise CleanupError("N8N_API_CONNECTION_FAILED") from None


def get(api, kind, item_id):
    status, data = api.call("GET", "/%s/%s" % (kind, item_id))
    if status == 404:
        return None
    if status != 200 or not isinstance(data, dict):
        raise CleanupError("N8N_GET_INVALID_%s" % kind)
    return data


def list_all(api, kind, query=None):
    items, cursor, seen = [], None, set()
    for _ in range(MAX_PAGES):
        params = {"limit": "100"}
        if query:
            params.update(query)
        if cursor:
            params["cursor"] = cursor
        path = "/%s?%s" % (kind, urllib.parse.urlencode(params))
        status, page = api.call("GET", path)
        if status != 200 or not isinstance(page, dict) or not isinstance(page.get("data"), list):
            raise CleanupError("N8N_LIST_INVALID_%s" % kind)
        items.extend(page["data"])
        cursor = page.get("nextCursor")
        if not cursor:
            return items
        if not isinstance(cursor, str) or cursor in seen:
            raise CleanupError("N8N_PAGINATION_INVALID")
        seen.add(cursor)
    raise CleanupError("N8N_PAGINATION_LIMIT")


def check_protected(api):
    for workflow_id in sorted(PROTECTED_IDS):
        workflow = get(api, "workflows", workflow_id)
        if not workflow or str(workflow.get("id")) != workflow_id or workflow.get("active") is not True:
            raise CleanupError("PROTECTED_WORKFLOW_NOT_ACTIVE_" + workflow_id)
        print("PROTECTED_OK id=" + workflow_id + " active=true")


def credential_summary(workflow):
    credentials = set()
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        raise CleanupError("TARGET_NODES_INVALID")
    for node in nodes:
        if not isinstance(node, dict):
            raise CleanupError("TARGET_NODE_INVALID")
        node_creds = node.get("credentials") or {}
        if not isinstance(node_creds, dict):
            raise CleanupError("TARGET_CREDENTIALS_INVALID")
        for cred in node_creds.values():
            if not isinstance(cred, dict) or not isinstance(cred.get("id"), str) or not isinstance(cred.get("name"), str):
                raise CleanupError("TARGET_CREDENTIAL_INVALID")
            credentials.add((cred["name"], cred["id"]))
    # JSON表現で改行やログ制御文字を無害化する。Credential値は取得も表示もしない。
    for name, cred_id in sorted(credentials):
        print("TARGET_CREDENTIAL " + json.dumps({"name": name, "id": cred_id}, ensure_ascii=True))


def verify_execution_owner(api, execution_id):
    data = get(api, "executions", execution_id)
    if data is None:
        return False
    if str(data.get("id")) != execution_id or str(data.get("workflowId")) != TARGET_ID:
        raise CleanupError("EXECUTION_OWNER_MISMATCH_" + execution_id)
    return True


def report_workflows(api):
    workflows = list_all(api, "workflows")
    for wf in sorted(workflows, key=lambda x: str(x.get("id", ""))):
        if str(wf.get("id")) == PRODUCTION_ID:
            continue
        # API提供のフィールドだけを表示し、ノードやCredential内容は表示しない。
        if not isinstance(wf.get("id"), str) or not isinstance(wf.get("name"), str) or not isinstance(wf.get("active"), bool):
            raise CleanupError("WORKFLOW_REPORT_INVALID")
        print("OTHER_WORKFLOW " + json.dumps({
            "id": wf["id"], "name": wf["name"], "active": wf["active"],
            "updatedAt": wf.get("updatedAt"),
        }, ensure_ascii=True, sort_keys=True))


def cleanup(api):
    # 削除対象IDは外部入力で変更できない。保護対象への書込みも実装しない。
    if TARGET_ID in PROTECTED_IDS or TARGET_ID == PRODUCTION_ID:
        raise CleanupError("TARGET_IS_PROTECTED")
    target = get(api, "workflows", TARGET_ID)
    if target is None:
        # Workflowが既に消されていた場合でも指定された実行1293が他に残っていないか確認する。
        if get(api, "executions", TARGET_EXECUTION_ID) is not None:
            raise CleanupError("TARGET_GONE_BUT_EXECUTION_1293_EXISTS")
        print("TARGET_ALREADY_ABSENT_NO_CHANGES")
        report_workflows(api)
        return
    if str(target.get("id")) != TARGET_ID or target.get("name") != TARGET_NAME:
        raise CleanupError("TARGET_ID_OR_NAME_MISMATCH")
    if TARGET_ID in PROTECTED_IDS:
        raise CleanupError("TARGET_IS_PROTECTED")
    credential_summary(target)

    # 書込みの前に保護3件と削除候補全件の所有元を検査する。
    check_protected(api)
    summaries = list_all(api, "executions", {"workflowId": TARGET_ID})
    ids = set()
    for summary in summaries:
        execution_id = str(summary.get("id", ""))
        if not execution_id.isdecimal() or str(summary.get("workflowId")) != TARGET_ID:
            raise CleanupError("EXECUTION_LIST_OWNER_MISMATCH")
        ids.add(execution_id)
    if verify_execution_owner(api, TARGET_EXECUTION_ID):
        ids.add(TARGET_EXECUTION_ID)
    for execution_id in sorted(ids, key=int):
        if not verify_execution_owner(api, execution_id):
            raise CleanupError("EXECUTION_DISAPPEARED_PRECHECK_" + execution_id)

    print("PRECHECK_OK execution_count=%d" % len(ids))
    for execution_id in sorted(ids, key=int):
        status, _ = api.call("DELETE", "/executions/" + execution_id)
        if status not in (200, 204, 404):
            raise CleanupError("EXECUTION_DELETE_FAILED_" + execution_id)
        print("EXECUTION_DELETE_ATTEMPT id=" + execution_id)
    status, _ = api.call("DELETE", "/workflows/" + TARGET_ID)
    if status not in (200, 204, 404):
        raise CleanupError("WORKFLOW_DELETE_FAILED")
    print("WORKFLOW_DELETE_ATTEMPT id=" + TARGET_ID)

    if get(api, "workflows", TARGET_ID) is not None:
        raise CleanupError("WORKFLOW_STILL_EXISTS")
    for execution_id in sorted(ids | {TARGET_EXECUTION_ID}, key=int):
        if get(api, "executions", execution_id) is not None:
            raise CleanupError("EXECUTION_STILL_EXISTS_" + execution_id)
    remaining = list_all(api, "executions", {"workflowId": TARGET_ID})
    if remaining:
        raise CleanupError("EXECUTION_LIST_STILL_EXISTS")
    print("POSTCHECK_TARGET_404 id=" + TARGET_ID)
    print("POSTCHECK_EXECUTION_1293_404")
    print("POSTCHECK_EXECUTIONS_404 count=%d" % len(ids | {TARGET_EXECUTION_ID}))
    check_protected(api)
    print("POSTCHECK_PROTECTED_3_ACTIVE_OK")
    report_workflows(api)


def main():
    if os.environ.get("RUNNER_NAME") != "luna-shaman-deploy" or os.geteuid() == 0:
        raise CleanupError("WRONG_RUNNER")
    if os.path.exists("/etc/shaman-ci-isolated"):
        raise CleanupError("CI_RUNNER_FORBIDDEN")
    cleanup(Api(os.environ.get("N8N_DEPLOY_API_KEY")))


if __name__ == "__main__":
    try:
        main()
    except CleanupError as exc:
        print("CLEANUP_FAILURE " + str(exc), file=sys.stderr)
        sys.exit(1)
