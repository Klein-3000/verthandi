"""单实例守卫。

第二个实例不创建任何窗口：它把"请置顶"的请求通过本地套接字发给第一个实例，
然后立刻退出。判重使用 Win32 命名互斥体——抢占是原子的，进程退出（含崩溃）
由内核释放，不会留下需要人工清理的锁文件或僵尸端口。

顺序很重要：`claim()` 必须在 QApplication 创建之前调用，否则第二实例会先
把主窗口建出来再退，屏幕上就会闪一下。
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

SERVER_NAME = "Verthandi.SingleInstance"
MUTEX_NAME = r"Local\Verthandi.SingleInstance"
MESSAGE_ACTIVATE = b"ACTIVATE"

_ERROR_ALREADY_EXISTS = 183
_HANDSHAKE_TIMEOUT_MS = 300

_mutex_handle: int | None = None


class SingleInstance(QObject):
    """第一个实例持有的监听端：收到消息就发 `activated`。"""

    activated = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._server = QLocalServer(self)
        # 只接受当前用户会话的连接
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._drain_pending)
        if not self._server.listen(SERVER_NAME):
            # 上一次崩溃可能残留同名管道，清掉后重试，否则消息永远发不出去
            QLocalServer.removeServer(SERVER_NAME)
            self._server.listen(SERVER_NAME)

    @staticmethod
    def claim() -> bool:
        """抢占单实例锁。

        返回 True 表示本进程是第一个实例；False 表示已有实例在运行或正在启动。
        """
        global _mutex_handle
        if sys.platform != "win32":
            return True
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
        if not handle:
            # 拿不到锁（权限/资源异常）时宁可多开，也不能让程序起不来
            return True
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        _mutex_handle = handle  # 持有到进程结束，由内核回收
        return True

    @staticmethod
    def notify_existing(timeout_ms: int = 5000) -> bool:
        """请求已有实例置顶；调用方随后应立即退出。"""
        if sys.platform == "win32":
            # 先把自己抢前台的权限转授给已有实例（ASFW_ANY），
            # 否则对方调 SetForegroundWindow 只会闪一下任务栏。
            try:
                ctypes.WinDLL("user32").AllowSetForegroundWindow(-1)
            except OSError:
                pass

        from PySide6.QtCore import QCoreApplication

        # 只需事件循环承载 socket；这里绝不能创建 QApplication（那会建主窗口）
        if QCoreApplication.instance() is None:
            QCoreApplication([sys.argv[0]])

        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            if _ping():
                return True
            if time.monotonic() >= deadline:
                return False
            # 第一个实例可能还在初始化（加载 Qt、建窗口），稍后再试
            time.sleep(0.15)

    def _drain_pending(self) -> None:
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            if conn is None:
                return
            ready = conn.waitForReadyRead(_HANDSHAKE_TIMEOUT_MS)
            payload = bytes(conn.readAll()) if ready else b""
            if payload.startswith(MESSAGE_ACTIVATE):
                self.activated.emit()
            # 未来可加：elif payload.startswith(MESSAGE_SHUTDOWN): ...
            conn.disconnectFromServer()
            conn.deleteLater()


def _ping() -> bool:
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(_HANDSHAKE_TIMEOUT_MS):
        sock.abort()
        return False
    sock.write(MESSAGE_ACTIVATE)
    sock.flush()
    sock.waitForBytesWritten(_HANDSHAKE_TIMEOUT_MS)
    sock.disconnectFromServer()
    return True


def force_foreground(hwnd: int) -> bool:
    """Win32 层抢前台：Qt 的 activateWindow() 在后台进程里往往只会闪任务栏。"""
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
        user32.SetForegroundWindow.restype = wintypes.BOOL
        return bool(user32.SetForegroundWindow(wintypes.HWND(hwnd)))
    except (OSError, OverflowError):
        return False
