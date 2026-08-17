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

async def concatenate_videos(path_1: str, path_2: str, output_path: str) -> str:
    """Concatenate two videos using FFmpeg's concat demuxer."""
    if not is_ffmpeg_available():
        raise RuntimeError("ffmpeg is not available")
        
    # Create temp list file
    fd, list_file_path = tempfile.mkstemp(suffix=".txt", text=True)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(f"file '{os.path.abspath(path_1)}'\n")
            f.write(f"file '{os.path.abspath(path_2)}'\n")
            
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", 
            "-i", list_file_path, "-c", "copy", output_path
        ]
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await process.communicate()
        
        if process.returncode != 0:
            error_msg = stderr.decode('utf-8')
            raise RuntimeError(f"FFmpeg concatenation failed: {error_msg}")
            
        return output_path
    finally:
        # Cleanup
        if os.path.exists(list_file_path):
            os.remove(list_file_path)
