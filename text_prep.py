import re

# Web-extracted text regularly loses the space between two sentences at a block-element
# boundary (Readability's/the DOM's own textContent just concatenates adjacent text nodes with
# nothing between them) — confirmed directly against a real extracted article: "...command
# centers.They were banks.Israeli fighter jets..." got read as "command center DOT they were
# banks DOT isreal...", because a lowercase-letter-period-immediately-followed-by-a-capital-
# letter is exactly the shape _FILENAME_DOT_RE below is designed to catch and mistook it for a
# filename. Real file extensions are essentially always lowercase ("example.txt", not
# "example.Txt") — that asymmetry is the reliable signal used here to tell the two apart: insert
# the missing space (restoring a normal sentence break) whenever a lowercase letter is followed
# immediately by a period and then an uppercase letter, before the filename regex ever runs, so
# it no longer has a same-shaped, space-free match to misfire on.
_MISSING_SENTENCE_SPACE_RE = re.compile(r"([a-z])\.([A-Z])")
# A period with no surrounding whitespace almost never ends a sentence in normal prose —
# real sentence-ending periods are followed by a space (or end of text). This is what
# distinguishes "example.file" from "End of sentence. Next one." — but it also matches
# genuine decimals ("3.12"), which read fine as-is and must NOT be touched, so the left side
# is required to start with a letter, not a digit.
_FILENAME_DOT_RE = re.compile(r"\b([A-Za-z_][\w-]*)\.([A-Za-z]{1,6})\b")
# "a.m"/"p.m" (from "a.m."/"p.m.") match the exact same one-letter-dot-one-letter shape as a
# filename — confirmed directly in a real generated recording's transcript, Whisper heard
# "4:37 a.m." respoken as "four... at dot M" after this turned it into "a dot m.". Excluded by
# name rather than trying to generalize the regex, since these are the only common English
# abbreviations that collide with the filename pattern at this length.
_TIME_ABBREVIATIONS = {"a.m", "p.m"}

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

# Confirmed directly against real generated audio: colons and semicolons get pronounced as
# literal words ("colon", "span"/"spanned" for semicolon) instead of read as a pause, and
# parentheses get the same treatment — the model tries to vocalize the punctuation mark itself
# rather than treating it as a stage direction for pacing. A comma already reads as a natural
# pause everywhere else in this app, so re-punctuating all four as commas (rather than
# stripping them, which would lose the pause entirely) gets the intended effect without
# introducing new symbols the model might also try to pronounce.
#
# The colon lookaround excludes one flanked by digits on both sides (3:00, a 16:9 ratio) — a
# blanket replacement would turn "3:00" into "3, 00", which is wrong in a completely different
# way. Semicolons have no equivalent legitimate numeric use worth protecting.
_COLON_RE = re.compile(r"(?<!\d):(?!\d)")
_SEMICOLON_RE = re.compile(r";")
_PAREN_OPEN_RE = re.compile(r"\s*\(\s*")
_PAREN_CLOSE_RE = re.compile(r"\s*\)")
# An em dash reads as a natural pause in real speech (setting off a parenthetical or an abrupt
# turn, the same job a comma or parenthesis does) — confirmed directly by ear against real
# generated audio that neither Chatterbox nor Sesame pause on it at all without this: the model
# just reads straight through as if the dash weren't there. Spaced on both sides first so an
# unspaced em dash ("Utah—both dressed") doesn't fuse into "Utah, both" with no gap at all once
# turned into a bare comma.
#
# Deliberately NOT widened to an en dash or a plain double hyphen, despite both being common
# plain-text em-dash substitutes — tried that and reverted it after real testing turned up two
# separate regressions: an en dash has its own distinct, standard job (a number range, "1997–
# 2003") that isn't a pause at all and would get mispronounced as two separate numbers; a bare
# "--" collides with ordinary technical text (CLI flags like "--verbose", JS/TS spread syntax
# "...args", Markdown/YAML "---" delimiters), none of which this app has any way to tell apart
# from a real dash substitute. Same reasoning for ellipsis below — only the real character.
_EM_DASH_RE = re.compile(r"\s*—\s*")
# An ellipsis marks a trailing-off pause, the same shape as a comma's — confirmed directly on
# real dialogue-heavy text ("We didn't…it's a team sport") where Sesame turned this into
# outright unrelated word insertions rather than any kind of pause.
_ELLIPSIS_RE = re.compile(r"\s*…\s*")
# A colon/semicolon/paren landing immediately before existing punctuation (a parenthetical
# right at the end of a sentence, e.g. "...offerings (like this one)." ) would otherwise leave
# a stray ", ." or ",," behind once turned into a comma — collapse that down to just the
# punctuation that was already there.
_REDUNDANT_PUNCT_RE = re.compile(r",\s*([.,!?;:])")
# Same problem, reverse order: an em dash/ellipsis landing immediately AFTER a sentence's real
# terminal punctuation (an attribution dash right after a closing quote's period, dialogue that
# ends right where a converted-to-comma symbol used to be) leaves a stray ".," behind that the
# rule above doesn't catch, since it only ever looks for comma-then-punctuation, not
# punctuation-then-comma. Confirmed against real cases: a quote-attribution em dash right after
# a period, and dialogue ending right at a closing quote mark.
_REDUNDANT_PUNCT_RE_REVERSE = re.compile(r"([.!?])\s*,")


def _pause_out_symbols(text):
    text = _PAREN_OPEN_RE.sub(", ", text)
    text = _PAREN_CLOSE_RE.sub(",", text)
    text = _COLON_RE.sub(",", text)
    text = _SEMICOLON_RE.sub(",", text)
    text = _EM_DASH_RE.sub(", ", text)
    text = _ELLIPSIS_RE.sub(", ", text)
    text = _REDUNDANT_PUNCT_RE.sub(r"\1", text)
    return _REDUNDANT_PUNCT_RE_REVERSE.sub(r"\1", text)


# Chatterbox: a literal double/smart quote character is a confirmed, deterministic bug in this
# exact model build (mlx-community Chatterbox Turbo, upstream issue #433) — it produces a ~1.2s
# non-speech "sigh" sound, unrelated to voice or content. Sesame has no equivalent filed bug,
# but confirmed directly by isolated testing (identical dialogue text, "Lehi, Utah" sentence
# removed to rule it out as the cause) that the SAME shape of text — curly quotes plus an
# ellipsis — reliably produces real word-level nonsense inserted into the sentence, not the
# collapse-to-garbage pattern documented elsewhere for these models. Previously only applied to
# Chatterbox (main.py had its own inline version of this exact fix); centralized here so Sesame
# gets the same protection instead of silently lacking it. Straight/curly double quotes and the
# curly OPENING single quote (‘) are unambiguously quote marks, safe to remove outright.
_PROBLEM_QUOTES_RE = re.compile("[\"“”‘]")
# The curly CLOSING single quote (’) does double duty as a real apostrophe in contractions
# ("didn't") — unlike the marks above, deleting it outright turns "didn't" into "didnt",
# "he'll" into "hell", "we're" into "were": different words, different pronunciation, not just
# a missing squiggle. Normalizing to a plain straight apostrophe instead keeps the contraction
# intact for every backend while still removing the exact curly-quote-shaped character
# confirmed to trigger the Chatterbox/Sesame bugs above.
_CURLY_APOSTROPHE_RE = re.compile("’")


def strip_problem_quotes(text):
    text = _CURLY_APOSTROPHE_RE.sub("'", text)
    return _PROBLEM_QUOTES_RE.sub("", text)

# A blank-line gap (one or more empty/whitespace-only lines) between two lines of text — a
# real paragraph break, as opposed to a single mid-paragraph line wrap from word-wrapped or
# pasted text, which must NOT be treated as a break (that would chop a normal sentence in
# half with a spurious pause). \s* between the two \n's absorbs any number of further blank
# or whitespace-only lines in the gap (confirmed against a real pasted book excerpt with a
# "  " line — two bare spaces — sitting between two truly empty ones).
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")
_ENDS_WITH_TERMINAL_PUNCT_RE = re.compile(r"[.!?:;]\s*$")
_ENDS_WITH_PAUSE_PUNCT_RE = re.compile(r"[.!?:;,]\s*$")
# A poem's line break and a title glued directly onto its paragraph (no blank line between
# them, so _PARAGRAPH_SPLIT_RE never sees a boundary there at all) both reach this point as a
# bare "\n" with no textual meaning the model recognizes — it either garbles the line join or
# (matching this app's already-documented Sesame/Chatterbox instability) hallucinates outright
# on it, confirmed against real poetry test input. But a line break can ALSO be a plain
# word-wrapped sentence, split purely by the source document's line width, not a real pause —
# confirmed separately against a real pasted book excerpt where treating every line break as a
# pause chopped ordinary sentences in half. The distinguishing signal used here is the same
# one a human reader uses: a wrapped sentence continues onto a lowercase word; a poem line or
# an unpunctuated title starts a fresh, capitalized thought with nothing carrying it forward.
_STARTS_NEW_THOUGHT_RE = re.compile(r'^["\'“‘(]*[A-Z]')


def _pause_out_line_breaks(paragraph):
    lines = [ln.strip() for ln in paragraph.split("\n") if ln.strip()]
    if not lines:
        return ""
    pieces = [lines[0]]
    for line in lines[1:]:
        prev = pieces[-1]
        if _ENDS_WITH_PAUSE_PUNCT_RE.search(prev) or not _STARTS_NEW_THOUGHT_RE.match(line):
            pieces[-1] = f"{prev} {line}"
        else:
            pieces[-1] = f"{prev},"
            pieces.append(line)
    return " ".join(pieces)


def normalize_paragraph_breaks(text):
    """A chapter heading, subtitle, or quote attribution pasted from a book/document
    typically has no sentence-ending punctuation of its own — chunk_text's sentence splitter
    only recognizes .!?, so a title with none of those just runs straight into whatever comes
    next as one giant "sentence," which is exactly what read as the model "blowing past" the
    section breaks (confirmed against a real pasted chapter opening: title, subtitle, and an
    Einstein quote's byline all glued into one unpunctuated run). Giving every blank-line-
    separated paragraph a real terminal punctuation mark — appending a period if it doesn't
    already have one — makes each one its own proper sentence/chunk boundary, without asking
    the user to reformat anything themselves."""
    paragraphs = [p.strip() for p in _PARAGRAPH_SPLIT_RE.split(text) if p.strip()]
    paragraphs = [_pause_out_line_breaks(p) for p in paragraphs]
    fixed = [p if _ENDS_WITH_TERMINAL_PUNCT_RE.search(p) else p + "." for p in paragraphs]
    return " ".join(fixed)


def sanitize_for_speech(text):
    """Text-level cleanup applied before any provider (System/Kokoro/ElevenLabs/OpenAI) gets
    the text — these are phonemizer/prosody quirks, not something any one backend handles
    differently, so fixing it once here covers all of them."""
    text = _CONFUSABLE_RE.sub(lambda m: CONFUSABLE_LETTERS[m.group(0)], text)
    text = _MISSING_SENTENCE_SPACE_RE.sub(r"\1. \2", text)
    text = _FILENAME_DOT_RE.sub(
        lambda m: m.group(0) if m.group(0).lower() in _TIME_ABBREVIATIONS else f"{m.group(1)} dot {m.group(2)}",
        text)
    text = _OVERRIDE_RE.sub(lambda m: PRONUNCIATION_OVERRIDES[m.group(0)], text)
    text = _pause_out_symbols(text)
    return text
