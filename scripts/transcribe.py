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
from typing import Optional, Tuple
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


def _get_spotify_episode_title_via_oembed(episode_id: str) -> Optional[str]:
    """
    Use Spotify's oEmbed endpoint to get the episode title when the main
    Spotify page doesn't return useful metadata (e.g. Spotify Exclusives).
    Returns the episode title string or None on failure.
    """
    oembed_url = (
        f"https://open.spotify.com/oembed"
        f"?url=https://open.spotify.com/episode/{episode_id}"
    )
    try:
        resp = requests.get(oembed_url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("title")
    except Exception:
        return None


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


def _find_audio_in_rss(rss_url: str, episode_id: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse RSS feed and find the episode matching the Spotify episode ID.
    Returns (audio_url, episode_title) or (first_enclosure_url, first_episode_title).
    """
    print(f"  Parsing RSS feed: {rss_url}", file=sys.stderr)
    feed = feedparser.parse(rss_url)
    if not feed.entries:
        return None, None

    # Try to match by Spotify GUID or episode ID in links
    for entry in feed.entries:
        guid = entry.get("id", "") or entry.get("guid", "")
        link = entry.get("link", "")
        if episode_id in guid or episode_id in link:
            for enc in entry.get("enclosures", []):
                if enc.get("href"):
                    return enc["href"], entry.get("title")

    # No match found — don't fall back to first episode (wrong episode),
    # let the caller decide next steps
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

    # Last-ditch: try oEmbed to get episode title even if show name is missing
    episode_title_oembed: Optional[str] = None
    if not show or not title or title == "Spotify \u2013 Web Player":
        episode_title_oembed = _get_spotify_episode_title_via_oembed(episode_id)
        if episode_title_oembed and episode_title_oembed != "Spotify \u2013 Web Player":
            title = episode_title_oembed

    raise SpotifyNoAudioError(episode_id, title, show, meta.get("rss_url"), episode_title_oembed)


class SpotifyNoAudioError(RuntimeError):
    """Raised when a Spotify episode has no extractable audio URL."""
    def __init__(self, episode_id: str, title: str, show: str, rss_url: Optional[str], episode_title: Optional[str] = None):
        self.episode_id = episode_id
        self.title = title
        self.show = show
        self.rss_url = rss_url
        self.episode_title = episode_title
        super().__init__(
            f"Could not extract audio URL for Spotify episode {episode_id}.\n"
            "The episode may be Spotify-exclusive (no public RSS feed) or behind a paywall.\n"
            "Try a different episode or a podcast available on Apple Podcasts."
        )


# ---------------------------------------------------------------------------
# Apple Podcasts: extract audio URL via iTunes Lookup API
# ---------------------------------------------------------------------------

def _extract_apple_ids(url: str) -> Tuple[Optional[str], Optional[str]]:
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
# Summarization (extractive, fully local)
# ---------------------------------------------------------------------------

import math


def _score_sentence(sentence: str, all_sentences: list, doc_word_freq: dict, total_words: int) -> float:
    """
    Score a sentence by combining:
      - Position score: first and last sentences get a bonus
      - Length score: penalize very short, reward medium-length
      - Keyword score: sentences with frequent/important words score higher
    """
    words = sentence.lower().split()
    num_words = len(words)
    if num_words < 3:
        return -999.0

    # Position score: bonus for early and late sentences
    idx = all_sentences.index(sentence)
    n = len(all_sentences)
    if n <= 2:
        pos_score = 1.0
    else:
        pos_score = 1.0 - (abs(idx - 0) / n) * 0.5 + (abs(idx - (n - 1)) / n) * 0.3

    # Length score: prefer 10-40 words
    if num_words < 10:
        len_score = num_words / 10 * 0.5
    elif num_words <= 40:
        len_score = 1.0
    else:
        len_score = max(0.3, 1.0 - (num_words - 40) / 100)

    # Keyword score: TF-like — sum of (freq / total) for each word
    keyword_score = 0.0
    for w in words:
        # remove punctuation
        w = w.strip(".,!?\"'():;")
        if w in doc_word_freq:
            # IDF-like boost: rare words in doc score higher
            keyword_score += math.log(total_words / (1 + doc_word_freq[w]))

    # Combine
    return pos_score * len_score + (keyword_score / max(num_words, 1)) * 0.3


def _split_into_sentences(text: str) -> list:
    """Split text into sentences, handling common abbreviations."""
    # Protect common abbreviations
    for abbr in ("Mr.", "Mrs.", "Dr.", "Ms.", "Prof.", "Jr.", "vs.", "e.g.", "i.e.", "etc.", "U.S.", "U.K."):
        text = text.replace(abbr, abbr.replace(".", "<<<DOT>>>"))
    # Split on sentence-ending punctuation
    parts = re.split(r"(?<=[.!?])\s+", text)
    sentences = []
    for p in parts:
        # Restore abbreviations
        s = p.replace("<<<DOT>>>", ".")
        s = s.strip()
        if s and len(s) > 5:
            sentences.append(s)
    return sentences


def summarize(text: str, num_sentences: int = 8) -> str:
    """
    Extractive summarization — selects the most important sentences.
    Returns a string of the top `num_sentences` sentences in document order.
    """
    if not text or len(text.strip()) < 50:
        return text

    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()

    sentences = _split_into_sentences(text)
    if len(sentences) <= num_sentences:
        return text

    # Compute word frequencies for the document
    words = re.findall(r"[a-zA-Z']+", text.lower())
    word_freq: dict = {}
    for w in words:
        w = w.strip("'")
        if len(w) > 2:
            word_freq[w] = word_freq.get(w, 0) + 1
    total_words = max(len(words), 1)

    # Score each sentence
    scored = []
    for sent in sentences:
        score = _score_sentence(sent, sentences, word_freq, total_words)
        scored.append((sent, score))

    # Pick top sentences, then sort by first appearance in original doc
    top = sorted(scored, key=lambda x: -x[1])[:num_sentences]
    # Each element: (sentence, score); remember original index for ordering
    top_with_idx = [(sent, sentences.index(sent)) for sent, _ in top]
    top_with_idx.sort(key=lambda x: x[1])  # sort by original position

    summary_lines = [s for s, _ in top_with_idx]

    # Deduplicate near-duplicates (sentences that share >70% of words)
    final = []
    for s in summary_lines:
        s_words = set(re.findall(r"[a-zA-Z']+", s.lower()))
        is_dup = any(
            len(s_words & set(re.findall(r"[a-zA-Z']+", prev.lower()))) / max(len(s_words), 1) > 0.7
            for prev in final
        )
        if not is_dup:
            final.append(s)
        if len(final) >= num_sentences:
            break

    return " ".join(final)


# ---------------------------------------------------------------------------
# Apple Podcasts search (fallback for Spotify-exclusive episodes)
# ---------------------------------------------------------------------------

def _search_apple_podcasts(show_name: str) -> Optional[tuple[str, str, str]]:
    """
    Search iTunes/Apple Podcasts for a show by name.
    Returns (podcast_id, podcast_url, feed_url) or None if not found.
    """
    import urllib.parse

    search_url = (
        f"https://itunes.apple.com/search"
        f"?term={urllib.parse.quote(show_name)}"
        f"&entity=podcast"
        f"&limit=5"
    )
    print(f"  Searching Apple Podcasts for '{show_name}'...", file=sys.stderr)
    resp = requests.get(search_url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results", [])
    if not results:
        return None

    # Return the first match that has a collectionId (podcast ID)
    for r in results:
        cid = r.get("collectionId")
        if cid:
            collection_url = r.get("collectionViewUrl") or r.get("trackViewUrl")
            feed_url = r.get("feedUrl")
            return str(cid), collection_url, feed_url
    return None


def _find_episode_on_apple(podcast_id: str, episode_title: str):
    """
    Look up episodes for a podcast and try to find one matching episode_title.
    Returns (audio_url, title) or None.
    """
    import re as _re

    # Strip things like "( feat. X)" or "[with X]" for looser matching
    def normalize(s):
        s = s.lower()
        # Remove everything in parentheses or brackets
        s = _re.sub(r"[\(\[].*?[\)\]]", "", s)
        s = _re.sub(r"['\"]", "", s)
        s = _re.sub(r"\s+", " ", s).strip()
        return s

    target = normalize(episode_title)

    lookup_url = (
        f"https://itunes.apple.com/lookup?id={podcast_id}"
        f"&entity=podcastEpisode&limit=20"
    )
    print(f"  Looking for matching episode on Apple Podcasts...", file=sys.stderr)
    resp = requests.get(lookup_url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    best = None
    for result in data.get("results", []):
        if result.get("kind") != "podcast-episode":
            continue
        ep_title = result.get("trackName", "")
        # Exact-ish match (normalized)
        if normalize(ep_title) == target:
            episode_url = result.get("episodeUrl") or result.get("previewUrl")
            if episode_url:
                return episode_url, ep_title
        # Fallback: first episode as best-effort
        if best is None:
            episode_url = result.get("episodeUrl") or result.get("previewUrl")
            if episode_url:
                best = (episode_url, ep_title)

    return best


def _try_apple_podcasts_fallback(exc: SpotifyNoAudioError) -> tuple[str, str]:
    """
    Given a SpotifyNoAudioError from a failed Spotify episode, try to find
    the same show/episode on Apple Podcasts and return (audio_url, episode_title).
    Uses show name if available, otherwise falls back to episode title from oEmbed.
    Raises RuntimeError if Apple Podcasts also fails.
    """
    # Try oEmbed as a last resort to get the episode title if we don't have it
    episode_title = exc.episode_title
    if not episode_title:
        episode_title = _get_spotify_episode_title_via_oembed(exc.episode_id)
        if episode_title:
            print(f"  oEmbed episode title: {episode_title}", file=sys.stderr)

    show = exc.show
    title = episode_title or exc.title or "Unknown Episode"

    print(f"\n  Spotify-exclusive episode. Attempting Apple Podcasts fallback...", file=sys.stderr)

    podcast_id: Optional[str] = None
    feed_url: Optional[str] = None

    # Step 1: Try to find the podcast by show name (most reliable)
    if show:
        print(f"  Searching by show: '{show}'", file=sys.stderr)
        found = _search_apple_podcasts(show)
        if found:
            podcast_id, _, feed_url = found
            print(f"  Found podcast on Apple Podcasts (ID: {podcast_id})", file=sys.stderr)

    # Step 2: If show not found (or absent), search by episode title
    if not podcast_id and title and title != "Unknown Episode":
        print(f"  Searching by episode title: '{title}'", file=sys.stderr)
        found = _search_apple_podcasts(title)
        if found:
            podcast_id, _, feed_url = found
            print(f"  Found podcast via episode title (ID: {podcast_id})", file=sys.stderr)

    if not podcast_id:
        raise RuntimeError(
            "Could not find this podcast on Apple Podcasts (tried show name and episode title).\n"
            "Try finding the episode manually on Apple Podcasts."
        )

    # Step 3: Try RSS feed if we have it (only use if episode ID matches — avoids wrong episode)
    if feed_url:
        audio_url, rss_title = _find_audio_in_rss(feed_url, exc.episode_id)
        if audio_url and rss_title and rss_title != "Unknown Episode":
            # RSS returned a non-generic episode title — trust it (episode ID matched)
            print(f"  Episode matched via RSS: {rss_title}", file=sys.stderr)
            return audio_url, rss_title
        else:
            print(f"  RSS didn't match episode ID by GUID, trying Apple API...", file=sys.stderr)

    # Step 4: Use iTunes Lookup API to find episode by title (most reliable for title matching)
    if title and title != "Unknown Episode":
        result = _find_episode_on_apple(podcast_id, title)
        if result:
            audio_url, ep_title = result
            print(f"  Episode found via Apple API: {ep_title}", file=sys.stderr)
            return audio_url, ep_title

    raise RuntimeError(
        f"Found podcast on Apple Podcasts but could not locate this specific episode.\n"
        "Try finding the episode manually on Apple Podcasts."
    )

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
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Path to save the transcript (summary + transcript). If not provided, output goes to stdout only.",
    )
    args = parser.parse_args()

    url = args.url.strip()
    output_path = args.output
    print(f"\n🎙️  Podcast Transcriber", file=sys.stderr)
    print(f"   URL: {url}", file=sys.stderr)
    print(f"   Model: {args.model}", file=sys.stderr)
    if output_path:
        print(f"   Output: {output_path}", file=sys.stderr)
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
            try:
                audio_url, title = get_spotify_audio_url(url)
            except SpotifyNoAudioError as e:
                # Spotify-exclusive — try Apple Podcasts fallback (oEmbed will be attempted inside)
                audio_url, title = _try_apple_podcasts_fallback(e)
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

    # Download to a temp file
    tmpdir = tempfile.mkdtemp()
    audio_path = os.path.join(tmpdir, f"episode{ext}")
    try:
        download_audio(audio_url, audio_path)

        print(f"\nTranscribing '{title}'...", file=sys.stderr)
        transcript = transcribe(audio_path, args.model)

        print(f"  Generating summary...", file=sys.stderr)
        summary = summarize(transcript)

        # Build the output content
        sep = "=" * 60
        output_content = (
            f"{sep}\n"
            f"SUMMARY: {title}\n"
            f"{sep}\n"
            f"{summary}\n\n"
            f"{sep}\n"
            f"TRANSCRIPT: {title}\n"
            f"{sep}\n"
            f"{transcript}"
        )

        # Print to stdout
        print(output_content)

        # Save to file if requested
        if output_path:
            print(f"  Saving transcript to {output_path}...", file=sys.stderr)
            try:
                os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(output_content)
                print(f"  Transcript saved.", file=sys.stderr)
            except OSError as e:
                print(f"\nERROR: Could not write to {output_path}: {e}", file=sys.stderr)
                # Don't exit — transcript was already printed to stdout

    finally:
        # Always delete the audio file
        try:
            if os.path.exists(audio_path):
                os.remove(audio_path)
                print(f"  Audio file deleted.", file=sys.stderr)
        except OSError:
            pass
        # Clean up temp directory
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass


if __name__ == "__main__":
    main()
