from __future__ import annotations

import sys
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR = _base_dir()
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.application import VerthandiApplication  # noqa: E402


def main() -> int:
    app = VerthandiApplication(sys.argv)
    return app.run()


if __name__ == '__main__':
    sys.exit(main())
