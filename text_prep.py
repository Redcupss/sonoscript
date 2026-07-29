import re

# A period with no surrounding whitespace almost never ends a sentence in normal prose —
# real sentence-ending periods are followed by a space (or end of text). This is what
# distinguishes "example.file" from "End of sentence. Next one." — but it also matches
# genuine decimals ("3.12"), which read fine as-is and must NOT be touched, so the left side
# is required to start with a letter, not a digit.
_FILENAME_DOT_RE = re.compile(r"\b([A-Za-z_][\w-]*)\.([A-Za-z]{1,6})\b")

# Words with an internal capital (not ALL-CAPS, which most engines already spell out
# correctly as an acronym) can get an inconsistent or paused pronunciation — the phonemizer
# reads the capital as a new-word signal. Respelling to a plain lowercase-after-first-letter
# form sidesteps it. Small and built-in for now; extend as specific words come up.
PRONUNCIATION_OVERRIDES = {
    "GitHub": "git hub",
    # Not "Sono-Script" or "Sonoscript" — tested against real Kokoro output: plain "Sono
    # Script" is the correct pronunciation AND avoids an odd rising inflection that the
    # hyphenated form got whenever it landed at the very end of a sentence. That rise turned
    # out to be sentence-position prosody (same word mid-sentence was fine), not something
    # this substitution can fully control — if "SonoScript" happens to be the very last word
    # before a period in the user's text, a slight rise may still occasionally happen. No
    # known way to eliminate that entirely (see ROADMAP.md's pitch-shift DSP entry — prosody
    # like this isn't exposed as a controllable parameter, same as Puck's sentence-initial
    # rise).
    "SonoScript": "Sono Script",
}
_OVERRIDE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in PRONUNCIATION_OVERRIDES) + r")\b"
)

# Certain PDFs (screenwriting software and some word processors export this way) subset their
# fonts with a broken cmap/ToUnicode table: the glyph drawn for a plain letter is CID-mapped to
# the wrong Unicode code point, so copy-pasted text has real IPA/phonetic-extension characters
# standing in for ordinary Latin letters — visually identical, but a different character. Kokoro
# (espeak-ng) doesn't recognize these as belonging to any alphabet and falls back to reading the
# character's own hex code point digit-by-digit instead — "ɑ" (U+0251, Latin Alpha) came out as
# "letter two five one" (hex 251), confirmed by phonemizing the exact string against the
# bundled espeak backend. Mapped back to their plain intended letters before anything reaches
# a TTS backend, since this is a paste artifact, not real IPA transcription in normal prose.
CONFUSABLE_LETTERS = {
    "ɑ": "a",  # U+0251 Latin Alpha
    "ɡ": "g",  # U+0261 Latin Script G
    "ı": "i",  # U+0131 Latin Dotless I
}
_CONFUSABLE_RE = re.compile("[" + "".join(CONFUSABLE_LETTERS) + "]")


def sanitize_for_speech(text):
    """Text-level cleanup applied before any provider (System/Kokoro/ElevenLabs/OpenAI) gets
    the text — these are phonemizer/prosody quirks, not something any one backend handles
    differently, so fixing it once here covers all of them."""
    text = _CONFUSABLE_RE.sub(lambda m: CONFUSABLE_LETTERS[m.group(0)], text)
    text = _FILENAME_DOT_RE.sub(lambda m: f"{m.group(1)} dot {m.group(2)}", text)
    text = _OVERRIDE_RE.sub(lambda m: PRONUNCIATION_OVERRIDES[m.group(0)], text)
    return text
