"""Fetch every model JARVIS needs. Idempotent -- skips whatever is already here.

    python scripts/download_models.py

Roughly 800MB total: wake word (~2MB), VAD (~2MB), Whisper small.en (~480MB),
Kokoro (~340MB). Everything lands in models/ and is gitignored.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MODELS = ROOT / "models"

SILERO_URL = (
    "https://github.com/snakers4/silero-vad/raw/master/"
    "src/silero_vad/data/silero_vad.onnx"
)
KOKORO_BASE = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
)


def download(url: str, dest: Path, label: str) -> bool:
    import httpx

    if dest.exists() and dest.stat().st_size > 1024:
        print(f"  [ok]   {label} already present ({dest.stat().st_size/1e6:.0f}MB)")
        return True

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"  [get]  {label} ...", end="", flush=True)
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=120.0) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            done = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes(1 << 18):
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = done * 100 // total
                        print(f"\r  [get]  {label} ... {pct:3d}%  "
                              f"({done/1e6:.0f}/{total/1e6:.0f}MB)",
                              end="", flush=True)
        tmp.replace(dest)
        print(f"\r  [done] {label}{' ' * 30}")
        return True
    except Exception as e:
        print(f"\r  [FAIL] {label}: {e}")
        tmp.unlink(missing_ok=True)
        return False


def get_wakeword() -> bool:
    print("\nWake word (openWakeWord)")
    target = MODELS / "openwakeword"
    target.mkdir(parents=True, exist_ok=True)
    needed = ["hey_jarvis_v0.1.onnx", "melspectrogram.onnx", "embedding_model.onnx"]
    if all((target / n).exists() for n in needed):
        print("  [ok]   all wake word models present")
        return True
    try:
        import openwakeword.utils

        openwakeword.utils.download_models(
            model_names=["hey_jarvis_v0.1"], target_directory=str(target)
        )
    except Exception as e:
        print(f"  [warn] openwakeword downloader failed ({e}); trying direct")

    base = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"
    ok = True
    for name in needed:
        if not (target / name).exists():
            ok &= download(f"{base}/{name}", target / name, name)
    return ok


def get_vad() -> bool:
    print("\nVoice activity detection (silero)")
    return download(SILERO_URL, MODELS / "silero_vad.onnx", "silero_vad.onnx")


def get_kokoro() -> bool:
    print("\nText-to-speech (Kokoro-82M)")
    d = MODELS / "kokoro"
    ok = download(f"{KOKORO_BASE}/kokoro-v1.0.onnx", d / "kokoro-v1.0.onnx",
                  "kokoro-v1.0.onnx")
    ok &= download(f"{KOKORO_BASE}/voices-v1.0.bin", d / "voices-v1.0.bin",
                   "voices-v1.0.bin")
    return ok


def get_whisper() -> bool:
    print("\nSpeech-to-text (faster-whisper)")
    try:
        import yaml
        from faster_whisper import WhisperModel

        with open(ROOT / "config.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        name = cfg["stt"]["model"]
        print(f"  [get]  whisper {name} (first run downloads ~500MB) ...")
        WhisperModel(name, device="cpu", compute_type="int8",
                     download_root=str(MODELS / "whisper"))
        print(f"  [done] whisper {name}")
        return True
    except Exception as e:
        print(f"  [FAIL] whisper: {e}")
        return False


def get_embeddings() -> bool:
    print("\nSemantic memory embeddings")
    try:
        import yaml

        with open(ROOT / "config.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        if not cfg.get("memory", {}).get("semantic", True):
            print("  [skip] semantic memory disabled in config")
            return True

        from fastembed import TextEmbedding

        model = cfg["memory"]["embed_model"]
        print(f"  [get]  {model} ...")
        emb = TextEmbedding(model_name=model, cache_dir=str(MODELS / "embeddings"))
        list(emb.embed(["warmup"]))
        print(f"  [done] {model}")
        return True
    except Exception as e:
        print(f"  [warn] embeddings unavailable ({e})")
        print("         Memory will fall back to keyword search. Not fatal.")
        return True


def main() -> int:
    print("=" * 62)
    print(" JARVIS model acquisition")
    print("=" * 62)

    results = {
        "wake word": get_wakeword(),
        "vad": get_vad(),
        "whisper": get_whisper(),
        "kokoro": get_kokoro(),
        "embeddings": get_embeddings(),
    }

    print("\n" + "=" * 62)
    failed = [k for k, v in results.items() if not v]
    if failed:
        print(f" INCOMPLETE -- failed: {', '.join(failed)}")
        print(" Re-run this script; completed downloads are skipped.")
        return 1
    print(" All models ready.")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
