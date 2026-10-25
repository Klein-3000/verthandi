from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
from ctypes import wintypes
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from app.core.hoshimi_finder import find_hoshimi_exe
from app.core.log_bus import LogBus, LogSource
from app.core.paths import Paths
from app.core.settings import Settings


# ---------- Windows 常量 ----------

CREATE_NEW_CONSOLE = 0x00000010
CREATE_NEW_PROCESS_GROUP = 0x00000200
CTRL_BREAK_EVENT = 1

JobObjectExtendedLimitInformation = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001


class ServerState(str, Enum):
    STOPPED = 'stopped'
    STARTING = 'starting'
    RUNNING = 'running'
    STOPPING = 'stopping'


class ServerProcess(QObject):
    """hoshimi.exe 的进程包装（subprocess.Popen 实现）。

    - 启动：CREATE_NEW_CONSOLE + CREATE_NEW_PROCESS_GROUP + 隐藏控制台 + stdout 管道
    - 读取：reader 线程 → Qt Signal → 主线程 → log_bus
    - 停止：CTRL_BREAK → terminate → kill 三级
    - 首次启动：检测到提示自动回车
    - Job Object：KILL_ON_JOB_CLOSE，Verthandi 崩溃也能带走 hoshimi
    """

    state_changed = Signal(str)          # ServerState.value
    waiting_for_confirm = Signal()       # 需要用户手动确认
    confirm_resolved = Signal()          # 确认完成（自动或手动）
    _stdout_line = Signal(str)           # 内部：reader 线程 → 主线程
    _process_exited = Signal(int)        # 内部：monitor 线程 → 主线程

    FIRST_LAUNCH_HINTS = ('按回车', 'Press Enter', '按回车继续')

    def __init__(self, paths: Paths, settings: Settings, log_bus: LogBus):
        super().__init__()
        self.paths = paths
        self.settings = settings
        self.log_bus = log_bus

        self._state = ServerState.STOPPED
        self._proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._monitor_thread: threading.Thread | None = None
        self._job_handle = 0
        self._auto_confirm_pending = False
        self._stop_phase = 0

        self._stop_timer = QTimer(self)
        self._stop_timer.setSingleShot(True)
        self._stop_timer.timeout.connect(self._escalate_stop)

        self._stdout_line.connect(self._handle_line)
        self._process_exited.connect(self._on_process_exited)

    # ---------- 状态 ----------

    @property
    def state(self) -> ServerState:
        return self._state

    def _set_state(self, state: ServerState) -> None:
        if self._state == state:
            return
        self._state = state
        self.state_changed.emit(state.value)

    # ---------- 路径 ----------

    def hoshimi_exe(self) -> Path:
        exe, _version, _auto = find_hoshimi_exe(self.paths, self.settings)
        return exe

    # ---------- version ----------

    def version(self, timeout_ms: int = 5000) -> tuple[str, str] | None:
        exe = self.hoshimi_exe()
        if not exe.exists():
            return None
        try:
            result = subprocess.run(
                [str(exe), 'version'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout_ms / 1000,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        return self._parse_version((result.stdout or '') + (result.stderr or ''))

    @staticmethod
    def _parse_version(text: str) -> tuple[str, str] | None:
        version = compile_time = ''
        for line in text.splitlines():
            if '当前版本' in line and '：' in line:
                version = line.split('：', 1)[1].strip()
            elif '编译时间' in line and '：' in line:
                compile_time = line.split('：', 1)[1].strip()
        if version:
            return version, compile_time
        return None

    # ---------- 启动 ----------

    def start(self) -> bool:
        if self._state != ServerState.STOPPED:
            return False

        exe = self.hoshimi_exe()
        if not exe.exists():
            self.log_bus.emit_line(LogSource.APP, f'找不到 hoshimi.exe：{exe}', 'error')
            return False

        home = self.paths.home_dir(self.settings)
        runtime = self.paths.runtime_dir(self.settings)
        home.mkdir(parents=True, exist_ok=True)

        cmd = [
            str(exe), 'serve',
            '--home', str(home),
            '--runtime-dir', str(runtime),
        ]
        env = os.environ.copy()
        env['AGL_HTTP_PORT'] = str(self.settings.server.http_port)
        env['AGL_TCP_PORT'] = str(self.settings.server.game_port)

        creationflags = 0
        startupinfo = None
        if sys.platform == 'win32':
            creationflags = CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE

        self.log_bus.emit_line(
            LogSource.HOSHIMI, f'> {exe.name} {" ".join(cmd[1:])}', 'info'
        )

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
                env=env,
                cwd=str(exe.parent),
                creationflags=creationflags,
                startupinfo=startupinfo,
            )
        except OSError as exc:
            self.log_bus.emit_line(LogSource.APP, f'hoshimi 启动失败：{exc}', 'error')
            self._proc = None
            return False

        self._auto_confirm_pending = True
        self._stop_phase = 0
        self._set_state(ServerState.STARTING)

        self._reader_thread = threading.Thread(
            target=self._read_stdout, args=(self._proc,), daemon=True
        )
        self._reader_thread.start()
        self._monitor_thread = threading.Thread(
            target=self._monitor, args=(self._proc,), daemon=True
        )
        self._monitor_thread.start()

        self._create_job_and_assign(self._proc.pid)
        return True

    # ---------- 线程函数 ----------

    def _read_stdout(self, proc: subprocess.Popen) -> None:
        stdout = proc.stdout
        if stdout is None:
            return
        try:
            for raw in stdout:
                text = raw.rstrip('\r\n')
                if not text:
                    continue
                self._stdout_line.emit(text)
        except (OSError, ValueError):
            pass

    def _monitor(self, proc: subprocess.Popen) -> None:
        rc = proc.wait()
        self._process_exited.emit(rc)

    # ---------- 主线程日志处理 ----------

    def _handle_line(self, text: str) -> None:
        self.log_bus.emit_line(LogSource.HOSHIMI, text)
        if self._state == ServerState.STARTING and '服务已启动' in text:
            self._set_state(ServerState.RUNNING)
        if any(hint in text for hint in self.FIRST_LAUNCH_HINTS):
            self._maybe_auto_confirm()

    def _maybe_auto_confirm(self) -> None:
        if not self._auto_confirm_pending:
            return
        if self.settings.behavior.auto_confirm_first_launch:
            self._send_enter(auto=True)
            self.confirm_resolved.emit()
        else:
            self.waiting_for_confirm.emit()

    def send_enter(self) -> None:
        """用户点击「继续启动」时调用。"""
        if not self._auto_confirm_pending:
            return
        self._send_enter(auto=False)
        self.confirm_resolved.emit()

    def _send_enter(self, auto: bool = False) -> None:
        self._auto_confirm_pending = False
        if self._proc is None or self._proc.stdin is None:
            return
        try:
            self._proc.stdin.write('\n')
            self._proc.stdin.flush()
            msg = (
                '已自动确认首次启动提示（可在设置中关闭）'
                if auto else
                '已确认首次启动提示'
            )
            self.log_bus.emit_line(LogSource.APP, msg, 'info')
        except (OSError, ValueError):
            pass

    def _on_process_exited(self, rc: int) -> None:
        self._stop_timer.stop()
        self._stop_phase = 0
        self._proc = None
        if self._auto_confirm_pending:
            self._auto_confirm_pending = False
            self.confirm_resolved.emit()
        self._set_state(ServerState.STOPPED)
        self.log_bus.emit_line(
            LogSource.APP, f'hoshimi 已退出（code={rc}）', 'info'
        )
        self._close_job()

    # ---------- 停止（三级） ----------

    def stop(self) -> None:
        if self._state in (ServerState.STOPPED, ServerState.STOPPING):
            return
        if self._proc is None:
            return
        if self._auto_confirm_pending:
            self._auto_confirm_pending = False
            self.confirm_resolved.emit()
        self._set_state(ServerState.STOPPING)
        self._stop_phase = 1
        pid = self._proc.pid
        if self._send_ctrl_break(pid):
            self.log_bus.emit_line(LogSource.APP, f'已发送 CTRL_BREAK 到 pid={pid}', 'info')
            self._stop_timer.start(8000)
        else:
            self.log_bus.emit_line(
                LogSource.APP,
                'CTRL_BREAK 不可用（开发模式或控制台冲突），直接 terminate',
                'warning',
            )
            self._escalate_stop()

    def force_kill(self) -> None:
        """应用退出时兜底，不再等待。"""
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.kill()
            except OSError:
                pass
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        self._close_job()

    def _escalate_stop(self) -> None:
        if self._state != ServerState.STOPPING or self._proc is None:
            return
        if self._stop_phase == 1:
            self._stop_phase = 2
            self.log_bus.emit_line(LogSource.APP, 'CTRL_BREAK 超时，发送 terminate', 'warning')
            try:
                self._proc.terminate()
            except OSError:
                pass
            self._stop_timer.start(3000)
        elif self._stop_phase == 2:
            self.log_bus.emit_line(
                LogSource.APP, 'terminate 超时，强制 kill（可能未保存）', 'error'
            )
            try:
                self._proc.kill()
            except OSError:
                pass

    def _send_ctrl_break(self, pid: int) -> bool:
        """AttachConsole(pid) 成功才能发 CTRL_BREAK。

        Verthandi 自身有控制台（开发模式）时 AttachConsole 会失败；
        打包为 windowed 后无控制台，Attach 成功，CTRL_BREAK 生效。
        """
        if sys.platform != 'win32':
            return False
        try:
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel32.AttachConsole.argtypes = [wintypes.DWORD]
            kernel32.AttachConsole.restype = wintypes.BOOL
            if not kernel32.AttachConsole(pid):
                return False
            try:
                kernel32.GenerateConsoleCtrlEvent.argtypes = [wintypes.DWORD, wintypes.DWORD]
                kernel32.GenerateConsoleCtrlEvent.restype = wintypes.BOOL
                return bool(kernel32.GenerateConsoleCtrlEvent(CTRL_BREAK_EVENT, pid))
            finally:
                kernel32.FreeConsole()
        except OSError:
            return False

    # ---------- Job Object ----------

    def _create_job_and_assign(self, pid: int) -> None:
        if sys.platform != 'win32':
            return
        try:
            k32 = ctypes.WinDLL('kernel32', use_last_error=True)

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ('ReadOperationCount', ctypes.c_ulonglong),
                    ('WriteOperationCount', ctypes.c_ulonglong),
                    ('OtherOperationCount', ctypes.c_ulonglong),
                    ('ReadTransferCount', ctypes.c_ulonglong),
                    ('WriteTransferCount', ctypes.c_ulonglong),
                    ('OtherTransferCount', ctypes.c_ulonglong),
                ]

            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ('PerProcessUserTimeLimit', ctypes.c_int64),
                    ('PerJobUserTimeLimit', ctypes.c_int64),
                    ('LimitFlags', wintypes.DWORD),
                    ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t),
                    ('ActiveProcessLimit', wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t),
                    ('PriorityClass', wintypes.DWORD),
                    ('SchedulingClass', wintypes.DWORD),
                ]

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ('IoInfo', IO_COUNTERS),
                    ('ProcessMemoryLimit', ctypes.c_size_t),
                    ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryUsed', ctypes.c_size_t),
                    ('PeakJobMemoryUsed', ctypes.c_size_t),
                ]

            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
            k32.SetInformationJobObject.restype = wintypes.BOOL
            k32.SetInformationJobObject.argtypes = [
                wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD
            ]
            k32.AssignProcessToJobObject.restype = wintypes.BOOL
            k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]

            job = k32.CreateJobObjectW(None, None)
            if not job:
                return

            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k32.SetInformationJobObject(
                job, JobObjectExtendedLimitInformation,
                ctypes.byref(info), ctypes.sizeof(info)
            ):
                k32.CloseHandle(job)
                return

            handle = k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
            if not handle:
                k32.CloseHandle(job)
                return
            ok = k32.AssignProcessToJobObject(job, handle)
            k32.CloseHandle(handle)
            if not ok:
                k32.CloseHandle(job)
                return

            self._job_handle = job
            self.log_bus.emit_line(LogSource.APP, '已将 hoshimi 加入 Job Object', 'info')
        except Exception as exc:
            self.log_bus.emit_line(LogSource.APP, f'Job Object 创建失败：{exc}', 'warning')

    def _close_job(self) -> None:
        if not self._job_handle:
            return
        try:
            ctypes.WinDLL('kernel32').CloseHandle(wintypes.HANDLE(self._job_handle))
        except OSError:
            pass
        self._job_handle = 0
