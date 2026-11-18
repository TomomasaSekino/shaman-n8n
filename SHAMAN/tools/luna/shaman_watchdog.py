#!/usr/bin/env python3
"""SHAMAN 本番の独立監視。macOS launchd から5分ごとに実行する。

復旧は「毎朝の Schedule Trigger 由来」かつ「明白な一時エラー」だけに限定する。
WordPress 側の副作用を否定できない実行は、既存 recover_check が拒否する。
"""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_shaman_workflow as runner

STATE_DIR = Path.home() / ".local" / "share" / "shaman-watchdog"
DEFAULT_CONFIG = STATE_DIR / "config.json"
RETRY_AFTER_SECONDS = 30 * 60
RECENT_SECONDS = 24 * 60 * 60
HUNG_SECONDS = 6 * 60 * 60
NOTIFY_INTERVAL_SECONDS = 6 * 60 * 60

# 構文エラー、品質ゲート失敗、検索情報源0件は復旧しても改善が保証できないため自動では再実行しない。
TRANSIENT = re.compile(r"(?:\b429\b|\b5(?:00|02|03|04)\b|timeout|timed out|ETIMEDOUT|ECONNRESET|ECONNREFUSED|"
                       r"socket hang up|network error|connection reset|rate limit|temporarily unavailable)", re.I)


def transient_error(error):
    """n8nの構造化エラー（httpCode / description）を優先して一時障害を判定する。

    SHAMAN_RUN_NGは通信エラーを含む文面でも再実行しない。
    """
    fields = []
    codes = []
    def collect(value, depth=0):
        if depth > 4:
            return
        if isinstance(value, dict):
            for key, val in value.items():
                if key in ("httpCode", "statusCode", "status"):
                    try:
                        codes.append(int(str(val)))
                    except (TypeError, ValueError):
                        pass
                if key in ("message", "description", "error", "cause", "httpCode",
                           "statusCode", "status", "response", "detail"):
                    collect(val, depth + 1)
        elif isinstance(value, list):
            for part in value[:8]:
                collect(part, depth + 1)
        elif value is not None:
            fields.append(str(value)[:1000])
    collect(error)
    joined = " ".join(fields)
    if "SHAMAN_RUN_NG" in joined:
        return False
    # エラーコードが明示された場合も、任意の4xxを一時障害と判定しない。
    if any(code in (429, 500, 502, 503, 504) for code in codes):
        return True
    return bool(TRANSIENT.search(joined) or re.search(
        r"bad gateway|service unavailable|gateway timeout|too many requests", joined, re.I))


def _time(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def load_config(path=DEFAULT_CONFIG):
    path = Path(path).expanduser()
    stat = path.stat()
    if stat.st_mode & 0o077:
        raise RuntimeError("監視設定のパーミッションは 0600 にしてください: " + str(path))
    obj = json.loads(path.read_text(encoding="utf-8"))
    for key in ("n8n_api_key", "webhook_token"):
        if not isinstance(obj.get(key), str) or not obj[key].strip():
            raise RuntimeError("監視設定に " + key + " が必要です")
    return obj


def request_json(url, data=None, headers=None, timeout=15):
    hdr = {"Accept": "application/json", "User-Agent": "SHAMAN-Watchdog/1.0", **(headers or {})}
    if data is not None:
        hdr["Content-Type"] = "application/json"
        data = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=hdr)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        body = res.read()
        return json.loads(body) if body else {}


def notify(message, cfg, state, key, now, send=request_json):
    """同じ障害は6時間に1回まで。Discord URL が無くても次回の抑止記録は残す。"""
    if now - state["alerts"].get(key, 0) < NOTIFY_INTERVAL_SECONDS:
        return
    hook = cfg.get("discord_webhook_url", "").strip()
    if hook:
        try:
            send(hook, {"content": "[SHAMAN監視] " + message[:1700]})
        except urllib.error.HTTPError as exc:
            # Discordの認証/URL不備で監視本体を落とさない。Webhook URLはログに出さない。
            print("SHAMAN_WATCHDOG_NOTIFY_NG: Discord HTTP " + str(exc.code), file=sys.stderr)
            return
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            print("SHAMAN_WATCHDOG_NOTIFY_NG: Discord通信失敗 (" + type(exc).__name__ + ")", file=sys.stderr)
            return
    state["alerts"][key] = now


def _n8n_get(cfg, path, send=request_json):
    return send("http://127.0.0.1:5678/api/v1" + path,
                headers={"X-N8N-API-KEY": cfg["n8n_api_key"]})


def health():
    # /healthz は成功時のHTTPステータスだけ確認する。本文の形式には依存しない。
    with urllib.request.urlopen("http://127.0.0.1:5678/healthz", timeout=5) as response:
        if response.status != 200:
            raise OSError("healthz HTTP " + str(response.status))


def scan(cfg, state, now, send=request_json, run_recovery=None, check_health=None, save=None):
    """稼働確認、長時間実行検知、一時的な停止実行の安全な1回復旧。"""
    if run_recovery is None:
        run_recovery = runner.run
    if check_health is None:
        check_health = health
    if save is None:
        save = save_state
    try:
        check_health()
    except (urllib.error.URLError, TimeoutError, OSError):
        notify("n8n の healthz に接続できません。launchd のサービス状態を確認してください。",
               cfg, state, "health", now, send)
        return
    try:
        flows = _n8n_get(cfg, "/workflows?limit=100&name=SHAMAN", send).get("data") or []
        matches = [w for w in flows if w.get("name") == "SHAMAN"]
        if len(matches) != 1:
            raise RuntimeError("本番Workflow SHAMAN が1件に特定できません")
        wf_id = str(matches[0]["id"])
        # 新しい順。100件を越える日は自動復旧せずに運用警告を出す。
        entries = _n8n_get(cfg, "/executions?workflowId=" + wf_id + "&limit=100", send).get("data") or []
        if len(entries) >= 100:
            notify("直近の本番実行が100件に達しました。自動復旧対象を網羅できないため確認してください。",
                   cfg, state, "pagination", now, send)
            return
        for e in entries:
            execution_id = str(e.get("id", ""))
            if not execution_id.isdecimal():
                continue
            status = e.get("status")
            started = _time(e.get("startedAt"))
            stopped = _time(e.get("stoppedAt"))
            if status in ("running", "waiting", "new"):
                if started is not None and now - started > HUNG_SECONDS:
                    notify("実行 " + execution_id + " が6時間以上継続しています。自動停止はしません。",
                           cfg, state, "hung-" + execution_id, now, send)
                continue
            if status not in ("crashed", "error") or stopped is None:
                continue
            age = now - stopped
            if age < RETRY_AFTER_SECONDS or age > RECENT_SECONDS:
                continue
            if execution_id in state["attempted"]:
                continue
            detail = _n8n_get(cfg, "/executions/" + execution_id + "?includeData=true", send)
            result = ((detail.get("data") or {}).get("resultData") or {})
            runs = result.get("runData") or {}
            # 手動実行・通常のWebhook実行を自律的に再実行しない。
            if not isinstance(runs, dict) or "Daily Schedule" not in runs:
                continue
            error = result.get("error") or {}
            if not transient_error(error):
                notify("実行 " + execution_id + " は自動復旧の対象外です（失敗内容を確認してください）。",
                       cfg, state, "manual-" + execution_id, now, send)
                continue
            # 途中で監視が落ちても同じ実行をもう一度復旧しないよう、API呼び出し前に記録する。
            state["attempted"][execution_id] = {"at": now, "outcome": "started"}
            save(state)
            env = {
                "N8N_BASE_URL": "http://127.0.0.1:5678",
                "N8N_DEPLOY_API_KEY": cfg["n8n_api_key"],
                "SHAMAN_RUN_WEBHOOK_TOKEN": cfg["webhook_token"],
                "SHAMAN_RECOVER_EXECUTION": execution_id,
            }
            try:
                # PR #90 の実行履歴・WP未実行・同時実行ゼロ検証をそのまま利用する。
                run_recovery("production", env)
            except Exception as exc:
                state["attempted"][execution_id]["outcome"] = "failed"
                notify("実行 " + execution_id + " の自動復旧は失敗しました: " + str(exc)[:500],
                       cfg, state, "recover-failed-" + execution_id, now, send)
            else:
                state["attempted"][execution_id]["outcome"] = "completed"
                notify("実行 " + execution_id + " を1回復旧し、下書き投稿まで完了しました。",
                       cfg, state, "recover-ok-" + execution_id, now, send)
            # 1回の監視で1実行だけ回復。大量の再実行を避ける。
            break
    except Exception as exc:
        notify("監視処理の異常: " + type(exc).__name__ + " " + str(exc)[:500],
               cfg, state, "watchdog", now, send)


def save_state(state, path=STATE_DIR / "state.json"):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".state-", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main():
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    cfg = load_config(Path(os.environ.get("SHAMAN_WATCHDOG_CONFIG", str(DEFAULT_CONFIG))))
    with (STATE_DIR / "lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # 前回の監視・復旧が続いている
        state_file = STATE_DIR / "state.json"
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            state = {"attempted": {}, "alerts": {}}
        now = datetime.now(timezone.utc).timestamp()
        scan(cfg, state, now)
        # 7日を超えた記録は削除する（監視状態ファイルを肥大化させない）。
        state["attempted"] = {k: v for k, v in state["attempted"].items()
                              if now - v.get("at", 0) < 7 * 86400}
        state["alerts"] = {k: v for k, v in state["alerts"].items()
                           if now - v < 7 * 86400}
        save_state(state)


if __name__ == "__main__":
    main()
