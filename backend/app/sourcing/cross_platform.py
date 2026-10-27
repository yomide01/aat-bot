"""Original Single-Clip Discovery Engine — Strictly TikTok (Primary) & Original YouTube Videos (Fallback).

1. Primary Source: TikTok single-moment viral clips (discovered via Chrome TLS impersonation
   and downloaded in a single pass via yt-dlp without watermarks).
2. Secondary / Fallback Source: Original single-scene YouTube videos (NOT YouTube Shorts or compilations),
   discovered via authenticated YouTube Data API v3 prioritizing Creative Commons (CC-BY) and excluding
   Content-ID / media studio channels for full Fair Use & Monetization safety.
3. Strictly excludes all pre-made compilations, ranking videos, and stock/open-source libraries.
"""
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Set
from ..core.logger import logger
from ..posting.youtube_api import YouTubeShortsClient, COMPILATION_RE

BLOCK_WORDS = re.compile(
    r"\b(compilation|compilations|top\s*\d+|ranking|ranked|best\s+of|try\s+not\s+to\s+laugh|"
    r"part\s*\d+|mashup|montage|full\s+episode|podcast|interview|livestream|highlights\s+\d{4}|shorts)\b",
    re.I,
)


def _blocked_or_compilation(text: str) -> bool:
    if not text:
        return False
    return bool(BLOCK_WORDS.search(text) or COMPILATION_RE.search(text))


def _discover_tiktok_urls(queries: List[str], max_urls: int = 36) -> List[str]:
    """Discovers real TikTok video URLs (`tiktok.com/@user/video/ID`) using Chrome TLS impersonation."""
    tiktok_urls: List[str] = []
    try:
        from curl_cffi import requests as cffi_requests
        for q in queries:
            if len(tiktok_urls) >= max_urls:
                break
            for suffix in ("video -compilation -ranking", "funny viral -compilation"):
                if len(tiktok_urls) >= max_urls:
                    break
                search_q = urllib.parse.quote_plus(f"site:tiktok.com {q} {suffix}")
                for endpoint in (
                    f"https://search.brave.com/search?q={search_q}",
                    f"https://html.duckduckgo.com/html/?q={search_q}",
                    f"https://search.yahoo.com/search?p={search_q}",
                ):
                    try:
                        resp = cffi_requests.get(endpoint, impersonate="chrome", timeout=15)
                        if resp.status_code == 200:
                            decoded = urllib.parse.unquote(resp.text)
                            matches = re.findall(
                                r"https?://(?:www\.)?tiktok\.com/@[\w\.-]+/video/\d+",
                                decoded,
                            )
                            new_hits = 0
                            for m in matches:
                                if m not in tiktok_urls:
                                    tiktok_urls.append(m)
                                    new_hits += 1
                            if new_hits >= 4:
                                break
                    except Exception as exc:
                        logger.debug(f"TikTok search notice ({endpoint[:30]}): {exc}")
    except Exception as exc:
        logger.warning(f"curl_cffi TikTok discovery fallback notice: {exc}")
    return tiktok_urls[:max_urls]


def discover_tiktok_for_youtube(
    viral_queries: List[str],
    open_queries: Optional[List[str]] = None,
    per_query: int = 5,
    limit: int = 48,
    exclude_ids: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Discovers single-scene original clips strictly from:
      1. TikTok (Primary source — single-pass download & verification via yt-dlp)
      2. Original YouTube Videos (Secondary fallback source — via Official YouTube Data API v3,
         prioritizing Creative Commons CC-BY and excluding #shorts, compilations, and Content-ID channels)
    Never uses stock or open-source libraries (no Wikimedia, Pexels, Pixabay, etc.).
    """
    exclude = set(exclude_ids or set())
    found: List[Dict[str, Any]] = []
    seen_urls: Set[str] = set()

    # 1. PRIMARY: Discover individual TikTok video URLs (untouched primary sourcing)
    tiktok_urls = _discover_tiktok_urls(viral_queries, max_urls=36)
    for url in tiktok_urls:
        vid_match = re.search(r"/(@[\w\.-]+)/video/(\d+)", url)
        uploader = vid_match.group(1).lstrip("@") if vid_match else "tiktok_creator"
        vid_id = f"tt_{vid_match.group(2)}" if vid_match else url
        if vid_id in exclude or url in seen_urls:
            continue
        seen_urls.add(url)
        exclude.add(vid_id)
        found.append({
            "video_id": vid_id,
            "title": f"TikTok Moment by @{uploader}",
            "description": "",
            "url": url,
            "duration_sec": 12.0,
            "views": 0,
            "channel": uploader,
            "license": "transformative-credit",
            "credit": f"@{uploader} (TikTok)",
            "source": "tiktok",
            "target_platform": "youtube",
        })

    # 2. SECONDARY / FALLBACK: Official Authenticated YouTube Data API v3 (Original Videos Only — No Shorts/Compilations)
    #    Prioritizes Creative Commons (CC-BY) first, then un-claimed independent original footage.
    yt_added = 0
    yt_target = 15
    try:
        yt_client = YouTubeShortsClient()
        for cc_only in (True, False):
            if yt_added >= yt_target:
                break
            for q in viral_queries[:4]:
                if yt_added >= yt_target:
                    break
                api_cands = yt_client.search_raw_shorts_candidates(
                    query=f"{q} raw caught on camera -shorts -compilation -ranking",
                    max_results=per_query,
                    creative_commons_only=cc_only,
                )
                for c in api_cands:
                    cid = str(c.get("video_id") or "")
                    url = str(c.get("url") or "")
                    if not cid or cid in exclude or url in seen_urls:
                        continue
                    if _blocked_or_compilation(c.get("title", "")) or _blocked_or_compilation(c.get("description", "")):
                        continue
                    exclude.add(cid)
                    seen_urls.add(url)
                    found.append(c)
                    yt_added += 1
                    if yt_added >= yt_target:
                        break
    except Exception as exc:
        logger.debug(f"YouTube API fallback discovery note: {exc}")

    tt_count = sum(1 for c in found if c.get("source") == "tiktok")
    yt_count = len(found) - tt_count
    logger.info(
        f"[green]Discovered {len(found)} candidate clips "
        f"(Primary TikTok: {tt_count}, Fallback Original YouTube: {yt_count})[/green]"
    )
    return found[:limit]
