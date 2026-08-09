"""Step 9 — Source FOMC press conference audio and transcripts.

Downloads audio and official transcripts from Federal Reserve
FOMC press conferences. These serve as the audio domain for the
ASR and sentiment pipeline (Steps 19-22).

Why FOMC instead of earnings calls:
  Most earnings call audio is behind paywalls (Seeking Alpha, FactSet).
  FOMC press conferences are fully public, reliably hosted, have
  official transcripts for ground-truth comparison, and update
  roughly every 6 weeks. The ASR + sentiment pipeline is identical
  regardless of domain — this is a documented engineering tradeoff.

Audio source: Fed's official YouTube channel (extracted via yt-dlp)
Transcripts: Direct PDF download from federalreserve.gov
"""

import os
import sys
import time
import subprocess
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

# ── FOMC press conference dates ─────────────────────────────
# Each entry: (date_string, youtube_video_id)
# Date format matches the Fed's URL pattern: YYYYMMDD
# Video IDs from the Fed's official YouTube channel

PRESS_CONFERENCES = [
    # 2025
    ("20250129", "xb3wB1O1yBw"),  # Jan 2025
    ("20250319", "n5gMPGM1Xso"),  # Mar 2025
    ("20250507", "uptnGXPEZiM"),  # May 2025
    ("20250618", "2UjXIz38vYo"),  # Jun 2025
    ("20250730", "i_VWWGqEW0Y"),  # Jul 2025
    ("20250917", "P_UTlB8Nqak"),  # Sep 2025
    ("20251029", "bBJkk7x76I0"),  # Oct 2025
    ("20251210", "y5XS91JjWDI"),  # Dec 2025
    # 2026
    ("20260128", "Q9zrm_v7Q_E"),  # Jan 2026
    ("20260318", "H6wM5bK2g90"),  # Mar 2026
    ("20260429", "87P7VL8ex4E"),  # Apr 2026
    ("20260617", "S3lHRSZ2_kw"),  # Jun 2026
    ("20260729", "DLFXUkOc_7I"),  # Jul 2026
]

TRANSCRIPT_URL = "https://www.federalreserve.gov/mediacenter/files/FOMCpresconf{date}.pdf"
YOUTUBE_URL = "https://www.youtube.com/watch?v={video_id}"

AUDIO_DIR = os.path.join("data", "raw", "audio")
TRANSCRIPT_DIR = os.path.join("data", "raw", "transcripts")


def download_transcript(date_str):
    """Download the official transcript PDF from the Fed's website."""
    os.makedirs(TRANSCRIPT_DIR, exist_ok=True)
    filepath = os.path.join(TRANSCRIPT_DIR, f"FOMC_{date_str}_transcript.pdf")

    if os.path.exists(filepath):
        return None  # already have it

    url = TRANSCRIPT_URL.format(date=date_str)
    headers = {"User-Agent": settings.edgar_user_agent or "FinancialCopilot/1.0"}

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()

        with open(filepath, "wb") as f:
            f.write(response.content)

        return filepath

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return "not_available"
        raise


def download_audio(date_str, video_id):
    """Extract audio from the Fed's YouTube video using yt-dlp."""
    os.makedirs(AUDIO_DIR, exist_ok=True)
    filepath = os.path.join(AUDIO_DIR, f"FOMC_{date_str}.mp3")

    if os.path.exists(filepath):
        return None  # already have it

    url = YOUTUBE_URL.format(video_id=video_id)

    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--extract-audio",
                "--audio-format", "mp3",
                "--audio-quality", "5",  # medium quality, smaller files
                "--output", filepath.replace(".mp3", ".%(ext)s"),
                "--no-playlist",
                "--quiet",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip()
            if error_msg:
                print(f"    yt-dlp error: {error_msg[:100]}")
            return "failed"

        return filepath

    except FileNotFoundError:
        return "no_ytdlp"
    except subprocess.TimeoutExpired:
        return "timeout"


def main():
    print(f"Pulling {len(PRESS_CONFERENCES)} FOMC press conferences")
    print(f"Transcripts → {TRANSCRIPT_DIR}/")
    print(f"Audio → {AUDIO_DIR}/")
    print()

    transcript_ok = 0
    transcript_skip = 0
    transcript_fail = 0
    audio_ok = 0
    audio_skip = 0
    audio_fail = 0
    ytdlp_available = True

    for date_str, video_id in PRESS_CONFERENCES:
        label = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
        print(f"── FOMC {label} ──")

        # Transcript
        result = download_transcript(date_str)
        if result is None:
            transcript_skip += 1
            print(f"  Transcript: already have it")
        elif result == "not_available":
            transcript_fail += 1
            print(f"  Transcript: not yet available on Fed website")
        else:
            transcript_ok += 1
            print(f"  Transcript: downloaded")

        # Audio
        if not ytdlp_available:
            print(f"  Audio: skipped (yt-dlp not installed)")
        else:
            result = download_audio(date_str, video_id)
            if result is None:
                audio_skip += 1
                print(f"  Audio: already have it")
            elif result == "no_ytdlp":
                ytdlp_available = False
                print(f"  Audio: yt-dlp not found — skipping all audio")
                print(f"         Install with: pip install yt-dlp")
                print(f"         Also needs ffmpeg on your PATH")
            elif result == "failed":
                audio_fail += 1
                print(f"  Audio: download failed")
            elif result == "timeout":
                audio_fail += 1
                print(f"  Audio: timed out")
            else:
                audio_ok += 1
                print(f"  Audio: downloaded")

        time.sleep(0.5)
        print()

    print("=== Summary ===")
    print(f"Transcripts — Downloaded: {transcript_ok}, "
          f"Skipped: {transcript_skip}, Unavailable: {transcript_fail}")
    print(f"Audio       — Downloaded: {audio_ok}, "
          f"Skipped: {audio_skip}, Failed: {audio_fail}")

    if not ytdlp_available:
        print()
        print("Audio was skipped because yt-dlp is not installed.")
        print("The transcript PDFs are enough to continue — the sentiment")
        print("pipeline (Step 21) can work off text. You can add audio later.")


if __name__ == "__main__":
    main()