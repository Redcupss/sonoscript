# Roadmap

Where SonoScript is headed. Shipped work moves to [CHANGELOG.md](CHANGELOG.md);
this file tracks what's in progress, planned, or just an idea worth
considering later. Updated as priorities change — not a promise, a snapshot.

## In progress

Nothing in progress right now — next up is whichever of the Planned items
below gets picked.

## Planned

1. **Speed range widen to 0.5x–1.5x** (from 0.7x–1.2x), clamped per-provider
   — Kokoro/System can go wider, but ElevenLabs' API caps lower and would
   reject the extremes if sent through unclamped.
2. **Graduated Kokoro chunk sizing** — smaller first chunk for an even
   faster first sound, growing on subsequent chunks once prefetch has a
   comfortable time cushion.
3. **Per-voice custom defaults** — a config table so a voice (e.g. Puck)
   can have its own baseline speed/pitch that the UI's controls adjust on
   top of, instead of one global default for every voice.
4. **Pitch-shift DSP** — Kokoro's model has no native pitch parameter (only
   input_ids/style/speed), so this means shifting the generated waveform
   after the fact. The riskiest item here (real audio-quality risk if done
   naively) and what #3 needs to have something to set a default *for*.
   Note: taming one specific voice's trained sentence-initial pitch rise
   (Puck) isn't achievable this way or any other — that's baked into the
   model weights, not a tunable parameter, same root cause as SonoScript's
   remaining sentence-final rise (see CHANGELOG.md 1.6.1). Picking a
   different voice is the realistic option for Puck.

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
