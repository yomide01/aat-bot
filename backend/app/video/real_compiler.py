import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from ..core.config import DATA_DIR
from ..core.logger import logger
from ..video.probe import has_audio as probe_has_audio


class ViralRealCompiler:
    """
    Professional Creator-Style YouTube Shorts Ranking & Compilation Engine.
    - 100% Clean Lower Frame: NO bottom description box and NO on-screen creator credit clutter!
    - Full-screen 1080x1920 vertical framing with subtle cinema color grade & sharpness polish
    - Clean Top Hook Banner + Left-Side Progressive 1-to-5 Ranking List with drop-shadow typography
    - Climax-centered dynamic trimming with smooth audio fades, subtle transition tick, and -14.0 LUFS mastering
    - Stripped container metadata (-map_metadata -1 -map_chapters -1)
    """

    WIDTH = 1080
    HEIGHT = 1920

    @staticmethod
    def _load_font(size: int, bold: bool = True) -> ImageFont.ImageFont:
        font_candidates = [
            Path("C:/Windows/Fonts/arialbd.ttf"),
            Path("C:/Windows/Fonts/segoeuib.ttf"),
            Path("C:/Windows/Fonts/impact.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
            Path("C:/Windows/Fonts/arial.ttf"),
        ]
        for p in font_candidates:
            if p.exists():
                try:
                    return ImageFont.truetype(str(p), size)
                except Exception:
                    pass
        return ImageFont.load_default()

    @staticmethod
    def _probe_dimensions(clip_path: Path) -> Tuple[int, int]:
        try:
            res = subprocess.run(
                [
                    "ffprobe", "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0",
                    str(clip_path),
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            parts = res.stdout.strip().split("x")
            if len(parts) == 2:
                return int(parts[0]), int(parts[1])
        except Exception:
            pass
        return 1080, 1920

    @classmethod
    def render_top_banner(
        cls,
        main_title: str,
        rank_number: int,
        total_items: int = 5,
        subtitle: str = "",
        commentary: str = "",
        credit: str = "",
        rank_labels: Optional[Dict[int, str]] = None,
        output_image: Optional[Path] = None,
    ) -> Path:
        """
        Renders a clean, human-editor 1080x1920 transparent overlay:
        1. Sleek Top Hook Banner (Y: 98 - 182)
        2. Left-Side Progressive 1-to-5 Vertical Ranking List (X: 46, Y: 230 - 600)
           with soft drop shadow and bold stroke.
        3. ZERO bottom overlays — keeps the lower video canvas 100% clean and unobstructed!
        """
        if output_image is None:
            output_image = DATA_DIR / f"banner_rank_{rank_number}.png"

        labels = rank_labels or {}
        base = Image.new("RGBA", (cls.WIDTH, cls.HEIGHT), (0, 0, 0, 0))
        shadow_layer = Image.new("RGBA", (cls.WIDTH, cls.HEIGHT), (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow_layer)
        draw = ImageDraw.Draw(base)

        clean_title = (main_title or "WAIT FOR #1").strip()
        title_font_size = 44 if len(clean_title) <= 22 else (38 if len(clean_title) <= 28 else 32)
        font_title = cls._load_font(title_font_size, bold=True)
        font_tower_num = cls._load_font(46, bold=True)
        font_tower_lbl = cls._load_font(34, bold=True)

        # 1. Clean Top Hook Banner (White high-contrast creator style with dark text or sleek dark pill)
        pill_margin = 70
        shadow_draw.rounded_rectangle(
            (pill_margin + 4, 102, cls.WIDTH - pill_margin + 4, 188),
            radius=22,
            fill=(0, 0, 0, 160),
        )
        draw.rounded_rectangle(
            (pill_margin, 96, cls.WIDTH - pill_margin, 182),
            radius=22,
            fill=(252, 252, 255, 242),
            outline=(15, 18, 26, 255),
            width=3,
        )
        draw.text(
            (cls.WIDTH // 2, 139),
            clean_title[:32],
            font=font_title,
            fill=(12, 14, 20, 255),
            anchor="mm",
        )

        # 2. Left-Side Progressive 1-to-5 Vertical Ranking List (Y: 230 to 590)
        rank_colors = {
            1: (255, 215, 0, 255),    # Gold (#1)
            2: (0, 229, 255, 255),    # Cyan (#2)
            3: (65, 240, 130, 255),   # Neon Green (#3)
            4: (255, 160, 20, 255),   # Orange (#4)
            5: (255, 70, 95, 255),    # Crimson (#5)
        }
        base_y = 232
        row_h = 72
        for r_num in range(1, total_items + 1):
            ry = base_y + (r_num - 1) * row_h
            num_color = rank_colors.get(r_num, (255, 255, 255, 255))
            is_current = (r_num == rank_number)
            is_revealed = (r_num >= rank_number)

            # Unrevealed ranks stay blank next to the number (just like real human ranking templates!)
            lbl_text = labels.get(r_num, "").strip()[:20] if is_revealed else ""

            if is_current and lbl_text:
                box_w = min(520, max(210, 125 + len(lbl_text) * 18))
                shadow_draw.rounded_rectangle(
                    (36, ry - 2, 36 + box_w, ry + 54),
                    radius=14,
                    fill=(0, 0, 0, 150),
                )
                draw.rounded_rectangle(
                    (32, ry - 6, 32 + box_w, ry + 50),
                    radius=14,
                    fill=(14, 16, 24, 195),
                    outline=num_color,
                    width=3,
                )

            # Drop shadow for rank number
            shadow_draw.text((54, ry + 26), f"{r_num}.", font=font_tower_num, fill=(0, 0, 0, 200), anchor="lm")
            # Rank Number (1. to 5.)
            draw.text(
                (50, ry + 22),
                f"{r_num}.",
                font=font_tower_num,
                fill=num_color,
                anchor="lm",
                stroke_width=4,
                stroke_fill=(0, 0, 0, 255),
            )

            if lbl_text:
                shadow_draw.text((116, ry + 27), lbl_text, font=font_tower_lbl, fill=(0, 0, 0, 200), anchor="lm")
                txt_fill = (255, 255, 255, 255) if is_current else (235, 240, 248, 235)
                draw.text(
                    (112, ry + 23),
                    lbl_text,
                    font=font_tower_lbl,
                    fill=txt_fill,
                    anchor="lm",
                    stroke_width=3,
                    stroke_fill=(0, 0, 0, 255),
                )

        blurred_shadow = shadow_layer.filter(ImageFilter.GaussianBlur(radius=4))
        final_img = Image.alpha_composite(blurred_shadow, base)
        final_img.save(output_image, "PNG")
        return output_image

    @classmethod
    def clip_has_audio(cls, clip_path: Path) -> bool:
        try:
            return probe_has_audio(clip_path)
        except Exception:
            return False

    @classmethod
    def compile_moment_segment(
        cls,
        clip_path: Path,
        overlay_banner: Path,
        output_segment: Path,
        start_time_sec: float = 0.0,
        duration_sec: float = 5.0,
    ) -> Path:
        has_audio = cls.clip_has_audio(clip_path)
        w, h = cls._probe_dimensions(clip_path)
        is_vertical = h >= int(w * 0.82)

        if is_vertical:
            # Full-screen 1080x1920 vertical canvas with crisp contrast/sharpness & subtle entry flash
            filter_v = (
                "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,eq=contrast=1.05:saturation=1.09,"
                "unsharp=3:3:0.35:3:3:0.0,fade=t=in:st=0:d=0.10[vid];"
                "[vid][1:v]overlay=0:0,fps=30[outv]"
            )
        else:
            # Cinema center framing over soft ambient glass background for wide clips
            filter_v = (
                "[0:v]split=2[bgsrc][fgsrc];"
                "[bgsrc]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
                "boxblur=28:6,eq=brightness=-0.15:saturation=1.15[bg];"
                "[fgsrc]scale=1080:1480:force_original_aspect_ratio=decrease,"
                "eq=contrast=1.05:saturation=1.09,unsharp=3:3:0.35:3:3:0.0[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2,fade=t=in:st=0:d=0.10[vid];"
                "[vid][1:v]overlay=0:0,fps=30[outv]"
            )

        fade_out_st = max(0.1, duration_sec - 0.07)
        if has_audio:
            filter_a = (
                f"afade=t=in:st=0:d=0.06,afade=t=out:st={fade_out_st:.2f}:d=0.06,"
                "loudnorm=I=-14:TP=-1.0:LRA=11"
            )
            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{start_time_sec:.2f}",
                "-t", f"{duration_sec:.2f}",
                "-i", str(clip_path),
                "-loop", "1",
                "-t", f"{duration_sec:.2f}",
                "-i", str(overlay_banner),
                "-filter_complex", filter_v,
                "-map", "[outv]",
                "-map", "0:a:0",
                "-af", filter_a,
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "19",
                "-r", "30",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "192k",
                "-ar", "44100",
                "-ac", "2",
                "-map_metadata", "-1",
                "-map_chapters", "-1",
                str(output_segment),
            ]
        else:
            cmd = [
                "ffmpeg", "-y",
                "-ss", f"{start_time_sec:.2f}",
                "-t", f"{duration_sec:.2f}",
                "-i", str(clip_path),
                "-loop", "1",
                "-t", f"{duration_sec:.2f}",
                "-i", str(overlay_banner),
                "-f", "lavfi",
                "-t", f"{duration_sec:.2f}",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-filter_complex", filter_v,
                "-map", "[outv]",
                "-map", "2:a:0",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "19",
                "-r", "30",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "192k",
                "-ar", "44100",
                "-ac", "2",
                "-map_metadata", "-1",
                "-map_chapters", "-1",
                str(output_segment),
            ]

        logger.info(f"Rendering ranked segment -> {output_segment.name} ({duration_sec:.1f}s)")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            logger.error(f"Segment compile failed: {res.stderr[-300:]}")
            raise RuntimeError(f"FFmpeg segment render failed: {res.stderr[-300:]}")
        return output_segment

    @classmethod
    def assemble_full_ranking_video(
        cls,
        title: str,
        clips: List[Dict[str, Any]],
        output_path: Path,
    ) -> Path:
        segment_files: List[Path] = []
        total_items = len(clips)

        # Build the map of rank -> short 2-3 word label for the progressive 1-5 ranking tower
        rank_labels: Dict[int, str] = {}
        for item in clips:
            r_num = int(item.get("rank", 1))
            lbl = (
                item.get("rank_label")
                or item.get("punchline")
                or item.get("what_happens")
                or item.get("title")
                or f"Moment #{r_num}"
            )
            rank_labels[r_num] = str(lbl).strip()[:20]

        for idx, item in enumerate(clips):
            rank_num = int(item.get("rank", total_items - idx))
            clip_file = Path(item["filepath"])
            start_sec = float(item.get("start_sec", 0.0))
            dur_sec = float(item.get("duration", 5.0))

            banner_png = cls.render_top_banner(
                main_title=title,
                rank_number=rank_num,
                total_items=total_items,
                rank_labels=rank_labels,
                output_image=DATA_DIR / f"banner_rank_{idx}_{rank_num}.png",
            )

            seg_out = DATA_DIR / f"rank_segment_{idx}.mp4"
            cls.compile_moment_segment(
                clip_path=clip_file,
                overlay_banner=banner_png,
                output_segment=seg_out,
                start_time_sec=start_sec,
                duration_sec=dur_sec,
            )
            segment_files.append(seg_out)

        concat_list = DATA_DIR / "ranking_concat.txt"
        with open(concat_list, "w", encoding="utf-8") as f:
            for seg in segment_files:
                safe_path = str(seg.resolve()).replace("\\", "/")
                f.write(f"file '{safe_path}'\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "19",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            "-map_metadata", "-1",
            "-map_chapters", "-1",
            str(output_path),
        ]
        logger.info(f"Concatenating {len(segment_files)} ranked segments into {output_path.name}...")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode != 0:
            logger.error(f"Final concat failed: {res.stderr[-300:]}")
            raise RuntimeError("Final video assembly failed")

        for seg in segment_files:
            seg.unlink(missing_ok=True)
        for idx, item in enumerate(clips):
            rank_num = int(item.get("rank", total_items - idx))
            (DATA_DIR / f"banner_rank_{idx}_{rank_num}.png").unlink(missing_ok=True)
        concat_list.unlink(missing_ok=True)

        logger.info(f"[bold green]Compiled Master YouTube Short Ready:[/bold green] {output_path}")
        return output_path
