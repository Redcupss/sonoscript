# Per-voice baseline adjustments, applied on top of whatever the user has selected in the
# UI (currently just speed) rather than replacing it — e.g. Puck's default pitch just shifts
# the whole -1.5x..1.5x speed range down a bit, it doesn't override the user's own choice.
# Picked by ear against real generated speech, not guessed — see CHANGELOG.md.
VOICE_DEFAULTS = {
    "am_puck": {"pitch_semitones": -1.5},
}


def voice_pitch_semitones(voice_id):
    return VOICE_DEFAULTS.get(voice_id, {}).get("pitch_semitones", 0)
