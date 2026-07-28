from setuptools import setup

APP = ["main.py"]
OPTIONS = {
    "plist": {
        "LSUIElement": False,
        "CFBundleName": "SonoScript",
        "CFBundleDisplayName": "SonoScript – Text to Speech",
        "CFBundleIdentifier": "com.gilrodmedia.sonoscript",
    },
    "packages": ["certifi"],
    "iconfile": "icon.icns",
}

setup(
    app=APP,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
