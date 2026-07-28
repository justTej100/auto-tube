from pathlib import Path

import requests


def fetch_video(api_key: str, query: str, out_path: Path):
    """Pexels video search for vertical Shorts/TikTok. Prefers portrait
    clips, falls back to any orientation if portrait is empty, then picks
    the file closest to 1080px wide."""
    headers = {"Authorization": api_key}
    candidates = [query]
    words = query.split()
    if len(words) > 3:
        candidates.append(" ".join(words[:3]))

    last_error = None
    for q in candidates:
        for orientation in ("portrait", "square", None):
            try:
                params = {"query": q, "per_page": 5}
                if orientation:
                    params["orientation"] = orientation

                r = requests.get(
                    "https://api.pexels.com/videos/search",
                    params=params,
                    headers=headers,
                    timeout=30,
                )
                r.raise_for_status()
                videos = r.json().get("videos") or []
                if not videos:
                    label = orientation or "any"
                    last_error = RuntimeError(
                        f"No Pexels video results for query: {q} ({label})"
                    )
                    continue

                for video in videos:
                    files = [
                        f for f in video.get("video_files", [])
                        if f.get("file_type") == "video/mp4" and f.get("link")
                    ]
                    if not files:
                        continue

                    # Prefer near-1080-wide vertical sources.
                    best = min(files, key=lambda f: abs((f.get("width") or 0) - 1080))
                    download = requests.get(best["link"], timeout=120)
                    download.raise_for_status()
                    out_path.write_bytes(download.content)
                    if q != query or orientation != "portrait":
                        print(
                            f"Pexels used query={q!r} orientation={orientation or 'any'} "
                            f"(original query={query!r})"
                        )
                    return

                last_error = RuntimeError(f"No mp4 files in Pexels results for query: {q}")
            except Exception as e:
                last_error = e
                print(f"Pexels fetch failed for {q!r} ({orientation or 'any'}): {e}")

    raise RuntimeError(f"Pexels video fetch failed for {query!r}: {last_error}")
