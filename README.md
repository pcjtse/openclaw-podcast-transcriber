# Podcast Transcriber — OpenClaw Skill

Transcribe podcast episodes from Spotify or Apple Podcasts URLs using a local [OpenAI Whisper](https://github.com/openai/whisper) model. No transcription API key required.

## Features

- Accepts Spotify episode URLs (`open.spotify.com/episode/...`)
- Accepts Apple Podcasts episode URLs (`podcasts.apple.com/...`)
- Downloads audio automatically via public RSS feeds / iTunes API
- Transcribes locally using Whisper — your audio never leaves your machine
- Configurable model size: `tiny`, `base`, `small`, `medium`, `large`

## Prerequisites

Before installing the skill, ensure the following are available on your system:

| Dependency | Purpose | Install |
|-----------|---------|---------|
| Python 3.8+ | Run the transcription script | [python.org](https://www.python.org/downloads/) |
| ffmpeg | Audio processing required by Whisper | See below |

**Install ffmpeg:**

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg

# Arch Linux
sudo pacman -S ffmpeg
```

## Installation

### 1. Add the skill to OpenClaw

Copy (or symlink) this repository into your OpenClaw skills directory:

```bash
# Option A: clone directly into the skills directory
git clone https://github.com/pcjtse/openclaw-podcast-transcriber \
  ~/.openclaw/skills/podcast-transcriber

# Option B: symlink an existing clone
ln -s /path/to/openclaw-podcast-transcriber \
  ~/.openclaw/skills/podcast-transcriber
```

> OpenClaw also searches `~/.agents/skills/` and `<workspace>/skills/` — use whichever fits your setup.

### 2. Install Python dependencies

```bash
pip install openai-whisper requests feedparser
```

Or with `uv`:

```bash
uv pip install openai-whisper requests feedparser
```

> On first use, Whisper will automatically download the model weights (~74 MB for `base`). This requires an internet connection the first time only.

### 3. Verify the setup

```bash
python3 ~/.openclaw/skills/podcast-transcriber/scripts/transcribe.py --help
```

You should see the usage information printed. If `ffmpeg` or a Python package is missing, an error message will tell you exactly what to install.

## Usage in OpenClaw

Once installed, simply ask OpenClaw to transcribe a podcast:

```
Transcribe this podcast: https://open.spotify.com/episode/1OkEtDoje5m4j7qRuL32dq
```

```
Can you transcribe this Apple Podcasts episode?
https://podcasts.apple.com/us/podcast/my-show/id123456789?i=1000567890123
```

OpenClaw will run the transcription script and return the full plain-text transcript.

### Choosing a model

The default model (`base`) is fast and works well for clear speech. For podcasts with heavy accents, technical vocabulary, or background noise, request a larger model:

```
Transcribe this podcast using the medium model:
https://open.spotify.com/episode/...
```

| Model | Size | Speed | Accuracy |
|-------|------|-------|----------|
| `tiny` | ~39 MB | Fastest | Lower |
| `base` | ~74 MB | Fast | Good (default) |
| `small` | ~244 MB | Moderate | Better |
| `medium` | ~769 MB | Slow | High |
| `large` | ~1.5 GB | Slowest | Best |

## Running Manually

You can also run the script directly from the terminal:

```bash
python3 scripts/transcribe.py "<URL>"

# Use a specific model
python3 scripts/transcribe.py "<URL>" --model small
```

The transcript is printed to stdout. Metadata and progress are printed to stderr, so you can redirect just the transcript to a file:

```bash
python3 scripts/transcribe.py "https://open.spotify.com/episode/..." > transcript.txt
```

## Limitations

- **Spotify-exclusive podcasts** (e.g. Spotify Originals with no public RSS feed) cannot be downloaded. The script will report this clearly if it occurs.
- **Paywalled episodes** (e.g. Patreon-only content) are not accessible.
- Transcription speed depends on episode length and CPU performance. A 30-minute episode typically takes 1–5 minutes on CPU with the `base` model.
- GPU acceleration is used automatically if PyTorch detects a compatible GPU (CUDA or Apple Silicon MPS).

## Troubleshooting

**`ModuleNotFoundError: No module named 'whisper'`**
```bash
pip install openai-whisper
```

**`ffmpeg not found` or `FileNotFoundError: ffmpeg`**
Install ffmpeg using the instructions in [Prerequisites](#prerequisites).

**`Could not extract audio URL`**
The podcast may be Spotify-exclusive or behind a paywall. Try a different episode, or find the show's RSS feed URL and pass that directly.

**Slow transcription**
Use a smaller model (`--model tiny` or `--model base`) or run on a machine with a GPU.
