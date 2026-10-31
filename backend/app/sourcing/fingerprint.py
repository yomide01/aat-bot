"""Visual fingerprints for clips (perceptual dHash of sampled frames).

Why: the dedup vault matched only TikTok IDs / URLs / creator+duration. The same footage re-uploaded by another
account (or re-encoded/trimmed) has a different ID, so it slipped through as a "new" clip. A fingerprint of what
the clip LOOKS like catches those repeats, independent of who posted it or how it was trimmed.

Stored in the vault as 'vh:<16 hex chars>' keys (one per sampled frame).
"""
import subprocess
from typing import Iterable, List, Set

FRAME_BYTES = 72  # 9x8 grayscale pixels
MAX_DIST = 6      # bits (of 64) that may differ for two frames to count as "the same picture"
MIN_HITS = 3      # how many frames must match before we call two clips the same footage


def _duration(path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def frame_hashes(path, n: int = 16) -> List[str]:
    """Up to n dHashes spread evenly across the clip. Flat frames (black/solid) are skipped: they look identical
    in unrelated clips and would cause false matches."""
    dur = _duration(path)
    if dur <= 0:
        return []
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"fps={n / dur:.4f},scale=9:8:flags=area,format=gray",
           "-frames:v", str(n), "-f", "rawvideo", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=90).stdout
    except Exception:
        return []
    hashes: List[str] = []
    for i in range(0, len(raw) - FRAME_BYTES + 1, FRAME_BYTES):
        px = raw[i:i + FRAME_BYTES]
        mean = sum(px) / FRAME_BYTES
        if sum((p - mean) ** 2 for p in px) / FRAME_BYTES < 25:  # std < 5: flat frame
            continue
        bits = 0
        for r in range(8):
            row = px[r * 9:(r + 1) * 9]
            for c in range(8):
                bits = (bits << 1) | (1 if row[c] > row[c + 1] else 0)
        hashes.append(f"{bits:016x}")
    return hashes


def keys(hashes: Iterable[str]) -> List[str]:
    return ["vh:" + h for h in hashes]


def vault_hashes(vault: Iterable[str]) -> Set[int]:
    return {int(k[3:], 16) for k in vault if isinstance(k, str) and k.startswith("vh:") and len(k) == 19}


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def is_repeat(hashes: List[str], known: Set[int], max_dist: int = MAX_DIST, min_hits: int = MIN_HITS) -> bool:
    """True if enough frames of this clip look like frames we have already used."""
    if len(hashes) < 3 or not known:
        return False  # too little picture information to judge: do not guess
    hits = sum(1 for h in hashes if any(_hamming(int(h, 16), k) <= max_dist for k in known))
    return hits >= min(min_hits, len(hashes))
