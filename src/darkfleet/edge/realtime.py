"""Best-effort POSIX real-time scheduling, shared by the three edge processes.

QNX 8.0 (and Linux) let a privileged process move to ``SCHED_FIFO``; macOS ships the
constants but not ``os.sched_setscheduler``, so every call here is optional. Nothing in
the unit depends on elevation: a refused request is logged once by the caller and the
process keeps running at its normal scheduling class.

``current_schedule`` is what the status files report, so the console shows the policy a
process actually got rather than the one it asked for.
"""

from __future__ import annotations

import os
from typing import Any

SCHED_OTHER = "SCHED_OTHER"
SCHED_FIFO = "SCHED_FIFO"
SCHED_RR = "SCHED_RR"
SCHED_UNKNOWN = "SCHED_UNKNOWN"

_REALTIME = (SCHED_FIFO, SCHED_RR)


def _policy_name(policy: int) -> str:
    for name in (SCHED_FIFO, SCHED_RR, SCHED_OTHER):
        value = getattr(os, name, None)
        if value is not None and int(value) == int(policy):
            return name
    return SCHED_UNKNOWN


def _nice() -> int:
    try:
        return int(os.nice(0))
    except OSError:  # an odd sandbox; report the neutral value rather than fail
        return 0


def current_schedule() -> dict[str, Any]:
    """The process's real state: ``{"policy": "SCHED_FIFO", "priority": 30, "nice": 0}``."""
    nice = _nice()
    getter = getattr(os, "sched_getscheduler", None)
    param = getattr(os, "sched_getparam", None)
    if getter is None or param is None:
        return {"policy": SCHED_OTHER, "priority": 0, "nice": nice}
    try:
        return {"policy": _policy_name(getter(0)), "priority": int(param(0).sched_priority), "nice": nice}
    except (OSError, AttributeError, ValueError):
        return {"policy": SCHED_UNKNOWN, "priority": 0, "nice": nice}


def try_fifo(priority: int) -> tuple[bool, dict[str, Any]]:
    """Ask for ``SCHED_FIFO`` at ``priority``. Returns ``(applied, current schedule)``.

    On macOS ``os.sched_setscheduler`` does not exist and on an unprivileged QNX/macOS
    process the call raises ``EPERM``; both are ordinary answers here, not errors.
    """
    setter = getattr(os, "sched_setscheduler", None)
    fifo = getattr(os, "SCHED_FIFO", None)
    param_cls = getattr(os, "sched_param", None)
    if setter is None or fifo is None or param_cls is None:
        return False, current_schedule()
    try:
        setter(0, fifo, param_cls(priority))
    except (OSError, AttributeError, ValueError):
        return False, current_schedule()
    return True, current_schedule()


def try_rr(priority: int) -> tuple[bool, dict[str, Any]]:
    """Ask for ``SCHED_RR`` at ``priority`` (the AI and the camera helper sit below the core)."""
    setter = getattr(os, "sched_setscheduler", None)
    rr = getattr(os, "SCHED_RR", None)
    param_cls = getattr(os, "sched_param", None)
    if setter is None or rr is None or param_cls is None:
        return False, current_schedule()
    try:
        setter(0, rr, param_cls(priority))
    except (OSError, AttributeError, ValueError):
        return False, current_schedule()
    return True, current_schedule()


def drop_realtime() -> dict[str, Any]:
    """Return the calling process to ``SCHED_OTHER`` at priority 0.

    A child inherits its parent's scheduling policy across fork/exec, so the untrusted
    detector and the stress workers must give back the supervisor's SCHED_FIFO before
    they take their nice value. Best effort, like the rest of this module.
    """
    setter = getattr(os, "sched_setscheduler", None)
    other = getattr(os, "SCHED_OTHER", None)
    param_cls = getattr(os, "sched_param", None)
    if setter is not None and other is not None and param_cls is not None:
        # Linux/macOS want priority 0 for SCHED_OTHER; QNX reserves 0 for its idle thread and
        # rejects it, so fall back to QNX's lowest ordinary user priorities (default user priority
        # is 10). Without the fallback the call failed silently and the detector stayed SCHED_FIFO.
        for priority in (0, 8, 9, 10):
            try:
                setter(0, other, param_cls(priority))
                break
            except (OSError, AttributeError, ValueError):
                continue
    return current_schedule()


def is_realtime(sched: dict[str, Any] | None) -> bool:
    return bool(sched) and sched.get("policy") in _REALTIME


def describe(sched: dict[str, Any]) -> str:
    """One-line label: ``SCHED_FIFO 30`` or ``SCHED_OTHER nice +10``."""
    if is_realtime(sched):
        return f"{sched['policy']} {sched['priority']}"
    nice = int(sched.get("nice", 0))
    return f"{sched.get('policy', SCHED_OTHER)} nice {nice:+d}"
