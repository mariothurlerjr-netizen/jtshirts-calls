"""Configuration loader. Reads .env and sets up paths."""
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# RingCentral credentials
RC_CLIENT_ID = os.getenv("RC_CLIENT_ID", "").strip()
RC_CLIENT_SECRET = os.getenv("RC_CLIENT_SECRET", "").strip()
RC_JWT = os.getenv("RC_JWT", "").strip()
RC_SERVER = os.getenv("RC_SERVER", "https://platform.ringcentral.com").rstrip("/")

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

# Dashboard
DISPLAY_TIMEZONE = os.getenv("DISPLAY_TIMEZONE", "America/New_York")

# History cutoff
_hist_start = os.getenv("HISTORY_START_DATE", "").strip()
if _hist_start:
    HISTORY_START = datetime.fromisoformat(_hist_start)
else:
    HISTORY_START = datetime(datetime.now().year, 1, 1)

# Paths
DATA_DIR = PROJECT_ROOT / "data"
RECORDINGS_DIR = DATA_DIR / "recordings"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
REPORTS_DIR = PROJECT_ROOT / "reports"
DB_PATH = DATA_DIR / "calls.db"

# Ensure directories exist
for d in [DATA_DIR, RECORDINGS_DIR, TRANSCRIPTS_DIR, REPORTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Call quality thresholds (in seconds)
MIN_DURATION_FOR_RECORDING_DOWNLOAD = 120  # only download 2min+
MIN_DURATION_FOR_REAL_CALL = 15            # under this = no pickup or instant hangup


def validate():
    """Raise clear error if required credentials are missing."""
    missing = []
    if not RC_CLIENT_ID:
        missing.append("RC_CLIENT_ID")
    if not RC_CLIENT_SECRET:
        missing.append("RC_CLIENT_SECRET")
    if not RC_JWT:
        missing.append("RC_JWT")
    if not OPENAI_API_KEY:
        missing.append("OPENAI_API_KEY")
    if missing:
        raise RuntimeError(
            f"Missing .env values: {', '.join(missing)}.\n"
            f"See .env.example for template."
        )
