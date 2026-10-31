"""True Multimodal Video + Audio Watching & Adaptive Full-Story Trimming Engine.

Watches the ENTIRE source clip at crisp 360p @ 8fps + 64kbps audio like a human video editor.
Powered by the Gemini 3.8 / 3.7 / 3.6 / 3.5 / 3.0 Flash Multimodal Pool
(with `gemini-2.5-flash` as the dead-last emergency fallback).

Hard Guarantees:
1. NEVER accepts a clip without Gemini actually watching the video stream (zero blind fallbacks).
2. Smart Quota Cooldown: If a model returns 429 daily quota exhaustion (`PerDay` / `retryDelay`),
   it is cooled down for the entire run (3600s) so zero time is wasted re-uploading video payloads.
3. True Adaptive Clip Durations (5.0s to 22.0s per clip — ZERO rigid 12s ceiling):
   - Clips <= 18.0s play in full from 0.0s to `dur` so neither the beginning setup nor the ending payoff is ever cut off.
   - Clips > 18.0s include the full setup (`setup_start_sec - 1.5s`) through the complete aftermath (`aftermath_end_sec + 1.8s`).
   - If a clip's setup-to-payoff story exceeds 22.0s, the clip is REJECTED instead of chopping off its beginning or ending.
4. Mandatory Comedy & Hook Gate: Rejects dry news reports, grim dashcams, and boring clips (`humor_score >= 7.0`, `viral_score >= 7.4`).
"""
import base64
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple
import httpx
from ..core.config import DATA_DIR, settings
from ..core.logger import logger

MODEL_POOL = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
]

SHARED_COOLDOWN_UNTIL: Dict[str, float] = {}


def ordered_active_models() -> List[str]:
    now = time.time()
    ready = [m for m in MODEL_POOL if SHARED_COOLDOWN_UNTIL.get(m, 0.0) <= now]
    cooling = [m for m in MODEL_POOL if SHARED_COOLDOWN_UNTIL.get(m, 0.0) > now]
    return ready + cooling


def record_model_error(model_name: str, status_code: int = 0, body_text: str = "") -> None:
    if status_code == 404:
        SHARED_COOLDOWN_UNTIL[model_name] = time.time() + 3600.0
    elif status_code == 429:
        if "PerDay" in body_text or "retryDelay" in body_text:
            SHARED_COOLDOWN_UNTIL[model_name] = time.time() + 3600.0
        else:
            SHARED_COOLDOWN_UNTIL[model_name] = time.time() + 65.0
    else:
        SHARED_COOLDOWN_UNTIL[model_name] = time.time() + 30.0


def _gemini(parts: List[Dict[str, Any]], timeout: float = 40.0) -> Tuple[Any, str]:
    """Prioritizes Gemini 3.x Flash models with instant daily-quota skip and uses gemini-2.5-flash last."""
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"response_mime_type": "application/json", "temperature": 0.2},
    }
    last = None
    for model_name in ordered_active_models():
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        try:
            with httpx.Client(timeout=timeout, headers={"x-goog-api-key": settings.GEMINI_API_KEY}) as c:
                r = c.post(url, json=payload)
            if r.status_code in (429, 500, 503, 404):
                record_model_error(model_name, r.status_code, r.text[:600])
                last = f"{model_name}:{r.status_code}"
                continue
            r.raise_for_status()
            parsed = json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
            return parsed, model_name
        except Exception as exc:
            record_model_error(model_name, 0, str(exc))
            last = f"{model_name}:{exc}"
    raise RuntimeError(f"Gemini model pool exhausted ({last})")


def _build_watchable_proxy_mp4(path: Path, max_duration: float = 45.0) -> Path:
    """Builds a crisp 360p @ 8fps MP4 proxy with 64kbps audio covering the ENTIRE candidate clip."""
    qa_dir = DATA_DIR / "score_frames"
    qa_dir.mkdir(parents=True, exist_ok=True)
    proxy = qa_dir / f"{path.stem}_watch_proxy.mp4"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(path),
        "-t", f"{max_duration:.1f}",
        "-vf", "scale=360:-2,fps=8",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "64k", "-ac", "1", "-ar", "22050",
        str(proxy),
    ]
    subprocess.run(cmd, capture_output=True, timeout=60)
    return proxy


def _compute_adaptive_story_window(
    dur: float,
    setup_start: float,
    climax_sec: float,
    aftermath_end: float,
) -> Tuple[float, float, bool]:
    """
    Adaptive Human Story Window (Zero Rigid 12s Cap):
    1. If the raw clip is <= 18.0s, play the ENTIRE clip from 0.0s to `dur` so the beginning cause
       and ending result are NEVER chopped off.
    2. If the raw clip is > 18.0s, include from `max(0.0, setup_start - 1.5)` through
       `min(dur, max(aftermath_end + 1.8, climax_sec + 4.0))`.
    3. If the required story arc is > 22.0s, return `story_fits=False` so the pipeline rejects the clip
       instead of chopping off its beginning or ending!
    """
    if dur <= 18.0:
        return 0.0, round(dur, 2), True

    safe_start = max(0.0, setup_start - 1.5)
    if safe_start < 2.5:
        safe_start = 0.0

    safe_end = min(dur, max(aftermath_end + 1.8, climax_sec + 4.0))
    if (dur - safe_end) < 2.5:
        safe_end = dur

    story_len = safe_end - safe_start
    if story_len > 22.5:
        return round(safe_start, 2), round(story_len, 2), False

    if story_len < 6.5:
        safe_start = max(0.0, safe_start - 2.0)
        safe_end = min(dur, safe_start + 7.5)
        story_len = safe_end - safe_start

    return round(safe_start, 2), round(story_len, 2), True


def _clean_human_label(title: str) -> str:
    cleaned = re.sub(r"#\S+", "", title or "").strip()
    cleaned = re.sub(r"[^\w\s'!?-]", "", cleaned).strip()
    words = [w for w in cleaned.split() if w.lower() not in {"shorts", "short", "tiktok", "fyp", "viral", "video"}]
    return " ".join(words[:3]).title() or "Watch This"


def score_clip_visually(
    path: Path,
    title: str,
    theme: str,
    window_sec: float = 10.0,
    vibe_rules: str = "",
) -> Dict[str, Any]:
    """
    Watches full 360p @ 8fps video motion + listens to the audio stream across the ENTIRE clip.
    Rejects any clip that is unfunny/boring, missing its setup/cause, missing its payoff/aftermath,
    or requires >22s to tell its story.
    """
    dur = float(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True,
        ).stdout.strip() or 8.0
    )

    if dur < 5.0 or dur > 35.0:
        logger.info(f"[yellow]Rejected '{path.name}': Raw duration ({dur:.1f}s) outside complete-scene bounds (5s-35s)[/yellow]")
        return {"funny_score": 0.0, "reason": f"rejected: raw duration {dur:.1f}s", "start_sec": 0.0, "rejected": True}

    proxy = _build_watchable_proxy_mp4(path, max_duration=dur)
    if not (proxy.exists() and 1000 < proxy.stat().st_size < 4 * 1024 * 1024):
        proxy.unlink(missing_ok=True)
        return {"funny_score": 0.0, "reason": "rejected: proxy encode failed", "start_sec": 0.0, "rejected": True}

    try:
        b64_video = base64.b64encode(proxy.read_bytes()).decode("ascii")
        prompt = (
            "You are a ruthless, experienced human YouTube Shorts creator and comedy editor watching a candidate clip with both eyes and ears.\n"
            f"Target Ranking Sub-Niche / Topic: '{theme}'\n"
            f"Sub-Niche Vibe Requirement: {vibe_rules or 'Must genuinely match the topic, be funny/entertaining, and show both the setup and full payoff.'}\n"
            f"Raw Metadata Title: {title} (Total duration: {dur:.1f}s)\n\n"
            "Watch the ENTIRE video from 0.0s to the final frame AND listen to the audio track.\n"
            "Audit this clip strictly like a human viewer:\n"
            "1. `is_raw_single_clip` (boolean): `false` if this is already a compilation, news broadcast package, split-screen reaction, or has ranking numbers burned in.\n"
            "2. `full_setup_and_payoff_visible` (boolean): `true` ONLY if the viewer clearly sees BOTH:\n"
            "   (a) The SETUP / CAUSE at the beginning (what triggered the fail, karma, prank, or twist), AND\n"
            "   (b) The FULL RESULT / REACTION at the end (never cuts off early before the aftermath finishes!).\n"
            "3. `is_funny_or_entertaining` (boolean): `true` ONLY if this clip is genuinely funny, comedic, absurd, or wildly entertaining to watch. "
            "Set `false` if it is a dry news report, serious police dashcam, grim accident, talking head, or boring to watch!\n"
            "4. `matches_subniche_and_vibe` (boolean): Does the visual action genuinely fit "
            f"'{theme}' and ({vibe_rules})?\n"
            "5. `actual_vibe` (string): One of: 'epic_fail', 'hilarious', 'prank', 'football_skill', 'sports_highlight', "
            "'embarrassing', 'wild_crazy', 'instant_karma', 'cute_wholesome', 'plot_twist', 'genius_skill', or 'boring_mundane'.\n"
            "6. `viral_score` (1.0 to 10.0): Overall viral entertainment & replay value.\n"
            "7. `humor_score` (1.0 to 10.0): How funny, comedic, or enjoyable is this clip to watch? (Must be >= 7.0).\n"
            "8. `hook_strength` (1.0 to 10.0): How visually gripping are the first 2 seconds?\n"
            "9. `climax_intensity` (1.0 to 10.0): How strong/funny is the payoff moment?\n"
            f"10. `setup_start_sec` (0.0 to {max(0.0, dur - 4.0):.1f}): Exact timestamp where the setup/cause begins so nothing at the start is missed (use 0.0 if the setup starts right away).\n"
            f"11. `climax_sec` (0.0 to {dur:.1f}): Exact timestamp where the punchline/karma/fail hits.\n"
            f"12. `aftermath_end_sec` (0.0 to {dur:.1f}): Exact timestamp AFTER the result and reaction have completely finished playing out.\n"
            "13. `what_happens`: Clear, specific 12-20 word human description covering BOTH the cause/setup and the final result.\n"
            "14. `story_arc`: `Setup (cause) -> Climax -> Aftermath (result)` as seen on screen.\n"
            "15. `audio_cue`: What is heard in the audio.\n"
            "16. `standout_detail`: One specific funny detail a human viewer notices.\n"
            "17. `why_rankworthy`: Why this clip earns a spot in a viral ranking video.\n"
            "18. `rank_label`: Sharp 2-3 word human label for the on-screen 1-5 list (max 18 chars) matching what is visually shown.\n"
            "19. `punchline`: Witty human creator remark (5-10 words).\n"
            "20. `commentary`: One sharp sentence of human creator commentary.\n"
            "21. `premise_shown` (boolean): The finished video will be titled exactly: "
            f"'{theme}'. Answer `true` ONLY if the literal premise of that title visibly or audibly happens in THIS clip "
            "(example: for 'the cameraman loses it laughing', the person filming must be audibly laughing so hard they cannot hold the camera still). "
            "A similar mood, the same general category, or just being funny is `false`. Be strict: a viewer who reads that title must not feel misled by this clip.\n"
            "22. `theme_fit` (0.0 to 10.0): How exactly this clip delivers the title's premise (10 = textbook example, 0 = unrelated).\n"
            "23. `theme_evidence`: One sentence naming what you actually saw/heard that proves or disproves the premise.\n\n"
            "Return strict JSON:\n"
            "{\n"
            "  \"is_raw_single_clip\": true,\n"
            "  \"full_setup_and_payoff_visible\": true,\n"
            "  \"is_funny_or_entertaining\": true,\n"
            "  \"matches_subniche_and_vibe\": true,\n"
            "  \"actual_vibe\": \"hilarious\",\n"
            "  \"viral_score\": 8.8,\n"
            "  \"humor_score\": 8.6,\n"
            "  \"hook_strength\": 8.5,\n"
            "  \"climax_intensity\": 9.0,\n"
            "  \"setup_start_sec\": 0.0,\n"
            "  \"climax_sec\": 6.5,\n"
            "  \"aftermath_end_sec\": 11.5,\n"
            "  \"what_happens\": \"...\",\n"
            "  \"story_arc\": \"...\",\n"
            "  \"audio_cue\": \"...\",\n"
            "  \"standout_detail\": \"...\",\n"
            "  \"why_rankworthy\": \"...\",\n"
            "  \"rank_label\": \"...\",\n"
            "  \"punchline\": \"...\",\n"
            "  \"commentary\": \"...\",\n"
            "  \"premise_shown\": false,\n"
            "  \"theme_fit\": 3.0,\n"
            "  \"theme_evidence\": \"...\"\n"
            "}"
        )
        parts = [
            {"inline_data": {"mime_type": "video/mp4", "data": b64_video}},
            {"text": prompt},
        ]
        res, used_model = _gemini(parts)
        proxy.unlink(missing_ok=True)

        is_raw = bool(res.get("is_raw_single_clip", False))
        full_story = bool(res.get("full_setup_and_payoff_visible", False))
        is_funny = bool(res.get("is_funny_or_entertaining", False))
        matches = bool(res.get("matches_subniche_and_vibe", False))
        actual_vibe = str(res.get("actual_vibe") or "").strip().lower()
        what_happens = str(res.get("what_happens") or "").strip()
        score = float(res.get("viral_score", 0.0))
        humor = float(res.get("humor_score", 0.0))
        theme_fit = float(res.get("theme_fit", 0.0) or 0.0)
        premise_shown = bool(res.get("premise_shown", False)) and theme_fit >= 7.0  # strict: both must agree

        if not is_raw:
            logger.info(f"[yellow]Rejected '{path.name}': Not a clean single clip ({what_happens[:60]})[/yellow]")
            return {"funny_score": 0.0, "reason": "rejected: compilation/news edit", "start_sec": 0.0, "rejected": True}
        if not full_story:
            logger.info(f"[yellow]Rejected '{path.name}': Missing setup cause or cut-off payoff ({what_happens[:60]})[/yellow]")
            return {"funny_score": 0.0, "reason": "rejected: incomplete setup or payoff", "start_sec": 0.0, "rejected": True}
        if not is_funny or humor < 7.0 or score < 7.4 or not matches or actual_vibe == "boring_mundane":
            logger.info(
                f"[yellow]Rejected '{path.name}': Not funny/engaging enough "
                f"(viral={score}, humor={humor}, funny={is_funny}, vibe={actual_vibe}, action='{what_happens[:55]}')[/yellow]"
            )
            return {
                "funny_score": 0.0,
                "reason": f"rejected: viral={score} humor={humor} funny={is_funny}",
                "start_sec": 0.0,
                "rejected": True,
            }

        setup_s = float(res.get("setup_start_sec", res.get("action_start_sec", 0.0)))
        climax_s = float(res.get("climax_sec", dur * 0.6))
        after_s = float(res.get("aftermath_end_sec", res.get("action_end_sec", dur)))

        trim_start, trim_dur, story_fits = _compute_adaptive_story_window(dur, setup_s, climax_s, after_s)
        if not story_fits:
            logger.info(
                f"[yellow]Rejected '{path.name}': Full setup->payoff story requires {trim_dur:.1f}s (>22.5s); refusing to chop clip![/yellow]"
            )
            return {"funny_score": 0.0, "reason": "rejected: story arc too long to fit without chopping", "start_sec": 0.0, "rejected": True}

        combined_score = round((score * 0.55) + (humor * 0.45), 2)
        logger.info(
            f"[cyan]Human-Watched Verified Clip ({used_model}) '{path.name}':[/cyan] "
            f"Score={combined_score}/10 (Viral={score}, Humor={humor}) | Vibe={actual_vibe} | "
            f"AdaptiveWindow={trim_start:.1f}s..{trim_start + trim_dur:.1f}s (dur={trim_dur:.1f}s / raw={dur:.1f}s) | "
            f"Action='{what_happens[:70]}'"
        )
        return {
            "funny_score": combined_score,
            "viral_score": score,
            "humor_score": humor,
            "hook_strength": float(res.get("hook_strength", score)),
            "climax_intensity": float(res.get("climax_intensity", score)),
            "actual_vibe": actual_vibe,
            "reason": what_happens,
            "start_sec": trim_start,
            "trim_duration": trim_dur,
            "climax_sec": climax_s,
            "action_end_sec": after_s,
            "rank_label": str(res.get("rank_label") or _clean_human_label(title))[:20].strip(),
            "punchline": str(res.get("punchline") or what_happens)[:55].strip(),
            "what_happens": what_happens,
            "story_arc": str(res.get("story_arc") or "").strip(),
            "audio_cue": str(res.get("audio_cue") or "").strip(),
            "standout_detail": str(res.get("standout_detail") or "").strip(),
            "why_rankworthy": str(res.get("why_rankworthy") or "").strip(),
            "commentary": str(res.get("commentary") or "")[:110].strip(),
            "premise_shown": premise_shown,
            "theme_fit": theme_fit,
            "theme_evidence": str(res.get("theme_evidence") or "").strip()[:200],
            "watched_by_model": used_model,
            "rejected": False,
        }
    except Exception as exc:
        proxy.unlink(missing_ok=True)
        logger.warning(f"Rejected '{path.name}' because Gemini video watch failed ({exc}) — never accepting un-watched clips.")
        return {"funny_score": 0.0, "reason": f"rejected: watch failed ({exc})", "start_sec": 0.0, "rejected": True}
