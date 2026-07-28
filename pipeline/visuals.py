from pathlib import Path

import requests


def fetch_video(api_key: str, query: str, out_path: Path):
    """Pexels video search -- same key/auth as the old photo search, just
    the /videos/ endpoint. Tries the full query first, then a shortened
    fallback if Pexels returns nothing for a too-specific phrase. Picks
    the file closest to 1080p so segments stay a consistent resolution."""
    headers = {"Authorization": api_key}
    candidates = [query]
    words = query.split()
    if len(words) > 3:
        candidates.append(" ".join(words[:3]))

    last_error = None
    for q in candidates:
        try:
            r = requests.get(
                "https://api.pexels.com/videos/search",
                params={"query": q, "per_page": 5, "orientation": "landscape"},
                headers=headers,
                timeout=30,
            )
            r.raise_for_status()
            videos = r.json().get("videos") or []
            if not videos:
                last_error = RuntimeError(f"No Pexels video results for query: {q}")
                print(f"No Pexels videos for {q!r}; trying fallback..." if q != candidates[-1] else f"No Pexels videos for {q!r}")
                continue

            for video in videos:
                files = [
                    f for f in video.get("video_files", [])
                    if f.get("file_type") == "video/mp4" and f.get("link")
                ]
                if not files:
                    continue

                best = min(files, key=lambda f: abs((f.get("width") or 0) - 1920))
                download = requests.get(best["link"], timeout=120)
                download.raise_for_status()
                out_path.write_bytes(download.content)
                if q != query:
                    print(f"Pexels fallback query used: {q!r} (original: {query!r})")
                return

            last_error = RuntimeError(f"No mp4 files in Pexels results for query: {q}")
        except Exception as e:
            last_error = e
            print(f"Pexels fetch failed for {q!r}: {e}")

    raise RuntimeError(f"Pexels video fetch failed for {query!r}: {last_error}")
