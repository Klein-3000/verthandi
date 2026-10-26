from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import psutil

from app.core.log_bus import LogBus, LogSource
from app.core.sdk_config import SdkConfigError, is_loopback, set_sdk_endpoints

DOMAINS = ("open.ys4fun.com", "webstatic.ys4fun.com", "prod-api-activity.ys4fun.com")
PREFS_NAMES = ("ys_mix_prefs.json", "ys_prefs.json")
PROXY_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


# ---------- 结果与异常 ----------


class SwitchError(Exception):
    """切服失败的结构化异常。code 用于 GUI 分类处理。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class SwitchResult:
    def __init__(self, mode: str, message: str, needs_relogin: bool = False):
        self.mode = mode
        self.message = message
        self.needs_relogin = needs_relogin


class CheckResult:
    def __init__(self, code: str, label: str, ok: bool, message: str = ""):
        self.code = code
        self.label = label
        self.ok = ok
        self.message = message


# ---------- 工具 ----------


def _is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _paths_equal(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(
        os.path.abspath(str(b))
    )


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def _write_json(path: Path, obj: dict, compress: bool = False) -> None:
    if compress:
        s = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    else:
        s = json.dumps(obj, ensure_ascii=False, indent=2)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(s)


def _get_client_mode(url_txt: Path) -> str:
    config = _read_json(url_txt)
    url = config.get("url")
    if not url:
        raise SwitchError("bad_url_config", f"入口配置缺少 url：{url_txt}")
    parsed = urlparse(str(url))
    if not parsed.scheme or parsed.scheme.lower() not in ("http", "https"):
        raise SwitchError("bad_url_scheme", f"入口配置不是有效 HTTP URL：{url_txt}")
    return "Local" if is_loopback(parsed.hostname or "") else "Official"


def _switch_hosts(content: str, target_mode: str) -> str:
    domains_lower = {d.lower() for d in DOMAINS}
    lines = []
    for raw_line in content.split("\n"):
        line = raw_line.removesuffix("\r")
        parts = line.split("#", 1)
        tokens = parts[0].split()
        if len(tokens) < 2:
            lines.append(line)
            continue
        matched = [t for t in tokens[1:] if t.lower() in domains_lower]
        if not matched:
            lines.append(line)
            continue
        if tokens[0] not in ("127.0.0.1", "::1"):
            if target_mode == "Local":
                raise SwitchError(
                    "hosts_non_loopback",
                    f"hosts 存在非回环映射，请先检查：\n{line}",
                )
            lines.append(line)
            continue
        remaining = [t for t in tokens[1:] if t.lower() not in domains_lower]
        if remaining:
            rebuilt = tokens[0] + " " + " ".join(remaining)
            if len(parts) == 2:
                rebuilt += " #" + parts[1]
            lines.append(rebuilt)
    return "\r\n".join(lines).rstrip("\r\n") + "\r\n"


def _get_proxy() -> tuple[int, str]:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PROXY_REG_PATH) as key:
            enable = 0
            server = ""
            try:
                enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
            except FileNotFoundError:
                pass
            try:
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
            except FileNotFoundError:
                pass
        return int(enable or 0), str(server or "")
    except FileNotFoundError:
        return 0, ""


def _set_proxy_enable(value: int) -> None:
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, PROXY_REG_PATH, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, value)


def _flush_dns() -> None:
    try:
        subprocess.run(["ipconfig", "/flushdns"], capture_output=True, check=False)
    except Exception:
        pass


# ---------- 主操作类 ----------


class SwitchOperation:
    """官私服切换操作。

    构造时传入所有固定配置，`switch()` 执行；`preflight()` 只做检查。

    - 可通过 `log_bus` 记录日志（GUI 模式），否则输出到 stdout（CLI 模式）；
    - 所有异常统一为 `SwitchError(code, message)`；
    - 所有前置检查用 `preflight()` 返回 `CheckResult` 列表。
    """

    def __init__(
        self,
        *,
        client_dir: Path,
        state_dir: Path,
        prefs_dir: Path,
        hosts_path: Path,
        local_port: int,
        log_bus: LogBus | None = None,
    ):
        self.client_dir = Path(client_dir).resolve()
        self.state_dir = Path(state_dir).resolve()
        self.prefs_dir = Path(prefs_dir).resolve()
        self.hosts_path = Path(hosts_path).resolve()
        self.local_port = local_port
        self.log_bus = log_bus

        self.url_txt = (
            self.client_dir / "AetherGazer_Data" / "StreamingAssets" / "url.txt"
        )
        self.resources = self.client_dir / "AetherGazer_Data" / "resources.assets"
        self.state_json = self.state_dir / "state.json"
        self.official_bak = self.state_dir / "official-url.bak"

        self._system_hosts = Path(
            os.environ.get("SystemRoot", r"C:\Windows"),
            "System32",
            "drivers",
            "etc",
            "hosts",
        ).resolve()
        self._use_system_hosts = _paths_equal(self.hosts_path, self._system_hosts)

    # ---------- 日志 ----------

    def _log(self, message: str, level: str = "info") -> None:
        if self.log_bus is not None:
            self.log_bus.emit_line(LogSource.SWITCH, message, level)
        else:
            print(message)

    # ---------- 前置检查 ----------

    def preflight(self, target_mode: str) -> list[CheckResult]:
        """返回所有检查项的结果。全部 ok 才应执行 switch()。"""
        results: list[CheckResult] = []

        # 1. 客户端目录与 url.txt
        if not self.url_txt.exists():
            results.append(
                CheckResult(
                    "url_txt_missing",
                    "客户端入口文件",
                    False,
                    f"找不到 {self.url_txt}，请用启动器修复客户端",
                )
            )
            return results  # 后续检查都依赖这个
        results.append(CheckResult("url_txt", "客户端入口文件", True))

        # 2. 当前 mode
        try:
            current = _get_client_mode(self.url_txt)
        except SwitchError as e:
            results.append(
                CheckResult("mode_unknown", "当前客户端模式", False, e.message)
            )
            return results
        results.append(CheckResult("mode", "当前客户端模式", True))

        if current == target_mode:
            results.append(
                CheckResult(
                    "same_mode",
                    "切换目标",
                    False,
                    f"客户端已经处于{'本地服' if current == 'Local' else '官服'}模式",
                )
            )
            return results
        results.append(CheckResult("same_mode", "切换目标", True))

        # 3. 游戏未运行
        if self._is_game_running():
            results.append(
                CheckResult(
                    "game_running",
                    "游戏进程",
                    False,
                    "游戏正在运行，请先退出客户端再切换",
                )
            )
        else:
            results.append(CheckResult("game_running", "游戏进程", True))

        # 4. resources.assets
        if not self.resources.exists():
            results.append(
                CheckResult(
                    "resources_missing",
                    "SDK 资源文件",
                    False,
                    f"找不到 {self.resources}，请用启动器修复客户端",
                )
            )
        else:
            results.append(CheckResult("resources", "SDK 资源文件", True))

        # 5. 官服备份（切到 Local 时必须能取到官服地址）
        official_source = self._find_official_source(current)
        if official_source is None:
            results.append(
                CheckResult(
                    "official_backup_missing",
                    "官服入口备份",
                    False,
                    "缺少官服 url.txt 备份，请用启动器修复客户端后再切换",
                )
            )
        else:
            results.append(CheckResult("official_backup", "官服入口备份", True))

        # 6. hosts 修改需要 admin
        needs_admin = self._needs_admin_for_hosts(target_mode)
        if needs_admin and not _is_admin():
            results.append(
                CheckResult(
                    "admin_required",
                    "管理员权限",
                    False,
                    "hosts 中有 ys4fun.com 的回环映射需要清理，请以管理员身份重启 Verthandi",
                )
            )
        else:
            results.append(CheckResult("admin_required", "管理员权限", True))

        # 7. 系统代理冲突
        proxy_enable, proxy_server = _get_proxy()
        if proxy_enable == 1 and target_mode == "Local":
            local_proxy = proxy_server.lower() in (
                f"127.0.0.1:{self.local_port}".lower(),
                f"localhost:{self.local_port}".lower(),
            )
            if not local_proxy:
                results.append(
                    CheckResult(
                        "proxy_conflict",
                        "系统代理",
                        False,
                        "系统代理已开启，可能覆盖 hosts 路由。请先关闭代理再切换",
                    )
                )
            else:
                results.append(CheckResult("proxy_conflict", "系统代理", True))
        else:
            results.append(CheckResult("proxy_conflict", "系统代理", True))

        # 8. state.json 属于其他客户端
        if self.state_json.exists():
            try:
                state = _read_json(self.state_json)
                if not _paths_equal(
                    state.get("clientDir", ""), self.client_dir
                ) or not _paths_equal(state.get("prefsDir", ""), self.prefs_dir):
                    results.append(
                        CheckResult(
                            "state_dir_conflict",
                            "备份目录",
                            False,
                            "此备份目录属于其他客户端，请在设置页指定新的备份目录",
                        )
                    )
                else:
                    results.append(CheckResult("state_dir", "备份目录", True))
            except Exception:
                results.append(CheckResult("state_dir", "备份目录", True))
        else:
            results.append(CheckResult("state_dir", "备份目录", True))

        return results

    # ---------- 执行 ----------

    def switch(
        self,
        target_mode: str,
        progress: Callable[[str, str], None] | None = None,
    ) -> SwitchResult:
        """执行切换。target_mode 为 'Local' 或 'Official'。"""

        def report(stage: str, message: str) -> None:
            self._log(message)
            if progress is not None:
                progress(stage, message)

        if target_mode not in ("Local", "Official"):
            raise SwitchError("bad_mode", f"无效的切换目标：{target_mode}")

        report("check", "正在检查前置条件…")

        # 复跑检查，避免外部状态变化
        checks = self.preflight(target_mode)
        failures = [c for c in checks if not c.ok]
        if failures:
            messages = "\n".join(f"• {c.label}：{c.message}" for c in failures)
            raise SwitchError("preflight_failed", f"前置检查未通过：\n{messages}")

        current_mode = _get_client_mode(self.url_txt)
        official_source = self._find_official_source(current_mode)
        if official_source is None:
            raise SwitchError("official_backup_missing", "缺少官服入口备份")

        self.state_dir.mkdir(parents=True, exist_ok=True)
        if not self.official_bak.exists():
            shutil.copy2(official_source, self.official_bak)
            report("backup", f"已保存官服入口备份：{self.official_bak}")

        initial_hosts_bak = self.state_dir / "initial-hosts.bak"
        if not initial_hosts_bak.exists() and self.hosts_path.exists():
            shutil.copy2(self.hosts_path, initial_hosts_bak)

        hosts_before = (
            self._read_text(self.hosts_path) if self.hosts_path.exists() else ""
        )
        hosts_after = (
            _switch_hosts(hosts_before, target_mode) if hosts_before else hosts_before
        )

        # 快照
        snapshot: dict[Path, bytes | None] = {}
        for p in (
            self.url_txt,
            self.resources,
            self.hosts_path,
            *(self.prefs_dir / n for n in PREFS_NAMES),
        ):
            snapshot[p] = p.read_bytes() if p.exists() else None

        self._save_cache_profile(self.state_dir / current_mode)

        disable_proxy = False
        try:
            report("sdk", "正在更新 SDK 端点…")
            try:
                set_sdk_endpoints(
                    self.resources, target_mode, self.local_port, self.state_dir
                )
            except SdkConfigError as e:
                raise SwitchError(e.code, e.message)

            report("url", "正在更新客户端入口…")
            if target_mode == "Local":
                config = _read_json(self.official_bak)
                config["url"] = (
                    f"http://127.0.0.1:{self.local_port}/skzy-activity/gateway/get"
                )
                _write_json(self.url_txt, config, compress=True)
            else:
                shutil.copy2(self.official_bak, self.url_txt)

            if hosts_after != hosts_before:
                report("hosts", "正在更新 hosts…")
                self._write_text(self.hosts_path, hosts_after)

            if target_mode != current_mode:
                report("cache", "正在交换登录缓存…")
                self._restore_cache_profile(self.state_dir / target_mode)

            proxy_enable, proxy_server = _get_proxy()
            if proxy_enable == 1:
                local_proxy = proxy_server.lower() in (
                    f"127.0.0.1:{self.local_port}".lower(),
                    f"localhost:{self.local_port}".lower(),
                )
                if local_proxy:
                    disable_proxy = True
                    _set_proxy_enable(0)

            report("state", "正在写入状态…")
            _write_json(
                self.state_json,
                {
                    "clientDir": str(self.client_dir),
                    "prefsDir": str(self.prefs_dir),
                    "mode": target_mode,
                },
            )

        except Exception as exc:
            # 回滚
            for p, data in snapshot.items():
                try:
                    if data is None:
                        if p.exists():
                            p.unlink()
                    else:
                        if not p.exists() or p.read_bytes() != data:
                            p.write_bytes(data)
                except OSError as rb_err:
                    self._log(f"回滚 {p} 失败：{rb_err}", "warning")
            if disable_proxy:
                try:
                    _set_proxy_enable(1)
                except Exception:
                    pass
            if isinstance(exc, SwitchError):
                raise
            raise SwitchError("switch_failed", str(exc))

        if self._use_system_hosts:
            report("dns", "正在刷新 DNS 缓存…")
            _flush_dns()

        report("done", "切换完成")

        mode_label = "本地服" if target_mode == "Local" else "官服"
        if target_mode == "Local":
            message = f"已切换到{mode_label}。\n请启动服务后再打开游戏。"
            return SwitchResult(target_mode, message, needs_relogin=False)
        return SwitchResult(
            target_mode,
            f"已切换到{mode_label}。\n请通过 AetherGazer.exe 打开游戏（首次可能需要重新登录）。",
            needs_relogin=True,
        )

    # ---------- 状态查询 ----------

    def status(self) -> dict:
        current = "Unknown"
        if self.url_txt.exists():
            try:
                current = _get_client_mode(self.url_txt)
            except SwitchError:
                pass
        hosts_lines = []
        if self.hosts_path.exists():
            hosts_lines = [
                line
                for line in self._read_text(self.hosts_path).splitlines()
                if "ys4fun.com" in line
            ]
        return {
            "mode": current,
            "client_dir": str(self.client_dir),
            "state_dir": str(self.state_dir),
            "official_backup_exists": self.official_bak.exists(),
            "hosts_lines": hosts_lines,
        }

    # ---------- 内部 ----------

    def _is_game_running(self) -> bool:
        target = str(self.client_dir / "AetherGazer.exe").lower()
        for proc in psutil.process_iter(["name", "exe"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if name not in ("aethergazer", "aethergazer.exe"):
                    continue
                exe = proc.info.get("exe")
                if not exe:
                    return True
                if str(exe).lower() == target:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False

    def _find_official_source(self, current_mode: str) -> Path | None:
        if self.official_bak.exists():
            return self.official_bak
        if current_mode == "Official" and self.url_txt.exists():
            return self.url_txt
        return None

    def _needs_admin_for_hosts(self, target_mode: str) -> bool:
        if not self._use_system_hosts or not self.hosts_path.exists():
            return False
        try:
            before = self._read_text(self.hosts_path)
            after = _switch_hosts(before, target_mode)
        except SwitchError:
            return False
        return before != after

    def _save_cache_profile(self, profile_dir: Path) -> None:
        profile_dir.mkdir(parents=True, exist_ok=True)
        for name in PREFS_NAMES:
            src = self.prefs_dir / name
            dst = profile_dir / name
            if src.exists():
                shutil.copy2(src, dst)
            elif dst.exists():
                dst.unlink()

    def _restore_cache_profile(self, profile_dir: Path) -> None:
        self.prefs_dir.mkdir(parents=True, exist_ok=True)
        for name in PREFS_NAMES:
            src = profile_dir / name
            dst = self.prefs_dir / name
            if src.exists():
                shutil.copy2(src, dst)
            elif dst.exists():
                dst.unlink()

    @staticmethod
    def _read_text(path: Path) -> str:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
            return f.read()

    @staticmethod
    def _write_text(path: Path, text: str) -> None:
        with path.open("w", encoding="utf-8", newline="") as f:
            f.write(text)


# ---------- CLI 入口（开发调试用） ----------


def _cli_main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Hoshimi 官私服切换工具（Python 版）")
    parser.add_argument(
        "-Mode",
        "--mode",
        dest="mode",
        choices=["Local", "Official", "Status"],
        required=True,
    )
    parser.add_argument("-ClientDir", "--client-dir", dest="client_dir", required=True)
    parser.add_argument("-StateDir", "--state-dir", dest="state_dir", required=True)
    parser.add_argument(
        "-LocalPort", "--local-port", dest="local_port", type=int, default=8080
    )
    parser.add_argument(
        "-HostsPath",
        "--hosts-path",
        dest="hosts_path",
        default=os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"),
            "System32",
            "drivers",
            "etc",
            "hosts",
        ),
    )
    parser.add_argument(
        "-PrefsDir",
        "--prefs-dir",
        dest="prefs_dir",
        default=os.path.join(
            os.environ.get("USERPROFILE", os.path.expanduser("~")),
            "AppData",
            "LocalLow",
            "yongshi",
            "AetherGazer",
        ),
    )
    args = parser.parse_args(argv)

    op = SwitchOperation(
        client_dir=Path(args.client_dir),
        state_dir=Path(args.state_dir),
        prefs_dir=Path(args.prefs_dir),
        hosts_path=Path(args.hosts_path),
        local_port=args.local_port,
        log_bus=None,
    )

    try:
        if args.mode == "Status":
            info = op.status()
            print(f"客户端：{info['client_dir']}")
            print(f"入口环境：{info['mode']}")
            print(f"备份目录：{info['state_dir']}")
            print(
                f"官服入口备份：{'已保存' if info['official_backup_exists'] else '未保存'}"
            )
            for line in info["hosts_lines"]:
                print(line)
            return 0

        result = op.switch(args.mode)
        print(result.message)
        return 0
    except SwitchError as e:
        print(f"\n[错误] {e.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(_cli_main())
