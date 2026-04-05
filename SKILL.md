---
name: podcast-transcriber
description: Transcribe a podcast episode from a Spotify or Apple Podcasts URL using a local Whisper model. Automatically returns both a summary (extractive, fully local) and the full transcript. If a Spotify URL is Spotify-exclusive (no public RSS), the script uses the oEmbed endpoint to recover the episode title and automatically searches Apple Podcasts for the same show and continues transcription there. Audio file is automatically deleted after transcription. Use --output to save the transcript to a file. No API key required.
version: 1.4.0
metadata:
  openclaw:
    emoji: "🎙️"
    os: [macos, linux]
    requires:
      bins:
        - ffmpeg
      anyBins:
        - python3
        - python
    install:
      - kind: brew
        name: ffmpeg
        bins: [ffmpeg]
      - kind: uv
        name: openai-whisper
        bins: []
      - kind: uv
        name: requests
        bins: []
      - kind: uv
        name: feedparser
        bins: []
---

# Podcast Transcriber

Transcribe podcast episodes from Spotify or Apple Podcasts URLs using a local OpenAI Whisper model — no API key or internet-based transcription service needed.

## Supported URL Formats

- **Spotify episodes**: `https://open.spotify.com/episode/<ID>`
- **Apple Podcasts**: `https://podcasts.apple.com/...`

## How to Use This Skill

When the user provides a podcast URL, follow these steps:

### Step 1 — Confirm the URL

Identify whether the URL is a Spotify episode or Apple Podcasts link. If the URL is neither, inform the user that only Spotify and Apple Podcasts URLs are currently supported, and ask them to provide a supported URL.

### Step 2 — Run the Transcription Script

Execute the transcription script, passing the URL as an argument:

```bash
python3 scripts/transcribe.py "<URL>"
```

If `python3` is not available, try `python` instead.

To save the transcript to a file (recommended for long episodes):

```bash
python3 scripts/transcribe.py "<URL>" --output "/path/to/transcript.txt"
```

To use a more accurate (but slower) model, the user can request it:

```bash
python3 scripts/transcribe.py "<URL>" --model small --output "transcript.txt"
python3 scripts/transcribe.py "<URL>" --model medium --output "transcript.txt"
python3 scripts/transcribe.py "<URL>" --model large --output "transcript.txt"
```

Available models (fastest to most accurate): `tiny`, `base` (default), `small`, `medium`, `large`

**Audio file handling:** The downloaded audio file is automatically deleted after transcription completes (success or failure). The transcript is always printed to stdout, and optionally saved to a file with `--output`.

### Step 3 — Present the Output

The script prints progress messages (download, transcription, summary generation) to **stderr** and outputs the summary + transcript to **stdout** in this order:

```
========================================
SUMMARY: [Episode Title]
========================================
[Extractive summary — top ~8 key sentences]

========================================
TRANSCRIPT: [Episode Title]
========================================
[Full plain-text transcript]
```

Display both clearly to the user. Offer to answer questions about or summarize further.

### Step 4 — Handle Errors

Common issues and how to address them:

| Error | Resolution |
|-------|-----------|
| `ModuleNotFoundError: whisper` | Run `pip install openai-whisper` or `uv pip install openai-whisper` |
| `ffmpeg not found` | Install ffmpeg: `brew install ffmpeg` (macOS) or `sudo apt install ffmpeg` (Linux) |
| `Could not extract audio URL` (Spotify) | The episode may be Spotify-exclusive. The script **automatically searches Apple Podcasts** for the same show and tries to find the matching episode. If that also fails, try a different episode or find it manually on Apple Podcasts. |
| `Network error` | Check internet connection and retry. |

### Spotify → Apple Podcasts Fallback

When a Spotify episode is Spotify-exclusive (no public RSS feed), the script attempts to find the same episode on Apple Podcasts automatically:

1. **First** — tries to extract the **show name** from the Spotify episode page metadata
2. **If no show name** — falls back to the Spotify **oEmbed endpoint** (`open.spotify.com/oembed`) to retrieve the episode title
3. Searches **Apple Podcasts** via the iTunes Search API using the show name (primary) or episode title (secondary)
4. Uses the iTunes Lookup API to find the matching episode by title
5. Falls back to the most recent episode only if title matching also fails

This is fully automatic. If Apple Podcasts also fails, it reports the specific failure reason and suggests finding the episode manually.

### Notes

- The `base` Whisper model (~74 MB) downloads automatically on first use and is cached locally.
- Transcription time depends on episode length and hardware. A 30-minute episode typically takes 1–5 minutes on CPU.
- For GPU acceleration, ensure PyTorch with CUDA is installed.
- The downloaded audio file is deleted automatically after transcription (success or failure).
- Use `--output` / `-o` to save the transcript to a specific file path. The file contains both the summary and full transcript.
