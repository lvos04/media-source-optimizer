"""Rechtenwissel voor de alles-in-één-bewaker en de Docker-healthcheck."""

import ctypes
import os
from pathlib import Path

PR_CAPBSET_DROP = 24
PR_SET_NO_NEW_PRIVS = 38
_prctl = ctypes.CDLL(None, use_errno=True).prctl
_prctl.argtypes = (ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong)
_prctl.restype = ctypes.c_int


def runtime_identity() -> tuple[int, int]:
    try:
        uid, gid = (int(os.environ.get(name, "568")) for name in ("MSO_UID", "MSO_GID"))
        if 0 < uid < 2**32 - 1 and 0 < gid < 2**32 - 1:
            return uid, gid
    except ValueError:
        pass
    raise ValueError("MSO_UID en MSO_GID moeten geldige, positieve gebruikers- en groeps-ID’s zijn")


def _set_process_control(option: int, value: int) -> None:
    if _prctl(option, value, 0, 0, 0) != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, f"prctl({option}, {value}): {os.strerror(errno)}")


def process_status() -> dict[str, str]:
    status = Path("/proc/self/status").read_text()
    return dict(line.split(":\t", 1) for line in status.splitlines() if ":\t" in line)


def lower_privileges(uid: int, gid: int) -> None:
    root = os.geteuid() == 0
    if root:
        cap_last = int(Path("/proc/sys/kernel/cap_last_cap").read_text())
        for capability in range(cap_last + 1):
            _set_process_control(PR_CAPBSET_DROP, capability)
        os.setgroups([])
        os.setgid(gid)
        os.setuid(uid)
    _set_process_control(PR_SET_NO_NEW_PRIVS, 1)

    status = process_status()
    if status.get("Uid", "").split() != [str(uid)] * 4 or status.get("Gid", "").split() != [str(gid)] * 4:
        raise PermissionError(f"proces draait niet volledig als {uid}:{gid}")
    capabilities = ("CapEff", "CapPrm", "CapInh", "CapAmb", "CapBnd") if root else (
        "CapEff", "CapPrm", "CapInh", "CapAmb"
    )
    if any(status.get(field) != "0000000000000000" for field in capabilities) or status.get("NoNewPrivs") != "1":
        raise PermissionError("proces heeft nog capabilities of kan nieuwe rechten verkrijgen")
