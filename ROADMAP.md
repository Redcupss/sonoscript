# Roadmap

Where SonoScript is headed. Shipped work moves to [CHANGELOG.md](CHANGELOG.md);
this file tracks what's in progress, planned, or just an idea worth
considering later. Updated as priorities change — not a promise, a snapshot.

## In progress

Nothing in progress right now — next up is whichever of the Planned items
below gets picked.

## Planned

Nothing queued yet.

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
- **Front-end pitch control** — per-voice pitch defaults (1.7.0) are a
  fixed backend value per voice, not a UI slider the way speed is. Could
  add one that adjusts on top of a voice's baseline, same relationship the
  speed dropdown already has to speed. No blocker now that pitch_shift.py
  exists — genuinely just an unbuilt UI element.
- **Puck's sentence-initial pitch rise** — separate from the flat -1.5
  semitone correction shipped in 1.7.0 (which lowers the whole voice
  evenly). The rise is a *dynamic* prosody pattern baked into specific
  words at specific positions, not a static offset — pitch_shift.py has no
  way to selectively target that, and there's no known parameter for it
  either (see CHANGELOG.md 1.6.1's SonoScript investigation, same class of
  problem). Still unaddressed.

## Shipped

See [CHANGELOG.md](CHANGELOG.md) for everything already released.
