"""Short check for a late Approve / Reject, or for a TikTok confirmation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.orchestrator.approval import watch

if __name__ == "__main__":
    watch()
