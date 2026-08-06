# SonoScript

A native macOS text-to-speech reader. Paste in anything — an article, a chapter, your own draft — and have it read back to you, with a scrubber to seek around, live word-highlighting, and support for cloned/custom voices.

## Features

- Paste any length of text; long documents are chunked and generated automatically behind the scenes
- Play/pause, skip ±15s, and a scrubber to seek anywhere in the document
- Six voice backends: on-device Apple System voices, a free bundled local neural voice (Chatterbox), licensed voice cloning (Sesame), and bring-your-own-key ElevenLabs/OpenAI/other-compatible APIs
- Record or upload a reference clip to create your own custom cloned voice
- Automatic content verification — generated speech is transcribed back and checked against the source text, with automatic retries if it doesn't match
- History and permanent Saved recordings, with configurable storage location
- Built-in update checker

See [CHANGELOG.md](CHANGELOG.md) for the full list of what's shipped release by release, and [ROADMAP.md](ROADMAP.md) for what's planned next.

## Requirements

- macOS on Apple Silicon (the bundled local voice models run on [MLX](https://github.com/ml-explore/mlx), which is Apple Silicon–only)
- Python 3.12

## Running from source

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./run.sh
```

The first run downloads the local voice model weights, so it'll be slower to start than subsequent launches.

## Building the app

```bash
./.venv/bin/python3 setup.py py2app
```

Produces `dist/SonoScript.app`, ad-hoc code-signed automatically as part of the build. Since it isn't signed with a paid Apple Developer certificate, first launch will show Gatekeeper's "unidentified developer" prompt — that's expected, not an error.

## Project structure

| Path | What it is |
|---|---|
| `main.py` | The app itself — UI, playback, and orchestration. Everything else here is a supporting module it imports. |
| `chunking.py` | Splits long text into provider-sized generation chunks |
| `speech_verify.py` | Transcribes generated audio back and checks it against the source text |
| `text_prep.py` | Cleans/normalizes text before it's sent to a voice provider |
| `pitch_shift.py` | Per-voice pitch correction |
| `history.py` / `saved.py` | Recent-history and permanent-save bookkeeping |
| `license.py` | Verifies a Sesame license key (public-key check only — see `tools/sign_license.py`) |
| `bug_report.py` | In-app bug reporting (sends metadata only, never your actual text) |
| `config.py`, `voice_defaults.py`, `ui_helpers.py`, `widgets.py` | Shared config, defaults, and UI building blocks |
| `setup.py` | py2app build configuration |
| `chatterbox_assets/`, `sesame_assets/`, `whisper_assets/` | Bundled model assets and reference voice clips |
| `tools/` | Developer-only utilities not shipped in the app (e.g. `sign_license.py`, which needs a private key that never lives in this repo) |
| `CHANGELOG.md` | Full release history |
| `ROADMAP.md` | What's planned or being considered |

## License

Not yet decided — treat this as all-rights-reserved by default until a license file is added.
