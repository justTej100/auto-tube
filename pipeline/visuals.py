from pathlib import Path

import requests


def fetch_image(api_key: str, query: str, out_path: Path):
    headers = {"Authorization": api_key}
    r = requests.get(
        "https://api.pexels.com/v1/search",
        params={"query": query, "per_page": 1, "orientation": "landscape"},
        headers=headers,
        timeout=30,
    )
    r.raise_for_status()
    photos = r.json().get("photos")
    if not photos:
        raise RuntimeError(f"No Pexels results for query: {query}")
    img_url = photos[0]["src"]["large2x"]
    img = requests.get(img_url, timeout=60)
    img.raise_for_status()
    out_path.write_bytes(img.content)
