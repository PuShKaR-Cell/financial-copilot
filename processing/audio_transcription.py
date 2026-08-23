"""Step 19 — Transcribe audio and parse official transcripts.

Two input paths, one output table:

  1. Audio (.mp3)  -> faster-whisper -> timestamped segments
  2. Official PDFs -> text parser    -> speaker-attributed segments

Both write to transcript_segments in Postgres, tagged with
source_type so later steps can use either or both.

Where an event has BOTH audio and an official transcript, the
script computes word error rate — a real accuracy number for the
ASR pipeline measured against ground truth, rather than an
assertion that "Whisper works well".

Usage:
    python processing/audio_transcription.py              # everything
    python processing/audio_transcription.py --pdf-only   # skip ASR
"""

import os
import re
import sys
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import settings

import psycopg2
from pypdf import PdfReader

AUDIO_DIR = os.path.join("data", "raw", "audio")
TRANSCRIPT_DIR = os.path.join("data", "raw", "transcripts")

# "base" is the CPU sweet spot: noticeably better than "tiny",
# far faster than "small". Bump to "small" if you get GPU access.
WHISPER_MODEL = "base"


def get_db():
    return psycopg2.connect(settings.postgres_url)


# ── Storage ────────────────────────────────────────────────

def store_segments(event_id, source_type, segments):
    """Insert segments, skipping any that already exist."""
    conn = get_db()
    cur = conn.cursor()
    inserted = 0

    for i, seg in enumerate(segments):
        try:
            cur.execute("""
                INSERT INTO transcript_segments
                    (event_id, source_type, segment_index, speaker,
                     text, start_sec, end_sec)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_id, source_type, segment_index) DO NOTHING
            """, (
                event_id, source_type, i,
                seg.get("speaker"), seg["text"],
                seg.get("start"), seg.get("end"),
            ))
            if cur.rowcount > 0:
                inserted += 1
        except Exception as e:
            conn.rollback()
            print(f"    Warning: {e}")
            continue

    conn.commit()
    cur.close()
    conn.close()
    return inserted


def already_done(event_id, source_type):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM transcript_segments WHERE event_id=%s AND source_type=%s",
        (event_id, source_type),
    )
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return count > 0


# ── Path 1: audio -> Whisper ───────────────────────────────

def transcribe_audio(audio_path):
    """Run faster-whisper over one audio file.

    Returns a list of {text, start, end} segment dicts.
    """
    from faster_whisper import WhisperModel

    print(f"  Loading Whisper ({WHISPER_MODEL})...")
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")

    print("  Transcribing (this takes a while on CPU)...")
    start_time = time.time()

    segments_iter, info = model.transcribe(audio_path, beam_size=5)

    segments = []
    for seg in segments_iter:
        segments.append({
            "text": seg.text.strip(),
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
        })
        # Progress ping every 50 segments
        if len(segments) % 50 == 0:
            mins = seg.end / 60
            print(f"    ...{mins:.0f} min of audio processed "
                  f"({len(segments)} segments)")

    elapsed = time.time() - start_time
    audio_mins = info.duration / 60
    print(f"  Done: {len(segments)} segments, {audio_mins:.1f} min of audio "
          f"in {elapsed/60:.1f} min wall time")

    return segments


# ── Path 2: official transcript PDF -> segments ────────────

# FOMC transcripts label speakers in caps followed by a period,
# e.g. "CHAIR POWELL." or "MICHELLE SMITH."
SPEAKER_RE = re.compile(r'\b([A-Z][A-Z\.\s]{3,40}?)\.\s+(?=[A-Z])')

# Page-header text that bleeds into speaker labels during PDF extraction
HEADER_NOISE = re.compile(r'^(FINAL|PRELIMINARY|TRANSCRIPT|PAGE)\s+', re.I)


def clean_speaker(raw):
    """Normalise a captured speaker label, or return None if it's noise.

    Handles two failure modes seen in the raw parse:
      - PDF page headers ("FINAL") prefixed onto real names, which
        splits one speaker into two identities
      - short all-caps tokens in body text (CNBC, NGFS) mistaken
        for speaker labels
    """
    name = raw.strip()

    # Strip repeated page-header prefixes
    prev = None
    while prev != name:
        prev = name
        name = HEADER_NOISE.sub("", name).strip()

    name = re.sub(r'\s+', ' ', name)

    if len(name) < 5:
        return None
    # Real speakers are "FIRSTNAME LASTNAME" or a titled role
    if " " not in name and not name.startswith("CHAIR"):
        return None
    return name


def parse_transcript_pdf(pdf_path):
    """Extract speaker-attributed segments from an official transcript PDF."""
    reader = PdfReader(pdf_path)

    full_text = []
    for page in reader.pages:
        try:
            full_text.append(page.extract_text() or "")
        except Exception:
            continue

    text = "\n".join(full_text)
    text = re.sub(r'\s+', ' ', text)
    # Drop page-header noise common in Fed transcripts
    text = re.sub(r'Page \d+ of \d+', ' ', text)

    # Split on speaker labels, keeping who said what
    parts = SPEAKER_RE.split(text)

    segments = []
    # parts alternates: [preamble, speaker, body, speaker, body, ...]
    for i in range(1, len(parts) - 1, 2):
        speaker = clean_speaker(parts[i])
        body = parts[i + 1].strip()
        if len(body) < 40:      # skip fragments
            continue
        if speaker is None:
            # Not a real speaker label — fold this text back into the
            # previous segment rather than dropping content
            if segments:
                segments[-1]["text"] += " " + parts[i].strip() + ". " + body
            continue
        segments.append({"speaker": speaker, "text": body})

    # Fallback: if speaker parsing found nothing, chunk by paragraph
    if not segments:
        chunks = [c.strip() for c in re.split(r'(?<=[.!?])\s{2,}', text)]
        segments = [{"speaker": None, "text": c}
                    for c in chunks if len(c) > 80]

    return segments


# ── Word error rate ────────────────────────────────────────

def normalize_words(text):
    """Lowercase, strip punctuation, split to words — for fair WER."""
    text = text.lower()
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    return text.split()


def word_error_rate(reference, hypothesis):
    """Levenshtein distance at the word level, divided by reference length.

    Standard ASR accuracy metric: 0.0 is perfect, 0.15 means roughly
    15% of words were wrong (substituted, deleted, or inserted).
    """
    ref = normalize_words(reference)
    hyp = normalize_words(hypothesis)

    if not ref:
        return None

    # Classic DP edit-distance table, one row at a time to save memory
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        curr = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cost = 0 if r == h else 1
            curr[j] = min(
                prev[j] + 1,         # deletion
                curr[j - 1] + 1,     # insertion
                prev[j - 1] + cost,  # substitution
            )
        prev = curr

    return prev[len(hyp)] / len(ref)


def compute_wer(event_id):
    """Compare ASR output against the official transcript for one event."""
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT source_type, string_agg(text, ' ' ORDER BY segment_index)
        FROM transcript_segments
        WHERE event_id = %s
        GROUP BY source_type
    """, (event_id,))
    rows = dict(cur.fetchall())
    cur.close()
    conn.close()

    if "asr" not in rows or "official" not in rows:
        return None

    return word_error_rate(rows["official"], rows["asr"])


# ── Main ───────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-only", action="store_true",
                        help="skip audio transcription")
    args = parser.parse_args()

    # ---- Official transcripts ----
    print("=" * 55)
    print("  Official transcripts (PDF)")
    print("=" * 55)

    pdf_count = 0
    if os.path.isdir(TRANSCRIPT_DIR):
        for fn in sorted(os.listdir(TRANSCRIPT_DIR)):
            if not fn.endswith(".pdf"):
                continue
            event_id = fn.replace("FOMC_", "").replace("_transcript.pdf", "")

            if already_done(event_id, "official"):
                print(f"  · {event_id} (already parsed)")
                continue

            segments = parse_transcript_pdf(os.path.join(TRANSCRIPT_DIR, fn))
            n = store_segments(event_id, "official", segments)
            speakers = len({s["speaker"] for s in segments if s.get("speaker")})
            print(f"  ✓ {event_id}: {n} segments, {speakers} speakers")
            pdf_count += 1

    print(f"\n  {pdf_count} transcripts newly parsed")
    print()

    if args.pdf_only:
        return

    # ---- Audio ----
    print("=" * 55)
    print("  Audio transcription (Whisper)")
    print("=" * 55)

    audio_files = []
    if os.path.isdir(AUDIO_DIR):
        audio_files = sorted([f for f in os.listdir(AUDIO_DIR)
                              if f.endswith(".mp3")])

    if not audio_files:
        print("  No audio files found — skipping ASR")
        print("  (the transcript corpus above is enough to continue)")
        return

    for fn in audio_files:
        event_id = fn.replace("FOMC_", "").replace(".mp3", "")
        print(f"\n── {event_id} ──")

        if already_done(event_id, "asr"):
            print("  Already transcribed")
        else:
            segments = transcribe_audio(os.path.join(AUDIO_DIR, fn))
            n = store_segments(event_id, "asr", segments)
            print(f"  Stored {n} segments")

        # Ground-truth comparison, where possible
        wer = compute_wer(event_id)
        if wer is not None:
            print(f"  Word error rate vs official transcript: {wer:.1%}")
        else:
            print("  No official transcript for this event — WER unavailable")


if __name__ == "__main__":
    main()