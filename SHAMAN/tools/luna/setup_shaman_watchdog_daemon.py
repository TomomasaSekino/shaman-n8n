#!/usr/bin/env python3
"""SHAMAN監視LaunchDaemonの初回登録専用。lunaで管理者が1回だけ実行する。"""
import argparse
import os
from pathlib import Path
import plistlib
import pwd
import shutil
import subprocess
import sys
import tempfile

LABEL = "com.shaman.watchdog"
DAEMON = Path("/Library/LaunchDaemons") / (LABEL + ".plist")


def daemon_plist(python_exe, executable, home, username):
    """rootが読み込むが、処理本体は指定の一般ユーザーで実行する。"""
    return {
        "Label": LABEL,
        "UserName": username,
        "ProgramArguments": [str(python_exe), str(executable)],
        "RunAtLoad": True,
        "StartInterval": 300,
        "WorkingDirectory": str(home),
        "StandardOutPath": str(home / "Library" / "Logs" / "shaman-watchdog.log"),
        "StandardErrorPath": str(home / "Library" / "Logs" / "shaman-watchdog-error.log"),
    }


def register(username, python_exe=sys.executable, run=subprocess.run):
    if sys.platform != "darwin":
        raise RuntimeError("macOS専用です")
    if os.geteuid() != 0:
        raise RuntimeError("初回登録はsudoで実行してください")
    user = pwd.getpwnam(username)
    if user.pw_uid == 0 or user.pw_dir in ("", "/var/empty"):
        raise RuntimeError("rootや無効なホームディレクトリは指定できません")
    home = Path(user.pw_dir)
    binary = Path(python_exe)
    if not binary.is_absolute() or not binary.is_file():
        raise RuntimeError("Pythonの実行ファイルを絶対パスで指定してください")
    executable = home / ".local" / "share" / "shaman-watchdog" / "runtime" / "SHAMAN" / "tools" / "luna" / "shaman_watchdog.py"
    if not executable.is_file():
        raise RuntimeError("監視コードが配備されていません。先にPRの配備ジョブを完了してください")
    data = daemon_plist(binary, executable, home, username)
    # LaunchDaemonのplistだけをrootが管理。実際の監視はUserNameで一般ユーザー権限に降格。
    fd, temp = tempfile.mkstemp(prefix=".shaman-watchdog-", dir=str(DAEMON.parent))
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "wb") as f:
            plistlib.dump(data, f)
        os.chown(temp, 0, 0)
        os.replace(temp, DAEMON)
        os.chmod(DAEMON, 0o644)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    run(["launchctl", "bootout", "system/" + LABEL], capture_output=True, text=True)
    result = run(["launchctl", "bootstrap", "system", str(DAEMON)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("launchdの常駐登録に失敗: " + str(result.stderr)[:300])
    print("SHAMAN_WATCHDOG_DAEMON_REGISTERED " + str(DAEMON))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="SHAMAN監視LaunchDaemonを管理者が初回登録する")
    p.add_argument("--user", required=True, help="監視の実行ユーザー（例: luna-user）")
    p.add_argument("--python", default=sys.executable, help="監視用Python（絶対パス）")
    args = p.parse_args()
    register(args.user, args.python)
