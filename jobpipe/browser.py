from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
import webbrowser


def open_url(url: str) -> bool:
    return webbrowser.open(url)


def open_path(path: str | Path) -> bool:
    actual = Path(path)
    if not actual.exists():
        return False

    if sys.platform.startswith('darwin'):
        return _run_command(['open', str(actual)])
    if os.name == 'nt':  # pragma: no cover - Windows-specific
        os.startfile(str(actual))  # type: ignore[attr-defined]
        return True

    for command in (['xdg-open', str(actual)], ['gio', 'open', str(actual)]):
        if shutil.which(command[0]):
            return _run_command(command)
    return False


def copy_text(text: str) -> bool:
    if sys.platform.startswith('darwin'):
        return _run_command(['pbcopy'], input_text=text)
    if os.name == 'nt':  # pragma: no cover - Windows-specific
        return _run_command(['clip'], input_text=text)

    candidates = (
        ['wl-copy'],
        ['xclip', '-selection', 'clipboard'],
        ['xsel', '--clipboard', '--input'],
    )
    for command in candidates:
        if shutil.which(command[0]):
            return _run_command(command, input_text=text)
    return False


def copy_file(path: str | Path) -> bool:
    actual = Path(path)
    if not actual.exists():
        return False
    return copy_text(actual.read_text(encoding='utf-8'))


def _run_command(command: list[str], input_text: str | None = None) -> bool:
    try:
        subprocess.run(
            command,
            check=True,
            input=input_text,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    return True
