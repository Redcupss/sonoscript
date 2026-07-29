import re

# A single request for a whole long document either gets rejected outright (providers cap
# request length well under this) or takes long enough that playback can't start until the
# entire thing comes back. Chunking lets the first bit of audio start almost immediately
# while the rest generates in the background during playback.
CHUNK_TARGET_CHARS = 600
CHUNK_MAX_CHARS = 900
CHUNK_TARGET_CHARS_KOKORO = 180  # local inference runs at ~0.4x realtime — a 600-char first
                                  # chunk would mean 10+ seconds of dead air before playback
                                  # starts; smaller chunks only affect the FIRST-chunk wait,
                                  # since every chunk after that generates in the background
                                  # well within its predecessor's (much longer) playback time.
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
    chunk ahead comfortably outruns playback. target_chars is overridable per-provider —
    Kokoro's local inference runs at roughly half realtime (see _requestKokoroTTS), so the
    default 600 chars would mean 10+ seconds of dead air before the first chunk starts."""
    max_chars = round(target_chars * CHUNK_MAX_CHARS / CHUNK_TARGET_CHARS)
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s]
    chunks = []
    current = ""
    for sentence in sentences:
        pieces = _split_long_sentence(sentence, max_chars) if len(sentence) > max_chars else [sentence]
        for piece in pieces:
            if current and len(current) + len(piece) + 1 > target_chars:
                chunks.append(current)
                current = piece
            else:
                current = f"{current} {piece}".strip()
    if current:
        chunks.append(current)
    return chunks
