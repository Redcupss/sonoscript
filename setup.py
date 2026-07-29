import glob
import os
import sysconfig

from setuptools import setup

APP = ["main.py"]

LOCAL_TTS_PACKAGES = [
    "certifi",
    "kokoro_onnx",
    "onnxruntime",
    "numpy",
    "espeakng_loader",
    "phonemizer",
    # phonemizer's package __init__ unconditionally imports ALL of its backends (espeak,
    # festival, mbrola, segments) even though we only ever use espeak — main.py stubs out
    # festival/segments before ever importing kokoro_onnx (see
    # _stubUnusedPhonemizerBackends) so their own dependency trees (segments alone pulls in
    # csvw -> rdflib and jsonschema) never need bundling at all. attr/dlinfo/joblib are real
    # espeak-backend runtime deps, not part of that avoided tree.
    "attr", "attrs", "dlinfo", "joblib",
    # Per-voice pitch shift (see pitch_shift.py) — psola (TD-PSOLA via Praat/parselmouth) was
    # picked after comparing it against a hand-rolled phase vocoder and Spotify's pedalboard
    # by ear on real speech; this dependency chain is its own, unrelated to phonemizer's.
    "psola", "parselmouth", "pypar", "tqdm", "soundfile", "_soundfile_data",
]

# Some packages' PyPI *distribution* name doesn't match their *import* name — "phonemizer"
# installs as "phonemizer-fork" (coincidentally still prefix-matches below), but
# "parselmouth" installs as "praat-parselmouth" (does NOT prefix-match "parselmouth" at all,
# so its dist-info would silently never get found without this).
_DIST_INFO_NAME_OVERRIDES = {"parselmouth": "praat_parselmouth"}

# A few of these packages read their own version via importlib.metadata at import time
# (phonemizer, kokoro_onnx) — python.metadata.distribution() only finds a package if its
# *.dist-info directory sits alongside it on sys.path, but py2app's "packages" option only
# copies the importable package itself, not its dist-info. Copying each one's dist-info next
# to it (same lib/python3.12/ destination) as a data_files entry fixes that without needing
# any workaround baked into the app code for packages we don't otherwise touch.
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
    "includes": ["cffi", "_cffi_backend", "_soundfile"],
    "resources": ["kokoro_assets"],
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
