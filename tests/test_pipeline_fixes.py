"""End-to-end tests for the two reported problems: (1) caption not matching the video, (2) clips repeating."""
import shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import backend.app.orchestrator.pipeline as P
from backend.app.sourcing import fingerprint as fp

TMP = Path(tempfile.mkdtemp())
TOPIC = "5 Times The Cameraman Completely Lost It Laughing"


def ff(*a):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True)


def vid(name, src):
    p = TMP / name
    ff("-f", "lavfi", "-i", f"{src}=size=640x360:rate=25", "-t", "8", "-pix_fmt", "yuv420p", str(p))
    return p


def clip(i, path=None, premise=True, rank=0):
    path = path or TMP / f"c{i}.mp4"
    Path(path).touch()
    return {"video_id": f"tt_{i}", "url": f"https://www.tiktok.com/@u{i}/video/{i}", "channel": f"u{i}", "title": f"TikTok Moment by @u{i}",
            "filepath": str(path), "duration": 10.0, "funny_score": 9 - i * 0.1, "license": "transformative-credit", "credit": f"@u{i} (TikTok)",
            "what_happens": f"clip {i} shows thing {i}", "actual_vibe": "hilarious", "rank_label": f"Thing {i}", "punchline": f"p{i}",
            "premise_shown": premise, "theme_fit": 9.0 if premise else 2.0, "theme_evidence": "e", "start_sec": 0.0, "trim_duration": 8.0,
            "dedup_keys": [f"tt_{i}", f"https://www.tiktok.com/@u{i}/video/{i}", f"fp:u{i}:10.0"], "rank": rank}


class FakeBrain:
    CATEGORY_MAP = {}

    def __init__(self, titles, qa_boring=()):
        self.titles, self.calls, self.qa_boring, self.qa_done = list(titles), [], list(qa_boring), False

    def curate_and_rank_top_clips(self, sub_meta, pool, count=5, strategy_directives=None):
        out = [dict(c) for c in pool[:count]]
        for i, c in enumerate(out):
            c["rank"] = len(out) - i
        return out

    def generate_dynamic_short_packaging(self, **kw):
        self.calls.append(kw)
        t = self.titles.pop(0) if len(self.titles) > 1 else self.titles[0]
        return {"video_title": t, "banner_title": "BANNER", "description": "d", "tags": ["t"], "category_id": "23", "comment_question": "q"}

    def verify_packaging_claims(self, *a, **k):
        return {"ok": True, "issues": [], "checked": True}

    def review_video_with_gemini_vision(self, *a, **k):
        if self.qa_boring and not self.qa_done:
            self.qa_done = True
            return {"boring_or_weak_ranks": self.qa_boring}
        return {}


class FakeCompiler:
    def assemble_full_ranking_video(self, title, clips, output_path):
        Path(output_path).write_bytes(b"x")


def make_pipeline(brain):
    pl = P.ViralVideoPipeline.__new__(P.ViralVideoPipeline)
    pl.brain, pl.compiler = brain, FakeCompiler()
    pl.youtube_client = mock.Mock(fetch_trending_topics=lambda: [])
    pl.dispatcher = mock.Mock()
    return pl


class CaptionMatchesVideo(unittest.TestCase):
    def run_compile(self, brain, scored, count=5):
        pl = make_pipeline(brain)
        with mock.patch.object(P, "DATA_DIR", TMP), mock.patch.object(P, "OUTPUT_DIR", TMP):
            return pl.rank_and_compile("funny", TOPIC, scored, count=count, dispatch_telegram=False)

    def test_parroted_concept_title_is_rewritten_when_clips_do_not_match(self):
        scored = [clip(i, premise=(i == 1)) for i in range(1, 7)]              # only 1 clip really shows the concept
        brain = FakeBrain(["5 Times The Cameraman Completely Lost It Laughing #Shorts", "Chaos From Every Angle: #1 Will Shock You #Shorts"])
        res = self.run_compile(brain, scored)
        self.assertEqual(len(brain.calls), 2)                                    # first attempt rejected, rewritten once
        self.assertTrue(brain.calls[1]["revision_notes"])                        # the model was told exactly what was false
        self.assertFalse(brain.calls[1]["premise_verified"])
        self.assertNotIn("Cameraman", res["title"])
        self.assertEqual(res["packaging_guard"], "passed after rewrite")

    def test_stubborn_model_falls_back_to_a_plain_truthful_title(self):
        scored = [clip(i, premise=False) for i in range(1, 7)]
        brain = FakeBrain(["5 Times The Cameraman Completely Lost It Laughing #Shorts"])   # keeps repeating the false claim
        res = self.run_compile(brain, scored)
        self.assertNotIn("cameraman", (res["title"] + res["banner_title"]).lower())
        self.assertTrue(res["packaging_guard"].startswith("fallback"))

    def test_concept_title_allowed_when_every_clip_really_matches(self):
        scored = [clip(i, premise=True) for i in range(1, 7)]
        brain = FakeBrain(["5 Times The Cameraman Completely Lost It Laughing #Shorts"])
        res = self.run_compile(brain, scored)
        self.assertEqual(res["packaging_guard"], "passed")
        self.assertTrue(brain.calls[0]["premise_verified"])

    def test_on_premise_clips_are_chosen_over_higher_scoring_off_premise_ones(self):
        scored = [clip(i, premise=False) for i in range(1, 4)] + [clip(i, premise=True) for i in range(4, 10)]
        scored.sort(key=lambda c: c["funny_score"], reverse=True)                 # off-premise clips score higher
        res = self.run_compile(FakeBrain(["T #Shorts"]), scored)
        self.assertTrue(all(c["premise_shown"] for c in res["final_clips"]))


class NoRepeats(unittest.TestCase):
    def test_reserve_swapped_in_by_QA_is_remembered(self):
        """The leak: QA swaps a weak clip for a reserve; the reserve was shown but never saved to the vault."""
        scored = [clip(i) for i in range(1, 8)]                                  # 5 chosen + 2 reserves (#6, #7)
        brain = FakeBrain(["Some Honest Title #Shorts"], qa_boring=[3])
        pl = make_pipeline(brain)
        with mock.patch.object(P, "DATA_DIR", TMP), mock.patch.object(P, "OUTPUT_DIR", TMP):
            res = pl.rank_and_compile("funny", TOPIC, scored, count=5, dispatch_telegram=False)
        shown_ids = {c["video_id"] for c in res["final_clips"]}
        self.assertTrue(shown_ids & {"tt_6", "tt_7"}, "a reserve should have been swapped in")
        for c in res["final_clips"]:
            for k in (c["video_id"], c["url"], f"fp:{c['channel']}:10.0"):
                self.assertIn(k, res["new_dedup_keys"], f"{k} was shown in the video but not recorded as used")

    def test_title_is_rewritten_for_the_final_lineup_after_a_swap(self):
        scored = [clip(i) for i in range(1, 8)]
        brain = FakeBrain(["T1 #Shorts", "T2 #Shorts"], qa_boring=[3])
        pl = make_pipeline(brain)
        with mock.patch.object(P, "DATA_DIR", TMP), mock.patch.object(P, "OUTPUT_DIR", TMP):
            res = pl.rank_and_compile("funny", TOPIC, scored, count=5, dispatch_telegram=False)
        self.assertEqual(len(brain.calls), 2)                                    # written again after the swap
        last_clips = {c["video_id"] for c in brain.calls[-1]["ranked_clips"]}
        self.assertEqual(last_clips, {c["video_id"] for c in res["final_clips"]})
        self.assertEqual(res["title"], "T2 #Shorts")

    def test_reposted_footage_is_rejected_even_with_new_id_and_creator(self):
        a = vid("a.mp4", "mandelbrot"); b = vid("b.mp4", "testsrc2"); c = vid("c.mp4", "rgbtestsrc"); d = vid("d.mp4", "life")
        a_repost = TMP / "a_repost.mp4"
        ff("-ss", "1", "-i", str(a), "-vf", "scale=480:270", "-b:v", "300k", str(a_repost))
        vault = set(fp.keys(fp.frame_hashes(a))) | {"tt_1"}                       # clip A was used in an earlier video
        files = {"tt_10": a_repost, "tt_11": b, "tt_12": c, "tt_13": d, "tt_14": a_repost}
        cands = [{"video_id": k, "url": f"https://www.tiktok.com/@x{k}/video/{k}", "channel": f"x{k}", "title": "t", "duration_sec": 8,
                  "license": "transformative-credit", "credit": "@x (TikTok)"} for k in files]

        def fake_dl(cand, name):  # each real download is its own file (the pipeline deletes rejected ones)
            dst = TMP / f"dl_{cand['video_id']}.mp4"
            shutil.copy(files[cand["video_id"]], dst)
            return dict(cand, filepath=str(dst), duration=8.0), ""

        score = lambda *a, **k: {"funny_score": 8.0, "hook_strength": 8, "climax_intensity": 8, "actual_vibe": "hilarious", "reason": "r", "start_sec": 0,
                                 "trim_duration": 8, "rejected": False, "premise_shown": True, "theme_fit": 9.0, "theme_evidence": "e"}
        pl = make_pipeline(FakeBrain(["t"]))
        failures = []
        with mock.patch.object(P, "download_clip", fake_dl), mock.patch.object(P, "score_clip_visually", score), \
                mock.patch.object(P.settings, "GEMINI_API_KEY", "x"):
            got = pl.gather_clips(cands, TOPIC, count=3, used_clip_ids=vault, failures=failures)
        ids = {c["video_id"] for c in got}
        self.assertNotIn("tt_10", ids); self.assertNotIn("tt_14", ids)           # the repost of A: caught despite new id + creator
        self.assertEqual(ids, {"tt_11", "tt_12", "tt_13"})
        self.assertTrue(any("visual match" in f["reason"] for f in failures))
        self.assertTrue(all(any(k.startswith("vh:") for k in c["dedup_keys"]) for c in got))   # fingerprints get saved for next time

    def test_same_footage_twice_in_one_run_is_used_once(self):
        a = vid("a2.mp4", "mandelbrot"); b = vid("b2.mp4", "testsrc2")
        a2 = TMP / "a2_copy.mp4"
        ff("-i", str(a), "-vf", "scale=480:270", "-b:v", "300k", str(a2))
        files = {"tt_20": a, "tt_21": a2, "tt_22": b}
        cands = [{"video_id": k, "url": f"https://www.tiktok.com/@y{k}/video/{k}", "channel": f"y{k}", "title": "t", "duration_sec": 8,
                  "license": "transformative-credit", "credit": "@y (TikTok)"} for k in files]
        def fake_dl(cand, name):
            dst = TMP / f"dl2_{cand['video_id']}.mp4"
            shutil.copy(files[cand["video_id"]], dst)
            return dict(cand, filepath=str(dst), duration=8.0), ""
        score = lambda *a, **k: {"funny_score": 8.0, "actual_vibe": "hilarious", "reason": "r", "start_sec": 0, "trim_duration": 8, "rejected": False,
                                 "premise_shown": True, "theme_fit": 9.0}
        with mock.patch.object(P, "download_clip", fake_dl), mock.patch.object(P, "score_clip_visually", score), mock.patch.object(P.settings, "GEMINI_API_KEY", "x"):
            got = make_pipeline(FakeBrain(["t"])).gather_clips(cands, TOPIC, count=3, used_clip_ids=set(), failures=[])
        self.assertEqual({c["video_id"] for c in got}, {"tt_20", "tt_22"})


if __name__ == "__main__":
    unittest.main()


class RealPromptsAndScorer(unittest.TestCase):
    def test_packaging_prompt_is_truth_bound(self):
        from backend.app.ai.creator_brain import GeminiCreatorBrain
        brain = GeminiCreatorBrain.__new__(GeminiCreatorBrain)
        seen = {}

        def fake(parts, temperature=0.4, **k):
            seen["p"] = parts[0]["text"]
            return {"video_title": "Honest Title", "banner_title": "REAL HOOK", "opening_hook": "h", "ranked_breakdown": "b",
                    "comment_question": "q", "hashtags": "#Shorts", "tags": ["a"], "category_id": "23"}
        clips = [dict(clip(i), rank=6 - i) for i in range(1, 6)]
        with mock.patch.object(brain, "_call_gemini_json", fake):
            brain.generate_dynamic_short_packaging("funny", TOPIC, clips, "credits", premise_verified=False, matching_count=1,
                                                   revision_notes=["claims 5 times but only 1 clip shows it"])
            self.assertIn("NOT verified (only 1 of 5", seen["p"])
            self.assertIn("REJECTED FOR THESE FACTUAL PROBLEMS", seen["p"])
            self.assertIn("claims 5 times but only 1 clip shows it", seen["p"])
            brain.generate_dynamic_short_packaging("funny", TOPIC, clips, "credits", premise_verified=True, matching_count=5)
            self.assertIn("IS verified", seen["p"])
            self.assertNotIn("REJECTED FOR", seen["p"])

    def test_packaging_api_failure_never_falls_back_to_the_unverified_concept(self):
        from backend.app.ai.creator_brain import GeminiCreatorBrain
        brain = GeminiCreatorBrain.__new__(GeminiCreatorBrain)
        clips = [dict(clip(i), rank=6 - i) for i in range(1, 6)]
        with mock.patch.object(brain, "_call_gemini_json", side_effect=RuntimeError("down")):
            pk = brain.generate_dynamic_short_packaging("funny", TOPIC, clips, "c", premise_verified=False, matching_count=0)
        self.assertNotIn("cameraman", (pk["video_title"] + pk["banner_title"]).lower())

    def test_scorer_premise_needs_both_the_flag_and_a_high_theme_fit(self):
        from backend.app.sourcing import funny_scorer as fs
        v = vid("scorer.mp4", "mandelbrot")
        base = {"is_raw_single_clip": True, "full_setup_and_payoff_visible": True, "is_funny_or_entertaining": True,
                "matches_subniche_and_vibe": True, "actual_vibe": "hilarious", "viral_score": 8.5, "humor_score": 8.5, "hook_strength": 8,
                "climax_intensity": 8, "setup_start_sec": 0.0, "climax_sec": 4.0, "aftermath_end_sec": 7.0, "what_happens": "w", "story_arc": "s",
                "audio_cue": "a", "standout_detail": "d", "why_rankworthy": "y", "rank_label": "L", "punchline": "p", "commentary": "c",
                "theme_evidence": "saw it"}
        for flag, fit, expect in [(True, 9.0, True), (True, 4.0, False), (False, 9.5, False)]:
            with mock.patch.object(fs, "_gemini", return_value=(dict(base, premise_shown=flag, theme_fit=fit), "m")):
                r = fs.score_clip_visually(v, "t", TOPIC, window_sec=8.0)
            self.assertFalse(r["rejected"])
            self.assertEqual(r["premise_shown"], expect, (flag, fit))
