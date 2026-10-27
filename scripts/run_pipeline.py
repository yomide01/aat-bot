"""CLI entry point for AAT — Autonomous Self-Learning YouTube Shorts Ranking Engine.

Actions:
  prepare   : 1) Pulls live YouTube Data API v3 & Analytics API v2 telemetry for EVERY published video
                 (views, velocity views/hr, duration, publish hour in WAT, AVD, AVP %, likes %, comments %,
                 shares, subscribers gained, Shorts Feed vs Search split, and US/NG/Global geography).
              2) Runs the Gemini 3.8 Flash / 4 Argon / 3.x Forensic Strategist & rotates sub-niche.
              3) Sources brand-new single-scene clips strictly from TikTok & YouTube Shorts, verifies vibe
                 truthfulness & full climax payoff, ranks #5 -> #1, and compiles the clean Short.
  announce  : Sends the finished YouTube Short + AI Growth Diagnosis to Telegram with Approve / Reject buttons.
  watch     : Periodic check for late Approve / Reject taps or `/youtube THECODE` OAuth command.
  subagent  : Runs the continuous local Residential-IP Telegram Subagent & Co-Pilot bridge.
"""
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, Any, List, Set

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.core.config import DATA_DIR
from backend.app.core.logger import logger


def _flush_report(report: dict) -> None:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        return
    try:
        from backend.app.sourcing.bridge import publish_candidates
        publish_candidates(report, token, path="data/reports/last_run.json")
    except Exception as exc:
        logger.warning(f"Could not write report: {exc}")


def prepare(niche: str, topic: str, count: int = 5) -> None:
    from backend.app.orchestrator.approval import load_state, save_state, is_state_stale, watch
    from backend.app.orchestrator.pipeline import ViralVideoPipeline
    from backend.app.sourcing.niches import for_today, record_used_subniche
    from backend.app.sourcing.cross_platform import discover_tiktok_for_youtube
    from backend.app.sourcing.telegram_inbox import TelegramInbox

    inbox = TelegramInbox()
    token = os.environ.get("GITHUB_TOKEN", "")

    # 1. Process any pending Telegram button taps or commands BEFORE checking state
    try:
        watch()
    except Exception as exc:
        logger.warning(f"Pre-prepare Telegram check note: {exc}")

    prev_state: Dict[str, Any] = {}
    if token:
        prev_state = load_state(token) or {}
        if prev_state.get("status") in {"awaiting", "posting"}:
            is_manual = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            if is_manual or is_state_stale(prev_state, max_age_hours=6.0):
                logger.info("Clearing previous stale/manual awaiting state so fresh build can proceed.")
                prev_state["status"] = "expired"
                save_state(prev_state, token)
            else:
                inbox.send(
                    f"⏳ You still have a YouTube Short waiting for approval ('{prev_state.get('topic', 'Video')}').\n"
                    "Tap Approve or Reject on the video above, or send /reset to skip it and build a new one."
                )
                return
    else:
        prev_state = load_state("") or {}

    pipeline = ViralVideoPipeline()

    # 2. Load Permanent Content Deduplication Vault + Full Historical YouTube Channel Analytics
    used_clip_ids: Set[str] = set(prev_state.get("used_clip_ids") or [])
    used_titles: List[str] = list(prev_state.get("used_titles") or [])
    if prev_state.get("topic") and prev_state["topic"] not in used_titles:
        used_titles.insert(0, str(prev_state["topic"]))

    recent_subs: List[str] = list(prev_state.get("recent_subniches") or [])
    if prev_state.get("niche") and prev_state["niche"] not in recent_subs:
        recent_subs.insert(0, str(prev_state["niche"]))

    analytics = pipeline.youtube_client.fetch_channel_analytics(days=90)
    for vid in analytics.get("recent_videos") or []:
        t = str(vid.get("title") or "").strip()
        if t and t not in used_titles:
            used_titles.append(t)

    # 3. Deep Forensic Per-Video & Channel Diagnosis (Gemini 3.8 Flash / 4 Argon / 3.x Pool)
    strategy = pipeline.brain.analyze_channel_and_evolve_strategy(analytics, used_titles=used_titles)
    target_pacing = float(strategy.get("editing_pacing_sec") or 9.5)

    chosen = for_today(niche, topic, target_platform="youtube", exclude_subniches=recent_subs)
    seed_topic = str(chosen["topic"])
    yt_niche = str(chosen["id"])
    sub_label = str(chosen.get("label", yt_niche))
    vibe_rules = str(chosen.get("vibe_rules", ""))
    viral_queries = [str(q) for q in chosen.get("viral_queries", chosen["queries"])]

    autopsy_lines = []
    for a in (strategy.get("per_video_autopsy") or [])[:4]:
        autopsy_lines.append(f"  - {a.get('title', '')[:44]}: {a.get('verdict', '')[:110]}")
    autopsy_block = ("\n🔬 Per-Video Catalog Autopsy:\n" + "\n".join(autopsy_lines)) if autopsy_lines else ""

    inbox.send(
        f"🎬 Autonomous Ranking Build Started! ({strategy.get('active_model', 'gemini-3.8-flash')})\n"
        f"• Sub-Niche: {sub_label} ({yt_niche})\n"
        f"• Concept: {seed_topic}\n"
        f"• Channel Totals: {analytics.get('subscribers', 0)} subs | {analytics.get('total_views', 0):,} views across {analytics.get('total_videos', 0)} Shorts\n"
        f"• Dedup Vault: {len(used_clip_ids)} past clips & {len(used_titles)} past titles blocked\n\n"
        f"🧠 Forensic YouTube Shorts Diagnosis:\n"
        f"• Seed/Traffic Status: {strategy.get('shadowban_or_seed_status', 'Healthy')}\n"
        f"• Why Top Video Won: {strategy.get('viral_vs_stalled_reason', '')}\n"
        f"• Conversion & Comment Trigger: {strategy.get('engagement_diagnosis', '')}\n"
        f"• Peak Timing Insight: {strategy.get('peak_timing_insight', '')}\n"
        f"• Today's Adaptation: {strategy.get('ranking_tactic', '')} (~{target_pacing:.1f}s/clip full payoff)"
        f"{autopsy_block}"
    )

    report: Dict[str, Any] = {
        "niche": yt_niche,
        "subniche_label": sub_label,
        "seed_topic": seed_topic,
        "analytics_overview": {
            "subscribers": analytics.get("subscribers", 0),
            "total_views": analytics.get("total_views", 0),
            "total_videos": analytics.get("total_videos", 0),
            "traffic_sources": analytics.get("traffic_sources", []),
            "top_countries": analytics.get("top_countries", []),
            "best_upload_window_wat": analytics.get("best_upload_window_wat", ""),
            "published_videos": analytics.get("recent_videos", []),
        },
        "strategy": strategy,
        "viral_queries": viral_queries,
        "failures": [],
    }

    try:
        candidates = discover_tiktok_for_youtube(
            viral_queries=viral_queries,
            limit=30,
            exclude_ids=used_clip_ids,
        )
    except Exception as exc:
        candidates = []
        report["error"] = str(exc)[:300]

    report["candidates"] = len(candidates)
    scored = (
        pipeline.gather_clips(
            candidates,
            seed_topic,
            count=count,
            segment_sec=target_pacing,
            vibe_rules=vibe_rules,
            used_clip_ids=used_clip_ids,
            failures=report["failures"],
        )
        if candidates
        else []
    )
    report["scored"] = len(scored)

    if len(scored) < 3:
        inbox.send(
            f"⚠️ Only {len(scored)} brand-new clips passed strict '{sub_label}' verification on this pass. "
            "Try running `/create` again or run `python scripts/run_pipeline.py --action subagent` locally."
        )
        _flush_report(report)
        raise SystemExit(1)

    use = min(count, len(scored))
    updated_recent = record_used_subniche(yt_niche)
    for item in recent_subs:
        if item not in updated_recent:
            updated_recent.append(item)
    updated_recent = updated_recent[:12]

    try:
        result = pipeline.rank_and_compile(
            yt_niche,
            seed_topic,
            scored,
            count=use,
            segment_sec=target_pacing,
            subniche_info=chosen,
            strategy_directives=strategy,
            used_titles=used_titles,
            dispatch_telegram=False,
            output_filename="master_youtube_shorts.mp4",
        )
    except Exception as exc:
        report["error"] = str(exc)[:500]
        _flush_report(report)
        inbox.send(f"⚠️ Found original clips, but the edit failed: {exc}")
        raise SystemExit(1)

    # Update Permanent Deduplication Vault
    all_used_clips = list(result.get("new_dedup_keys") or []) + [x for x in used_clip_ids if x not in (result.get("new_dedup_keys") or [])]
    all_used_titles = [result["title"]] + [t for t in used_titles if t != result["title"]]

    job = {
        "topic": result["title"],
        "banner_title": result.get("banner_title", seed_topic[:24]),
        "niche": yt_niche,
        "recent_subniches": updated_recent,
        "used_clip_ids": all_used_clips[:2000],
        "used_titles": all_used_titles[:300],
        "strategy_diagnosis": strategy,
        "caption": result.get("description") or result["title"],
        "credits": result.get("credits") or "",
        "tags": result.get("tags") or [],
        "category_id": result.get("category_id") or chosen.get("category_id", "23"),
        "video_path": result["video_path"],
        "target_platform": "youtube",
    }
    pending_dir = DATA_DIR / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    (pending_dir / "local_job.json").write_text(json.dumps(job, indent=2), encoding="utf-8")
    report["video"] = result["video_path"]
    report["final_title"] = result["title"]
    report["segments"] = [
        {
            "rank": c.get("rank"),
            "rank_label": c.get("rank_label"),
            "what_happens": c.get("what_happens"),
            "start_sec": c.get("start_sec"),
            "duration": c.get("duration"),
            "raw_duration": c.get("raw_duration"),
        }
        for c in (result.get("ranked_plan") or [])
    ]
    _flush_report(report)
    logger.info(f"YouTube Short ready: {result['video_path']} ('{result['title']}')")


def main():
    parser = argparse.ArgumentParser(description="AAT — Autonomous YouTube Shorts Ranking Engine")
    parser.add_argument(
        "--action",
        choices=["prepare", "announce", "watch", "daily", "subagent"],
        default="daily",
    )
    parser.add_argument("--niche", default="")
    parser.add_argument("--topic", default="")
    parser.add_argument("--wait-approval-min", type=int, default=40)
    args = parser.parse_args()

    from backend.app.orchestrator.approval import announce, watch

    if args.action == "prepare":
        prepare(args.niche, args.topic)
    elif args.action == "announce":
        announce(args.wait_approval_min)
    elif args.action == "watch":
        watch()
    elif args.action == "subagent":
        from backend.app.telegram.bot_service import TelegramInteractiveBot
        bot = TelegramInteractiveBot()
        bot.run_polling_daemon()
    else:
        prepare(args.niche, args.topic)
        announce(args.wait_approval_min)


if __name__ == "__main__":
    main()
