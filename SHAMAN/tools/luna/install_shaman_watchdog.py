#!/usr/bin/env python3
"""GitHub Actionsの配備runnerから監視をlunaへインストール（macOS標準機能だけ）。"""
import json
import os
from pathlib import Path
import shutil
import sys

LABEL = "com.shaman.watchdog"
FILES = ("run_shaman_workflow.py", "deploy_shaman_google_sheets.py", "shaman_watchdog.py", "setup_shaman_watchdog_daemon.py")


def install():
    if sys.platform != "darwin":
        raise RuntimeError("macOS専用です")
    api_key = os.environ.get("SHAMAN_WATCHDOG_N8N_API_KEY", "").strip()
    token = os.environ.get("SHAMAN_RUN_WEBHOOK_TOKEN", "").strip()
    deploy_key = os.environ.get("N8N_DEPLOY_API_KEY", "").strip()
    if not api_key or not token:
        raise RuntimeError("監視用 Secrets（SHAMAN_WATCHDOG_N8N_API_KEY / SHAMAN_RUN_WEBHOOK_TOKEN）が必要です")
    if deploy_key and api_key == deploy_key:
        raise RuntimeError("監視専用キーに配備用 N8N_DEPLOY_API_KEY を流用することは禁止です")
    src = Path(__file__).resolve().parent
    shaman = src.parents[1]  # SHAMAN/tools/luna → SHAMAN
    home = Path.home()
    root = home / ".local" / "share" / "shaman-watchdog"
    runtime = root / "runtime" / "SHAMAN"
    scripts = runtime / "tools" / "luna"
    workflows = runtime / "workflows"
    for d in (root, runtime, scripts, workflows):
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        d.chmod(0o700)
    for name in FILES:
        shutil.copy2(src / name, scripts / name)
    shutil.copy2(shaman / "workflows" / "SHAMAN_Workflow.json", workflows / "SHAMAN_Workflow.json")
    config = root / "config.json"
    discord_url = os.environ.get("SHAMAN_WATCHDOG_DISCORD_WEBHOOK_URL", "").strip()
    if not discord_url and config.exists():
        try:
            discord_url = json.loads(config.read_text(encoding="utf-8")).get("discord_webhook_url", "")
        except (OSError, ValueError, TypeError):
            pass
    # 権限0600、シークレットをログやGitHubへ出力しない。
    import tempfile
    fd, temp = tempfile.mkstemp(dir=str(root), prefix=".config-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"n8n_api_key": api_key, "webhook_token": token,
                       "discord_webhook_url": discord_url}, f)
        os.replace(temp, config)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    (home / "Library" / "Logs").mkdir(parents=True, exist_ok=True)
    # launchdのsystemドメイン登録は管理者が一度だけ行う。Actions runnerにはsudoを渡さない。
    print("SHAMAN_WATCHDOG_RUNTIME_READY: 一般ユーザーの作業領域に監視コードと秘密設定を配備しました")
    print("SHAMAN_WATCHDOG_DAEMON_SETUP: /Library/LaunchDaemons/com.shaman.watchdog.plist を管理者が一度だけ登録してください")


if __name__ == "__main__":
    install()
