import os
import uuid
import shutil
import asyncio
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import yt_dlp

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DOWNLOAD_DIR = "temp_downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
FFMPEG_PATH = r'C:\ffmpeg\bin\ffmpeg.exe'

# The exact quality text mapping you requested
QUALITY_MAP = {
    2160: "2160p (4K): 256–512 kbps",
    1440: "1440p (2K): 256–512 kbps",
    1080: "1080p: 192–384 kbps",
    720: "720p: 192–384 kbps",
    480: "480p: 128–192 kbps",
    360: "360p: 64–128 kbps",
    240: "240p: 48–64 kbps",
    144: "144p: 24–48 kbps"
}

class BatchRequest(BaseModel):
    urls: list[str]
    type: str     # 'audio' or 'video'
    quality: str  # e.g., '1080', '720', or '192'

def cleanup_file(filepath: str):
    try:
        if os.path.isfile(filepath): os.remove(filepath)
        elif os.path.isdir(filepath): shutil.rmtree(filepath)
    except Exception as e:
        print(f"Error cleaning up {filepath}: {e}")

# NEW ENDPOINT: Fetch specific available qualities for a single video
@app.get("/api/info")
async def get_video_info(url: str):
    ydl_opts = {'quiet': True, 'ffmpeg_location': FFMPEG_PATH}
    def fetch():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
            
    try:
        info = await asyncio.to_thread(fetch)
        
        # Extract unique video heights available for this specific video
        available_heights = set()
        for f in info.get('formats', []):
            if f.get('vcodec') != 'none' and f.get('height'):
                # Only keep standard heights we have mapped
                if f.get('height') in QUALITY_MAP:
                    available_heights.add(f.get('height'))
                    
        # Sort descending and take the top 5 maximum available
        sorted_heights = sorted(list(available_heights), reverse=True)[:5]
        
        # Format for the frontend
        video_options = [{"value": h, "label": QUALITY_MAP[h]} for h in sorted_heights]
        
        # If no standard formats found, provide a default
        if not video_options:
            video_options = [{"value": 720, "label": "720p (Default Best)"}]

        # Standard Audio Bitrate Options
        audio_options = [
            {"value": 320, "label": "320 kbps (Studio Quality)"},
            {"value": 192, "label": "192 kbps (High Quality)"},
            {"value": 128, "label": "128 kbps (Standard Quality)"},
            {"value": 64, "label": "64 kbps (Low Quality - Voice only)"}
        ]

        return {"status": "success", "video_options": video_options, "audio_options": audio_options}
    except Exception as e:
        return {"status": "error", "message": str(e)}

async def download_media(url: str, is_audio: bool, quality: str) -> str:
    run_id = str(uuid.uuid4())
    output_template = f"{DOWNLOAD_DIR}/{run_id}/%(title)s.%(ext)s"
    
    ydl_opts = {
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
        'ffmpeg_location': FFMPEG_PATH,
    }

    if is_audio:
        # Request audio with bitrate <= requested quality, fallback to best available
        ydl_opts.update({
            'format': f'bestaudio[abr<={quality}]/bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': quality,
            }],
        })
    else:
        # Request video height <= requested quality, fallback to best available
        ydl_opts.update({
            'format': f'bestvideo[height<={quality}]+bestaudio/best',
            'merge_output_format': 'mp4',
        })

    def run_ytdlp():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
    await asyncio.to_thread(run_ytdlp)
    
    folder_path = f"{DOWNLOAD_DIR}/{run_id}"
    downloaded_files = os.listdir(folder_path)
    if not downloaded_files: raise HTTPException(status_code=500, detail="Download failed.")
    return os.path.join(folder_path, downloaded_files[0])

@app.get("/api/download/audio")
async def download_audio(url: str, quality: str, background_tasks: BackgroundTasks):
    try:
        filepath = await download_media(url, is_audio=True, quality=quality)
        background_tasks.add_task(cleanup_file, os.path.dirname(filepath)) 
        return FileResponse(path=filepath, filename=os.path.basename(filepath), media_type='audio/mpeg')
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/download/video")
async def download_video(url: str, quality: str, background_tasks: BackgroundTasks):
    try:
        filepath = await download_media(url, is_audio=False, quality=quality)
        background_tasks.add_task(cleanup_file, os.path.dirname(filepath))
        return FileResponse(path=filepath, filename=os.path.basename(filepath), media_type='video/mp4')
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/channel-info")
async def get_channel_info(url: str):
    if "@" in url and "youtube.com" in url:
        if "/videos" not in url and "/shorts" not in url and "/streams" not in url:
            url = url.split("?")[0] + "/videos"

    ydl_opts = {'extract_flat': True, 'quiet': True, 'ffmpeg_location': FFMPEG_PATH}
    def fetch_info():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
            
    try:
        info = await asyncio.to_thread(fetch_info)
        videos = []
        for e in info.get('entries', []):
            vid_url = e.get('url')
            if not vid_url and e.get('id'): vid_url = f"https://www.youtube.com/watch?v={e.get('id')}"
            if vid_url: videos.append({'title': e.get('title', 'Unknown Title'), 'url': vid_url})
        return {"status": "success", "channel_name": info.get('title', 'Channel'), "videos": videos}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/download/batch")
async def download_batch(request: BatchRequest, background_tasks: BackgroundTasks):
    run_id = str(uuid.uuid4())
    batch_dir = f"{DOWNLOAD_DIR}/{run_id}"
    os.makedirs(batch_dir, exist_ok=True)
    
    ydl_opts = {
        'outtmpl': f"{batch_dir}/%(title)s.%(ext)s",
        'quiet': True,
        'ffmpeg_location': FFMPEG_PATH, 
    }

    if request.type == 'audio':
        ydl_opts.update({
            'format': f'bestaudio[abr<={request.quality}]/bestaudio/best',
            'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3', 'preferredquality': request.quality}],
        })
    else:
        ydl_opts.update({
            'format': f'bestvideo[height<={request.quality}]+bestaudio/best',
            'merge_output_format': 'mp4',
        })

    def run_batch():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download(request.urls)
        shutil.make_archive(batch_dir, 'zip', batch_dir)
        
    try:
        await asyncio.to_thread(run_batch)
        zip_path = f"{batch_dir}.zip"
        background_tasks.add_task(cleanup_file, batch_dir)
        background_tasks.add_task(cleanup_file, zip_path)
        return FileResponse(path=zip_path, filename="Batch_Download.zip", media_type='application/zip')
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))