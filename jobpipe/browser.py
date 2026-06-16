from __future__ import annotations

import webbrowser


def open_url(url: str) -> bool:
    return webbrowser.open(url)
