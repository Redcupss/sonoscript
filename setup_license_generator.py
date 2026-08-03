import glob
import os
import sys
import sysconfig

from setuptools import setup

# license_generator.py resolves tools/sign_license.py via a runtime sys.path insert (see its
# own header comment) rather than a package-relative import, since sign_license.py is a
# dev-only script deliberately kept outside any package main.py imports (so py2app's
# modulegraph, which only traces from main.py, structurally can't pull it or the private
# signing key into the *shipped* app). Mirroring that same sys.path insert here — before
# setup() runs — makes modulegraph's static scan resolve "from sign_license import
# sign_license" as an ordinary top-level module, so it gets bundled the normal way.
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_REPO_ROOT, "tools"))

APP = ["license_generator.py"]

# Just PyNaCl for Ed25519 signing — this app deliberately shares none of SonoScript's TTS/ML
# dependency stack (see license_generator.py's own header comment).
PACKAGES = ["nacl"]

site_packages = sysconfig.get_paths()["purelib"]
dist_info_files = []
for pkg in PACKAGES:
    matches = glob.glob(os.path.join(site_packages, f"{pkg}[-_]*.dist-info")) + \
        glob.glob(os.path.join(site_packages, f"{pkg}[-_]*.egg-info"))
    for m in matches:
        dest = os.path.join("lib", "python3.12", os.path.basename(m))
        dist_info_files.append((dest, [f for f in glob.glob(os.path.join(m, "*")) if os.path.isfile(f)]))

OPTIONS = {
    "plist": {
        "LSUIElement": False,
        "CFBundleName": "License Generator",
        "CFBundleDisplayName": "SonoScript License Generator",
        "CFBundleIdentifier": "com.gilrodmedia.sonoscript.licensegenerator",
    },
    "packages": PACKAGES,
    # ui_helpers.py / widgets.py sit alongside license_generator.py in the repo root and are
    # imported directly (from ui_helpers import ..., from widgets import ...) — ordinary
    # top-level modules, no special handling needed beyond listing them so modulegraph is sure
    # to include them even though they're single files, not packages.
    # cffi/_cffi_backend: PyNaCl's nacl.bindings uses cffi to call libsodium — same reasoning
    # as the identical entry in the main app's setup.py. modulegraph can't see this dependency
    # since cffi loads its backend dynamically, not via a static import statement.
    "includes": ["ui_helpers", "widgets", "sign_license", "cffi", "_cffi_backend"],
    "iconfile": "icon.icns",
}

setup(
    app=APP,
    data_files=dist_info_files,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
