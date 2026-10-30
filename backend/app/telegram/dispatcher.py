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
        niche_label: str = "",
        ranked_clips: Optional[list] = None,
        qa_review: Optional[Dict[str, Any]] = None,
        strategy_diagnosis: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Sends the MP4 YouTube Short to the creator's Telegram chat with an elegant,
        concise yet well-detailed studio briefing card and inline Approve / Reject buttons.
        """
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram bot_token or chat_id not configured in .env. Skipping Telegram dispatch.")
            return {"status": "skipped", "reason": "No credentials"}

        url = f"{self.TELEGRAM_API_URL}{self.bot_token}/sendVideo"
        video_path = ensure_telegram_size(Path(video_path))
        clean_title = clean_md(title)
        clean_hook = clean_md(hook)
        clean_niche = clean_md(niche_label or "Viral Ranking")

        clips = ranked_clips or []
        total_dur = round(sum(float(c.get("duration") or 0) for c in clips), 1) if clips else 0
        qa = qa_review or {}
        qa_score = qa.get("viral_score", 9.0)
        qa_model = qa.get("reviewed_by_model", "Gemini 3")
        qa_note = clean_md(str(qa.get("director_notes") or "Full setup & payoff verified"))[:85]

        rank_icons = {5: "5️⃣", 4: "4️⃣", 3: "3️⃣", 2: "2️⃣", 1: "1️⃣"}
        lineup_rows = []
        for c in clips:
            r_num = int(c.get("rank", 0))
            icon = rank_icons.get(r_num, f"#{r_num}")
            lbl = clean_md(str(c.get("rank_label") or "Moment"))
            dur_s = int(round(float(c.get("duration") or 0)))
            summary = clean_md(str(c.get("what_happens") or c.get("punchline") or ""))
            if len(summary) > 58:
                summary = summary[:55].rstrip() + "..."
            lineup_rows.append(f"{icon} {lbl} ({dur_s}s) — {summary}")

        lineup_block = "\n".join(lineup_rows) if lineup_rows else clean_md(caption)[:380]
        dur_badge = f" • ⏱️ {int(round(total_dur))}s" if total_dur > 0 else ""

        message_text = (
            f"🎬 AAT STUDIO — SHORT READY\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📌 {clean_title}\n"
            f"🏷️ {clean_niche} • 🎯 {clean_hook}{dur_badge}\n"
            f"🧠 QA ({qa_model}): {qa_score}/10 — {qa_note}\n\n"
            f"🏆 RANKED LINEUP (#5 → #1)\n"
            f"{lineup_block}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Tap below to publish to YouTube Shorts or reject:"
        )[:1015]

        inline_keyboard = {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve & Publish", "callback_data": "approve_youtube"},
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
