"""
Клон голоса через XTTS.

Два режима:
- subprocess: XTTS живёт в отдельном venv_xtts (Python 3.11), нужен на Windows
- in-process: XTTS импортируется напрямую (контейнер, Linux, один Python)

В контейнере отдельного окружения нет, поэтому режим выбирается сам.
"""
import os, json, subprocess, threading, uuid, tempfile
from pathlib import Path

BASE = Path(__file__).parent
XTTS_PY = BASE / "venv_xtts" / "Scripts" / "python.exe"
DAEMON = BASE / "tts_daemon.py"
REF = BASE / "voice_ref.wav"
MODEL_CACHE = BASE / ".voice_cache"
MODEL_CACHE.mkdir(exist_ok=True)

INPROCESS = os.getenv("MITINI_TTS_INPROCESS") == "1" or not XTTS_PY.exists()
HAS_XTTS = REF.exists() and (DAEMON.exists() or INPROCESS)

_local = threading.local()


def find_ffmpeg() -> str:
    hits = list(Path(os.environ.get("LOCALAPPDATA", "")).glob("Microsoft/WinGet/Links/ffmpeg.exe"))
    return str(hits[0]) if hits else "ffmpeg"


def _get_local_tts():
    """Ленивый импорт XTTS в текущем процессе."""
    if not hasattr(_local, "tts"):
        import glob
        for p in glob.glob("/app/**/bin", recursive=True):
            os.environ["PATH"] = p + os.pathsep + os.environ["PATH"]
        os.environ["COQUI_TOS_AGREED"] = "1"
        import torch
        from TTS.api import TTS
        torch.set_num_threads(int(os.getenv("MITINI_THREADS", "4")))
        _local.tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2", progress_bar=False)
    return _local.tts


def _synth_local(text: str, out: str):
    tts = _get_local_tts()
    wav = out.rsplit(".", 1)[0] + ".wav"
    tts.tts_to_file(text=text, language="ru", file_path=wav,
                    speaker_wav=str(REF), temperature=0.65, speed=0.92)
    subprocess.run([find_ffmpeg(), "-y", "-i", wav, "-c:a", "libopus",
                    "-b:a", "48k", "-ar", "48000", out], check=True, capture_output=True)
    try:
        os.remove(wav)
    except OSError:
        pass


_lock = threading.Lock()
_proc = None


def clone_voice(text: str, timeout: int = 300) -> str | None:
    """Синтез клоном голоса. Возвращает путь к .ogg или None."""
    if not HAS_XTTS:
        return None
    out = Path(tempfile.gettempdir()) / f"clone_{uuid.uuid4().hex[:8]}.ogg"
    try:
        if INPROCESS:
            with _lock:
                _synth_local(text[:800], str(out))
        else:
            with _lock:
                _clone_subprocess(text[:800], str(out), timeout)
    except Exception:
        return None
    return str(out) if out.exists() else None


def warmup() -> bool:
    """Прогревает модель заранее, чтобы первый ответ не ждал загрузку."""
    if not HAS_XTTS:
        return False
    return clone_voice("Готов.") is not None


def shutdown():
    if not INPROCESS and _proc and _proc.poll() is None:
        try:
            _proc.stdin.write('{"cmd":"quit"}\n')
            _proc.stdin.flush()
            _proc.wait(timeout=5)
        except Exception:
            _proc.kill()


def _worker():
    """Держит демона живым, перезапускает при падении. stdout не читает — этим занят caller."""
    global _proc
    while True:
        try:
            _proc = subprocess.Popen(
                [str(XTTS_PY), str(DAEMON)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
                env={**os.environ, "COQUI_TOS_AGREED": "1", "PYTHONIOENCODING": "utf-8"},
            )
            _proc.wait()  # ждём завершения, stdout не трогаем
        except Exception:
            pass
        if _proc:
            _proc.poll()
        threading.Event().wait(1.0)


def _ensure_worker():
    global _proc
    if _proc is None or _proc.poll() is not None:
        _proc = None
        threading.Thread(target=_worker, daemon=True).start()
        for _ in range(200):
            if _proc is not None and _proc.stdin and _proc.stdout:
                return True
            threading.Event().wait(0.1)
    return _proc is not None


def _clone_subprocess(text: str, out: str, timeout: int = 180) -> str | None:
    """Синтез через постоянный процесс tts_daemon.py."""
    if not _ensure_worker():
        return None
    req = {"text": text, "ref": str(REF), "out": out}
    try:
        _proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        _proc.stdin.flush()
        line = _proc.stdout.readline()
    except Exception:
        return None
    if not line:
        return None
    try:
        res = json.loads(line)
    except json.JSONDecodeError:
        return None
    return out if res.get("ok") and Path(out).exists() else None