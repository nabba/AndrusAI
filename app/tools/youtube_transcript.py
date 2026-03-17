from crewai.tools import tool
from youtube_transcript_api import YouTubeTranscriptApi
import re


@tool("get_youtube_transcript")
def get_youtube_transcript(url_or_id: str) -> str:
    """
    Extract the transcript of a YouTube video.
    Accepts full YouTube URL or bare video ID.
    Returns plain text transcript, max 12000 chars.
    """
    # Extract video ID from URL if needed
    match = re.search(r"(?:v=|youtu\.be/)([\w-]{11})", url_or_id)
    video_id = match.group(1) if match else url_or_id.strip()

    try:
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
        text = " ".join(entry["text"] for entry in transcript_list)
        return text[:12000]
    except Exception as e:
        return f"Could not retrieve transcript: {str(e)}"
