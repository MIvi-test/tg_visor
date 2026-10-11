"""Bounded local still-image decoding, including legacy animated Telegram photos."""
import io
from pathlib import Path
import shutil
import subprocess

from PIL import Image, ImageOps, UnidentifiedImageError


def load_image(path: Path, max_side: int = 1600):
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("image file exceeds 64 MiB")
    try:
        with Image.open(path) as source:
            if source.width * source.height > 40_000_000:
                raise ValueError("image exceeds 40 megapixels")
            image = ImageOps.exif_transpose(source).convert("RGB")
    except UnidentifiedImageError:
        if path.suffix.lower() != ".mp4":
            raise
        executable = shutil.which("ffmpeg")
        if executable is None:
            raise ValueError("legacy animated photos require local ffmpeg") from None
        # Force MOV/MP4 parsing; external references and network protocols are disabled.
        result = subprocess.run([
            executable, "-nostdin", "-hide_banner", "-loglevel", "error",
            "-threads", "1", "-filter_threads", "1", "-protocol_whitelist", "file,pipe",
            "-f", "mov", "-enable_drefs", "0", "-use_absolute_path", "0", "-i", str(path),
            "-vf", f"scale={max_side}:{max_side}:force_original_aspect_ratio=decrease",
            "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1",
        ], capture_output=True, timeout=30, check=False)
        if result.returncode or not result.stdout or len(result.stdout) > 32 * 1024 * 1024:
            raise ValueError("cannot decode legacy animated photo") from None
        with Image.open(io.BytesIO(result.stdout)) as frame:
            image = frame.convert("RGB")
    image.thumbnail((max_side, max_side))
    return image
