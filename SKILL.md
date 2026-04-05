---
name: podcast-transcriber
description: Transcribe a podcast episode from a Spotify or Apple Podcasts URL using a local Whisper model. No API key required.
version: 1.0.0
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

To use a more accurate (but slower) model, the user can request it:

```bash
python3 scripts/transcribe.py "<URL>" --model small
python3 scripts/transcribe.py "<URL>" --model medium
python3 scripts/transcribe.py "<URL>" --model large
```

Available models (fastest to most accurate): `tiny`, `base` (default), `small`, `medium`, `large`

### Step 3 — Present the Transcript

The script prints episode metadata (title, show, duration) to **stderr** and the plain-text transcript to **stdout**.

Display the transcript clearly to the user. If it is long, offer to summarize it or answer questions about it.

### Step 4 — Handle Errors

Common issues and how to address them:

| Error | Resolution |
|-------|-----------|
| `ModuleNotFoundError: whisper` | Run `pip install openai-whisper` or `uv pip install openai-whisper` |
| `ffmpeg not found` | Install ffmpeg: `brew install ffmpeg` (macOS) or `sudo apt install ffmpeg` (Linux) |
| `Could not extract audio URL` | The podcast may be behind a paywall or use DRM. Try a different episode. |
| `Network error` | Check internet connection and retry. |

### Notes

- The `base` Whisper model (~74 MB) downloads automatically on first use and is cached locally.
- Transcription time depends on episode length and hardware. A 30-minute episode typically takes 1–5 minutes on CPU.
- For GPU acceleration, ensure PyTorch with CUDA is installed.
- All audio files are downloaded to a temporary directory and deleted after transcription.
