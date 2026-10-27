import json
import subprocess
import shutil
from pathlib import Path
from typing import Dict, Any, Optional
import httpx
from ..core.config import settings
from ..core.logger import logger
from ..posting.youtube_api import YouTubeShortsClient
from ..sourcing.telegram_inbox import clean_md, ensure_telegram_size


class TelegramHumanDispatcher:
    """Sends the finished YouTube Short to the creator's Telegram chat with Approve / Reject."""

    TELEGRAM_API_URL = "https://api.telegram.org/bot"

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
    ):
        self.bot_token = bot_token or getattr(settings, "TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or getattr(settings, "TELEGRAM_CHAT_ID", "")
        self.youtube_client = YouTubeShortsClient()

    def send_video_for_approval(
        self,
        video_path: Path,
        title: str,
        caption: str,
        hook: str,
        target_platform: str = "youtube",
    ) -> Dict[str, Any]:
        """
        Sends the MP4 YouTube Short to the creator's Telegram chat with inline Approve / Reject buttons.
        """
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram bot_token or chat_id not configured in .env. Skipping Telegram dispatch.")
            return {"status": "skipped", "reason": "No credentials"}

        url = f"{self.TELEGRAM_API_URL}{self.bot_token}/sendVideo"
        video_path = ensure_telegram_size(Path(video_path))
        title, hook = clean_md(title), clean_md(hook)
        caption = clean_md(caption)

        message_text = (
            f"▶️ NEW YOUTUBE SHORT READY (Sourced from TikTok)\n\n"
            f"Title: {title}\n"
            f"{hook}\n\n"
            f"{caption}\n\n"
            f"Approve uploads it to your YouTube channel. Reject discards it."
        )[:1000]

        inline_keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve & Post to YouTube", "callback_data": "approve_youtube"},
                    {"text": "❌ Reject", "callback_data": "reject_youtube"},
                ]
            ]
        }

        logger.info(f"Uploading YouTube Short {video_path.name} to Telegram chat {self.chat_id}...")

        try:
            curl_bin = shutil.which("curl.exe") or shutil.which("curl") or "curl"
            curl_cmd = [
                curl_bin,
                "-s",
                url,
                "-F", f"chat_id={self.chat_id}",
                "-F", f"video=@{str(video_path.resolve())}",
                "-F", f"caption={message_text}",
                "-F", f"reply_markup={json.dumps(inline_keyboard)}",
            ]
            res = subprocess.run(curl_cmd, capture_output=True, text=True, timeout=180)
            if res.returncode == 0 and res.stdout:
                res_json = json.loads(res.stdout)
                if res_json.get("ok"):
                    logger.info("[bold green]YouTube Short sent to Telegram for approval successfully![/bold green]")
                    return res_json
                else:
                    logger.warning(f"Telegram curl returned non-ok: {res_json}")
        except Exception as e:
            logger.warning(f"curl upload encountered error: {e}. Falling back to httpx...")

        with open(video_path, "rb") as video_file:
            files = {"video": (video_path.name, video_file, "video/mp4")}
            data = {
                "chat_id": self.chat_id,
                "caption": message_text,
                "reply_markup": json.dumps(inline_keyboard),
            }
            with httpx.Client(timeout=180.0) as client:
                resp = client.post(url, data=data, files=files)
                res_json = resp.json()
                if resp.status_code == 200 and res_json.get("ok"):
                    logger.info("[bold green]YouTube Short sent to Telegram for approval successfully![/bold green]")
                else:
                    logger.error(f"Telegram send failed: {res_json}")
                return res_json
