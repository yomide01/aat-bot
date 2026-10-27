"""Secure OAuth token store for YouTube Channel publishing.
Uses local PyNaCl SecretBox encryption (untracked by git) and GitHub Actions Encrypted Secrets.
Never commits token files to the repository so the repo is 100% safe when public."""
import base64
import hashlib
import json
from typing import Any, Dict, Optional
import httpx
from nacl import encoding, public
from nacl.secret import SecretBox
from .config import DATA_DIR, settings
from .logger import logger

LOCAL_TOKEN_FILE = DATA_DIR / "private" / "youtube_tokens.json"


def _box(secret_override: str = "") -> Optional[SecretBox]:
    secret = secret_override or settings.YOUTUBE_CLIENT_SECRET or settings.TELEGRAM_BOT_TOKEN
    if not secret:
        return None
    return SecretBox(hashlib.sha256(secret.encode("utf-8")).digest())


def load_tokens(github_token: str = "") -> Optional[Dict[str, Any]]:
    # 1. Try local encrypted cache first
    box = _box()
    if box and LOCAL_TOKEN_FILE.exists():
        try:
            raw = LOCAL_TOKEN_FILE.read_text(encoding="utf-8")
            blob = base64.b64decode(json.loads(raw)["blob"])
            data = json.loads(box.decrypt(blob).decode("utf-8"))
            if data.get("refresh_token") or data.get("access_token"):
                return data
        except Exception:
            pass

    # 2. Fall back to GitHub Actions Secrets injected via environment variables
    if settings.YOUTUBE_REFRESH_TOKEN or settings.YOUTUBE_ACCESS_TOKEN:
        return {
            "access_token": settings.YOUTUBE_ACCESS_TOKEN,
            "refresh_token": settings.YOUTUBE_REFRESH_TOKEN,
        }
    return None


def save_tokens(tokens: Dict[str, Any], github_token: str = "") -> None:
    # 1. Save to local encrypted cache (gitignored)
    box = _box()
    if box:
        blob = base64.b64encode(box.encrypt(json.dumps(tokens).encode("utf-8"))).decode("ascii")
        payload = json.dumps({"blob": blob})
        LOCAL_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOCAL_TOKEN_FILE.write_text(payload, encoding="utf-8")

    # 2. If a Personal Access Token is available, also sync directly to GitHub Actions Secrets
    gh_tok = github_token or settings.GITHUB_TOKEN
    if gh_tok and settings.GITHUB_REPO:
        try:
            headers = {"Authorization": f"Bearer {gh_tok}", "Accept": "application/vnd.github+json"}
            with httpx.Client(timeout=20.0, headers=headers) as client:
                pk_res = client.get(f"https://api.github.com/repos/{settings.GITHUB_REPO}/actions/secrets/public-key")
                if pk_res.status_code == 200:
                    pk = pk_res.json()
                    pub_key = public.PublicKey(pk["key"].encode("utf-8"), encoding.Base64Encoder())
                    sealed_box = public.SealedBox(pub_key)
                    for secret_name, val in [
                        ("YOUTUBE_ACCESS_TOKEN", tokens.get("access_token") or ""),
                        ("YOUTUBE_REFRESH_TOKEN", tokens.get("refresh_token") or ""),
                    ]:
                        if not val:
                            continue
                        enc_val = base64.b64encode(sealed_box.encrypt(val.encode("utf-8"))).decode("utf-8")
                        client.put(
                            f"https://api.github.com/repos/{settings.GITHUB_REPO}/actions/secrets/{secret_name}",
                            json={"encrypted_value": enc_val, "key_id": pk["key_id"]},
                        )
        except Exception as exc:
            logger.debug(f"GitHub Actions secret sync note: {exc}")
