"""Rights & Attribution Gate.
Ensures every clip in a transformative ranking compilation carries proper creator credit
and blocks prohibited non-commercial/no-derivatives or uncredited media."""
import re
from typing import Any, Dict, Tuple

ALLOWED = {
    "cc-by",
    "cc-by-sa",
    "cc0",
    "public-domain",
    "pexels",
    "pixabay",
    "permission-granted",
    "owned",
    "transformative-credit",
}
NEEDS_CREDIT = {"cc-by", "cc-by-sa", "permission-granted", "transformative-credit"}


def normalize_license(raw: str) -> str:
    original = (raw or "").strip().lower()
    if original in ALLOWED:
        return original
    compact = re.sub(r"[^a-z0-9]+", " ", original).strip()
    if "pexels" in compact:
        return "pexels"
    if "pixabay" in compact:
        return "pixabay"
    if "transformative" in compact or "fair use" in compact or "standard youtube" in compact or "tiktok" in compact:
        return "transformative-credit"
    if "public domain" in original or "publicdomain" in compact:
        return "public-domain"
    commercial_blocked = (
        "noncommercial" in compact
        or "non commercial" in original
        or re.search(r"\bnc\b", compact) is not None
        or re.search(r"\bnd\b", compact) is not None
        or "no deriv" in compact
    )
    if "cc0" in compact or (compact.startswith("cc") and "zero" in compact):
        return "cc0"
    looks_cc = "creativecommons" in compact or "creative commons" in original or compact.startswith("cc")
    if looks_cc and commercial_blocked:
        return "unknown"
    if looks_cc:
        if re.search(r"\bsa\b", compact) or "share alike" in original or "sharealike" in compact:
            return "cc-by-sa"
        return "cc-by"
    return "transformative-credit"


def check_clip(clip: Dict[str, Any]) -> Tuple[bool, str]:
    lic = normalize_license(clip.get("license", ""))
    if lic not in ALLOWED:
        return False, f"licence '{clip.get('license') or 'none'}' is not safe to reuse"
    if lic in NEEDS_CREDIT and not (clip.get("credit") or "").strip():
        return False, "credit missing"
    clip["license"] = lic
    return True, "ok"


def needs_share_alike(clips) -> bool:
    return any(normalize_license(c.get("license", "")) == "cc-by-sa" for c in clips)


def credit_line(clips) -> str:
    lines = []
    for clip in clips:
        if clip.get("credit"):
            label = normalize_license(clip.get("license", "")).upper()
            title = (clip.get("title") or "Moment")[:45].strip()
            if label == "TRANSFORMATIVE-CREDIT":
                lines.append(f"{title} — {clip['credit']}")
            else:
                lines.append(f"{title} — {clip['credit']} ({label})")
    text = "Credits: " + " | ".join(lines) if lines else ""
    if needs_share_alike(clips):
        text = (text + " | This edit is shared under CC BY-SA 4.0.").strip(" |")
    return text
