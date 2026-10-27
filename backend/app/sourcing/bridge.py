"""Cloud -> local hand-off. The cloud scout stores its shortlist in the repo; the local worker fetches it.
Telegram is the control channel (notification + 'Build' button + approval)."""
import base64
import json
from typing import Any, Dict, Optional
import httpx
from ..core.config import settings

PATH = "data/candidates/latest.json"


def _h(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def publish_candidates(payload: Dict[str, Any], token: str, path: str = PATH) -> None:
    """Called in GitHub Actions with the built-in GITHUB_TOKEN (needs contents: write)."""
    url = f"https://api.github.com/repos/{settings.GITHUB_REPO}/contents/{path}"
    with httpx.Client(timeout=30.0) as c:
        cur = c.get(url, headers=_h(token))
        body = {"message": "scout: update candidates", "content": base64.b64encode(json.dumps(payload, indent=2).encode()).decode()}
        if cur.status_code == 200:
            body["sha"] = cur.json()["sha"]
        c.put(url, headers=_h(token), json=body).raise_for_status()


def fetch_candidates() -> Optional[Dict[str, Any]]:
    """Called on the local worker with a read-only token."""
    if not settings.GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN is not set in backend/.env")
    url = f"https://api.github.com/repos/{settings.GITHUB_REPO}/contents/{PATH}"
    with httpx.Client(timeout=30.0) as c:
        r = c.get(url, headers=_h(settings.GITHUB_TOKEN))
        if r.status_code != 200:
            return None
        return json.loads(base64.b64decode(r.json()["content"]))
