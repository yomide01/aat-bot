"""Read and write small text files in this repo through the GitHub API."""
import base64
from typing import Optional
import httpx
from .config import settings


def _headers(token: str):
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def read_text(path: str, token: str) -> Optional[str]:
    if not token:
        return None
    url = f"https://api.github.com/repos/{settings.GITHUB_REPO}/contents/{path}"
    with httpx.Client(timeout=30.0) as client:
        res = client.get(url, headers=_headers(token))
    if res.status_code != 200:
        return None
    return base64.b64decode(res.json()["content"]).decode("utf-8")


def write_text(path: str, text: str, token: str, message: str) -> None:
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set")
    url = f"https://api.github.com/repos/{settings.GITHUB_REPO}/contents/{path}"
    body = {
        "message": message,
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
    }
    with httpx.Client(timeout=30.0) as client:
        current = client.get(url, headers=_headers(token))
        if current.status_code == 200:
            body["sha"] = current.json()["sha"]
        res = client.put(url, headers=_headers(token), json=body)
        res.raise_for_status()
