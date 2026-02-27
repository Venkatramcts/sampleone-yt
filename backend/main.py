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

# Allow Node.js frontend to communicate with Python backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In production, change "*" to "http://localhost:3000"
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure a temporary directory exists for downloads
DOWNLOAD_DIR = "temp_downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Pydantic model for batch requests
class BatchRequest(BaseModel):
    urls: list[str]

def cleanup_file(filepath: str):
    """Deletes the file/folder after it has been sent to the user."""
    try:
        if os.path.isfile(filepath):
            os.remove(filepath)
        elif os.path.isdir(filepath):
            shutil.rmtree(filepath)
    except Exception as e:
        print(f"Error cleaning up {filepath}: {e}")

async def download_media(url: str, is_audio: bool) -> str:
    """Helper function to download media using yt-dlp."""
    run_id = str(uuid.uuid4())
    output_template = f"{DOWNLOAD_DIR}/{run_id}/%(title)s.%(ext)s"
    
    ydl_opts = {
        'outtmpl': output_template,
        'quiet': True,
        'no_warnings': True,
    }

    if is_audio:
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
    else:
        # Get best video and best audio, then merge them (requires ffmpeg)
        ydl_opts.update({
            'format': 'bestvideo+bestaudio/best',
            'merge_output_format': 'mp4',
        })

    def run_ytdlp():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
            
    # Run in a separate thread to prevent blocking the async server
    await asyncio.to_thread(run_ytdlp)
    
    # Find the downloaded file
    folder_path = f"{DOWNLOAD_DIR}/{run_id}"
    downloaded_files = os.listdir(folder_path)
    if not downloaded_files:
        raise HTTPException(status_code=500, detail="Download failed.")
    
    return os.path.join(folder_path, downloaded_files[0])

@app.get("/api/download/audio")
async def download_audio(url: str, background_tasks: BackgroundTasks):
    try:
        filepath = await download_media(url, is_audio=True)
        filename = os.path.basename(filepath)
        # Schedule cleanup after the file is sent
        background_tasks.add_task(cleanup_file, os.path.dirname(filepath)) 
        return FileResponse(path=filepath, filename=filename, media_type='audio/mpeg')
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/download/video")
async def download_video(url: str, background_tasks: BackgroundTasks):
    try:
        filepath = await download_media(url, is_audio=False)
        filename = os.path.basename(filepath)
        background_tasks.add_task(cleanup_file, os.path.dirname(filepath))
        return FileResponse(path=filepath, filename=filename, media_type='video/mp4')
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/channel-info")
async def get_channel_info(url: str):
    ydl_opts = {'extract_flat': True, 'quiet': True}
    def fetch_info():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=False)
            
    try:
        info = await asyncio.to_thread(fetch_info)
        videos = [{'title': e.get('title'), 'url': e.get('url')} for e in info.get('entries', []) if e.get('url')]
        return {"status": "success", "channel_name": info.get('title'), "videos": videos}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/download/batch")
async def download_batch(request: BatchRequest, background_tasks: BackgroundTasks):
    run_id = str(uuid.uuid4())
    batch_dir = f"{DOWNLOAD_DIR}/{run_id}"
    os.makedirs(batch_dir, exist_ok=True)
    
    ydl_opts = {
        'outtmpl': f"{batch_dir}/%(title)s.%(ext)s",
        'format': 'bestaudio/best', # Defaulting batch to audio for speed in this MVP
        'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3'}],
        'quiet': True,
    }

    def run_batch():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download(request.urls)
        # Zip the directory
        shutil.make_archive(batch_dir, 'zip', batch_dir)
        
    try:
        await asyncio.to_thread(run_batch)
        zip_path = f"{batch_dir}.zip"
        # Cleanup both the folder and the zip file eventually
        background_tasks.add_task(cleanup_file, batch_dir)
        background_tasks.add_task(cleanup_file, zip_path)
        return FileResponse(path=zip_path, filename="Batch_Download.zip", media_type='application/zip')
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))