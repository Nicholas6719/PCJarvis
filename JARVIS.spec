# PyInstaller build spec -- produces dist/JARVIS/JARVIS.exe
#
# onedir rather than onefile: a single-file build unpacks ~400MB of native
# libraries to a temp directory on every launch, which adds several seconds to
# a startup that is already dominated by model loading.
#
# Several dependencies ship data files that PyInstaller cannot infer:
#   openwakeword   bundled ONNX models and resources
#   kokoro_onnx    tokenizer assets
#   espeakng_loader  the espeak-ng shared library and its dictionaries, which
#                  Kokoro needs for phonemisation and which fail at runtime
#                  with an unhelpful error if missing
#   webview        the Edge Chromium platform backend, imported dynamically

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

datas = []
binaries = []
hiddenimports = []

for pkg in ("openwakeword", "kokoro_onnx", "espeakng_loader", "phonemizer_fork",
            "language_tags", "csvw", "rdflib", "segments",
            "fastembed", "faster_whisper", "onnxruntime", "ctranslate2",
            "tokenizers", "trafilatura", "ddgs", "winsdk", "pycaw", "comtypes",
            "pywinauto"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass  # optional at build time; failures surface in the smoke test

hiddenimports += collect_submodules("webview.platforms")
hiddenimports += [
    "webview.platforms.edgechromium",
    "clr_loader", "pythonnet",
    "scipy.special._cdflib", "scipy._lib.array_api_compat.numpy.fft",
    "soundfile", "sounddevice", "keyboard", "pyperclip",
    "jarvis.tools.system", "jarvis.tools.web", "jarvis.tools.files",
    "jarvis.tools.media", "jarvis.tools.memory_tools",
]

# Our own assets. config.yaml is bundled as a fallback; the editable copy sits
# beside the exe and takes precedence (see jarvis/config.py).
datas += [
    ("jarvis/ui/web", "jarvis/ui/web"),
    ("jarvis/voice/ir", "jarvis/voice/ir"),
    ("config.yaml", "."),
    ("assets/jarvis.ico", "assets"),
]

a = Analysis(
    ["jarvis/app.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PySide6", "IPython",
              "pytest", "PIL.ImageQt"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="JARVIS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # no console window -- this is an app, not a script
    disable_windowed_traceback=False,
    icon="assets/jarvis.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="JARVIS",
)
