"""Run the app from the `python/` folder; adds `src` and this directory to sys.path."""

from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent
_src = _root / "src"
for p in (_src, _root):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from app import main

if __name__ == "__main__":
    main()
