"""Shared wire and model constants for the site client and standalone worker."""

MODEL_ID = "MelodyMachine/Deepfake-audio-detection-V2"
MODEL_REVISION = "de3cde5a29c449bb5268814e421b46bf6ebdcd72"
MODEL_ALGORITHM = f"melodymachine-deepfake-audio-v2-{MODEL_REVISION[:12]}"
FAKE_SCORE_THRESHOLD = 0.9995
SOURCE_SAMPLE_RATE = 48_000
WINDOW_MS = 5_000
WINDOW_OVERLAP_MS = 1_000
MAX_WINDOWS_PER_CALL = 4
