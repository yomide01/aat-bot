"""Official YouTube Data API v3 & YouTube Analytics API v2 Client.
Unlocks the full power of the creator's Google Cloud OAuth credentials:
1. Resumable YouTube Shorts Uploads with dynamic category, tags, and transformative description
2. Full Historical Per-Video Telemetry (every video ever published on the channel, its publish hour in WAT,
   duration, live views, velocity views/hr, like-to-view %, comment rate, averageViewDuration, averageViewPercentage,
   shares, and subscribers gained)
3. Channel-Wide YouTube Analytics API v2 Reports (Traffic Source breakdown: SHORTS vs YT_SEARCH vs CHANNEL,
   and Top Audience Geographies: US, NG, GB, ID, etc. with retention rates)
4. Live YouTube Trending Radar (`videos.list?chart=mostPopular`) for adaptive topic selection
5. Authenticated Creative Commons Short-Clip Discovery (`search.list` + `videos.list`) — never blocked on Cloud IPs!
"""
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode
import httpx
from ..core.config import settings
from ..core.logger import logger
from ..core.token_store import load_tokens, save_tokens

COMPILATION_RE = re.compile(
    r"\b(compilation|top\s*\d+|ranking|ranked|best\s+of|try\s+not\s+to\s+laugh|"
    r"part\s*\d+|mashup|montage|all\s+clips|collection|recap|highlights\s+reel|shorts)\b",
    re.I,
)

CONTENT_ID_RISK_RE = re.compile(
    r"\b(vevo|records|official\s+music|music\s+video|studios|netflix|disney|marvel|hbo|"
    r"espn|sky\s+sports|nba|nfl|fifa|uefa|premier\s+league|laliga|wwe|ufc|DAZN|"
    r"sony|warner|universal|paramount|bbc|cnn|fox\s+news|nbc|cbs|abc\s+news)\b",
    re.I,
)


def _parse_iso_duration(iso_str: str) -> float:
    """Parses ISO 8601 duration like PT15S or PT1M12S into seconds."""
    if not iso_str:
        return 0.0
    m = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", iso_str)
    if not m:
        return 0.0
    hrs = int(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    secs = int(m.group(3) or 0)
    return float(hrs * 3600 + mins * 60 + secs)


def _parse_published_wat(iso_ts: str) -> Dict[str, Any]:
    """Parses ISO 8601 publishedAt timestamp into UTC, Nigeria WAT (UTC+1), and age in hours."""
    if not iso_ts:
        return {"published_wat": "Unknown", "hour_wat": -1, "age_hours": 24.0}
    try:
        dt_utc = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        now_utc = datetime.now(timezone.utc)
        age_hours = max(0.25, (now_utc - dt_utc).total_seconds() / 3600.0)
        wat_tz = timezone(timedelta(hours=1))
        dt_wat = dt_utc.astimezone(wat_tz)
        return {
            "published_wat": dt_wat.strftime("%Y-%m-%d %H:%M WAT"),
            "hour_wat": dt_wat.hour,
            "day_of_week": dt_wat.strftime("%A"),
            "age_hours": round(age_hours, 1),
        }
    except Exception:
        return {"published_wat": iso_ts[:16], "hour_wat": -1, "day_of_week": "", "age_hours": 24.0}


class YouTubeShortsClient:
    """Official YouTube Data API v3 & YouTube Analytics API v2 Client."""

    AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
    API_BASE = "https://www.googleapis.com/youtube/v3"
    ANALYTICS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"

    def __init__(
        self,
        client_id: str = "",
        client_secret: str = "",
        redirect_uri: str = "",
        access_token: str = "",
        refresh_token: str = "",
    ):
        self.client_id = client_id or settings.YOUTUBE_CLIENT_ID
        self.client_secret = client_secret or settings.YOUTUBE_CLIENT_SECRET
        self.redirect_uri = redirect_uri or settings.YOUTUBE_REDIRECT_URI or "https://yomide01.github.io/callback"
        saved = load_tokens("") or {}
        self.access_token = access_token or saved.get("access_token") or settings.YOUTUBE_ACCESS_TOKEN
        self.refresh_token = refresh_token or saved.get("refresh_token") or settings.YOUTUBE_REFRESH_TOKEN

    def ensure_valid_token(self) -> bool:
        """Refreshes the OAuth access token if a refresh_token is available."""
        if self.refresh_token and self.client_id and self.client_secret:
            res = self.refresh_access_token(self.refresh_token)
            if res.get("access_token"):
                return True
        return bool(self.access_token)

    def get_authorization_url(self, state: str = "aat_youtube") -> str:
        """Generates the Google OAuth 2.0 authorization URL for YouTube Channel upload & analytics access."""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": (
                "https://www.googleapis.com/auth/youtube.upload "
                "https://www.googleapis.com/auth/youtube.readonly "
                "https://www.googleapis.com/auth/yt-analytics.readonly"
            ),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"{self.AUTH_URL}?{urlencode(params)}"

    def exchange_code_for_token(self, auth_code: str) -> Dict[str, Any]:
        """Exchanges the Google OAuth authorization code for access_token and refresh_token."""
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": auth_code.strip(),
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
        }
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(self.TOKEN_URL, data=data)
            res_data = resp.json()
            if "access_token" in res_data:
                self.access_token = res_data["access_token"]
                if res_data.get("refresh_token"):
                    self.refresh_token = res_data["refresh_token"]
                logger.info("[green]Successfully obtained YouTube OAuth Access Token![/green]")
            return res_data

    def refresh_access_token(self, refresh_token: str = "") -> Dict[str, Any]:
        """Refreshes an expired YouTube OAuth access token using the long-lived refresh token."""
        token_to_use = refresh_token or self.refresh_token
        if not token_to_use:
            return {}
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": token_to_use,
            "grant_type": "refresh_token",
        }
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(self.TOKEN_URL, data=data)
                res_data = resp.json()
                if "access_token" in res_data:
                    self.access_token = res_data["access_token"]
                    save_tokens({
                        "access_token": self.access_token,
                        "refresh_token": res_data.get("refresh_token") or token_to_use,
                    })
                return res_data
        except Exception as exc:
            logger.warning(f"YouTube token refresh notice: {exc}")
            return {}

    def query_channel_info(self) -> Dict[str, Any]:
        """Returns the authenticated creator's YouTube channel title, handle, subscriber count, and view count."""
        self.ensure_valid_token()
        if not self.access_token:
            return {}
        headers = {"Authorization": f"Bearer {self.access_token}"}
        params = {"part": "snippet,statistics,contentDetails", "mine": "true"}
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(f"{self.API_BASE}/channels", headers=headers, params=params)
                raw = resp.json()
                items = raw.get("items") or []
                if not items:
                    return raw
                ch = items[0]
                snip = ch.get("snippet") or {}
                stats = ch.get("statistics") or {}
                uploads_pl = ((ch.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads", "")
                return {
                    "id": ch.get("id", ""),
                    "title": snip.get("title", ""),
                    "handle": snip.get("customUrl", ""),
                    "subscribers": int(stats.get("subscriberCount") or 0),
                    "views": int(stats.get("viewCount") or 0),
                    "videos": int(stats.get("videoCount") or 0),
                    "uploads_playlist_id": uploads_pl,
                    "raw": raw,
                }
        except Exception as exc:
            logger.warning(f"query_channel_info error: {exc}")
            return {}

    def fetch_channel_analytics(self, days: int = 90) -> Dict[str, Any]:
        """
        Comprehensive YouTube Channel & Per-Video Intelligence Engine:
        1. Fetches EVERY published video on the channel via `playlistItems` + `videos.list`
           (including exact publish timestamp in Nigeria WAT, duration in seconds, views, velocity views/hr,
           likes, like-to-view %, comments, and comment rate).
        2. Merges deep per-video YouTube Analytics API v2 telemetry (`dimensions=video`:
           averageViewDuration, averageViewPercentage, estimatedMinutesWatched, shares, subscribersGained).
        3. Queries Traffic Source Breakdown (`SHORTS` feed vs `YT_SEARCH` vs `CHANNEL`) and
           Top Audience Geographies (`dimensions=country` with per-country retention %).
        4. Computes best-performing upload window and algorithmic seed tier for each video.
        """
        ch = self.query_channel_info()
        if not self.access_token:
            return {"connected": False}

        headers = {"Authorization": f"Bearer {self.access_token}"}
        end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start_date = (datetime.now(timezone.utc) - timedelta(days=max(days, 90))).strftime("%Y-%m-%d")

        analytics_summary: Dict[str, Any] = {
            "connected": True,
            "channel_title": ch.get("title") or "YouTube Channel",
            "handle": ch.get("handle") or "",
            "subscribers": ch.get("subscribers", 0),
            "total_views": ch.get("views", 0),
            "total_videos": ch.get("videos", 0),
            "period_days": days,
            "recent_videos": [],
            "report": {},
            "traffic_sources": [],
            "top_countries": [],
            "best_upload_window_wat": "",
        }

        per_video_analytics: Dict[str, Dict[str, Any]] = {}

        with httpx.Client(timeout=25.0) as client:
            # 1. Channel-wide aggregate report
            try:
                r = client.get(
                    self.ANALYTICS_URL,
                    headers=headers,
                    params={
                        "ids": "channel==MINE",
                        "startDate": start_date,
                        "endDate": end_date,
                        "metrics": "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,comments,shares,subscribersGained,subscribersLost",
                    },
                )
                if r.status_code == 200:
                    data = r.json()
                    cols = [c.get("name") for c in data.get("columnHeaders") or []]
                    rows = data.get("rows") or []
                    if rows and cols:
                        analytics_summary["report"] = dict(zip(cols, rows[0]))
            except Exception as exc:
                logger.debug(f"YouTube Analytics aggregate query note: {exc}")

            # 2. Per-video deep retention & conversion report (`dimensions=video`)
            try:
                r_vid = client.get(
                    self.ANALYTICS_URL,
                    headers=headers,
                    params={
                        "ids": "channel==MINE",
                        "startDate": "2024-01-01",
                        "endDate": end_date,
                        "dimensions": "video",
                        "metrics": "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,dislikes,comments,shares,subscribersGained,subscribersLost",
                        "sort": "-views",
                        "maxResults": 200,
                    },
                )
                if r_vid.status_code == 200:
                    data_v = r_vid.json()
                    cols_v = [c.get("name") for c in data_v.get("columnHeaders") or []]
                    for row in data_v.get("rows") or []:
                        row_dict = dict(zip(cols_v, row))
                        vid_key = str(row_dict.get("video") or "")
                        if vid_key:
                            per_video_analytics[vid_key] = row_dict
            except Exception as exc:
                logger.debug(f"Per-video analytics query note: {exc}")

            # 3. Traffic source breakdown (`dimensions=insightTrafficSourceType`)
            try:
                r_tr = client.get(
                    self.ANALYTICS_URL,
                    headers=headers,
                    params={
                        "ids": "channel==MINE",
                        "startDate": start_date,
                        "endDate": end_date,
                        "dimensions": "insightTrafficSourceType",
                        "metrics": "views,estimatedMinutesWatched",
                        "sort": "-views",
                    },
                )
                if r_tr.status_code == 200:
                    rows_tr = r_tr.json().get("rows") or []
                    total_tr_views = sum(int(x[1]) for x in rows_tr) or 1
                    for x in rows_tr:
                        if int(x[1]) > 0:
                            analytics_summary["traffic_sources"].append({
                                "source": str(x[0]),
                                "views": int(x[1]),
                                "watch_minutes": int(x[2]),
                                "share_pct": round((int(x[1]) / total_tr_views) * 100.0, 1),
                            })
            except Exception as exc:
                logger.debug(f"Traffic source analytics note: {exc}")

            # 4. Top audience geographies (`dimensions=country`)
            try:
                r_ct = client.get(
                    self.ANALYTICS_URL,
                    headers=headers,
                    params={
                        "ids": "channel==MINE",
                        "startDate": start_date,
                        "endDate": end_date,
                        "dimensions": "country",
                        "metrics": "views,estimatedMinutesWatched,averageViewPercentage",
                        "sort": "-views",
                        "maxResults": 8,
                    },
                )
                if r_ct.status_code == 200:
                    rows_ct = r_ct.json().get("rows") or []
                    total_ct_views = sum(int(x[1]) for x in rows_ct) or 1
                    for x in rows_ct:
                        if int(x[1]) > 0:
                            analytics_summary["top_countries"].append({
                                "country": str(x[0]),
                                "views": int(x[1]),
                                "watch_minutes": int(x[2]),
                                "avg_view_pct": round(float(x[3]), 1),
                                "share_pct": round((int(x[1]) / total_ct_views) * 100.0, 1),
                            })
            except Exception as exc:
                logger.debug(f"Country analytics note: {exc}")

            # 5. Fetch ALL uploaded videos on the channel via paginated playlistItems + videos.list
            uploads_pl = ch.get("uploads_playlist_id")
            if uploads_pl:
                try:
                    all_vid_ids: List[str] = []
                    page_token: Optional[str] = None
                    for _ in range(5):  # Up to 250 videos
                        pl_params: Dict[str, Any] = {
                            "part": "contentDetails",
                            "playlistId": uploads_pl,
                            "maxResults": 50,
                        }
                        if page_token:
                            pl_params["pageToken"] = page_token
                        pl_resp = client.get(f"{self.API_BASE}/playlistItems", headers=headers, params=pl_params)
                        if pl_resp.status_code != 200:
                            break
                        pl_json = pl_resp.json()
                        for item in pl_json.get("items") or []:
                            vid_id = (item.get("contentDetails") or {}).get("videoId")
                            if vid_id and vid_id not in all_vid_ids:
                                all_vid_ids.append(vid_id)
                        page_token = pl_json.get("nextPageToken")
                        if not page_token:
                            break

                    for i in range(0, len(all_vid_ids), 50):
                        batch_ids = all_vid_ids[i : i + 50]
                        v_resp = client.get(
                            f"{self.API_BASE}/videos",
                            headers=headers,
                            params={"part": "snippet,contentDetails,statistics", "id": ",".join(batch_ids)},
                        )
                        if v_resp.status_code != 200:
                            continue
                        for v in v_resp.json().get("items") or []:
                            vid_id = str(v.get("id") or "")
                            vs = v.get("statistics") or {}
                            vsnip = v.get("snippet") or {}
                            vc = v.get("contentDetails") or {}
                            dur_sec = _parse_iso_duration(vc.get("duration", ""))
                            pub_info = _parse_published_wat(vsnip.get("publishedAt", ""))
                            live_views = int(vs.get("viewCount") or 0)
                            live_likes = int(vs.get("likeCount") or 0)
                            live_comments = int(vs.get("commentCount") or 0)

                            deep = per_video_analytics.get(vid_id) or {}
                            views = max(live_views, int(deep.get("views") or 0))
                            likes = max(live_likes, int(deep.get("likes") or 0))
                            comments = max(live_comments, int(deep.get("comments") or 0))
                            age_hrs = float(pub_info.get("age_hours") or 24.0)
                            velocity_vph = round(views / max(1.0, min(age_hrs, 72.0)), 1)
                            like_pct = round((likes / max(1, views)) * 100.0, 2)
                            comment_pct = round((comments / max(1, views)) * 100.0, 3)
                            avd_sec = float(deep.get("averageViewDuration") or 0.0)
                            avp_pct = round(float(deep.get("averageViewPercentage") or 0.0), 1)
                            shares = int(deep.get("shares") or 0)
                            subs_gained = int(deep.get("subscribersGained") or 0)

                            if views >= 2000 or velocity_vph >= 55.0:
                                algo_tier = "BREAKOUT_SEED (High Algorithmic Push)"
                            elif views >= 1000:
                                algo_tier = "HEALTHY_SEED (Strong Test Pool)"
                            elif age_hrs < 12.0:
                                algo_tier = "WARMING_UP (Early Seed Testing)"
                            else:
                                algo_tier = "CAPPED_SEED (Stalled Before Phase 2)"

                            analytics_summary["recent_videos"].append({
                                "id": vid_id,
                                "title": vsnip.get("title", ""),
                                "published_wat": pub_info.get("published_wat", ""),
                                "hour_wat": pub_info.get("hour_wat", -1),
                                "day_of_week": pub_info.get("day_of_week", ""),
                                "age_hours": age_hrs,
                                "duration_sec": int(dur_sec),
                                "views": views,
                                "velocity_vph": velocity_vph,
                                "likes": likes,
                                "like_rate_pct": like_pct,
                                "comments": comments,
                                "comment_rate_pct": comment_pct,
                                "avg_view_duration_sec": avd_sec,
                                "avg_view_pct": avp_pct,
                                "watch_minutes": int(deep.get("estimatedMinutesWatched") or 0),
                                "shares": shares,
                                "subs_gained": subs_gained,
                                "algo_tier": algo_tier,
                                "url": f"https://www.youtube.com/shorts/{vid_id}",
                            })
                except Exception as exc:
                    logger.debug(f"Full video history telemetry note: {exc}")

        # Compute actual live totals & best upload hour from published videos
        vids = analytics_summary["recent_videos"]
        if vids:
            sum_views = sum(int(v.get("views", 0)) for v in vids)
            analytics_summary["total_views"] = max(int(analytics_summary["total_views"]), sum_views)
            analytics_summary["total_videos"] = max(int(analytics_summary["total_videos"]), len(vids))
            top_vid = max(vids, key=lambda v: int(v.get("views", 0)))
            analytics_summary["best_upload_window_wat"] = (
                f"{top_vid.get('published_wat')} ('{top_vid.get('title', '')[:42]}...' -> {top_vid.get('views'):,} views)"
            )

        return analytics_summary

    def fetch_trending_topics(self, region_code: str = "NG", max_results: int = 8) -> List[str]:
        """Fetches currently trending video titles on YouTube via the official Data API v3."""
        self.ensure_valid_token()
        if not self.access_token:
            return []
        headers = {"Authorization": f"Bearer {self.access_token}"}
        params = {
            "part": "snippet",
            "chart": "mostPopular",
            "regionCode": region_code,
            "maxResults": max_results,
        }
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.get(f"{self.API_BASE}/videos", headers=headers, params=params)
                if r.status_code != 200:
                    return []
                return [
                    (item.get("snippet") or {}).get("title", "")
                    for item in r.json().get("items") or []
                    if (item.get("snippet") or {}).get("title")
                ]
        except Exception:
            return []

    def search_raw_shorts_candidates(
        self,
        query: str,
        max_results: int = 6,
        creative_commons_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Uses the official authenticated YouTube Data API v3 (`search.list` + `videos.list`)
        to discover ORIGINAL single-scene YouTube videos (NOT YouTube Shorts or compilations).
        Enforces strict Monetization, Fair Use & Anti-Content-ID safety:
          1. Prioritizes Creative Commons (`videoLicense='creativeCommon'`, CC-BY) original videos.
          2. Excludes `#shorts`, compilations, ranking videos, and re-uploads.
          3. Blocks major TV networks, music labels, studios, and sports leagues (`CONTENT_ID_RISK_RE`).
          4. Skips videos flagged with active third-party Content ID (`contentDetails.licensedContent`) when not CC-BY.
        """
        self.ensure_valid_token()
        if not self.access_token:
            return []
        headers = {"Authorization": f"Bearer {self.access_token}"}
        search_params: Dict[str, Any] = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "videoDuration": "short",
            "videoEmbeddable": "true",
            "maxResults": max_results * 3,
            "relevanceLanguage": "en",
            "safeSearch": "moderate",
        }
        if creative_commons_only:
            search_params["videoLicense"] = "creativeCommon"

        try:
            with httpx.Client(timeout=20.0) as client:
                s_resp = client.get(f"{self.API_BASE}/search", headers=headers, params=search_params)
                if s_resp.status_code != 200:
                    return []
                vid_ids = [
                    (item.get("id") or {}).get("videoId")
                    for item in s_resp.json().get("items") or []
                    if (item.get("id") or {}).get("videoId")
                ]
                if not vid_ids:
                    return []

                v_resp = client.get(
                    f"{self.API_BASE}/videos",
                    headers=headers,
                    params={
                        "part": "snippet,contentDetails,statistics,status",
                        "id": ",".join(vid_ids[:20]),
                    },
                )
                if v_resp.status_code != 200:
                    return []

                candidates: List[Dict[str, Any]] = []
                for item in v_resp.json().get("items") or []:
                    vid = item.get("id")
                    snip = item.get("snippet") or {}
                    title = snip.get("title") or ""
                    desc = snip.get("description") or ""
                    channel_title = snip.get("channelTitle") or "Creator"

                    # 1. Exclude YouTube Shorts (#shorts), compilations, and ranking videos
                    if COMPILATION_RE.search(title) or COMPILATION_RE.search(desc[:300]):
                        continue
                    # 2. Exclude major TV/music/studio/sports Content-ID rights-holders
                    if CONTENT_ID_RISK_RE.search(channel_title) or CONTENT_ID_RISK_RE.search(title):
                        continue

                    c_details = item.get("contentDetails") or {}
                    status = item.get("status") or {}
                    lic_raw = status.get("license", "")
                    is_cc = (lic_raw == "creativeCommon")

                    # 3. Skip Content-ID licensed videos unless explicitly licensed under Creative Commons (CC-BY)
                    if not is_cc and bool(c_details.get("licensedContent", False)):
                        continue
                    if not bool(status.get("embeddable", True)):
                        continue

                    dur = _parse_iso_duration(c_details.get("duration", ""))
                    if not (5.0 <= dur <= 120.0):
                        continue

                    lic = "cc-by" if is_cc else "transformative-credit"
                    views = int((item.get("statistics") or {}).get("viewCount") or 0)
                    candidates.append({
                        "video_id": f"ytapi_{vid}",
                        "title": title[:100],
                        "description": desc[:200],
                        "url": f"https://www.youtube.com/watch?v={vid}",
                        "duration_sec": dur,
                        "views": views,
                        "channel": channel_title,
                        "license": lic,
                        "credit": f"@{channel_title} (CC-BY)" if lic == "cc-by" else f"@{channel_title} (YouTube)",
                        "source": "youtube",
                        "target_platform": "youtube",
                    })
                    if len(candidates) >= max_results:
                        break
                return candidates
        except Exception as exc:
            logger.debug(f"YouTube API search notice for '{query}': {exc}")
            return []

    def upload_short(
        self,
        video_path: Path,
        title: str,
        description: str = "",
        tags: Optional[List[str]] = None,
        category_id: str = "23",
        privacy_status: str = "public",
    ) -> Dict[str, Any]:
        """
        Uploads a vertical MP4 video to the creator's YouTube Channel as a YouTube Short
        using Google's official resumable upload endpoint.
        """
        self.ensure_valid_token()
        if not self.access_token:
            return {"ok": False, "error": "YouTube access_token is missing"}

        video_path = Path(video_path)
        if not video_path.exists():
            return {"ok": False, "error": f"Video file not found: {video_path}"}

        clean_title = (title or "Top 5 Viral Moments").strip()
        if "#shorts" not in clean_title.lower():
            clean_title = f"{clean_title[:88]} #Shorts"
        else:
            clean_title = clean_title[:100]

        clean_desc = (description or clean_title).strip()
        if "#shorts" not in clean_desc.lower():
            clean_desc += "\n\n#Shorts #ViralMoments #Ranking #FunnyMoments"

        tag_list = tags or ["Shorts", "Funny Moments", "Ranking", "Viral", "Original Commentary"]
        video_size = os.path.getsize(video_path)

        metadata = {
            "snippet": {
                "title": clean_title,
                "description": clean_desc[:4900],
                "tags": tag_list[:15],
                "categoryId": str(category_id or "23"),
                "defaultLanguage": "en",
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False,
                "embeddable": True,
            },
        }

        init_headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(video_size),
            "X-Upload-Content-Type": "video/mp4",
        }

        with httpx.Client(timeout=180.0) as client:
            init_resp = client.post(
                f"{self.UPLOAD_URL}?uploadType=resumable&part=snippet,status",
                headers=init_headers,
                json=metadata,
            )
            if init_resp.status_code not in (200, 201):
                return {"ok": False, "error": f"YouTube init HTTP {init_resp.status_code}: {init_resp.text[:250]}"}

            upload_url = init_resp.headers.get("Location")
            if not upload_url:
                return {"ok": False, "error": "YouTube did not return a resumable upload Location header"}

            with open(video_path, "rb") as vf:
                put_headers = {
                    "Content-Length": str(video_size),
                    "Content-Type": "video/mp4",
                }
                put_resp = client.put(upload_url, headers=put_headers, content=vf.read())
                if put_resp.status_code not in (200, 201):
                    return {"ok": False, "error": f"YouTube upload HTTP {put_resp.status_code}: {put_resp.text[:250]}"}

                res_json = put_resp.json()
                vid_id = res_json.get("id", "")
                logger.info(f"[bold green]Uploaded YouTube Short successfully! Video ID: {vid_id}[/bold green]")
                return {
                    "ok": True,
                    "video_id": vid_id,
                    "url": f"https://www.youtube.com/shorts/{vid_id}",
                    "privacy": privacy_status,
                }
