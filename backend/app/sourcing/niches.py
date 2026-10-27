"""Adaptive Sub-Niche Rotation & Creator Strategy Engine under the Master 'Ranking Videos' Niche.

Rotates dynamically across 16+ high-retention viral sub-niches (Epic Fails, Football Moments,
Pranks, Embarrassing Moments, Funny Moments, Wild Moments, Crazy Stunts, Instant Karma,
Gym Fails, Basketball Highlights, Cute Moments, etc.) and supports unlimited custom sub-niches.
Tracks recently used sub-niches so the bot never gets stuck repeating the same sub-niche.
"""
import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from ..core.config import DATA_DIR

# West Africa Time (Lagos) is permanently UTC+1 (no DST).
LAGOS_TZ = timezone(timedelta(hours=1))

SUB_NICHES: List[Dict[str, Any]] = [
    {
        "id": "epic_fails",
        "label": "Epic Fails & Instant Regret",
        "topic": "Ranking Epic Fails Until I Lose It",
        "vibe": "epic_fail",
        "vibe_rules": (
            "MUST show a real physical fail, slip, fall, crash, drop, or instant regret mistake. "
            "Reject calm, cute, or talking-head clips where nothing goes wrong."
        ),
        "category_id": "23",
        "viral_queries": [
            "epic fail caught on camera",
            "instant regret fail moment",
            "funny slip and fall fail",
            "what could go wrong fail",
        ],
    },
    {
        "id": "football_moments",
        "label": "Insane Football Moments",
        "topic": "Ranking 1-in-a-Million Football Moments",
        "vibe": "football_skill",
        "vibe_rules": (
            "MUST show real football/soccer gameplay: an insane goal, ankle-breaker skill, goalkeeper save, "
            "funny football miss, or wild pitch reaction. Reject non-football clips."
        ),
        "category_id": "17",
        "viral_queries": [
            "insane football moment caught on camera",
            "200 iq football skill goal",
            "funny football miss reaction",
            "unbelievable goalkeeper save",
        ],
    },
    {
        "id": "pranks",
        "label": "Hilarious Pranks & Jump Reactions",
        "topic": "Ranking Pranks That Went Way Too Far",
        "vibe": "prank",
        "vibe_rules": (
            "MUST show a clear prank setup and a genuine shocked, scared, or laughing human reaction. "
            "Reject clips without a prank or human reaction."
        ),
        "category_id": "23",
        "viral_queries": [
            "funny scare prank reaction",
            "harmless public prank caught on camera",
            "best friend prank reaction",
            "jump scare prank hilarious",
        ],
    },
    {
        "id": "embarrassing_moments",
        "label": "Most Embarrassing Moments Caught on Camera",
        "topic": "Ranking Embarrassing Moments That Hurt To Watch",
        "vibe": "embarrassing",
        "vibe_rules": (
            "MUST show someone experiencing genuine awkwardness, public embarrassment, premature celebration, "
            "or 'dying inside' on camera. Reject generic cute clips."
        ),
        "category_id": "23",
        "viral_queries": [
            "embarrassing moment caught on camera",
            "people dying inside funny",
            "awkward public fail moment",
            "premature celebration fail",
        ],
    },
    {
        "id": "funny_moments",
        "label": "Pure Comedy & Contagious Laughter",
        "topic": "Ranking Clips That Made The Cameraman Lose It",
        "vibe": "hilarious",
        "vibe_rules": (
            "MUST be genuinely hilarious with a clear comedic punchline, unexpected twist, or contagious laughter. "
            "Reject mundane or merely 'cute' clips that aren't actually funny."
        ),
        "category_id": "23",
        "viral_queries": [
            "contagious laughter funny moment",
            "try not to laugh viral clip",
            "unexpected funny moment caught on camera",
            "hilarious reaction video",
        ],
    },
    {
        "id": "wild_moments",
        "label": "Wild Caught-On-Camera Moments",
        "topic": "Ranking Wild Moments Nobody Would Believe If Not Filmed",
        "vibe": "wild_crazy",
        "vibe_rules": (
            "MUST show a wild, shocking, or unbelievable real-life event caught on camera (close call, crazy nature/weather, "
            "or chaotic street moment). Reject slow or mundane footage."
        ),
        "category_id": "24",
        "viral_queries": [
            "unbelievable moment caught on camera",
            "if it wasnt filmed nobody would believe",
            "crazy close call caught on camera",
            "wild moment on camera",
        ],
    },
    {
        "id": "crazy_moments",
        "label": "Crazy Stunts & Adrenaline Moments",
        "topic": "Ranking Crazy Moments With Zero Fear",
        "vibe": "wild_crazy",
        "vibe_rules": (
            "MUST show a high-energy stunt, parkour jump, extreme save, or jaw-dropping physical feat. "
            "Reject low-energy clips."
        ),
        "category_id": "24",
        "viral_queries": [
            "insane stunt caught on camera",
            "crazy parkour jump moment",
            "1 in a million luck caught on camera",
            "extreme reflex save",
        ],
    },
    {
        "id": "instant_karma",
        "label": "Instant Karma Caught on Camera",
        "topic": "Ranking Instant Karma Moments That Hit Immediately",
        "vibe": "instant_karma",
        "vibe_rules": (
            "MUST show someone acting cocky, showboating, or doing something foolish followed immediately by instant karma."
        ),
        "category_id": "23",
        "viral_queries": [
            "instant karma caught on camera",
            "showboating gone wrong funny",
            "instant justice moment",
            "karma hit immediately clip",
        ],
    },
    {
        "id": "gym_fails",
        "label": "Gym Fails & Ego Lifting Chaos",
        "topic": "Ranking Gym Fails That Defy Physics",
        "vibe": "epic_fail",
        "vibe_rules": (
            "MUST take place in a gym or workout setting and show a funny workout blunder, treadmill fail, or ego-lifting fail."
        ),
        "category_id": "17",
        "viral_queries": [
            "funny gym fail caught on camera",
            "treadmill fail hilarious",
            "ego lifting fail gym",
            "workout gone wrong funny",
        ],
    },
    {
        "id": "basketball_highlights",
        "label": "Coldest Basketball & Ankle-Breaker Moments",
        "topic": "Ranking Coldest Basketball Moments Caught On Camera",
        "vibe": "sports_highlight",
        "vibe_rules": (
            "MUST show basketball action: a nasty ankle-breaker, poster dunk, buzzer beater, or hilarious basketball blunder."
        ),
        "category_id": "17",
        "viral_queries": [
            "ankle breaker basketball reaction",
            "insane basketball trick shot",
            "coldest basketball moment",
            "funny basketball fail",
        ],
    },
    {
        "id": "cute_moments",
        "label": "Wholesome & Cute Moments",
        "topic": "Ranking Wholesome Moments That Melted Everyone's Heart",
        "vibe": "cute_wholesome",
        "vibe_rules": (
            "MUST be genuinely adorable, wholesome, or heartwarming (cute baby/toddler reaction, wholesome surprise, or adorable pet antic)."
        ),
        "category_id": "15",
        "viral_queries": [
            "wholesome moment caught on camera",
            "cute toddler funny reaction",
            "adorable puppy reaction",
            "heartwarming surprise reaction",
        ],
    },
    {
        "id": "plot_twists",
        "label": "Unexpected Plot Twists",
        "topic": "Ranking Plot Twists Nobody Saw Coming",
        "vibe": "plot_twist",
        "vibe_rules": (
            "MUST have a clear setup followed by an unexpected twist ending in the final seconds."
        ),
        "category_id": "24",
        "viral_queries": [
            "unexpected ending funny short",
            "wait for the end plot twist",
            "never let them know your next move",
            "unexpected twist caught on camera",
        ],
    },
    {
        "id": "wedding_party_fails",
        "label": "Wedding & Party Disasters",
        "topic": "Ranking Party Moments That Turned Into Pure Chaos",
        "vibe": "epic_fail",
        "vibe_rules": (
            "MUST show a wedding, birthday, cake, or dance-floor celebration going hilariously wrong."
        ),
        "category_id": "23",
        "viral_queries": [
            "wedding fail funny moment",
            "dance floor fail caught on camera",
            "birthday cake fail hilarious",
            "party fail moment",
        ],
    },
    {
        "id": "work_fails",
        "label": "Bad Day at Work Blunders",
        "topic": "Ranking People Having The Worst Day At Work",
        "vibe": "epic_fail",
        "vibe_rules": (
            "MUST show a workplace, delivery, construction, or kitchen blunder caught on camera."
        ),
        "category_id": "23",
        "viral_queries": [
            "bad day at work fail",
            "delivery driver funny fail",
            "workplace blunder caught on camera",
            "construction funny fail",
        ],
    },
    {
        "id": "skills_200iq",
        "label": "200 IQ God-Tier Human Skills",
        "topic": "Ranking 200 IQ Moments That Look Like CGI",
        "vibe": "genius_skill",
        "vibe_rules": (
            "MUST show an extraordinary human skill, dad reflex save, or precision trick shot."
        ),
        "category_id": "24",
        "viral_queries": [
            "dad reflex save caught on camera",
            "200 iq moment caught on camera",
            "insane human skill viral",
            "impossible trick shot reaction",
        ],
    },
    {
        "id": "animals",
        "label": "Chaotic Pet & Animal Antics",
        "topic": "Ranking Unhinged Pet Moments Caught On Camera",
        "vibe": "hilarious",
        "vibe_rules": (
            "MUST show an animal or pet doing something genuinely chaotic, clumsy, or hilarious—NOT just sitting or eating calmly."
        ),
        "category_id": "15",
        "viral_queries": [
            "clumsy cat jump fail",
            "dog instant regret funny",
            "chaotic pet moment caught on camera",
            "funny animal fail reaction",
        ],
    },
]

ALIASES = {
    "fail": "epic_fails",
    "fails": "epic_fails",
    "epic fail": "epic_fails",
    "epic fails": "epic_fails",
    "epic_fails": "epic_fails",
    "football": "football_moments",
    "soccer": "football_moments",
    "football moments": "football_moments",
    "football_moments": "football_moments",
    "prank": "pranks",
    "pranks": "pranks",
    "embarrassing": "embarrassing_moments",
    "embarrassing moments": "embarrassing_moments",
    "embarassing": "embarrassing_moments",
    "embarassing moments": "embarrassing_moments",
    "awkward": "embarrassing_moments",
    "funny": "funny_moments",
    "funny moments": "funny_moments",
    "funny_moments": "funny_moments",
    "hilarious": "funny_moments",
    "comedy": "funny_moments",
    "wild": "wild_moments",
    "wild moments": "wild_moments",
    "wild_moments": "wild_moments",
    "crazy": "crazy_moments",
    "crazy moments": "crazy_moments",
    "crazy_moments": "crazy_moments",
    "stunts": "crazy_moments",
    "karma": "instant_karma",
    "instant karma": "instant_karma",
    "instant_karma": "instant_karma",
    "gym": "gym_fails",
    "gym fails": "gym_fails",
    "basketball": "basketball_highlights",
    "cute": "cute_moments",
    "cute moments": "cute_moments",
    "wholesome": "cute_moments",
    "twist": "plot_twists",
    "plot twist": "plot_twists",
    "plot twists": "plot_twists",
    "wedding": "wedding_party_fails",
    "party": "wedding_party_fails",
    "work": "work_fails",
    "200iq": "skills_200iq",
    "skills": "skills_200iq",
    "talents": "skills_200iq",
    "sport": "football_moments",
    "sports": "football_moments",
    "animals": "animals",
    "pets": "animals",
}


def _load_recent_subniches() -> List[str]:
    recent: List[str] = []
    for p in (DATA_DIR / "pending" / "state.json", DATA_DIR / "telegram_state.json"):
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                for item in data.get("recent_subniches") or []:
                    if item and item not in recent:
                        recent.append(str(item))
                last_niche = data.get("niche")
                if last_niche and last_niche not in recent:
                    recent.insert(0, str(last_niche))
            except Exception:
                pass
    return recent[:8]


def record_used_subniche(subniche_id: str) -> List[str]:
    recent = _load_recent_subniches()
    updated = [subniche_id] + [x for x in recent if x != subniche_id]
    updated = updated[:10]
    state_file = DATA_DIR / "telegram_state.json"
    try:
        current = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
        current["recent_subniches"] = updated
        state_file.write_text(json.dumps(current, indent=2), encoding="utf-8")
    except Exception:
        pass
    return updated


def for_today(
    niche: str = "",
    topic: str = "",
    target_platform: str = "youtube",
    exclude_subniches: Optional[List[str]] = None,
) -> Dict[str, Any]:
    picked = (niche or "").strip().lower()
    if picked in {"", "auto", "random", "rotate", "daily"}:
        recent = set(_load_recent_subniches()) | set(exclude_subniches or [])
        # Exclude 'animals' if it was used recently, and rotate across all fresh sub-niches based on hour+day seed
        available = [s for s in SUB_NICHES if s["id"] not in recent]
        if not available:
            available = [s for s in SUB_NICHES if s["id"] != "animals"]
        now = datetime.now(LAGOS_TZ)
        seed = now.timetuple().tm_yday * 7 + now.hour + now.minute
        chosen = dict(available[seed % len(available)])
    else:
        mapped_id = ALIASES.get(picked)
        if mapped_id:
            chosen = dict(next((s for s in SUB_NICHES if s["id"] == mapped_id), SUB_NICHES[0]))
        else:
            # Custom sub-niche entered by user (e.g. "barber pranks", "skateboarding fails", "ufc knockouts")
            clean = niche.strip()
            chosen = {
                "id": clean.lower().replace(" ", "_"),
                "label": f"{clean.title()} Moments",
                "topic": f"Ranking Top 5 {clean.title()} Moments",
                "vibe": clean.lower(),
                "vibe_rules": (
                    f"MUST genuinely show '{clean}' in action with high entertainment value. "
                    f"Reject any clip that does not clearly depict '{clean}'."
                ),
                "category_id": "23",
                "viral_queries": [
                    f"{clean} caught on camera",
                    f"{clean} viral moment",
                    f"funny {clean} reaction",
                    f"best {clean} short clip",
                ],
            }

    custom_topic = (topic or "").strip()
    if custom_topic and custom_topic.lower() not in {"top 5 funniest fails", "top 5 funniest moments"}:
        chosen["topic"] = custom_topic

    chosen["queries"] = list(chosen["viral_queries"])
    return chosen
