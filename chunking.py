import re

# A single request for a whole long document either gets rejected outright (providers cap
# request length well under this) or takes long enough that playback can't start until the
# entire thing comes back. Chunking lets the first bit of audio start almost immediately
# while the rest generates in the background during playback.
CHUNK_TARGET_CHARS = 600
CHUNK_MAX_CHARS = 900

# Kokoro's local inference runs at ~0.45x realtime (measured) — a flat 600-char first chunk
# would mean 10+ seconds of dead air before playback starts. Only the FIRST few chunks need
# to be small: prefetch of chunk N+1 only has to finish generating before chunk N's (much
# longer) playback ends, and at this RTF a chunk can grow by more than 2x the previous one's
# size and still finish generating well within its predecessor's playback time — so growing
# quickly back up to the normal size (rather than staying small for the whole document) keeps
# the fast-start benefit without fragmenting a long document into far more chunks than needed.
KOKORO_CHUNK_SCHEDULE = [150, 300, 450]  # chunk 3+ falls back to CHUNK_TARGET_CHARS


def kokoro_chunk_target(index):
    if index < len(KOKORO_CHUNK_SCHEDULE):
        return KOKORO_CHUNK_SCHEDULE[index]
    return CHUNK_TARGET_CHARS


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _split_long_sentence(sentence, max_chars):
    """Word-boundary fallback for a single run of text longer than max_chars with no
    sentence-ending punctuation to split on (rare, but a title/list/heading can do this)."""
    words = sentence.split(" ")
    pieces = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 > max_chars:
            if current:
                pieces.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        pieces.append(current)
    return pieces


def chunk_text(text, target_chars=CHUNK_TARGET_CHARS):
    """Split into ~target_chars chunks on sentence boundaries, so each one is well under
    every provider's per-request limit and fast enough to generate that prefetching one
    chunk ahead comfortably outruns playback. target_chars is overridable per-provider — it
    can be a fixed int, or a callable(chunk_index) -> int for graduated sizing (e.g.
    kokoro_chunk_target, above)."""
    get_target = target_chars if callable(target_chars) else (lambda i: target_chars)
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s]
    chunks = []
    current = ""
    for sentence in sentences:
        # len(chunks) is the index of whatever chunk is currently being accumulated — it
        # only advances when one gets appended below, so this always reflects "the target
        # for the chunk in progress right now," not the one that was just finished.
        target = get_target(len(chunks))
        max_chars = round(target * CHUNK_MAX_CHARS / CHUNK_TARGET_CHARS)
        pieces = _split_long_sentence(sentence, max_chars) if len(sentence) > max_chars else [sentence]
        for piece in pieces:
            target = get_target(len(chunks))
            if current and len(current) + len(piece) + 1 > target:
                chunks.append(current)
                current = piece
            else:
                current = f"{current} {piece}".strip()
    if current:
        chunks.append(current)
    return chunks
