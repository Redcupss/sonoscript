import glob
import os
import sys
import sysconfig

from setuptools import setup

# py2app's modulegraph walks the import graph AND recursively descends each module's own AST
# to find nested imports — for a dependency this deep (transformers/mlx_audio have very long
# import chains plus large, deeply-nested source files), the two recursions compound and blow
# Python's default limit of 1000 partway through the scan (RecursionError inside
# modulegraph's own ast.NodeVisitor, not in any of this project's code). Bumped well above
# what a real build has been observed to need.
sys.setrecursionlimit(10000)

APP = ["main.py"]

LOCAL_TTS_PACKAGES = [
    "certifi",
    "numpy",
    # Per-voice pitch shift (see pitch_shift.py) — psola (TD-PSOLA via Praat/parselmouth) was
    # picked after comparing it against a hand-rolled phase vocoder and Spotify's pedalboard
    # by ear on real speech; this dependency chain is its own, unrelated to phonemizer's.
    "psola", "parselmouth", "pypar", "tqdm", "soundfile", "_soundfile_data",
    # Chatterbox Turbo (MLX) — Phase 1 packaging spike. mlx_audio/transformers/huggingface_hub
    # all do importlib.metadata lookups at import/runtime, so they need the same
    # dist-info-alongside-package treatment as everything else in this list. NOTE: "mlx"
    # itself is deliberately NOT here — it's a namespace package (no __init__.py, standard for
    # packages built around a compiled core), and py2app's "packages" option crashes on that
    # shape (ImportError inside py2app's own get_bootstrap(), which uses a legacy
    # imp.find_module()-style lookup that predates PEP 420 namespace packages and can't handle
    # them at all). mlx is instead copied verbatim in the post-setup() step below, the same
    # "take explicit control of a whole directory tree" approach used for chatterbox_assets.
    # This list is a starting point based on mlx-audio's declared deps plus the obvious
    # transitive ML stack — expect to discover more missing entries via real
    # ModuleNotFoundError/ImportError when the frozen app actually launches, same iterative
    # process that shaped every other block.
    "mlx_lm", "mlx_audio", "huggingface_hub", "transformers", "tokenizers",
    "safetensors", "sentencepiece", "scipy",
    # sounddevice's bundled PortAudio binary (libportaudio.dylib) lives in a data directory,
    # same shape as _soundfile_data above — dlopen() can't load a library from inside the
    # zipped python312.zip, it needs a real file on the real filesystem. mlx_audio.tts.generate
    # imports sounddevice unconditionally at module load (for its own optional playback
    # feature, which SonoScript doesn't use — audio goes through AVAudioPlayer instead), so
    # this needs to load cleanly even though we never call into it.
    "_sounddevice_data",
    # Sesame license verification (see license.py) — Ed25519 via PyNaCl. A regular package
    # (has __init__.py), not a namespace package like mlx, so it needs no special treatment.
    "nacl",
]

# Some packages' PyPI *distribution* name doesn't match their *import* name — "parselmouth"
# installs as "praat-parselmouth" (does NOT prefix-match "parselmouth" at all, so its
# dist-info would silently never get found without this).
_DIST_INFO_NAME_OVERRIDES = {"parselmouth": "praat_parselmouth"}

# A few of these packages read their own version via importlib.metadata at import time —
# python.metadata.distribution() only finds a package if its *.dist-info directory sits
# alongside it on sys.path, but py2app's "packages" option only copies the importable package
# itself, not its dist-info. Copying each one's dist-info next to it (same lib/python3.12/
# destination) as a data_files entry fixes that without needing any workaround baked into the
# app code for packages we don't otherwise touch.
site_packages = sysconfig.get_paths()["purelib"]
dist_info_files = []
for pkg in LOCAL_TTS_PACKAGES:
    search_name = _DIST_INFO_NAME_OVERRIDES.get(pkg, pkg)
    matches = glob.glob(os.path.join(site_packages, f"{search_name}[-_]*.dist-info")) + \
        glob.glob(os.path.join(site_packages, f"{search_name}[-_]*.egg-info"))
    for m in matches:
        dest = os.path.join("lib", "python3.12", os.path.basename(m))
        dist_info_files.append((dest, [f for f in glob.glob(os.path.join(m, "*")) if os.path.isfile(f)]))

OPTIONS = {
    "plist": {
        "LSUIElement": False,
        "CFBundleName": "SonoScript",
        "CFBundleDisplayName": "SonoScript – Text to Speech",
        "CFBundleIdentifier": "com.gilrodmedia.sonoscript",
    },
    "packages": LOCAL_TTS_PACKAGES,
    # Single-file modules (not directory packages) need "includes", not "packages" — same
    # reasoning as the existing cffi/_soundfile entries. sounddevice/miniaudio are Chatterbox's
    # audio I/O deps (mlx-audio uses them internally); their private compiled companions
    # (_sounddevice.py, _miniaudio.abi3.so) ride along automatically once the parent modules
    # are found, but listing them explicitly avoids relying on that.
    "includes": ["cffi", "_cffi_backend", "_soundfile", "sounddevice", "_sounddevice", "miniaudio", "_miniaudio"],
    # mlx is a namespace package (no __init__.py — standard for a package wrapping a compiled
    # core) with no clean way through py2app's automatic bundling: leaving it to modulegraph's
    # default tracing makes py2app synthesize its OWN minimal, incomplete "mlx" package
    # (a generated __init__.py + a small extension-loading stub) and freeze that INSIDE
    # python312.zip — that synthetic in-zip package wins Python's import resolution over any
    # real, complete copy placed elsewhere on sys.path (a regular/zipped package always beats
    # namespace-package portions), so mlx.core loads but every other submodule (e.g.
    # mlx._reprlib_fix) 404s, since the zip stub never contained mlx's actual .py files.
    # Explicitly excluding "mlx" stops modulegraph from auto-discovering or zipping ANY part
    # of it, so the plain, complete, unmodified copy placed in the post-setup() step below
    # becomes the only version of "mlx" on sys.path — resolved as a normal namespace package
    # from a real directory, which correctly finds both its .py submodules AND core's compiled
    # extension (core.cpython-312-darwin.so, found via CPython's standard ABI-tagged-suffix
    # extension search — no special stub or renaming needed once py2app stops interfering).
    "excludes": ["mlx"],
    "resources": ["chatterbox_assets"],
    "iconfile": "icon.icns",
}

setup(
    app=APP,
    data_files=dist_info_files,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)

# parselmouth is a single-file compiled extension (parselmouth.cpython-*-darwin.so), not a
# package — py2app's modulegraph mishandles that shape and, alongside correctly placing the
# real thing at Resources/lib/python3.12/lib-dynload/parselmouth.so, ALSO writes a broken
# duplicate at Resources/lib/python3.12/parselmouth.py containing the raw compiled binary.
# That duplicate shadows the working one on sys.path and fails immediately when Python tries
# to parse binary data as source ("SyntaxError: source code string cannot contain null
# bytes"). Deleting it is safe: the correct .so still resolves the import on its own.
_bogus_parselmouth = os.path.join(
    "dist", "SonoScript.app", "Contents", "Resources", "lib", "python3.12", "parselmouth.py")
if os.path.exists(_bogus_parselmouth):
    os.remove(_bogus_parselmouth)
    print(f"Removed broken duplicate: {_bogus_parselmouth}")

# mlx is a namespace package (see the LOCAL_TTS_PACKAGES comment above) that py2app's
# "packages" option cannot bundle at all — copied verbatim here instead, the same explicit-
# control approach as chatterbox_assets, so nothing about its non-code assets (mlx.metallib,
# the Metal compute shader binary; libmlx.dylib/libjaccl.dylib, its C++ core libraries)
# depends on modulegraph's default tracing correctly guessing what to include.
import shutil
_mlx_src = os.path.join(sysconfig.get_paths()["purelib"], "mlx")
_mlx_dest = os.path.join("dist", "SonoScript.app", "Contents", "Resources", "lib", "python3.12", "mlx")
if os.path.isdir(_mlx_src) and not os.path.exists(_mlx_dest):
    shutil.copytree(_mlx_src, _mlx_dest, symlinks=False)
    print(f"Copied mlx package verbatim: {_mlx_src} -> {_mlx_dest}")
    for m in glob.glob(os.path.join(site_packages, "mlx-*.dist-info")):
        dest = os.path.join(
            "dist", "SonoScript.app", "Contents", "Resources", "lib", "python3.12",
            os.path.basename(m))
        shutil.copytree(m, dest, symlinks=False)
        print(f"Copied mlx dist-info: {m} -> {dest}")
