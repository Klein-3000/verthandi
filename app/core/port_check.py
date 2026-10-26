from __future__ import annotations

import socket
from dataclasses import dataclass, field
from enum import Enum

try:
    import psutil
except ImportError:
    psutil = None


_WSAEADDRINUSE = 10048
_WSAEACCES = 10013
_EADDRINUSE = 98
_EACCES = 13


class PortState(str, Enum):
    FREE = "free"
    OCCUPIED = "occupied"
    DENIED = "denied"
    UNKNOWN = "unknown"


@dataclass
class ProcessInfo:
    pid: int
    proto: str
    name: str = ""
    user: str = ""
    exe: str = ""
    cmdline: str = ""

    def display(self) -> str:
        if self.name:
            return f"{self.name}（PID {self.pid}）"
        return f"PID {self.pid}"


@dataclass
class PortResult:
    port: int
    host: str
    tcp: PortState
    udp: PortState
    occupied_by: list[ProcessInfo] = field(default_factory=list)
    detail: str = ""

    @property
    def is_free(self) -> bool:
        return self.tcp == PortState.FREE and self.udp == PortState.FREE

    def failed_protocols(self) -> list[str]:
        parts = []
        if self.tcp != PortState.FREE:
            parts.append("TCP")
        if self.udp != PortState.FREE:
            parts.append("UDP")
        return parts

    def summary(self) -> str:
        if self.is_free:
            return "空闲"
        proto = " / ".join(self.failed_protocols())
        if self.occupied_by:
            names = "、".join(p.display() for p in self.occupied_by[:3])
            more = (
                f" 等 {len(self.occupied_by)} 个进程"
                if len(self.occupied_by) > 3
                else ""
            )
            return f"{proto} 被 {names}{more} 占用"
        if any(s == PortState.DENIED for s in (self.tcp, self.udp)):
            return f"{proto} 无法确定（权限不足）"
        return f"{proto} 已被占用"


# ---------- psutil：权威检测 ----------


def _scan_listeners(port: int) -> tuple[list[ProcessInfo], bool]:
    if psutil is None:
        return [], False

    try:
        conns = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, PermissionError):
        return [], False
    except Exception:
        return [], False

    result: list[ProcessInfo] = []
    seen: set[tuple[int, int]] = set()

    for c in conns:
        try:
            if not c.laddr or c.laddr.port != port:
                continue
            if c.type == socket.SOCK_STREAM and c.status != psutil.CONN_LISTEN:
                continue
            if c.type not in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
                continue
            if c.pid is None:
                continue
            key = (c.pid, c.type)
            if key in seen:
                continue
            seen.add(key)

            info = ProcessInfo(
                pid=c.pid,
                proto="TCP" if c.type == socket.SOCK_STREAM else "UDP",
            )
            try:
                p = psutil.Process(c.pid)
                try:
                    info.name = p.name() or ""
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    pass
                try:
                    info.user = p.username() or ""
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    pass
                try:
                    info.exe = p.exe() or ""
                except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                    pass
                try:
                    cmd = p.cmdline() or []
                    info.cmdline = " ".join(cmd)[:200]
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    pass
            except psutil.NoSuchProcess:
                pass
            result.append(info)
        except Exception:
            continue

    return result, True


# ---------- bind：补充检测 ----------


def _try_bind(host: str, port: int, udp: bool) -> tuple[PortState, str]:
    sock_type = socket.SOCK_DGRAM if udp else socket.SOCK_STREAM
    s = socket.socket(socket.AF_INET, sock_type)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                pass
        s.bind((host, port))
        return PortState.FREE, ""
    except PermissionError as e:
        return PortState.DENIED, str(e)
    except OSError as e:
        code = getattr(e, "winerror", None) or e.errno
        if code in (_WSAEADDRINUSE, _EADDRINUSE):
            return PortState.OCCUPIED, str(e)
        if code in (_WSAEACCES, _EACCES):
            return PortState.DENIED, str(e)
        return PortState.UNKNOWN, str(e)
    finally:
        s.close()


# ---------- 对外接口 ----------


def check_port(port: int, host: str = "0.0.0.0") -> PortResult:
    procs, permission_ok = _scan_listeners(port)

    if procs:
        tcp = (
            PortState.OCCUPIED
            if any(p.proto == "TCP" for p in procs)
            else PortState.FREE
        )
        udp = (
            PortState.OCCUPIED
            if any(p.proto == "UDP" for p in procs)
            else PortState.FREE
        )
        return PortResult(port, host, tcp, udp, procs)

    if permission_ok:
        tcp_state, tcp_detail = _try_bind(host, port, udp=False)
        udp_state, udp_detail = _try_bind(host, port, udp=True)
        return PortResult(
            port, host, tcp_state, udp_state, [], tcp_detail or udp_detail
        )

    tcp_state, tcp_detail = _try_bind(host, port, udp=False)
    udp_state, udp_detail = _try_bind(host, port, udp=True)
    return PortResult(port, host, tcp_state, udp_state, [], tcp_detail or udp_detail)
