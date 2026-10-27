"""High-reliability single-pass clip downloader for TikTok (Primary) & Original YouTube Videos (Fallback).

Strictly enforces during download:
- Must NOT be a compilation, ranking video, or full episode (`COMPILATION_CHECK_RE`)
- TikTok clips: 5.0s to 65.0s; Original YouTube fallback videos: 5.0s to 120.0s (from which only a short
  8s-12s transformative highlight window is extracted for Fair Use & Monetization compliance).
"""
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from ..core.config import CLIPS_DIR
from ..core.logger import logger
from ..video.probe import probe_duration
from .rights import check_clip, normalize_license

COMPILATION_CHECK_RE = re.compile(
    r"\b(compilation|compilations|top\s*5|top\s*10|ranking\s+video|try\s+not\s+to\s+laugh|"
    r"mashup|montage|full\s+episode|podcast|livestream)\b",
    re.I,
)


def download_clip(candidate: Dict[str, Any], name: str, max_height: int = 1080) -> Tuple[Optional[Dict[str, Any]], str]:
    # 1. Pre-downloaded local file
    if candidate.get("filepath") and Path(candidate["filepath"]).exists():
        p = Path(candidate["filepath"])
        if p.stat().st_size > 20_000:
            clip = {**candidate, "duration": probe_duration(p) or candidate.get("duration_sec", 6.0)}
            return clip, "ok"

    # 2. TikTok (Primary) or Original YouTube Video (Fallback) via yt-dlp
    import yt_dlp
    dest_path = CLIPS_DIR / f"{name}.mp4"
    if dest_path.exists():
        dest_path.unlink()

    is_youtube = "youtube.com" in str(candidate.get("url", "")) or "youtu.be" in str(candidate.get("url", ""))
    max_allowed_dur = 120.0 if is_youtube else 65.0

    def _match_filter(info_dict, *, incomplete=False):
        dur = info_dict.get("duration")
        if dur is not None and (dur < 5.0 or dur > max_allowed_dur):
            return f"Duration {dur}s outside single-clip range (5.0-{max_allowed_dur}s)"
        title = str(info_dict.get("title") or "")
        if COMPILATION_CHECK_RE.search(title):
            return "Skipped compilation/ranking video"
        return None

    opts: Dict[str, Any] = {
        "format": f"bestvideo[height<={max_height}][ext=mp4]+bestaudio[ext=m4a]/best[height<={max_height}][ext=mp4]/best",
        "outtmpl": str(CLIPS_DIR / f"{name}.%(ext)s"),
        "merge_output_format": "mp4",
        "match_filter": _match_filter,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 25,
        "nocheckcertificate": True,
    }
    if is_youtube:
        opts["extractor_args"] = {
            "youtube": {
                "player_client": ["android_vr", "tv", "ios", "mweb", "web"],
            }
        }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(candidate["url"], download=True)
            if not info:
                return None, "extract_info returned empty"
            if "entries" in info and info["entries"]:
                info = info["entries"][0]

            raw_title = (info.get("title") or info.get("description") or candidate.get("title") or "Viral Moment").strip()
            if COMPILATION_CHECK_RE.search(raw_title):
                dest_path.unlink(missing_ok=True)
                return None, "compilation title rejected"

            uploader = info.get("uploader") or info.get("channel") or candidate.get("channel") or "Creator"
            platform = "TikTok" if "tiktok.com" in str(candidate.get("url", "")) else "YouTube"
            lic = normalize_license(info.get("license") or candidate.get("license") or "transformative-credit")
            credit = f"@{uploader} ({platform})"

            clip = {
                **candidate,
                "title": raw_title[:100],
                "channel": uploader,
                "license": lic,
                "credit": credit,
            }
            ok, why = check_clip(clip)
            if not ok:
                dest_path.unlink(missing_ok=True)
                return None, why

            path = CLIPS_DIR / f"{name}.mp4"
            if not path.exists():
                prepared = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
                if prepared.exists():
                    path = prepared
            if not path.exists() or path.stat().st_size < 20_000:
                return None, "skipped or empty after yt-dlp match_filter"

            dur = probe_duration(path) or float(info.get("duration") or 6.0)
            if dur < 5.0 or dur > max_allowed_dur:
                path.unlink(missing_ok=True)
                return None, f"duration {dur:.1f}s outside bounds (5.0-{max_allowed_dur}s)"

            clip.update(
                filepath=str(path),
                duration=dur,
                start_sec=0.0,
            )
            return clip, "ok"
    except Exception as exc:
        logger.debug(f"Download skip for {candidate.get('url')}: {exc}")
        return None, str(exc)[:160]
