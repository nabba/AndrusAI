from crewai.tools import tool
from youtube_transcript_api import YouTubeTranscriptApi
import logging
import re

logger = logging.getLogger(__name__)

# youtube-transcript-api v1.x uses instance methods: api.fetch(), api.list()
_api = YouTubeTranscriptApi()


def _extract_video_id(url_or_id: str) -> str | None:
    """Extract an 11-char YouTube video ID from various URL formats."""
    url_or_id = url_or_id[:300].strip()
    # Patterns: youtube.com/watch?v=ID, youtu.be/ID, youtube.com/embed/ID, youtube.com/v/ID
    # Must handle trailing ?si=..., &feature=..., etc.
    match = re.search(r"(?:v=|youtu\.be/|embed/|/v/)([\w-]{11})", url_or_id)
    if match:
        return match.group(1)
    # Maybe it's already a bare ID
    clean = url_or_id.split("?")[0].split("&")[0].strip()
    if re.fullmatch(r"[\w-]{11}", clean):
        return clean
    return None


def _entries_to_text(entries) -> str:
    """Convert transcript entries (list of snippet objects or dicts) to plain text."""
    parts = []
    for entry in entries:
        if isinstance(entry, dict):
            parts.append(entry.get("text", ""))
        elif hasattr(entry, "text"):
            parts.append(entry.text)
        else:
            parts.append(str(entry))
    return " ".join(parts)


@tool("get_youtube_transcript")
def get_youtube_transcript(url_or_id: str) -> str:
    """
    Extract the transcript of a YouTube video.
    Accepts full YouTube URL (including youtu.be links with ?si= params) or bare video ID.
    Tries manual captions first, then auto-generated captions in multiple languages.
    Returns plain text transcript, max 12000 chars.
    """
    video_id = _extract_video_id(url_or_id)
    if not video_id:
        return f"Invalid YouTube video ID or URL: {url_or_id[:80]}"

    # Strategy 1: Direct fetch (uses default language selection)
    try:
        entries = _api.fetch(video_id)
        text = _entries_to_text(entries)
        if text.strip():
            logger.info(f"YouTube transcript extracted for {video_id} ({len(text)} chars)")
            return text[:12000]
    except Exception as exc:
        logger.debug(f"Direct fetch failed for {video_id}: {exc}")

    # Strategy 2: List available transcripts and pick the best one
    try:
        transcript_list = _api.list(video_id)

        # Try to find any transcript from the listing
        # The list() result has .manual and .generated attributes in some versions,
        # or is iterable. Try fetching each available one.
        best = None
        for t in transcript_list:
            if hasattr(t, "is_generated") and not t.is_generated:
                best = t  # prefer manual
                break
        if best is None:
            for t in transcript_list:
                best = t
                break

        if best is not None:
            if hasattr(best, "fetch"):
                entries = best.fetch()
            else:
                # Some versions: the transcript object IS the entries
                entries = best
            text = _entries_to_text(entries)
            if text.strip():
                logger.info(f"YouTube transcript (listed) for {video_id} ({len(text)} chars)")
                return text[:12000]
    except Exception as exc:
        logger.debug(f"List-based fetch failed for {video_id}: {exc}")

    # Strategy 3: Fetch with explicit language codes
    for langs in [["en"], ["en-US", "en-GB"]]:
        try:
            entries = _api.fetch(video_id, languages=langs)
            text = _entries_to_text(entries)
            if text.strip():
                logger.info(f"YouTube transcript ({langs}) for {video_id} ({len(text)} chars)")
                return text[:12000]
        except Exception:
            continue

    logger.warning(f"All transcript strategies failed for {video_id}")
    return (
        f"Could not retrieve transcript for video {video_id}. "
        f"The video may not have any captions (manual or auto-generated)."
    )
