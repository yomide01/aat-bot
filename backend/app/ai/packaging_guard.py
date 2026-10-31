"""Fact-check for the title / banner / description before anything is rendered or posted.

The bug this fixes: the title was written from the pre-chosen concept ("5 Times The Cameraman Completely Lost It
Laughing") even when most clips did not show that, and was never re-checked after QA swapped clips.
"""
import re
from typing import Dict, List

STOP = {"times", "moments", "moment", "ranking", "ranked", "funniest", "best", "worst", "most", "epic", "wild", "crazy",
        "caught", "camera", "video", "videos", "shorts", "short", "these", "those", "ever", "fails", "viral", "hilarious",
        "unbelievable", "people", "things", "about", "their", "would", "could", "watch", "wait", "final", "number"}
WORDNUM = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
COUNT_RE = re.compile(r"\b(\d{1,2}|two|three|four|five|six|seven|eight|nine|ten)\s+(?:[a-z'-]+\s+){0,2}?"
                      r"(times|moments|clips|fails|people|reasons|ways|kids|dogs|cats|pets)\b", re.I)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", s.lower().replace("#shorts", "")).strip()


def concept_words(topic: str) -> List[str]:
    return [w for w in re.findall(r"[a-z]+", topic.lower()) if len(w) >= 5 and w not in STOP]


def claim_issues(title: str, banner: str, topic: str, matching: int, total: int, verified: bool) -> List[str]:
    """Deterministic checks. Returns a list of problems (empty = fine)."""
    issues: List[str] = []
    text = f"{title} {banner}".lower()
    if verified:
        return issues  # every clip shows the concept, repeating it is truthful
    words = concept_words(topic)
    used = [w for w in words if w in text]
    if _norm(title).startswith(_norm(topic)[:30]) and _norm(topic):
        issues.append("the title just repeats the pre-chosen concept, but not every clip shows it")
    if words and len(used) >= min(2, len(words)):
        issues.append(f"the title/banner claims the concept ({', '.join(used)}) but only {matching} of {total} clips actually show it")
    for m in COUNT_RE.finditer(title):
        n = int(m.group(1)) if m.group(1).isdigit() else WORDNUM[m.group(1).lower()]
        if used and n > matching:
            issues.append(f"claims '{m.group(0)}' but only {matching} clip(s) show that")
    return issues


VIBE_WORDS: Dict[str, str] = {"epic_fail": "EPIC FAILS", "hilarious": "FUNNY MOMENTS", "prank": "PRANKS", "football_skill": "FOOTBALL SKILLS",
                              "sports_highlight": "SPORTS MOMENTS", "embarrassing": "CRINGE MOMENTS", "wild_crazy": "WILD MOMENTS",
                              "instant_karma": "INSTANT KARMA", "cute_wholesome": "WHOLESOME MOMENTS", "plot_twist": "PLOT TWISTS",
                              "genius_skill": "GENIUS MOVES"}


def safe_packaging(niche_id: str, clips: List[dict], category_id: str) -> Dict:
    """Last-resort packaging built ONLY from what the clips are known to show. No concept, no invented claims."""
    ordered = sorted(clips, key=lambda c: int(c.get("rank", 0)))
    top = ordered[0] if ordered else {}
    vibes = [str(c.get("actual_vibe") or "") for c in clips if c.get("actual_vibe")]
    dominant = max(set(vibes), key=vibes.count) if vibes else ""
    banner = VIBE_WORDS.get(dominant, "RANKED MOMENTS")
    label = str(top.get("rank_label") or top.get("punchline") or "the #1 moment").strip().rstrip(".")
    title = f"{len(clips)} Moments Ranked: #1 Is {label[:40]} #Shorts"
    lines = [f"#{c.get('rank')} {c.get('rank_label', 'Moment')}: {c.get('what_happens') or c.get('title')}" for c in sorted(clips, key=lambda c: -int(c.get("rank", 0)))]
    desc = ("Ranked from #%d down to #1.\n\n👇 Which one was your favorite?\n\n🎬 RANKED BREAKDOWN:\n%s\n\n#Shorts #Ranking" % (len(clips), "\n".join(lines)))
    return {"video_title": title[:100], "banner_title": banner[:24], "description": desc, "comment_question": "Which one was your favorite?",
            "tags": ["Shorts", niche_id, "Ranking", "Viral Shorts"], "category_id": category_id}
