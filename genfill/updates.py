"""Startup check for a newer release on GitHub (one small request; can be turned off in Settings)."""
from __future__ import annotations

import json
import re
import urllib.request

from . import __version__

REPO = "JohnyDaFace/photocraft-genfill"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"


def _parts(version: str) -> tuple[int, ...]:
    """'v1.10.2' -> (1, 10, 2); anything after the numbers (-beta…) is ignored."""
    m = re.match(r"v?(\d+(?:\.\d+)*)", version.strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def is_newer(tag: str, current: str = __version__) -> bool:
    new, cur = _parts(tag), _parts(current)
    width = max(len(new), len(cur))
    return bool(new) and new + (0,) * (width - len(new)) > cur + (0,) * (width - len(cur))


def newer_release(current: str = __version__, timeout: float = 5) -> tuple[str, str] | None:
    """(version, page url) when GitHub has a newer release; None when not, or when offline."""
    req = urllib.request.Request(API_URL, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": f"photocraft-genfill/{current}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except (OSError, ValueError):
        return None
    tag = str(data.get("tag_name", ""))
    if data.get("draft") or data.get("prerelease") or not is_newer(tag, current):
        return None
    return tag.lstrip("v"), str(data.get("html_url") or RELEASES_URL)
