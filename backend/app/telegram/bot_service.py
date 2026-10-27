import json
import os
import time
import threading
from pathlib import Path
from typing import Dict, Any, Optional
import httpx

from ..core.config import settings, DATA_DIR, OUTPUT_DIR
from ..core.logger import logger
from ..posting.youtube_api import YouTubeShortsClient
from ..ai.creator_brain import MODEL_POOL, GeminiCreatorBrain
from ..orchestrator.approval import (
    connect_youtube,
    youtube_login_instructions,
    format_status_summary,
    load_state,
    save_state,
    post_and_confirm_youtube,
    _deliver,
)
from ..sourcing.telegram_inbox import TelegramInbox


class TelegramInteractiveBot:
    """
    Intelligent Conversational Co-Pilot & Residential-IP Subagent Bridge for AAT (YouTube Shorts Engine).
    Powered by the Gemini 3.8 Flash / 3.7 Flash / 3.6 Flash / 3.5 Flash Multimodal Pool
    (with gemini-2.5-flash as dead-last fallback) + Official YouTube Data API v3 & YouTube Analytics API v2.
    """

    TELEGRAM_API_URL = "https://api.telegram.org/bot"

    def __init__(self, bot_token: Optional[str] = None, creator_chat_id: Optional[str] = None):
        self.bot_token = bot_token or getattr(settings, "TELEGRAM_BOT_TOKEN", "")
        self.creator_chat_id = str(creator_chat_id or getattr(settings, "TELEGRAM_CHAT_ID", ""))
        self.gemini_api_key = getattr(settings, "GEMINI_API_KEY", "")
        self.gemini_model = getattr(settings, "GEMINI_MODEL", "gemini-3.8-flash")
        self.state_file = DATA_DIR / "telegram_state.json"
        self.youtube_client = YouTubeShortsClient()
        self.inbox = TelegramInbox()
        self._load_state()

    def _load_state(self):
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    self.state = json.load(f)
            except Exception:
                self.state = {"last_update_id": 0, "latest_video": {}, "chat_history": []}
        else:
            self.state = {"last_update_id": 0, "latest_video": {}, "chat_history": []}

    def _save_state(self):
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2)
        except Exception:
            pass

    def send_message(self, text: str, reply_markup: Optional[Dict[str, Any]] = None) -> bool:
        url = f"{self.TELEGRAM_API_URL}{self.bot_token}/sendMessage"
        payload: Dict[str, Any] = {
            "chat_id": self.creator_chat_id,
            "text": text[:4000],
            "parse_mode": "Markdown",
        }
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)

        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(url, json=payload)
                if resp.status_code == 200 and resp.json().get("ok"):
                    return True
                payload.pop("parse_mode", None)
                resp2 = client.post(url, json=payload)
                return resp2.status_code == 200
        except Exception as e:
            logger.error(f"Error sending Telegram message: {e}")
            return False

    def format_live_analytics(self) -> str:
        """Pulls full channel & per-video telemetry from YouTube Data API v3 & YouTube Analytics API v2."""
        stats = self.youtube_client.fetch_channel_analytics(days=90)
        if not stats.get("connected"):
            return "⚠️ YouTube channel is not connected yet. Send `/login` to connect!"
        rep = stats.get("report") or {}
        traffic = stats.get("traffic_sources") or []
        countries = stats.get("top_countries") or []

        lines = [
            f"📊 *LIVE YOUTUBE ANALYTICS — {stats.get('channel_title')} ({stats.get('handle', '')})*",
            "",
            f"• *Subscribers:* {stats.get('subscribers', 0):,}",
            f"• *Total Live Catalog Views:* {stats.get('total_views', 0):,}",
            f"• *Published Shorts:* {stats.get('total_videos', 0)}",
            f"• *Best Upload Window (WAT):* {stats.get('best_upload_window_wat', '16:30 - 19:30 WAT')}",
        ]
        if rep:
            lines.extend([
                "",
                "📈 *YouTube Analytics API v2 Deep Metrics:*",
                f"• *Tracked Views:* {rep.get('views', 0):,} | *Watch Time:* {rep.get('estimatedMinutesWatched', 0):,} mins",
                f"• *Avg View Duration:* {rep.get('averageViewDuration', 0)}s | *Avg View %:* {float(rep.get('averageViewPercentage') or 0):.1f}%",
                f"• *Likes:* {rep.get('likes', 0)} | *Comments:* {rep.get('comments', 0)} | *Shares:* {rep.get('shares', 0)}",
            ])
        if traffic:
            tr_str = " | ".join(f"{t['source']}: {t['share_pct']}%" for t in traffic[:4])
            lines.append(f"• *Traffic Sources:* {tr_str}")
        if countries:
            ct_str = " | ".join(f"{c['country']}: {c['share_pct']}% (AVP {c['avg_view_pct']}%)" for c in countries[:4])
            lines.append(f"• *Top Audience Countries:* {ct_str}")

        recent = stats.get("recent_videos") or []
        if recent:
            lines.append(f"\n🎬 *Complete Published Video Catalog ({len(recent)} Shorts):*")
            for v in recent[:10]:
                avp_tag = f" | 🎯 {v.get('avg_view_pct')}% AVP" if v.get("avg_view_pct") else ""
                lines.append(
                    f"• *{v.get('title')}* ({v.get('duration_sec', 0)}s)\n"
                    f"  🕒 {v.get('published_wat')} | 🏷️ {v.get('algo_tier')}\n"
                    f"  👁️ {v.get('views', 0):,} views ({v.get('velocity_vph', 0)}/hr) | 👍 {v.get('likes', 0)} ({v.get('like_rate_pct', 0)}%) | 💬 {v.get('comments', 0)}{avp_tag}\n"
                    f"  🔗 {v.get('url')}"
                )
        try:
            diag = GeminiCreatorBrain().analyze_channel_and_evolve_strategy(stats)
            lines.extend([
                "",
                f"🧠 *AI FORENSIC CHANNEL DIAGNOSIS ({diag.get('active_model', 'Gemini 3.8')}):*",
                f"• *Algorithm & Seed Status:* {diag.get('shadowban_or_seed_status', '')}",
                f"• *Why Top Video Won:* {diag.get('viral_vs_stalled_reason', '')}",
                f"• *Conversion & Comment Diagnosis:* {diag.get('engagement_diagnosis', '')}",
                f"• *Peak Timing Insight (WAT vs US):* {diag.get('peak_timing_insight', '')}",
                f"• *Next Build Adaptation:* {diag.get('ranking_tactic', '')} (~{diag.get('editing_pacing_sec', 9.5)}s/clip)",
            ])
            autopsies = diag.get("per_video_autopsy") or []
            if autopsies:
                lines.append("\n🔬 *Per-Video Autopsy:*")
                for a in autopsies[:5]:
                    lines.append(f"• *{a.get('title', '')[:48]}:* {a.get('verdict', '')}")
        except Exception:
            pass
        return "\n".join(lines)

    def ask_gemini_copilot(self, user_prompt: str) -> str:
        """Consults the Gemini 3.8 / 3.7 / 3.6 / 3.5 Flash model pool with live YouTube channel analytics."""
        self._load_state()
        latest_yt = self.state.get("latest_youtube") or self.state.get("latest_video") or {}
        live_stats = self.youtube_client.fetch_channel_analytics(days=90)

        context_summary = (
            f"\nLIVE YOUTUBE CHANNEL ANALYTICS:\n{json.dumps(live_stats)}\n"
            f"\nLATEST COMPILED YOUTUBE SHORT:\n"
            f"- Title: {latest_yt.get('title', 'N/A')}\n"
            f"- Banner Header: {latest_yt.get('banner_title', 'N/A')}\n"
            f"- Niche: {latest_yt.get('niche', 'N/A')}\n"
            f"- QA Review: {json.dumps(latest_yt.get('qa_review', {}))}\n"
            f"- Ranked Clips & Sources: {json.dumps(latest_yt.get('sources', []))}\n"
        )

        system_instruction = (
            "You are AAT (Autonomous YouTube Shorts Viral Engine) Co-Pilot, an elite, witty, internet-native AI video director. "
            "You are chatting directly with your creator/owner on Telegram. "
            "You have direct access to their live YouTube Data API v3 & YouTube Analytics API v2 telemetry (every published video, views, velocity, watch time, retention %, traffic sources, top countries, likes, comments, subscribers) "
            "and full details on every single-scene clip compiled into their Shorts. "
            "Answer their questions accurately using the real data below, and keep replies concise and conversational.\n"
            f"{context_summary}"
        )

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": system_instruction},
                        {"text": f"Creator says: {user_prompt}"},
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.65,
                "maxOutputTokens": 700,
            },
        }

        for model_name in MODEL_POOL:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
            try:
                with httpx.Client(timeout=25.0, headers={"x-goog-api-key": self.gemini_api_key}) as client:
                    resp = client.post(url, json=payload)
                    if resp.status_code in (429, 500, 503, 404):
                        continue
                    data = resp.json()
                    if "candidates" in data and len(data["candidates"]) > 0:
                        return data["candidates"][0]["content"]["parts"][0]["text"]
            except Exception:
                continue
        return "🤖 I'm here! Send `/create` to build a new YouTube Short, `/analytics` for full per-video diagnosis, or `/sources` to inspect the clips."

    def _manual_file(self) -> Path:
        return DATA_DIR / "manual_clips.json"

    def _add_manual_clip(self, args: str) -> str:
        from ..sourcing.rights import check_clip, normalize_license
        bits = [x.strip() for x in args.split("|")]
        if len(bits) < 3 or not bits[0].startswith("http"):
            return (
                "Usage: `/clip <url> | <licence> | <credit>`\n"
                "Licence can be: `transformative-credit`, `cc-by`, `cc0`, `public-domain`, `permission-granted`, `owned`."
            )
        clip = {
            "url": bits[0],
            "license": normalize_license(bits[1]),
            "credit": bits[2],
            "title": bits[2],
            "video_id": "manual" + str(abs(hash(bits[0])) % 10**8),
        }
        ok, why = check_clip(clip)
        if not ok:
            return "❌ Not added: " + why
        f = self._manual_file()
        items = json.loads(f.read_text()) if f.exists() else []
        items.append(clip)
        f.write_text(json.dumps(items, indent=2))
        return f"✅ Added! {len(items)} manual clip(s) queued for the next YouTube Short build."

    def _start_subagent_build(self, niche_or_topic: str = ""):
        def run():
            try:
                from scripts.run_pipeline import prepare
                from ..orchestrator.approval import announce
                self.send_message(
                    "🚀 *Subagent Residential-IP Build Started!*\n"
                    "🔍 Finding *original single-scene clips* (excluding compilations)\n"
                    "👁️ Watching video motion + audio with the Gemini 3.8 / 3.x Flash Pool & crafting dynamic title/commentary..."
                )
                prepare(niche=niche_or_topic, topic="", count=5)
                announce(wait_min=1)
            except SystemExit:
                pass
            except Exception as ex:
                self.send_message(f"⚠️ Subagent build encountered an error: {ex}")

        threading.Thread(target=run, daemon=True).start()

    def handle_command(self, text: str) -> str:
        clean = text.strip()
        parts = clean.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        gh_token = os.environ.get("GITHUB_TOKEN", "")

        if cmd in {"/start", "/help"}:
            return (
                "👋 *Yo Creator! AAT YouTube Shorts Ranking Engine is LIVE!*\n\n"
                "🔄 *How I Work:*\n"
                "• Master Niche: *Viral Ranking Shorts (#5 to #1)* rotating across sub-niches (*Epic Fails, Football, Pranks, Embarrassing, Funny, Wild, Crazy, Instant Karma, Gym Fails, Cute, 200 IQ* & more).\n"
                "• Sources *original single-scene clips* strictly from *TikTok & YouTube* (never compilations).\n"
                "• Watches continuous video + audio with the *Gemini 3.8 / 3.x Flash Pool*, verifies true vibe match, trims around the full climax payoff, and renders the progressive `1-5` Ranking Tower.\n\n"
                "⚡ *Commands:*\n"
                "• `/create [sub-niche]` — Build a fresh Ranking Short (leave blank to auto-rotate, or try `fails`, `football`, `pranks`, `embarrassing`, `funny`, `wild`, `crazy`, `karma`, `cute`)\n"
                "• `/analytics` — View deep per-video YouTube analytics, retention %, traffic sources & AI diagnosis\n"
                "• `/sources` — View original clip links, actions & scores\n"
                "• `/status` — Check YouTube channel connection & pending video\n"
                "• `/login` — Get your 1-click Google/YouTube authorization link\n"
                "• `/reset` — Clear pending approval queue\n"
                "• Ask me anything in plain English!"
            )
        elif cmd in {"/login", "/connect"}:
            return youtube_login_instructions()
        elif cmd == "/youtube":
            if not arg:
                return youtube_login_instructions()
            return connect_youtube(arg, gh_token)
        elif cmd == "/status":
            st = load_state(gh_token) or {"status": "idle"}
            return format_status_summary(st, gh_token)
        elif cmd == "/reset":
            st = load_state(gh_token) or {}
            st["status"] = "idle"
            save_state(st, gh_token)
            return "🔄 Approval queue reset to `idle`. Ready for a fresh YouTube Short build!"
        elif cmd == "/sources":
            self._load_state()
            yt = self.state.get("latest_youtube") or self.state.get("latest_video") or {}
            if not yt.get("sources"):
                return "ℹ️ No recent clips recorded yet. Send `/create` to build a YouTube Short!"
            lines = [f"▶️ *SOURCES FOR: {yt.get('title', 'Latest Short')}*"]
            for s in yt["sources"]:
                lines.append(
                    f"• *#{s.get('rank')} ({s.get('funny_score', 'n/a')}/10):* {s.get('what_happens') or s.get('title')}\n"
                    f"  💬 _{s.get('commentary') or s.get('punchline')}_\n"
                    f"  🔗 {s.get('url')}"
                )
            return "\n".join(lines)
        elif cmd in {"/analytics", "/diagnosis", "/growth"}:
            return self.format_live_analytics()
        elif cmd in {"/create", "/build", "/new"}:
            self._start_subagent_build(arg)
            return "⚡ Starting content-matched YouTube Short build on your residential IP..."
        elif cmd == "/clip":
            return self._add_manual_clip(arg)
        else:
            return self.ask_gemini_copilot(text)

    def handle_callback_query(self, query: Dict[str, Any]):
        query_id = query.get("id")
        data = query.get("data") or ""
        gh_token = os.environ.get("GITHUB_TOKEN", "")

        ack_url = f"{self.TELEGRAM_API_URL}{self.bot_token}/answerCallbackQuery"
        try:
            with httpx.Client(timeout=10.0) as client:
                client.post(ack_url, json={"callback_query_id": query_id, "text": f"Processing: {data}..."})
        except Exception:
            pass

        if data == "build_latest":
            self._start_subagent_build()
            return

        self._load_state()
        st = load_state(gh_token) or {"status": "idle"}

        if data in {"approve_youtube", "approve_post", "approve_tiktok"}:
            self.send_message("🚀 *APPROVED YOUTUBE SHORT:* Uploading to your YouTube channel...")
            yt_info = self.state.get("latest_youtube") or self.state.get("latest_video") or {}
            vpath = Path(yt_info.get("video_path") or (OUTPUT_DIR / "master_youtube_shorts.mp4"))
            title = st.get("topic") or yt_info.get("title") or "Top 5 Viral Moments #Shorts"
            desc = st.get("caption") or yt_info.get("post_caption") or yt_info.get("credits") or ""
            tags = st.get("tags") or yt_info.get("tags")
            cat_id = str(st.get("category_id") or yt_info.get("category_id") or "23")
            res = post_and_confirm_youtube(vpath, title, desc, self.inbox, gh_token, tags=tags, category_id=cat_id)
            _deliver(res, self.inbox)
            st["status"] = res["status"]
            st["video_id"] = res.get("video_id", "")
            st["url"] = res.get("url", "")
            save_state(st, gh_token)

        elif data in {"reject_youtube", "reject_post", "reject_tiktok"}:
            st["status"] = "rejected"
            save_state(st, gh_token)
            self.send_message("🗑️ *Rejected YouTube Short.* Nothing was uploaded to YouTube.")

    def poll_once(self):
        last_id = self.state.get("last_update_id", 0)
        url = f"{self.TELEGRAM_API_URL}{self.bot_token}/getUpdates"
        params = {"offset": last_id + 1, "timeout": 5}

        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.get(url, params=params)
                if resp.status_code != 200:
                    return
                updates = resp.json().get("result", [])
                for u in updates:
                    uid = u["update_id"]
                    if uid > self.state.get("last_update_id", 0):
                        self.state["last_update_id"] = uid
                        self._save_state()
                        st = load_state("") or {"status": "idle"}
                        st["tg_offset"] = uid + 1
                        save_state(st, "")

                    if "callback_query" in u:
                        self.handle_callback_query(u["callback_query"])
                    elif "message" in u:
                        msg = u["message"]
                        text = msg.get("text")
                        chat_id = str(msg.get("chat", {}).get("id"))
                        if chat_id != self.creator_chat_id:
                            continue
                        sender_name = msg.get("from", {}).get("first_name", "Creator")
                        logger.info(f"Received message from {sender_name}: '{text}'")
                        if text:
                            reply = self.handle_command(text)
                            self.send_message(reply)
        except Exception as e:
            logger.debug(f"Poll check: {e}")

    def run_polling_loop(self, max_seconds: Optional[int] = None):
        logger.info("[bold green]AAT YouTube Shorts Telegram Subagent & Co-Pilot Service Started![/bold green]")
        start = time.time()
        while True:
            if max_seconds and (time.time() - start > max_seconds):
                break
            self.poll_once()
            time.sleep(2)

    run_polling_daemon = run_polling_loop


if __name__ == "__main__":
    bot = TelegramInteractiveBot()
    bot.run_polling_loop()
