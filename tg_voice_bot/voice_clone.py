"""
Клон голоса через XTTS v2 — с упором на экономию памяти (Render 2 ГБ).

Два режима:
- subprocess: XTTS живёт в отдельном venv_xtts (Python 3.11) — локально на Windows
- in-process: XTTS в этом же окружении — контейнер на Render

Режимы памяти (MITINI_MODE), перебираются от дешёвого к дорогому:
- int8  — динамическая квантизация Linear-слоёв, веса ~1.9 ГБ -> ~1.0 ГБ
- bf16  — модель в bfloat16, ~1.1 ГБ, качество выше квантизации
- fp32  — как есть, нужно 2.5+ ГБ, тариф Pro

Диагностика: MITINI_MODE=auto (по умолчанию) + логи в stdout с MemAvailable.
"""
import atexit
import gc
import glob
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
import uuid
from pathlib import Path

if os.name == "nt":  # librosa/torchcodec грузят ffmpeg как библиотеку — нужны DLL
    for _p in glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft",
                                     "WinGet", "Packages", "Gyan.FFmpeg.Shared*", "**", "bin"),
                        recursive=True):
        os.environ["PATH"] = _p + os.pathsep + os.environ["PATH"]


BASE = Path(__file__).parent
DATA = Path(os.getenv("MITINI_DATA_DIR") or (BASE / "data"))
DATA.mkdir(parents=True, exist_ok=True)

REF = Path(os.getenv("MITINI_REF_PATH") or (DATA / "voice_ref.wav"))
XTTS_PY = BASE / "venv_xtts" / "Scripts" / "python.exe"
DAEMON = BASE / "tts_daemon.py"
MODEL = os.getenv("MITINI_MODEL", "tts_models/multilingual/multi-dataset/xtts_v2")

INPROCESS = os.getenv("MITINI_TTS_INPROCESS") == "1" or not XTTS_PY.exists()
MODE = os.getenv("MITINI_MODE", "auto").lower()
THREADS = int(os.getenv("MITINI_THREADS", "2"))
MAX_CHARS = int(os.getenv("MITINI_MAX_CHARS", "400"))

_lock = threading.Lock()
_local = threading.local()
_proc = None
_state = {"mode": None, "loaded_at": 0.0, "fail": ""}


def log(msg):
    print(f"[xtts] {msg}", flush=True)


def has_ref() -> bool:
    return REF.exists() and REF.stat().st_size > 1000


def mem_mb() -> int:
    """Свободная память в МБ (Linux) или 0, если не посчитать."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0


def rss_mb() -> int:
    """Сколько памяти занял процесс — главная цифра на Render (Linux)."""
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0



def find_ffmpeg() -> str:
    p = os.getenv("FFMPEG_PATH")
    if p and Path(p).exists():
        return p
    hits = list(Path(os.environ.get("LOCALAPPDATA", "")).glob("Microsoft/WinGet/Links/ffmpeg.exe"))
    if hits:
        return str(hits[0])
    return shutil.which("ffmpeg") or "ffmpeg"


REF_MAX_BYTES = 24_000 * 2 * 45 + 4096  # 45 c моно 24 кГц


# ---------- работа с образцом голоса ----------

def ref_path() -> Path:
    return REF


def save_ref(src: Path) -> Path:
    """Нормализует присланный файл в эталон: wav 24 кГц моно, тихая часть вырезана."""
    REF.parent.mkdir(parents=True, exist_ok=True)
    tmp = REF.with_suffix(".tmp.wav")
    subprocess.run(
        [find_ffmpeg(), "-y", "-i", str(src), "-t", "45",
         "-af", "highpass=f=60,speechnorm=e=12:m=3", "-ar", "24000", "-ac", "1",
         str(tmp)],
        capture_output=True, check=True,
    )
    if not tmp.exists() or tmp.stat().st_size < 1000:
        raise RuntimeError("ffmpeg не смог обработать запись")
    if tmp.stat().st_size > REF_MAX_BYTES:
        raise RuntimeError("запись слишком длинная — нужно до 45 секунд речи")
    tmp.replace(REF)
    return REF



# ---------- in-process XTTS ----------

def _unload():
    if hasattr(_local, "tts"):
        del _local.tts
    gc.collect()


def _apply_mode(tts, mode: str) -> str:
    """Ужимает модель до нужного режима. Возвращает реально применённый режим.

    Внимание: у coqui Xtts.eval() возвращает None, поэтому eval() вызываем
    отдельной строкой и никогда не присваиваем его результат.
    """
    model = tts.synthesizer.tts_model
    model.eval()
    import torch

    if mode == "int8":
        try:
            from torch.ao.quantization import quantize_dynamic

            # inplace=True: возвращённая копия теряет .config, а на него смотрит coqui
            quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8, inplace=True)
            tts.synthesizer.tts_model = model
            return "int8"
        except Exception as e:
            log(f"int8 не удался ({type(e).__name__}: {e}), падаю на bf16")
            mode = "bf16"
    if mode == "bf16":
        try:
            model = model.to(torch.bfloat16)
            model.eval()
            tts.synthesizer.tts_model = model
            return "bf16"
        except Exception as e:
            log(f"bf16 не удался ({type(e).__name__}: {e}), остаюсь на fp32")
            tts.synthesizer.tts_model = model
            return "fp32"
    tts.synthesizer.tts_model = model
    return "fp32"



def _smoke(tts) -> None:
    """Пробная фраза: режим, который не может синтезировать, в проде не нужен."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    try:
        tts.tts_to_file(text="тест", language="ru", file_path=tmp,
                        speaker_wav=str(REF), temperature=0.65, speed=0.92)
        if os.path.getsize(tmp) < 1000:
            raise RuntimeError("пробная синтезация дала пустой звук")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _get_local_tts():
    """Ленивая загрузка XTTS. Режим памяти подбирается с проверкой — до первого ответа."""
    if hasattr(_local, "tts"):
        return _local.tts

    os.environ["COQUI_TOS_AGREED"] = "1"
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    import torch

    torch.set_num_threads(max(1, THREADS))
    torch.set_grad_enabled(False)

    from TTS.api import TTS

    order = ["int8", "bf16", "fp32"] if MODE == "auto" else [MODE]
    last = None
    for m in order:
        t0 = time.time()
        try:
            log(f"режим {m}: гружу {MODEL}, свободно {mem_mb()} МБ")
            tts = TTS(MODEL, progress_bar=False)
            real = _apply_mode(tts, m)
            _smoke(tts)
            _state["mode"] = real
            _local.tts = tts
            log(f"режим {real} работает: процесс {rss_mb()} МБ, "
                f"свободно {mem_mb()} МБ, за {time.time() - t0:.0f} с")
            return tts
        except Exception as e:
            last = e
            log(f"режим {m} не подошёл: {type(e).__name__}: {e}")
            log(traceback.format_exc()[-1200:])
            _local.tts = None
            gc.collect()
    _state["fail"] = f"{type(last).__name__}: {last}"
    raise RuntimeError(f"XTTS не поднялся ни в одном режиме: {_state['fail']}")



def _to_ogg(wav: str, ogg: str):
    subprocess.run([find_ffmpeg(), "-y", "-i", wav, "-c:a", "libopus",
                    "-b:a", "48k", "-ar", "48000", ogg], check=True, capture_output=True)
    try:
        os.remove(wav)
    except OSError:
        pass


def _synth_local(text: str, out: str) -> str:
    tts = _get_local_tts()
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    import torch

    try:
        with torch.inference_mode():
            tts.tts_to_file(text=text, language="ru", file_path=tmp,
                            speaker_wav=str(REF), temperature=0.65, speed=0.92)
        _to_ogg(tmp, out)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return out


# ---------- subprocess XTTS (локально, Windows) ----------

def _worker():
    global _proc
    while True:
        try:
            _proc = subprocess.Popen(
                [str(XTTS_PY), str(DAEMON)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1,
                env={**os.environ, "COQUI_TOS_AGREED": "1", "PYTHONIOENCODING": "utf-8"},
            )
            _proc.wait()
        except Exception as e:
            log(f"воркер упал: {type(e).__name__}: {e}")
        time.sleep(1.0)


def _ensure_worker() -> bool:
    global _proc
    if _proc is None or _proc.poll() is not None:
        _proc = None
        threading.Thread(target=_worker, daemon=True).start()
        for _ in range(600):
            if _proc is not None and _proc.stdin and _proc.stdout:
                return True
            time.sleep(0.1)
    return _proc is not None


def _clone_subprocess(text: str, out: str) -> str | None:
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
    if not res.get("ok"):
        log(f"ответ воркера: {res.get('err')}")
    return out if res.get("ok") and Path(out).exists() else None


# ---------- публичный API ----------

def status() -> dict:
    return {
        "backend": "in-process" if INPROCESS else "subprocess",
        "mode": _state["mode"] or MODE,
        "model": MODEL,
        "ref": str(REF) if has_ref() else None,
        "mem_mb": mem_mb(),
        "rss_mb": rss_mb(),
        "threads": THREADS,
        "fail": _state["fail"],
    }


def clone_voice(text: str) -> str | None:
    """Синтез клоном голоса. Возвращает путь к .ogg или None."""
    if not has_ref():
        return None
    text = (text or "").strip()[:MAX_CHARS]
    if not text:
        return None
    out = Path(tempfile.gettempdir()) / f"clone_{uuid.uuid4().hex[:8]}.ogg"
    with _lock:
        try:
            if INPROCESS:
                _synth_local(text, str(out))
            else:
                _clone_subprocess(text, str(out))
        except MemoryError:
            log("память кончилась, выгружаю модель")
            _unload()
            return None
        except Exception as e:
            log(f"синтез не удался: {type(e).__name__}: {e}")
            return None
    return str(out) if out.exists() else None


def warmup() -> bool:
    """Прогревает модель заранее, чтобы первый ответ не ждал загрузку."""
    if not has_ref():
        return False
    if not INPROCESS:
        return _ensure_worker()
    try:
        with _lock:
            _get_local_tts()
        log("модель готова")
        return True
    except Exception as e:
        log(f"прогрев не удался: {type(e).__name__}: {e}")
        return False


def shutdown():
    global _proc
    if _proc and _proc.poll() is None:
        try:
            _proc.stdin.write('{"cmd":"quit"}\n')
            _proc.stdin.flush()
            _proc.wait(timeout=5)
        except Exception:
            try:
                _proc.kill()
            except Exception:
                pass
    _unload()


atexit.register(shutdown)

# обратная совместимость: было константой, теперь функция
HAS_XTTS = has_ref()
