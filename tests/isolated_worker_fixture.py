"""Offline-only child module used to exercise the bounded subprocess protocol."""

from __future__ import annotations

import json
import os
import signal
import sys
import time


def main() -> int:
    request = json.load(sys.stdin)
    mode = request.get("fixture")
    if mode == "hang":
        time.sleep(float(request.get("sleep_seconds", 60)))
        return 0
    if mode == "ignore_terminate":
        if os.name == "posix":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(float(request.get("sleep_seconds", 60)))
        return 0
    if mode == "exit":
        return int(request.get("exit_code", 7))
    if mode == "signal" and os.name == "posix":
        os.kill(os.getpid(), signal.SIGTERM)
        return 0
    if mode == "invalid_json":
        sys.stdout.write('{"protocol_version":1,"ok":')
        return 0
    if mode == "incomplete":
        json.dump({"protocol_version": 1}, sys.stdout)
        return 0
    if mode == "leak_stderr":
        sys.stderr.write(str(request.get("secret", "secret")))
        return int(request.get("exit_code", 9))
    json.dump(
        {
            "protocol_version": 1,
            "ok": True,
            "result": {
                "sentinel": request.get("sentinel"),
                "argv": sys.argv,
                "pid": os.getpid(),
            },
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
