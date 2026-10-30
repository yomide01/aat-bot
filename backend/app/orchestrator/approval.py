"""Telegram Approval & Official YouTube Shorts Publishing Engine.

Handles:
1. Sending the compiled YouTube Short (sourced from original clips) to Telegram with Approve / Reject buttons.
2. Immediate callback acknowledgement (toast + chat message) the second a button is tapped.
3. Official YouTube Data API v3 Shorts resumable upload with dynamic content-matched title,
   transformative description, tags, and category ID.
4. One-time Google/YouTube OAuth code exchange via Telegram commands (`/youtube THECODE`, `/login`, `/status`, `/reset`).
"""
import io
import json
import os
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import httpx

from ..core.config import DATA_DIR, settings
from ..core.gh_files import read_text, write_text
from ..core.logger import logger
from ..core.token_store import load_tokens, save_tokens
from ..posting.youtube_api import YouTubeShortsClient
from ..sourcing.telegram_inbox import TelegramInbox, ensure_telegram_size
from ..telegram.dispatcher import TelegramHumanDispatcher

STATE_PATH = "data/pending/state.json"
LOCAL_JOB = DATA_DIR / "pending" / "local_job.json"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())


def _now_ts() -> int:
    return int(time.time())


def load_state(token: str) -> Optional[Dict[str, Any]]:
    if token:
        raw = read_text(STATE_PATH, token)
        if raw:
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
    local_state = DATA_DIR / "pending" / "state.json"
    if local_state.exists():
        try:
            return json.loads(local_state.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def save_state(state: Dict[str, Any], token: str) -> None:
    state["updated"] = _now()
    state["updated_ts"] = _now_ts()
    local_state = DATA_DIR / "pending" / "state.json"
    local_state.parent.mkdir(parents=True, exist_ok=True)
    try:
        local_state.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning(f"Could not save local state cache: {exc}")
    if token:
        try:
            write_text(STATE_PATH, json.dumps(state, indent=2), token, f"aat: {state.get('status', 'update')}")
        except Exception as exc:
            logger.warning(f"Could not write state to GitHub: {exc}")


def is_state_stale(state: Dict[str, Any], max_age_hours: float = 12.0) -> bool:
    ts = state.get("updated_ts")
    if ts:
        return (_now_ts() - int(ts)) > int(max_age_hours * 3600)
    updated_str = state.get("updated") or ""
    if not updated_str:
        return True
    try:
        t = time.strptime(updated_str, "%Y-%m-%d %H:%M:%S UTC")
        age_sec = time.time() - time.mktime(t)
        return age_sec > (max_age_hours * 3600)
    except Exception:
        return True


def _ack(inbox: TelegramInbox, callback_id: Optional[str], text: str = "") -> None:
    if callback_id:
        try:
            payload: Dict[str, Any] = {"callback_query_id": callback_id}
            if text:
                payload["text"] = text[:190]
            inbox._call("answerCallbackQuery", **payload)
        except Exception:
            pass


def youtube_login_instructions() -> str:
    if not settings.YOUTUBE_CLIENT_ID:
        return "YouTube posting is not set up yet (YOUTUBE_CLIENT_ID is missing)."
    url = YouTubeShortsClient().get_authorization_url(state="aat_youtube")
    return (
        "▶️ ONE-TIME YOUTUBE CHANNEL CONNECTION:\n"
        "1. Open this link and select your YouTube channel:\n"
        f"{url}\n\n"
        "2. Copy the command shown on the callback page and send it here:\n"
        "/youtube THECODE"
    )


def login_instructions() -> str:
    return youtube_login_instructions()


def connect_youtube(code: str, token: str) -> str:
    client = YouTubeShortsClient()
    try:
        res = client.exchange_code_for_token(code.strip())
    except Exception as exc:
        return f"❌ YouTube login failed: {exc}"
    access = res.get("access_token")
    refresh = res.get("refresh_token")
    if not access:
        err_desc = res.get("error_description") or res.get("error") or str(res)
        return f"❌ Google did not accept that code ({err_desc}). Open the link from /login again to get a fresh code."
    saved_prev = load_tokens(token) or {}
    final_refresh = refresh or saved_prev.get("refresh_token") or settings.YOUTUBE_REFRESH_TOKEN
    save_tokens({
        "access_token": access,
        "refresh_token": final_refresh or "",
    }, token)
    ch = client.query_channel_info()
    ch_title = ch.get("title") or "your YouTube channel"
    ch_handle = f" ({ch.get('handle')})" if ch.get("handle") else ""
    return f"✅ YouTube Channel connected: {ch_title}{ch_handle}! Every Short you approve will now upload directly."


def _youtube_client(token: str) -> YouTubeShortsClient:
    client = YouTubeShortsClient()
    saved = load_tokens(token) or {}
    refresh = saved.get("refresh_token") or settings.YOUTUBE_REFRESH_TOKEN
    if saved.get("access_token"):
        client.access_token = saved["access_token"]
    if refresh:
        client.refresh_token = refresh
    if refresh and settings.YOUTUBE_CLIENT_ID and settings.YOUTUBE_CLIENT_SECRET:
        refreshed = client.refresh_access_token(refresh)
        if refreshed.get("access_token"):
            client.access_token = refreshed["access_token"]
            save_tokens({
                "access_token": refreshed["access_token"],
                "refresh_token": refreshed.get("refresh_token") or refresh,
            }, token)
    return client


def post_and_confirm_youtube(
    video: Path,
    title: str,
    description: str,
    inbox: TelegramInbox,
    token: str,
    tags: Optional[List[str]] = None,
    category_id: str = "23",
) -> Dict[str, Any]:
    if not (settings.YOUTUBE_CLIENT_ID and settings.YOUTUBE_CLIENT_SECRET):
        return {
            "status": "manual",
            "video_id": "",
            "url": "",
            "message": "YouTube API credentials are not configured yet.\n\n" + youtube_login_instructions(),
            "manual_file": False,
            "video": video,
        }
    try:
        client = _youtube_client(token)
    except Exception as exc:
        return {
            "status": "manual",
            "video_id": "",
            "url": "",
            "message": f"Could not refresh YouTube login ({exc}).\n\n" + youtube_login_instructions(),
            "manual_file": False,
            "video": video,
        }
    if not client.access_token:
        return {
            "status": "manual",
            "video_id": "",
            "url": "",
            "message": "Your YouTube channel is not connected yet.\n\n" + youtube_login_instructions(),
            "manual_file": False,
            "video": video,
        }
    inbox.send(f"📤 Uploading '{title}' to your YouTube channel via YouTube Data API v3...")
    res = client.upload_short(
        video_path=video,
        title=title,
        description=description,
        tags=tags,
        category_id=category_id,
        privacy_status="public",
    )
    if res.get("ok"):
        return {
            "status": "posted",
            "video_id": res.get("video_id", ""),
            "url": res.get("url", ""),
            "message": f"🎉 PUBLISHED TO YOUTUBE SHORTS!\n🎬 Title: {title}\n🔗 {res.get('url')}",
            "manual_file": False,
            "video": video,
        }
    return {
        "status": "failed",
        "video_id": "",
        "url": "",
        "message": f"⚠️ YouTube upload failed: {res.get('error')}\nThe video is in this chat above so you can upload it directly.",
        "manual_file": False,
        "video": video,
    }


def _deliver(result: Dict[str, Any], inbox: TelegramInbox) -> None:
    inbox.send(result["message"][:4000])
    if result.get("manual_file") and result.get("video") and Path(result["video"]).exists():
        path = ensure_telegram_size(Path(result["video"]))
        inbox.send_document(path, caption="YouTube Short file")


def _download_artifact(run_id: str, token: str, preferred_name: str = "") -> Path:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        listing = client.get(
            f"https://api.github.com/repos/{settings.GITHUB_REPO}/actions/runs/{run_id}/artifacts",
            headers=headers,
        )
        listing.raise_for_status()
        arts = [a for a in listing.json().get("artifacts", []) if a.get("name") == "aat-pending" and not a.get("expired")]
        if not arts:
            raise RuntimeError("The saved video artifact has expired on GitHub. Send /create or trigger a new build.")
        blob = client.get(arts[-1]["archive_download_url"], headers=headers)
        blob.raise_for_status()
        payload = blob.content
    dest_dir = DATA_DIR / "output"
    dest_dir.mkdir(parents=True, exist_ok=True)
    extracted: List[Path] = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if name.endswith(".mp4") and ".." not in name]
        if not names:
            raise RuntimeError("The saved video package had no .mp4 file in it.")
        for name in names:
            target = dest_dir / Path(name).name
            with archive.open(name) as src, open(target, "wb") as out:
                out.write(src.read())
            extracted.append(target)
    if preferred_name:
        for p in extracted:
            if p.name == preferred_name:
                return p
    return extracted[0]


def _classify(update: Dict[str, Any], chat_id: str) -> Optional[Tuple[str, Optional[str], Optional[str]]]:
    if "callback_query" in update:
        cb = update["callback_query"]
        msg = cb.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(chat_id):
            return None
        data = cb.get("data") or ""
        cb_id = cb.get("id")
        if data in {"approve_youtube", "approve_post", "approve_tiktok"}:
            return "approve", "youtube", cb_id
        if data in {"reject_youtube", "reject_post", "reject_tiktok"}:
            return "reject", "youtube", cb_id
        return "ack", data, cb_id

    msg = update.get("message") or {}
    if str((msg.get("chat") or {}).get("id")) != str(chat_id):
        return None
    text = (msg.get("text") or "").strip()
    if not text:
        return None
    lowered = text.lower()

    if lowered in {"approve", "yes", "post", "approve youtube", "post youtube", "yes youtube", "approve yt"}:
        return "approve", "youtube", None
    if lowered in {"reject", "no", "reject youtube", "no youtube", "reject yt"}:
        return "reject", "youtube", None
    if lowered.startswith("/youtube"):
        parts = text.split(maxsplit=1)
        if len(parts) == 2 and parts[1].strip():
            return "youtube_code", parts[1].strip(), None
        return "login_help", "youtube", None
    if lowered in {"/login", "/connect"}:
        return "login_help", "youtube", None
    if lowered == "/reset":
        return "reset", None, None
    if lowered == "/status":
        return "status", None, None
    return "chat", text, None


def format_status_summary(state: Dict[str, Any], token: str) -> str:
    yt_client = YouTubeShortsClient()
    ch = yt_client.query_channel_info()
    yt_ok = bool(ch.get("title") or yt_client.access_token or yt_client.refresh_token)
    ch_label = f"✅ {ch.get('title')} ({ch.get('handle', '')})" if ch.get("title") else ("✅ Connected" if yt_ok else "⚠️ Not connected (/login)")

    lines = [
        "⚡ AAT STUDIO — SYSTEM STATUS",
        "━━━━━━━━━━━━━━━━━━",
        f"📺 Channel: {ch_label}",
        f"📈 Stats: {ch.get('subscribers', 0):,} subs • {ch.get('views', 0):,} views • {ch.get('videos', 0)} Shorts",
        f"🚦 Queue: {state.get('status', 'idle').upper()}",
    ]
    if state.get("topic"):
        lines.append(f"🎬 Latest: {state.get('topic')} ({state.get('status', 'idle')})")
    if state.get("url"):
        lines.append(f"🔗 Live URL: {state.get('url')}")
    return "\n".join(lines)


def _apply_youtube_action(
    action: str,
    state: Dict[str, Any],
    inbox: TelegramInbox,
    token: str,
    local_video: Optional[Path] = None,
) -> None:
    if action == "reject":
        state["status"] = "rejected"
        save_state(state, token)
        inbox.send(f"🗑️ Rejected YouTube Short ('{state.get('topic', 'Video')}'). Nothing was uploaded.")
        return

    if state.get("status") == "posted":
        inbox.send(f"ℹ️ This Short was already published: {state.get('url', '')}")
        return

    video_path: Optional[Path] = None
    if local_video and local_video.exists():
        video_path = local_video
    else:
        candidate_local = DATA_DIR / "output" / (state.get("video_name") or "master_youtube_shorts.mp4")
        if candidate_local.exists():
            video_path = candidate_local
        elif state.get("run_id") and token:
            try:
                video_path = _download_artifact(str(state["run_id"]), token, preferred_name=state.get("video_name") or "")
            except Exception as exc:
                inbox.send(f"⚠️ Could not retrieve video from GitHub artifact: {exc}")
                state["status"] = "failed"
                save_state(state, token)
                return

    if not video_path or not video_path.exists():
        inbox.send("⚠️ Video file was not found on disk. Send `/create` to build a fresh Short.")
        state["status"] = "failed"
        save_state(state, token)
        return

    state["status"] = "posting"
    save_state(state, token)

    res = post_and_confirm_youtube(
        video=video_path,
        title=state.get("topic") or "Top 5 Viral Moments #Shorts",
        description=state.get("caption") or state.get("credits") or "",
        inbox=inbox,
        token=token,
        tags=state.get("tags"),
        category_id=str(state.get("category_id") or "23"),
    )
    state["status"] = res["status"]
    state["video_id"] = res.get("video_id", "")
    state["url"] = res.get("url", "")
    save_state(state, token)
    _deliver(res, inbox)


def _handle_non_approval_action(
    action: str,
    arg: Optional[str],
    state: Dict[str, Any],
    inbox: TelegramInbox,
    token: str,
) -> None:
    if action == "youtube_code":
        try:
            inbox.send(connect_youtube(arg or "", token))
        except Exception as exc:
            inbox.send(f"❌ Could not save YouTube login: {exc}")
    elif action == "login_help":
        inbox.send(youtube_login_instructions())
    elif action == "reset":
        state["status"] = "idle"
        save_state(state, token)
        inbox.send("🔄 Queue reset to idle. Ready for the next YouTube Short build!")
    elif action == "status":
        inbox.send(format_status_summary(state, token))
    elif action == "chat" and arg:
        try:
            from ..telegram.bot_service import TelegramInteractiveBot
            bot = TelegramInteractiveBot(bot_token=inbox.token, creator_chat_id=inbox.chat)
            reply = bot.handle_command(arg)
            inbox.send(reply)
        except Exception as exc:
            logger.warning(f"Chat reply error: {exc}")


def _drain(
    inbox: TelegramInbox,
    state: Dict[str, Any],
    token: str,
    local_video: Optional[Path],
    seconds: int,
) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        wait = min(15, max(1, int(deadline - time.time()))) if seconds > 5 else 0
        updates = inbox._updates(timeout=wait)
        for update in updates:
            classified = _classify(update, inbox.chat)
            if not classified:
                continue
            action, arg, cb_id = classified
            if action in {"approve", "reject"}:
                toast = f"✅ {action.title()} received!"
                _ack(inbox, cb_id, text=toast)
                state["tg_offset"] = inbox.offset
                _apply_youtube_action(action, state, inbox, token, local_video=local_video)
                return True
            elif action == "ack":
                _ack(inbox, cb_id, text="Received!")
            else:
                _ack(inbox, cb_id)
                _handle_non_approval_action(action, arg, state, inbox, token)
            state["tg_offset"] = inbox.offset
            save_state(state, token)

        if seconds <= 5:
            break
    state["tg_offset"] = inbox.offset
    return False


def announce(wait_min: int) -> None:
    if not LOCAL_JOB.exists():
        logger.info("No video waiting to announce")
        return
    job = json.loads(LOCAL_JOB.read_text(encoding="utf-8"))
    token = os.environ.get("GITHUB_TOKEN", "")
    inbox = TelegramInbox()

    prev_state = load_state(token) or {}
    if prev_state.get("tg_offset"):
        inbox.offset = int(prev_state["tg_offset"])
    else:
        res = inbox._call("getUpdates", offset=-1, timeout=0)
        result = res.get("result") or []
        inbox.offset = (result[-1]["update_id"] + 1) if result else 0

    video = Path(job["video_path"])
    state: Dict[str, Any] = {
        "status": "awaiting",
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "artifact": "aat-pending",
        "topic": job.get("topic", "Top 5 Viral Moments #Shorts"),
        "banner_title": job.get("banner_title", "TOP 5 MOMENTS"),
        "niche": job.get("niche", "epic_fails"),
        "subniche_label": job.get("subniche_label", ""),
        "recent_subniches": job.get("recent_subniches") or prev_state.get("recent_subniches") or [],
        "used_clip_ids": job.get("used_clip_ids") or prev_state.get("used_clip_ids") or [],
        "used_titles": job.get("used_titles") or prev_state.get("used_titles") or [],
        "strategy_diagnosis": job.get("strategy_diagnosis") or prev_state.get("strategy_diagnosis") or {},
        "qa_review": job.get("qa_review") or {},
        "ranked_plan": job.get("ranked_plan") or [],
        "caption": job.get("caption", ""),
        "credits": job.get("credits", ""),
        "tags": job.get("tags") or [],
        "category_id": job.get("category_id") or "23",
        "video_name": video.name,
        "tg_offset": inbox.offset,
        "video_id": "",
        "url": "",
    }
    save_state(state, token)

    sent = TelegramHumanDispatcher().send_video_for_approval(
        video_path=video,
        title=state["topic"],
        caption=state["caption"],
        hook=state["banner_title"],
        target_platform="youtube",
        niche_label=state.get("subniche_label") or state.get("niche", ""),
        ranked_clips=state.get("ranked_plan") or [],
        qa_review=state.get("qa_review") or {},
        strategy_diagnosis=state.get("strategy_diagnosis") or {},
    )
    if not (isinstance(sent, dict) and sent.get("ok")):
        inbox.send("⚠️ Telegram refused the video upload. It is saved; reply 'approve' to publish it to YouTube Shorts.")
        logger.warning(f"Telegram video send failed: {sent}")

    settled = _drain(inbox, state, token, video, max(60, wait_min * 60))
    if not settled:
        save_state(state, token)
        inbox.send(
            "⏸️ Pausing this cloud runner so your GitHub minutes aren't burned overnight. "
            "Your YouTube Short stays saved! Tap Approve or Reject anytime — the background watcher checks every 30 minutes and will upload it as soon as you tap."
        )


def watch() -> None:
    token = os.environ.get("GITHUB_TOKEN", "")
    state = load_state(token) or {"status": "idle"}
    inbox = TelegramInbox()

    if "tg_offset" not in state and state.get("status", "idle") == "idle":
        res = inbox._call("getUpdates", offset=-1, timeout=0)
        result = res.get("result") or []
        state["tg_offset"] = (result[-1]["update_id"] + 1) if result else 0
        state["status"] = "idle"
        save_state(state, token)
        logger.info("Saved starting Telegram offset.")
        return

    inbox.offset = int(state.get("tg_offset") or 0)
    before = inbox.offset
    _drain(inbox, state, token, None, seconds=4)
    if inbox.offset != before:
        state["tg_offset"] = inbox.offset
        save_state(state, token)
