"""Telegram as the human bridge for a cloud-only run: send clips in, get approval out."""
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
from ..core.config import settings, CLIPS_DIR
from ..core.logger import logger
from .rights import check_clip, normalize_license

API = "https://api.telegram.org"
MAX_BOT_DOWNLOAD = 20 * 1024 * 1024
TG_UPLOAD_LIMIT = 45 * 1024 * 1024


def clean_md(text: str) -> str:
    return re.sub(r"[*_`\[\]]", "", text or "")


def ensure_telegram_size(path: Path) -> Path:
    """Telegram bots can upload ~50MB. Re-encode a lighter copy when the master is too big."""
    if path.stat().st_size <= TG_UPLOAD_LIMIT:
        return path
    out = path.with_name(path.stem + "_tg.mp4")
    subprocess.run(["ffmpeg", "-y", "-i", str(path), "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,fps=30",
                    "-c:v", "libx264", "-b:v", "5M", "-maxrate", "6M", "-bufsize", "10M", "-c:a", "aac", "-b:a", "128k", str(out)],
                   capture_output=True)
    return out if out.exists() else path


class TelegramInbox:
    def __init__(self):
        self.token = settings.TELEGRAM_BOT_TOKEN
        self.chat = str(settings.TELEGRAM_CHAT_ID)
        self.offset = 0
        self.enabled = bool(self.token and self.chat)

    def _call(self, method: str, **kw) -> Dict[str, Any]:
        with httpx.Client(timeout=40.0) as c:
            r = c.post(f"{API}/bot{self.token}/{method}", json=kw)
            return r.json()

    def send(self, text: str, markup: Optional[Dict[str, Any]] = None) -> None:
        if not self.enabled:
            logger.info(f"[telegram disabled] {text}")
            return
        payload = {"chat_id": self.chat, "text": text[:4000]}
        if markup:
            payload["reply_markup"] = markup
        self._call("sendMessage", **payload)

    def send_document(self, path: Path, caption: str = "") -> None:
        if not self.enabled:
            return
        with open(path, "rb") as f, httpx.Client(timeout=300.0) as c:
            c.post(f"{API}/bot{self.token}/sendDocument", data={"chat_id": self.chat, "caption": caption[:1000]},
                   files={"document": (path.name, f, "video/mp4")})

    def skip_old_updates(self) -> None:
        if not self.enabled:
            return
        res = self._call("getUpdates", offset=-1, timeout=0).get("result", [])
        if res:
            self.offset = res[-1]["update_id"] + 1

    def _updates(self, timeout: int = 20) -> List[Dict[str, Any]]:
        res = self._call("getUpdates", offset=self.offset, timeout=timeout, allowed_updates=["message", "callback_query"])
        ups = res.get("result", [])
        if ups:
            self.offset = ups[-1]["update_id"] + 1
        return ups

    def _download(self, file_id: str, name: str) -> Optional[Path]:
        info = self._call("getFile", file_id=file_id)
        if not info.get("ok"):
            return None  # Telegram refuses files over 20MB here
        dest = CLIPS_DIR / name
        with httpx.Client(timeout=120.0) as c:
            r = c.get(f"{API}/file/bot{self.token}/{info['result']['file_path']}")
            dest.write_bytes(r.content)
        return dest

    def collect_clips(self, need: int, wait_sec: int) -> List[Dict[str, Any]]:
        """Waits for the creator to send video files. Caption format: `licence | credit`."""
        got: List[Dict[str, Any]] = []
        end = time.time() + wait_sec
        while len(got) < need and time.time() < end:
            for u in self._updates(timeout=20):
                m = u.get("message") or {}
                if str(m.get("chat", {}).get("id")) != self.chat:
                    continue
                media = m.get("video") or m.get("animation") or (m.get("document") if str(m.get("document", {}).get("mime_type", "")).startswith("video/") else None)
                if not media:
                    continue
                bits = [b.strip() for b in (m.get("caption") or "").split("|")]
                if len(bits) < 2:
                    self.send("❌ Add a caption like: `owned | your name` or `cc-by | creator name` or `permission-granted | creator name`".replace("`", ""))
                    continue
                clip = {"license": normalize_license(bits[0]), "credit": bits[1], "title": bits[1], "url": "sent via Telegram",
                        "video_id": f"tg{m['message_id']}"}
                ok, why = check_clip(clip)
                if not ok:
                    self.send(f"❌ Can't use that clip: {why}")
                    continue
                p = self._download(media["file_id"], f"tg_{m['message_id']}.mp4")
                if not p:
                    self.send("❌ Telegram only lets me read files up to 20MB. Send a shorter/smaller clip.")
                    continue
                clip["filepath"] = str(p)
                got.append(clip)
                self.send(f"✅ Got clip {len(got)}/{need}")
        return got

    def wait_for_decision(self, wait_sec: int) -> Optional[str]:
        end = time.time() + wait_sec
        while time.time() < end:
            for u in self._updates(timeout=25):
                cb = u.get("callback_query")
                if cb and str(cb.get("message", {}).get("chat", {}).get("id")) == self.chat:
                    self._call("answerCallbackQuery", callback_query_id=cb["id"])
                    if cb.get("data") in ("approve_post", "reject_post"):
                        return "approve" if cb["data"] == "approve_post" else "reject"
        return None
