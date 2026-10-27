from __future__ import annotations

import sys
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR = _base_dir()
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.application import VerthandiApplication
from app.core.single_instance import SingleInstance


def main() -> int:
    # 单实例判定必须早于 QApplication：第二实例只发“置顶”请求就退出，不建窗口。
    # 否则 SettingsStore / SessionLogWriter / ServerProcess / GameWatcher 都会被初始化两份。
    if not SingleInstance.claim():
        SingleInstance.notify_existing()
        return 0
    app = VerthandiApplication(sys.argv)
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
