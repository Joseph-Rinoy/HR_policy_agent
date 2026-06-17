from __future__ import annotations

import sys
from pathlib import Path


def app_base_dir() -> Path:
    """Folder to look beside for ``.env`` and ``policies/``.

    When the app is frozen by PyInstaller, this is the folder that contains
    the ``.exe`` (so files dropped next to it are found). In development it is
    this project's folder.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent
