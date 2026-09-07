"""
FFmpeg helper module for video operations in the Odysseus Media Studio.
Provides asynchronous functions for video metadata extraction, frame extraction, and video concatenation.
"""

import os
import json
import asyncio
import logging
import shutil
import tempfile

logger = logging.getLogger(__name__)

def is_ffmpeg_available() -> bool:
    """Check whether ffmpeg and ffprobe are available on PATH."""
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

async def get_video_info(video_path: str) -> dict:
    """Read video metadata using ffprobe."""
    if not is_ffmpeg_available():
        logger.warning("ffprobe is not available")
        return {}

    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", video_path
    ]
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await process.communicate()
        
        if process.returncode != 0:
            logger.warning(f"ffprobe failed with return code {process.returncode}")
            return {}
            
        data = json.loads(stdout.decode('utf-8'))
        
        info = {}
        # Get duration from format
        format_info = data.get("format", {})
        if "duration" in format_info:
            info["duration"] = float(format_info["duration"])
            
        # Get video stream specific info
        streams = data.get("streams", [])
        video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        
        info["has_audio"] = any(st.get("codec_type") == "audio" for st in streams)

        if video_stream:
            if "width" in video_stream:
                info["width"] = int(video_stream["width"])
            if "height" in video_stream:
                info["height"] = int(video_stream["height"])
            if "codec_name" in video_stream:
                info["codec"] = video_stream["codec_name"]
            
            # FPS could be in r_frame_rate e.g. "30/1"
            fps_str = video_stream.get("r_frame_rate", "0/0")
            if fps_str != "0/0" and "/" in fps_str:
                num, den = fps_str.split("/")
                if int(den) != 0:
                    info["fps"] = float(num) / float(den)
                    
        return info
    except Exception as e:
        logger.warning(f"Error getting video info for {video_path}: {e}")
        return {}

async def extract_frame_at(video_path: str, seconds: float) -> bytes:
    """Extract a frame at a specific time position as PNG bytes."""
    if not is_ffmpeg_available():
        raise RuntimeError("ffmpeg is not available")
        
    cmd = [
        "ffmpeg", "-ss", str(seconds), "-i", video_path,
        "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"
    ]
    
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    
    if process.returncode != 0:
        error_msg = stderr.decode('utf-8')
        raise RuntimeError(f"FFmpeg frame extraction failed: {error_msg}")
        
    return stdout

async def extract_last_frame(video_path: str) -> bytes:
    """Extract the very last frame of a video as PNG bytes."""
    info = await get_video_info(video_path)
    if not info or "duration" not in info:
        raise RuntimeError("Could not determine video duration")
        
    duration = info["duration"]
    target_time = max(0.0, duration - 0.1)
    
    return await extract_frame_at(video_path, target_time)

async def _streams_are_concat_compatible(path_1: str, path_2: str) -> bool:
    """Whether the two files can be joined with stream copy.

    The concat demuxer with ``-c copy`` requires matching codec, resolution and
    frame rate. When they differ it does not reliably fail — it often exits 0
    and writes a file that stalls or truncates at the join. So compare first
    and only take the copy path when the parameters actually line up.
    """
    a, b = await asyncio.gather(get_video_info(path_1), get_video_info(path_2))
    if not a or not b:
        return False
    if a.get("codec") != b.get("codec"):
        return False
    if bool(a.get("has_audio")) != bool(b.get("has_audio")):
        return False
    if (a.get("width"), a.get("height")) != (b.get("width"), b.get("height")):
        return False
    fps_a, fps_b = a.get("fps"), b.get("fps")
    if fps_a and fps_b and abs(fps_a - fps_b) > 0.05:
        return False
    return True


async def _run_ffmpeg(cmd: list) -> tuple:
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await process.communicate()
    return process.returncode, stderr.decode("utf-8", "replace")


async def concatenate_videos(path_1: str, path_2: str, output_path: str) -> str:
    """Concatenate two videos, re-encoding when stream copy would not be safe.

    Extension segments regularly come back from the model with a different
    resolution or frame rate than the source, which is exactly the case
    ``-c copy`` mishandles silently. The concat *filter* re-encodes and
    normalises instead, so it is the fallback whenever the streams differ (and
    the retry path when a copy attempt fails outright).
    """
    if not is_ffmpeg_available():
        raise RuntimeError("ffmpeg is not available")

    if await _streams_are_concat_compatible(path_1, path_2):
        fd, list_file_path = tempfile.mkstemp(suffix=".txt", text=True)
        try:
            with os.fdopen(fd, 'w') as f:
                f.write(f"file '{os.path.abspath(path_1)}'\n")
                f.write(f"file '{os.path.abspath(path_2)}'\n")

            rc, err = await _run_ffmpeg([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", list_file_path, "-c", "copy", output_path
            ])
            if rc == 0:
                return output_path
            logger.warning("Stream-copy concat failed, re-encoding instead: %s", err[-500:])
        finally:
            if os.path.exists(list_file_path):
                os.remove(list_file_path)
    else:
        logger.info("Concat inputs differ in codec/size/fps — re-encoding")

    # Re-encode path: the concat filter scales the second input to the first
    # one's frame size and produces a single consistent stream.
    info_a, info_b = await asyncio.gather(get_video_info(path_1), get_video_info(path_2))
    width, height = info_a.get("width"), info_a.get("height")
    fps = info_a.get("fps") or 30
    scale = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
        if width and height else "null"
    )

    # Keep audio when both inputs have it. Dropping it would silently strip the
    # soundtrack from models generating with generate_audio=true.
    keep_audio = bool(info_a.get("has_audio")) and bool(info_b.get("has_audio"))

    if keep_audio:
        filter_complex = (
            f"[0:v]{scale},fps={fps}[v0];"
            f"[1:v]{scale},fps={fps}[v1];"
            f"[0:a]aresample=48000,asetpts=N/SR/TB[a0];"
            f"[1:a]aresample=48000,asetpts=N/SR/TB[a1];"
            f"[v0][a0][v1][a1]concat=n=2:v=1:a=1[outv][outa]"
        )
        maps = ["-map", "[outv]", "-map", "[outa]", "-c:a", "aac", "-b:a", "192k"]
    else:
        filter_complex = (
            f"[0:v]{scale},fps={fps}[v0];"
            f"[1:v]{scale},fps={fps}[v1];"
            f"[v0][v1]concat=n=2:v=1:a=0[outv]"
        )
        maps = ["-map", "[outv]"]

    rc, err = await _run_ffmpeg([
        "ffmpeg", "-y", "-i", path_1, "-i", path_2,
        "-filter_complex", filter_complex, *maps,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        output_path
    ])
    if rc != 0:
        raise RuntimeError(f"FFmpeg concatenation failed: {err}")

    return output_path
