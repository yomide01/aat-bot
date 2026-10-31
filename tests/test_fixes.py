import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.app.sourcing import fingerprint as fp
from backend.app.ai import packaging_guard as pg

TMP = Path(tempfile.mkdtemp())


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def make(name, src, dur=10):
    p = TMP / name
    ff("-f", "lavfi", "-i", f"{src}=size=640x360:rate=25", "-t", str(dur), "-pix_fmt", "yuv420p", str(p))
    return p


class Fingerprint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = make("a.mp4", "mandelbrot")
        # the SAME footage re-uploaded: re-encoded smaller, lower quality, 1.5s trimmed off the start
        cls.a_repost = TMP / "a_repost.mp4"
        ff("-ss", "1.5", "-i", str(cls.a), "-vf", "scale=480:270", "-b:v", "300k", str(cls.a_repost))
        cls.b = make("b.mp4", "testsrc2")
        cls.c = make("c.mp4", "cellauto")
        cls.black = TMP / "black.mp4"
        ff("-f", "lavfi", "-i", "color=c=black:size=640x360:rate=25", "-t", "8", str(cls.black))

    def test_repost_is_detected(self):
        known = fp.vault_hashes(fp.keys(fp.frame_hashes(self.a)))
        self.assertTrue(fp.is_repeat(fp.frame_hashes(self.a_repost), known))

    def test_different_footage_is_not_flagged(self):
        known = fp.vault_hashes(fp.keys(fp.frame_hashes(self.a)))
        self.assertFalse(fp.is_repeat(fp.frame_hashes(self.b), known))
        self.assertFalse(fp.is_repeat(fp.frame_hashes(self.c), known))

    def test_black_frames_never_match(self):
        self.assertEqual(fp.frame_hashes(self.black), [])
        known = fp.vault_hashes(fp.keys(fp.frame_hashes(self.a)))
        self.assertFalse(fp.is_repeat(fp.frame_hashes(self.black), known))

    def test_empty_vault_is_safe(self):
        self.assertFalse(fp.is_repeat(fp.frame_hashes(self.a), set()))


# The exact failing video from the repo's history
REAL = [("Energetic movie set dancing with crew", "hilarious"), ("A blindfolded height test ends in screams", "hilarious"),
        ("A surprise table-sliding reaction", "hilarious"), ("A cameraman's hysterical wheezing laugh", "hilarious"),
        ("A house hunting display bed collapses", "epic_fail")]


class Packaging(unittest.TestCase):
    TOPIC = "5 Times The Cameraman Completely Lost It Laughing"

    def test_the_reported_bug_is_caught(self):
        bad = "5 Times The Cameraman Completely Lost It Laughing #Shorts"
        issues = pg.claim_issues(bad, "CAMERAMAN LOSES IT", self.TOPIC, matching=1, total=5, verified=False)
        self.assertTrue(issues, "title claiming 5 cameraman clips must be flagged when only 1 clip shows it")

    def test_truthful_title_passes(self):
        self.assertEqual(pg.claim_issues("A Bed Collapse And 4 More Wild Reactions #Shorts", "WILD REACTIONS", self.TOPIC, 1, 5, False), [])

    def test_verified_concept_may_be_repeated(self):
        self.assertEqual(pg.claim_issues("5 Times The Cameraman Completely Lost It Laughing #Shorts", "X", self.TOPIC, 5, 5, True), [])

    def test_safe_fallback_makes_no_concept_claim(self):
        clips = [{"rank": 5 - i, "rank_label": l[:18], "what_happens": l, "actual_vibe": v} for i, (l, v) in enumerate(REAL)]
        pk = pg.safe_packaging("funny", clips, "23")
        self.assertEqual(pg.claim_issues(pk["video_title"], pk["banner_title"], self.TOPIC, 1, 5, False), [])
        self.assertNotIn("cameraman", (pk["video_title"] + pk["banner_title"]).lower())
        self.assertIn("#Shorts", pk["video_title"]); self.assertLessEqual(len(pk["video_title"]), 100)
        for l, _ in REAL: self.assertIn(l, pk["description"])


if __name__ == "__main__":
    unittest.main()
