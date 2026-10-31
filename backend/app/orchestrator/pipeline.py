import json
from pathlib import Path
from typing import Dict, Any, List, Optional, Set
from ..core.config import settings, DATA_DIR, OUTPUT_DIR
from ..core.logger import logger
from ..ai.creator_brain import GeminiCreatorBrain
from ..video.real_compiler import ViralRealCompiler
from ..telegram.dispatcher import TelegramHumanDispatcher
from ..posting.youtube_api import YouTubeShortsClient
from ..sourcing.downloader import download_clip
from ..sourcing.funny_scorer import score_clip_visually
from ..sourcing.rights import check_clip, credit_line
from ..sourcing import fingerprint
from ..ai import packaging_guard


class ViralVideoPipeline:
    """
    Production-Grade Autonomous YouTube Shorts Ranking & Channel Growth Pipeline.
    - Studies past YouTube Analytics across every published video to evolve pacing, ranking arcs, and hooks
    - Permanently deduplicates clips, URLs, creator-duration fingerprints, and titles
    - Sources single-scene original clips from TikTok (Primary), using Original YouTube Videos
      strictly as a fallback when TikTok yields fewer than the required clips
    - Enforces Fair Use & Monetization compliance (1 clip max per creator, short transformative climax slices,
      9:16 visual reframing, progressive 1-5 commentary HUD, and full creator attribution)
    - Watches 360p @ 8fps video + 64kbps audio streams via the Gemini 3.8 / 3.7 / 3.6 / 3.5 Flash Pool
      (with gemini-2.5-flash as dead-last fallback)
    """

    def __init__(self):
        self.brain = GeminiCreatorBrain()
        self.compiler = ViralRealCompiler()
        self.dispatcher = TelegramHumanDispatcher()
        self.youtube_client = YouTubeShortsClient()

    @staticmethod
    def _clip_fingerprints(clip: Dict[str, Any]) -> Set[str]:
        keys: Set[str] = set()
        if clip.get("video_id"):
            keys.add(str(clip["video_id"]).strip())
        if clip.get("url"):
            keys.add(str(clip["url"]).strip().rstrip("/"))
        ch = str(clip.get("channel") or "").strip().lower()
        dur = float(clip.get("duration") or clip.get("duration_sec") or 0.0)
        if ch and dur > 0:
            keys.add(f"fp:{ch}:{round(dur, 1)}")
        return keys

    def gather_clips(
        self,
        candidates: List[Dict[str, Any]],
        topic: str,
        count: int = 5,
        segment_sec: float = 8.5,
        vibe_rules: str = "",
        used_clip_ids: Optional[Set[str]] = None,
        notify=None,
        failures: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Downloads candidate clips from TikTok (Primary) first, and falls back to Original YouTube Videos
        only if TikTok candidates do not produce enough verified clips.
        Enforces permanent deduplication and max 1 clip per creator for Fair Use & Monetization safety.
        """
        failures = failures if failures is not None else []
        seen_vault: Set[str] = set(used_clip_ids or set())
        known_vh: Set[int] = fingerprint.vault_hashes(seen_vault)  # what past clips LOOKED like (catches re-uploads)
        seen_creators_in_run: Set[str] = set()
        scored: List[Dict[str, Any]] = []

        for i, cand in enumerate(candidates):
            # Keep looking until we have enough clips that truly show the video's premise (or a fallback pool).
            on_premise = sum(1 for c in scored if c.get("premise_shown"))
            if on_premise >= count + 1 or len(scored) >= count + 4:
                break
            cand_keys = self._clip_fingerprints(cand)
            if cand_keys & seen_vault:
                failures.append({"url": cand.get("url"), "reason": "skipped: previously used clip"})
                continue
            ok, why = check_clip(cand)
            if not ok:
                failures.append({"url": cand.get("url"), "reason": why})
                continue

            clip, reason = download_clip(cand, f"cand_{cand.get('video_id', i)}")
            if not clip:
                failures.append({"url": cand.get("url"), "reason": reason})
                continue

            post_dl_keys = self._clip_fingerprints(clip)
            if post_dl_keys & seen_vault:
                Path(clip["filepath"]).unlink(missing_ok=True)
                failures.append({"url": cand.get("url"), "reason": "skipped: duplicate creator+duration fingerprint"})
                continue

            # Same footage re-uploaded by another account / re-encoded / trimmed? IDs differ, pictures don't.
            vh = fingerprint.frame_hashes(Path(clip["filepath"]))
            if fingerprint.is_repeat(vh, known_vh):
                Path(clip["filepath"]).unlink(missing_ok=True)
                failures.append({"url": cand.get("url"), "reason": "skipped: same footage as a clip used before (visual match)"})
                continue

            # Fair Use & Anti-Spam Rule: Never take more than 1 clip from the same creator in a single compilation
            creator_key = str(clip.get("channel") or "").strip().lower()
            if creator_key and creator_key not in {"tiktok_creator", "creator"} and creator_key in seen_creators_in_run:
                Path(clip["filepath"]).unlink(missing_ok=True)
                failures.append({"url": cand.get("url"), "reason": f"skipped: max 1 clip per creator ({creator_key})"})
                continue

            if settings.GEMINI_API_KEY:
                res = score_clip_visually(
                    Path(clip["filepath"]),
                    clip.get("title", ""),
                    topic,
                    window_sec=segment_sec,
                    vibe_rules=vibe_rules,
                )
                if res.get("rejected"):
                    Path(clip["filepath"]).unlink(missing_ok=True)
                    failures.append({"url": cand.get("url"), "reason": res.get("reason", "rejected by Gemini QA")})
                    continue
            else:
                res = {
                    "funny_score": self._fallback_score(clip, topic),
                    "hook_strength": 7.5,
                    "climax_intensity": 7.5,
                    "actual_vibe": "viral",
                    "reason": (clip.get("title") or "viral moment")[:80],
                    "start_sec": clip.get("start_sec") or 0.0,
                    "trim_duration": segment_sec,
                    "rank_label": (clip.get("title") or "Moment")[:18],
                    "punchline": (clip.get("title") or "Watch this moment")[:48],
                    "what_happens": clip.get("title") or "",
                    "story_arc": "",
                    "audio_cue": "",
                    "standout_detail": "",
                    "why_rankworthy": "",
                    "commentary": "",
                }
            if res["funny_score"] <= 0:
                Path(clip["filepath"]).unlink(missing_ok=True)
                failures.append({"url": cand.get("url"), "reason": "low score"})
                continue

            seen_vault.update(post_dl_keys)
            known_vh.update(int(h, 16) for h in vh)
            if creator_key:
                seen_creators_in_run.add(creator_key)
            clip.update(
                funny_score=res["funny_score"],
                hook_strength=res.get("hook_strength", res["funny_score"]),
                climax_intensity=res.get("climax_intensity", res["funny_score"]),
                actual_vibe=res.get("actual_vibe", ""),
                score_reason=res["reason"],
                start_sec=res["start_sec"],
                trim_duration=res.get("trim_duration", segment_sec),
                rank_label=res.get("rank_label") or (res.get("punchline") or clip.get("title", "Moment"))[:18],
                punchline=res.get("punchline") or (res["reason"] or clip.get("title", ""))[:50],
                what_happens=res.get("what_happens") or res["reason"] or clip.get("title", ""),
                story_arc=res.get("story_arc") or "",
                audio_cue=res.get("audio_cue") or "",
                standout_detail=res.get("standout_detail") or "",
                why_rankworthy=res.get("why_rankworthy") or "",
                commentary=res.get("commentary") or "",
                dedup_keys=list(post_dl_keys) + fingerprint.keys(vh),
                premise_shown=bool(res.get("premise_shown", False)),
                theme_fit=float(res.get("theme_fit", 0.0) or 0.0),
                theme_evidence=str(res.get("theme_evidence") or ""),
            )
            scored.append(clip)
        # Clips that really show the premise first, then by score.
        return sorted(scored, key=lambda c: (bool(c.get("premise_shown")), c["funny_score"]), reverse=True)

    def _package_with_guard(self, niche, topic, clips, credits_str, trending_titles, used_titles, strategy) -> Dict[str, Any]:
        """Writes the title/banner/description, then fact-checks it against what the clips really show.
        Rewrites once with the problems listed; if it is still not truthful, uses a plain packaging built only from the clips."""
        total = len(clips)
        matching = sum(1 for c in clips if c.get("premise_shown"))
        verified = total > 0 and matching == total
        notes: List[str] = []
        for attempt in range(2):
            pk = self.brain.generate_dynamic_short_packaging(
                niche_id=niche, seed_topic=topic, ranked_clips=clips, credits_text=credits_str,
                trending_titles=trending_titles, used_titles=used_titles, strategy_directives=strategy,
                premise_verified=verified, matching_count=matching, revision_notes=notes or None,
            )
            notes = packaging_guard.claim_issues(pk["video_title"], pk["banner_title"], topic, matching, total, verified)
            if not notes:
                chk = self.brain.verify_packaging_claims(pk["video_title"], pk["banner_title"], pk["description"], clips)
                notes = [] if chk.get("ok", True) else list(chk.get("issues") or ["unspecified false claim"])
            if not notes:
                pk["guard"] = "passed" if attempt == 0 else "passed after rewrite"
                return pk
            logger.warning(f"Packaging rejected by fact-check (attempt {attempt + 1}): {notes}")
        pk = packaging_guard.safe_packaging(niche, clips, self.brain.CATEGORY_MAP.get(niche, "23"))
        pk["guard"] = "fallback: " + "; ".join(notes)[:200]
        return pk

    def _fallback_score(self, clip: Dict[str, Any], topic: str) -> float:
        import re
        text = f"{clip.get('title', '')} {clip.get('description', '')}".lower()
        hits = sum(1 for word in re.findall(r"[a-z0-9]{4,}", (topic or "").lower()) if word in text)
        return round(6.5 + min(hits, 3) * 0.5, 2)

    def rank_and_compile(
        self,
        niche: str,
        topic: str,
        scored: List[Dict[str, Any]],
        count: int = 5,
        segment_sec: float = 8.5,
        subniche_info: Optional[Dict[str, Any]] = None,
        strategy_directives: Optional[Dict[str, Any]] = None,
        used_titles: Optional[List[str]] = None,
        dispatch_telegram: bool = True,
        output_filename: str = "master_youtube_shorts.mp4",
    ) -> Dict[str, Any]:
        """Comparatively curates and orders clips from #5 down to #1 using full human-watched scene details."""
        if len(scored) < count:
            raise RuntimeError(f"Only {len(scored)} usable original clips (need {count}).")

        sub_meta = subniche_info or {"id": niche, "label": topic}
        on_premise = [c for c in scored if c.get("premise_shown")]
        pool = on_premise if len(on_premise) >= count else scored  # prefer a lineup where every clip fits the title
        curated = self.brain.curate_and_rank_top_clips(
            sub_meta,
            pool,
            count=count,
            strategy_directives=strategy_directives,
        )

        curated_ids = {id(c) for c in curated}
        reserves = [c for c in scored if id(c) not in curated_ids and c.get("filepath") not in {x.get("filepath") for x in curated}]
        reserves.sort(key=lambda c: not c.get("premise_shown"))  # on-premise reserves get swapped in first

        def _format_ranked_item(c: Dict[str, Any], rank_num: int) -> Dict[str, Any]:
            raw_dur = float(c.get("duration") or 10.0)
            if raw_dur <= 18.0:
                start_s = 0.0
                final_dur = round(raw_dur, 2)
            else:
                start_s = float(c.get("start_sec") or 0.0)
                ideal_dur = float(c.get("trim_duration") or (raw_dur - start_s))
                final_dur = round(min(max(ideal_dur, 6.5), max(5.0, raw_dur - start_s)), 2)
            return {
                "rank": int(rank_num),
                "video_id": c.get("video_id", ""),
                "title": c["title"],
                "rank_label": (c.get("rank_label") or c.get("punchline") or c["title"])[:20],
                "what_happens": c.get("what_happens") or c.get("score_reason") or c["title"],
                "story_arc": c.get("story_arc") or "",
                "audio_cue": c.get("audio_cue") or "",
                "standout_detail": c.get("standout_detail") or "",
                "why_rankworthy": c.get("why_rankworthy") or "",
                "actual_vibe": c.get("actual_vibe") or niche,
                "punchline": (c.get("punchline") or c.get("score_reason") or c["title"])[:52],
                "commentary": c.get("commentary") or "",
                "filepath": c["filepath"],
                "url": c["url"],
                "start_sec": start_s,
                "duration": final_dur,
                "raw_duration": raw_dur,
                "funny_score": c["funny_score"],
                "license": c["license"],
                "credit": c.get("credit", ""),
                "channel": c.get("channel", ""),
                "dedup_keys": list(c.get("dedup_keys") or self._clip_fingerprints(c)),
                "premise_shown": bool(c.get("premise_shown")),
                "theme_fit": c.get("theme_fit"),
                "theme_evidence": c.get("theme_evidence", ""),
            }

        ranked = []
        new_dedup_keys: List[str] = []
        for idx, c in enumerate(curated):
            for k in c.get("dedup_keys") or self._clip_fingerprints(c):
                if k not in new_dedup_keys:
                    new_dedup_keys.append(k)
            ranked.append(_format_ranked_item(c, int(c.get("rank", count - idx))))

        formatted_reserves = [_format_ranked_item(r, 0) for r in reserves]

        res = self.compile_authentic_ranking_video(
            niche=niche,
            topic=topic,
            clips=ranked,
            reserve_clips=formatted_reserves,
            used_titles=used_titles,
            strategy_directives=strategy_directives,
            output_filename=output_filename,
            dispatch_telegram=dispatch_telegram,
        )
        # Remember what is REALLY in the final video. QA can swap a weak clip for a reserve during compile;
        # previously the reserve was shown but never recorded, so it could come back in a later video.
        final_clips = res.get("final_clips") or ranked
        for c in final_clips:
            for k in c.get("dedup_keys") or self._clip_fingerprints(c):
                if k not in new_dedup_keys:
                    new_dedup_keys.append(k)
        res["new_dedup_keys"] = new_dedup_keys
        res["ranked_plan"] = final_clips
        return res

    def compile_authentic_ranking_video(
        self,
        niche: str,
        topic: str,
        clips: List[Dict[str, Any]],
        reserve_clips: Optional[List[Dict[str, Any]]] = None,
        used_titles: Optional[List[str]] = None,
        strategy_directives: Optional[Dict[str, Any]] = None,
        output_filename: str = "master_youtube_shorts.mp4",
        dispatch_telegram: bool = True,
    ) -> Dict[str, Any]:
        final_master = OUTPUT_DIR / output_filename
        reserves_pool = list(reserve_clips or [])

        missing = [
            item.get("title", f"clip #{i}")
            for i, item in enumerate(clips)
            if not Path(item["filepath"]).exists()
        ]
        if missing:
            msg = f"Aborting: {len(missing)} clip(s) could not be sourced: {', '.join(missing)}"
            logger.error(msg)
            raise RuntimeError(msg)

        credits_str = credit_line(clips)
        trending_titles = self.youtube_client.fetch_trending_topics()
        packaging = self._package_with_guard(niche, topic, clips, credits_str, trending_titles, used_titles, strategy_directives)

        dynamic_title = packaging["video_title"]
        banner_header = packaging["banner_title"]
        dynamic_desc = packaging["description"]
        dynamic_tags = packaging["tags"]
        category_id = packaging["category_id"]

        logger.info(
            f"[bold cyan]Compiling Creator Ranking Short:[/bold cyan] '{dynamic_title}' "
            f"(Top Hook: '{banner_header}')"
        )
        self.compiler.assemble_full_ranking_video(
            title=banner_header,
            clips=clips,
            output_path=final_master,
        )

        qa_result = self.brain.review_video_with_gemini_vision(
            final_master,
            expected_title=dynamic_title,
            ranked_clips=clips,
        )

        # Active Self-Healing Director Gate: Fix missing setups, early cuts, weak clips, or label mismatches!
        missing_setup_ranks = {int(r) for r in (qa_result.get("missing_setup_ranks") or []) if str(r).isdigit()}
        early_ranks = {int(r) for r in (qa_result.get("early_cut_ranks") or []) if str(r).isdigit()}
        boring_ranks = {int(r) for r in (qa_result.get("boring_or_weak_ranks") or []) if str(r).isdigit()}
        label_fixes = {str(k): str(v) for k, v in (qa_result.get("label_fixes") or {}).items() if v}

        if missing_setup_ranks or early_ranks or boring_ranks or label_fixes:
            logger.info(
                f"[bold yellow]Self-Healing Director Gate Triggered:[/bold yellow] "
                f"MissingSetup={sorted(missing_setup_ranks)} | EarlyCut={sorted(early_ranks)} | "
                f"Boring={sorted(boring_ranks)} | LabelFixes={label_fixes}"
            )
            modified = False
            swapped = False
            for idx, c in enumerate(clips):
                r_num = int(c.get("rank", 0))
                if r_num in boring_ranks and reserves_pool:
                    replacement = dict(reserves_pool.pop(0))
                    replacement["rank"] = r_num
                    clips[idx] = replacement
                    modified = True
                    swapped = True
                    continue
                if str(r_num) in label_fixes:
                    c["rank_label"] = label_fixes[str(r_num)][:20].strip()
                    modified = True
                raw_d = float(c.get("raw_duration") or c.get("duration") or 12.0)
                if r_num in missing_setup_ranks and r_num in early_ranks:
                    c["start_sec"] = 0.0
                    c["duration"] = min(23.0, raw_d)
                    modified = True
                elif r_num in missing_setup_ranks:
                    old_end = min(raw_d, float(c.get("start_sec", 0.0)) + float(c.get("duration", 10.0)))
                    c["start_sec"] = 0.0
                    c["duration"] = min(23.0, max(old_end, min(raw_d, 15.0)))
                    modified = True
                elif r_num in early_ranks:
                    c["duration"] = round(max(6.0, raw_d - float(c.get("start_sec", 0.0))), 2)
                    modified = True

            if modified:
                credits_str = credit_line(clips)
                if swapped or label_fixes:
                    # The clips changed after the title was written: write and fact-check it again for the FINAL lineup.
                    packaging = self._package_with_guard(niche, topic, clips, credits_str, trending_titles, used_titles, strategy_directives)
                    dynamic_title = packaging["video_title"]
                    banner_header = packaging["banner_title"]
                    dynamic_desc = packaging["description"]
                    dynamic_tags = packaging["tags"]
                    category_id = packaging["category_id"]
                self.compiler.assemble_full_ranking_video(
                    title=banner_header,
                    clips=clips,
                    output_path=final_master,
                )
                qa_result["self_healed"] = True

        state_file = DATA_DIR / "telegram_state.json"
        current_state = {}
        if state_file.exists():
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    current_state = json.load(f)
            except Exception:
                pass

        video_record = {
            "title": dynamic_title,
            "banner_title": banner_header,
            "niche": niche,
            "target_platform": "youtube",
            "video_path": str(final_master),
            "qa_review": qa_result,
            "strategy_diagnosis": strategy_directives or {},
            "credits": credits_str,
            "post_caption": dynamic_desc,
            "tags": dynamic_tags,
            "category_id": category_id,
            "sources": [
                {
                    "rank": c.get("rank", len(clips) - i),
                    "rank_label": c.get("rank_label", ""),
                    "title": c.get("title", f"Moment #{len(clips) - i}"),
                    "what_happens": c.get("what_happens", ""),
                    "story_arc": c.get("story_arc", ""),
                    "audio_cue": c.get("audio_cue", ""),
                    "standout_detail": c.get("standout_detail", ""),
                    "punchline": c.get("punchline", ""),
                    "commentary": c.get("commentary", ""),
                    "url": c.get("url", ""),
                    "credit": c.get("credit", ""),
                    "license": c.get("license", ""),
                    "funny_score": c.get("funny_score"),
                }
                for i, c in enumerate(clips)
            ],
            "status": "Ready for approval",
        }
        current_state["latest_video"] = video_record
        current_state["latest_youtube"] = video_record
        current_state["last_diagnosis"] = strategy_directives or {}
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(current_state, f, indent=2)

        if dispatch_telegram:
            logger.info("Dispatching finished YouTube Short to creator Telegram...")
            self.dispatcher.send_video_for_approval(
                video_path=final_master,
                title=dynamic_title,
                caption=dynamic_desc,
                hook=banner_header,
            )

        return {
            "video_path": str(final_master),
            "title": dynamic_title,
            "banner_title": banner_header,
            "description": dynamic_desc,
            "tags": dynamic_tags,
            "category_id": category_id,
            "qa": qa_result,
            "credits": credits_str,
            "final_clips": clips,
            "packaging_guard": packaging.get("guard", ""),
        }
