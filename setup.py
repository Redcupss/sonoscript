import glob
import os
import sysconfig

from setuptools import setup

APP = ["main.py"]

KOKORO_PACKAGES = [
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
]

# A few of these packages read their own version via importlib.metadata at import time
# (phonemizer, kokoro_onnx) — python.metadata.distribution() only finds a package if its
# *.dist-info directory sits alongside it on sys.path, but py2app's "packages" option only
# copies the importable package itself, not its dist-info. Copying each one's dist-info next
# to it (same lib/python3.12/ destination) as a data_files entry fixes that without needing
# any workaround baked into the app code for packages we don't otherwise touch.
site_packages = sysconfig.get_paths()["purelib"]
dist_info_files = []
for pkg in KOKORO_PACKAGES:
    matches = glob.glob(os.path.join(site_packages, f"{pkg}[-_]*.dist-info")) + \
        glob.glob(os.path.join(site_packages, f"{pkg}[-_]*.egg-info"))
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
    "packages": KOKORO_PACKAGES,
    "includes": ["cffi", "_cffi_backend"],
    "resources": ["kokoro_assets"],
    "iconfile": "icon.icns",
}

setup(
    app=APP,
    data_files=dist_info_files,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
