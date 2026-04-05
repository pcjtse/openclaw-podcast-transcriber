#!/usr/bin/env python3
"""
OpenClaw Podcast Transcriber
Transcribes podcast episodes from Spotify or Apple Podcasts URLs
using a local OpenAI Whisper model (no API key required).

Usage:
    python3 transcribe.py <URL> [--model base]

Supported URLs:
    - https://open.spotify.com/episode/<ID>
    - https://podcasts.apple.com/...
"""

import argparse
import json
import os
import re
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, parse_qs

try:
    import requests
except ImportError:
    print("ERROR: 'requests' not installed. Run: pip install requests", file=sys.stderr)
    sys.exit(1)

try:
    import feedparser
except ImportError:
    print("ERROR: 'feedparser' not installed. Run: pip install feedparser", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# URL Detection
# ---------------------------------------------------------------------------

def detect_url_type(url: str) -> str:
    """Return 'spotify', 'apple', or raise ValueError."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if "open.spotify.com" in host:
        if "/episode/" not in parsed.path:
            raise ValueError(
                "Spotify URL must be an episode URL: https://open.spotify.com/episode/<ID>"
            )
        return "spotify"
    if "podcasts.apple.com" in host or "itunes.apple.com" in host:
        return "apple"
    raise ValueError(
        f"Unsupported URL: {url}\n"
        "Supported platforms: Spotify (open.spotify.com/episode/...) and Apple Podcasts (podcasts.apple.com/...)"
    )


# ---------------------------------------------------------------------------
# Spotify: extract audio URL via JSON-LD + RSS fallback
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _extract_spotify_episode_id(url: str) -> str:
    match = re.search(r"/episode/([A-Za-z0-9]+)", url)
    if not match:
        raise ValueError(f"Could not extract episode ID from Spotify URL: {url}")
    return match.group(1)


def _get_spotify_metadata(episode_id: str) -> dict:
    """
    Fetch Spotify episode page and extract JSON-LD metadata.
    Returns dict with keys: title, show, audio_url (may be None), rss_url (may be None).
    """
    url = f"https://open.spotify.com/episode/{episode_id}"
    print(f"  Fetching Spotify episode page...", file=sys.stderr)
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    html = resp.text

    title = show = audio_url = rss_url = None

    # Try JSON-LD blocks
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    ):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue

        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            t = item.get("@type", "")
            if t in ("PodcastEpisode", "Episode", "AudioObject"):
                title = title or item.get("name") or item.get("headline")
                # Some platforms embed audio URL here
                for key in ("contentUrl", "audio", "url"):
                    val = item.get(key)
                    if isinstance(val, dict):
                        val = val.get("contentUrl") or val.get("url")
                    if val and (val.endswith(".mp3") or val.endswith(".m4a") or "audio" in val):
                        audio_url = audio_url or val
                # RSS feed may be embedded
                rss_url = rss_url or item.get("associatedMedia", {}).get("url") if isinstance(item.get("associatedMedia"), dict) else rss_url

            if t in ("PodcastSeries", "RadioSeries"):
                show = show or item.get("name")
                rss_url = rss_url or item.get("url")

    # Fallback: look for RSS in <link> tags
    if not rss_url:
        link_match = re.search(
            r'<link[^>]+type=["\']application/rss\+xml["\'][^>]+href=["\']([^"\']+)["\']',
            html,
            re.IGNORECASE,
        )
        if link_match:
            rss_url = link_match.group(1)

    # Fallback: look for show title in <title> tag
    if not title:
        t_match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
        if t_match:
            title = t_match.group(1).strip()

    return {"title": title, "show": show, "audio_url": audio_url, "rss_url": rss_url}


def _find_audio_in_rss(rss_url: str, episode_id: str) -> tuple[str | None, str | None]:
    """
    Parse RSS feed and find the episode matching the Spotify episode ID.
    Returns (audio_url, episode_title) or (first_enclosure_url, first_episode_title).
    """
    print(f"  Parsing RSS feed: {rss_url}", file=sys.stderr)
    feed = feedparser.parse(rss_url)
    if not feed.entries:
        return None, None

    # Try to match by Spotify GUID or just return the first/most-recent episode
    # (without a direct ID match, we can't reliably identify the exact episode from RSS alone)
    for entry in feed.entries:
        # Some RSS feeds include Spotify episode IDs in GUIDs or links
        guid = entry.get("id", "") or entry.get("guid", "")
        link = entry.get("link", "")
        if episode_id in guid or episode_id in link:
            for enc in entry.get("enclosures", []):
                if enc.get("href"):
                    return enc["href"], entry.get("title")

    # No match found — return the most recent episode as a best-effort
    first = feed.entries[0]
    for enc in first.get("enclosures", []):
        if enc.get("href"):
            return enc["href"], first.get("title")

    return None, None


def get_spotify_audio_url(url: str) -> tuple[str, str]:
    """
    Return (audio_url, episode_title) for a Spotify episode URL.
    Raises RuntimeError if audio URL cannot be determined.
    """
    episode_id = _extract_spotify_episode_id(url)
    meta = _get_spotify_metadata(episode_id)

    title = meta.get("title") or "Unknown Episode"
    show = meta.get("show") or ""
    if show:
        print(f"  Show: {show}", file=sys.stderr)
    print(f"  Episode: {title}", file=sys.stderr)

    # Direct audio URL from JSON-LD
    if meta.get("audio_url"):
        return meta["audio_url"], title

    # Try RSS feed
    if meta.get("rss_url"):
        audio_url, rss_title = _find_audio_in_rss(meta["rss_url"], episode_id)
        if audio_url:
            return audio_url, rss_title or title

    # Last resort: try Spotify's unofficial podcast RSS mirror
    # (some shows expose RSS at predictable paths)
    fallback_rss = f"https://anchor.fm/s/{episode_id}/podcast/rss"
    try:
        resp = requests.head(fallback_rss, headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            audio_url, rss_title = _find_audio_in_rss(fallback_rss, episode_id)
            if audio_url:
                return audio_url, rss_title or title
    except requests.RequestException:
        pass

    raise RuntimeError(
        f"Could not extract audio URL for Spotify episode {episode_id}.\n"
        "The episode may be Spotify-exclusive (no public RSS feed) or behind a paywall.\n"
        "Try a different episode or a podcast available on Apple Podcasts."
    )


# ---------------------------------------------------------------------------
# Apple Podcasts: extract audio URL via iTunes Lookup API
# ---------------------------------------------------------------------------

def _extract_apple_ids(url: str) -> tuple[str | None, str | None]:
    """
    Extract (podcast_id, episode_id) from an Apple Podcasts URL.
    Episode ID is in query param ?i=<episode_id>.
    Podcast ID is in path as /id<podcast_id>.
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    episode_id = qs.get("i", [None])[0]

    podcast_id_match = re.search(r"/id(\d+)", parsed.path)
    podcast_id = podcast_id_match.group(1) if podcast_id_match else None

    return podcast_id, episode_id


def get_apple_audio_url(url: str) -> tuple[str, str]:
    """
    Return (audio_url, episode_title) for an Apple Podcasts URL.
    Uses the public iTunes Lookup API — no authentication required.
    """
    podcast_id, episode_id = _extract_apple_ids(url)

    if not podcast_id:
        raise ValueError(
            f"Could not extract podcast ID from Apple Podcasts URL: {url}\n"
            "Expected format: https://podcasts.apple.com/.../id<PODCAST_ID>?i=<EPISODE_ID>"
        )

    print(f"  Podcast ID: {podcast_id}", file=sys.stderr)
    if episode_id:
        print(f"  Episode ID: {episode_id}", file=sys.stderr)

    # If we have an episode ID, look it up directly
    if episode_id:
        lookup_url = f"https://itunes.apple.com/lookup?id={episode_id}&entity=podcastEpisode"
        print(f"  Looking up episode via iTunes API...", file=sys.stderr)
        resp = requests.get(lookup_url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for result in data.get("results", []):
            episode_url = result.get("episodeUrl") or result.get("previewUrl")
            if episode_url:
                title = result.get("trackName") or result.get("collectionName") or "Unknown Episode"
                print(f"  Episode: {title}", file=sys.stderr)
                return episode_url, title

    # Fallback: look up podcast and get most recent episode
    lookup_url = (
        f"https://itunes.apple.com/lookup?id={podcast_id}"
        f"&entity=podcastEpisode&limit=1"
    )
    print(f"  Looking up most recent episode via iTunes API...", file=sys.stderr)
    resp = requests.get(lookup_url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    for result in data.get("results", []):
        if result.get("kind") == "podcast-episode":
            episode_url = result.get("episodeUrl") or result.get("previewUrl")
            if episode_url:
                title = result.get("trackName") or "Unknown Episode"
                show = result.get("collectionName") or ""
                if show:
                    print(f"  Show: {show}", file=sys.stderr)
                print(f"  Episode: {title}", file=sys.stderr)
                return episode_url, title

    raise RuntimeError(
        f"Could not find episode audio URL via iTunes API for podcast ID {podcast_id}.\n"
        "The podcast may not be available in the iTunes catalog or the URL may be invalid."
    )


# ---------------------------------------------------------------------------
# Audio Download
# ---------------------------------------------------------------------------

def download_audio(audio_url: str, dest_path: str) -> None:
    """Stream-download audio to dest_path, showing progress."""
    print(f"  Downloading audio...", file=sys.stderr)
    resp = requests.get(audio_url, headers=HEADERS, stream=True, timeout=60)
    resp.raise_for_status()

    total = int(resp.headers.get("Content-Length", 0))
    downloaded = 0
    start = time.time()

    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 64):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    elapsed = time.time() - start
                    speed = downloaded / elapsed / 1024 / 1024 if elapsed > 0 else 0
                    print(
                        f"\r  {pct:.1f}% ({downloaded/1024/1024:.1f}/{total/1024/1024:.1f} MB) "
                        f"@ {speed:.1f} MB/s   ",
                        end="",
                        file=sys.stderr,
                    )
    print(file=sys.stderr)
    size_mb = os.path.getsize(dest_path) / 1024 / 1024
    print(f"  Downloaded {size_mb:.1f} MB", file=sys.stderr)


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def transcribe(audio_path: str, model_name: str) -> str:
    """Transcribe audio file using local Whisper model. Returns plain text."""
    try:
        import whisper
    except ImportError:
        print(
            "ERROR: 'openai-whisper' not installed.\n"
            "Install it with: pip install openai-whisper",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"  Loading Whisper model '{model_name}' (downloads on first use)...", file=sys.stderr)
    model = whisper.load_model(model_name)
    print(f"  Transcribing — this may take a few minutes...", file=sys.stderr)
    result = model.transcribe(audio_path, fp16=False)
    return result["text"].strip()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Transcribe a podcast episode from Spotify or Apple Podcasts."
    )
    parser.add_argument("url", help="Spotify or Apple Podcasts episode URL")
    parser.add_argument(
        "--model",
        default="base",
        choices=["tiny", "base", "small", "medium", "large"],
        help="Whisper model size (default: base). Larger = more accurate but slower.",
    )
    args = parser.parse_args()

    url = args.url.strip()
    print(f"\n🎙️  Podcast Transcriber", file=sys.stderr)
    print(f"   URL: {url}", file=sys.stderr)
    print(f"   Model: {args.model}", file=sys.stderr)
    print(file=sys.stderr)

    # Detect URL type and extract audio URL
    try:
        url_type = detect_url_type(url)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Platform: {url_type.capitalize()}", file=sys.stderr)

    try:
        if url_type == "spotify":
            audio_url, title = get_spotify_audio_url(url)
        else:
            audio_url, title = get_apple_audio_url(url)
    except (RuntimeError, ValueError, requests.RequestException) as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\n  Audio URL resolved successfully.", file=sys.stderr)

    # Determine file extension from URL
    ext = ".mp3"
    for candidate in [".m4a", ".mp3", ".ogg", ".wav", ".aac"]:
        if candidate in audio_url.lower():
            ext = candidate
            break

    # Download to temp file and transcribe
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, f"episode{ext}")
        try:
            download_audio(audio_url, audio_path)
        except requests.RequestException as e:
            print(f"\nERROR downloading audio: {e}", file=sys.stderr)
            sys.exit(1)

        print(f"\nTranscribing '{title}'...", file=sys.stderr)
        transcript = transcribe(audio_path, args.model)

    print(f"\n--- Transcript: {title} ---", file=sys.stderr)
    print(f"(Tip: use --model small/medium/large for higher accuracy)\n", file=sys.stderr)

    # Output plain transcript to stdout
    print(transcript)


if __name__ == "__main__":
    main()
