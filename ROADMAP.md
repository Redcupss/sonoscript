# Roadmap

Where SonoScript is headed. Shipped work moves to [CHANGELOG.md](CHANGELOG.md);
this file tracks what's in progress, planned, or just an idea worth
considering later. Updated as priorities change — not a promise, a snapshot.

## In progress

1. **Repo hygiene** — set up Git LFS for `kokoro_assets/` (86MB+ of
   binaries don't belong in normal git history), commit the backlog of
   uncommitted work in logical chunks (scrubber polish / System voices /
   Kokoro) instead of one giant blob, push to origin.
2. **Split `main.py` into modules** — it's grown to ~2,900 lines covering
   TTS backends, the playback engine, UI views, and config/chunking all in
   one file. Natural seams already exist; better to split now than let more
   feature work pile into the monolith.

## Planned

3. **Text preprocessing pass** — filename/extension periods (`example.file`)
   currently get read as sentence-ending pauses; camelCase words like
   GitHub/SonoScript get mispronounced or paused around. Fix: a shared
   preprocessing step before any provider gets the text, plus a small
   pronunciation-override dictionary for known problem words.
4. **Speed range widen to 0.5x–1.5x** (from 0.7x–1.2x), clamped per-provider
   — Kokoro/System can go wider, but ElevenLabs' API caps lower and would
   reject the extremes if sent through unclamped.
5. **Graduated Kokoro chunk sizing** — smaller first chunk for an even
   faster first sound, growing on subsequent chunks once prefetch has a
   comfortable time cushion.
6. **Per-voice custom defaults** — a config table so a voice (e.g. Puck)
   can have its own baseline speed/pitch that the UI's controls adjust on
   top of, instead of one global default for every voice.
7. **Pitch-shift DSP** — Kokoro's model has no native pitch parameter (only
   input_ids/style/speed), so this means shifting the generated waveform
   after the fact. The riskiest item here (real audio-quality risk if done
   naively) and what #6 needs to have something to set a default *for*.
   Note: taming one specific voice's trained sentence-initial pitch rise
   (Puck) isn't achievable this way or any other — that's baked into the
   model weights, not a tunable parameter. Picking a different voice is the
   realistic option there.

## Ideas / maybe

Unscoped, not committed to — things worth considering as the app matures:

- **Save/resume position per document** — if you paste a long document,
  quit, and relaunch, pick back up where you left off instead of starting
  at 0:00.
- **Reading history / recent pastes** — a short list of recently-read
  texts so a long document doesn't have to be re-pasted after it's cleared.
- **Export to audio file** — render a whole document to an M4A/MP3 instead
  of only ever playing it live, for listening outside the app.
- **Keyboard shortcuts reference** — a small cheat-sheet (Cmd+Z undo is
  already fixed; spacebar play/pause, arrow-key skip, etc. could use a
  visible reference for a non-technical user).
- **Reading speed auto-ramp** — start a touch slower and speed up after a
  few seconds, the way some audiobook apps ease in.
- **Menu bar / background playback indicator** — since this is meant to run
  unattended for long documents, a menu bar item showing play/pause and
  progress without needing the main window focused.

## Shipped

See [CHANGELOG.md](CHANGELOG.md) for everything already released.
