"""
Постоянный процесс-воркер XTTS (отдельный venv_xtts, локально на Windows).
Держит модель в памяти и отвечает на команды по stdin.

Формат: {"text": "...", "ref": "...", "out": "..."} -> {"ok": true, "path": "..."}
        {"cmd": "quit"}

Логику памяти/режимов берёт из voice_clone, чтобы локальный и контейнерный
пути вели себя одинаково.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))

os.environ["COQUI_TOS_AGREED"] = "1"
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

import voice_clone as vc  # noqa: E402

_lock = __import__("threading").Lock()


def synth(text: str, out: str) -> str:
    with _lock:
        vc._synth_local(text[:vc.MAX_CHARS], out)
    return out


def main():
    sys.stderr.write(f"свободно {vc.mem_mb()} МБ, потоков {vc.THREADS}\n")
    sys.stderr.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if line == '{"cmd":"quit"}':
            print(json.dumps({"ok": True}), flush=True)
            break
        out = None
        try:
            req = json.loads(line)
            out = req["out"]
            synth(req["text"], out)
            print(json.dumps({"ok": True, "path": out}), flush=True)
        except Exception as e:
            print(json.dumps({"ok": False, "err": f"{type(e).__name__}: {e}"}), flush=True)


if __name__ == "__main__":
    main()
