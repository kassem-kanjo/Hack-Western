"""Real-time scheduling helpers: what the status file may claim about each process."""

from __future__ import annotations

import pytest

from darkfleet.edge import realtime

FIFO = {"policy": "SCHED_FIFO", "priority": 30, "nice": 0}
OTHER = {"policy": "SCHED_OTHER", "priority": 0, "nice": 10}


def test_current_schedule_always_returns_fielded_data() -> None:
    sched = realtime.current_schedule()

    assert set(sched) == {"policy", "priority", "nice"}
    assert sched["policy"].startswith("SCHED_")
    assert isinstance(sched["priority"], int)
    assert isinstance(sched["nice"], int)


def test_describe_labels_fifo_and_nice() -> None:
    assert realtime.describe(FIFO) == "SCHED_FIFO 30"
    assert realtime.describe(OTHER) == "SCHED_OTHER nice +10"
    assert realtime.describe({"policy": "SCHED_OTHER", "priority": 0, "nice": 0}) == ("SCHED_OTHER nice +0")


def test_is_realtime_only_for_fifo_and_rr() -> None:
    assert realtime.is_realtime(FIFO)
    assert realtime.is_realtime({"policy": "SCHED_RR", "priority": 20, "nice": 0})
    assert not realtime.is_realtime(OTHER)
    assert not realtime.is_realtime({})
    assert not realtime.is_realtime(None)


def test_try_fifo_falls_back_when_the_os_has_no_scheduler_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(realtime.os, "sched_setscheduler", raising=False)
    monkeypatch.delattr(realtime.os, "sched_param", raising=False)

    applied, sched = realtime.try_fifo(30)

    assert applied is False
    assert set(sched) == {"policy", "priority", "nice"}


def test_try_fifo_falls_back_when_the_os_refuses_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny(*_args: object) -> None:
        raise PermissionError("EPERM: not the superuser")

    monkeypatch.setattr(realtime.os, "sched_setscheduler", deny, raising=False)
    monkeypatch.setattr(realtime.os, "sched_param", lambda priority: priority, raising=False)

    applied, sched = realtime.try_fifo(30)

    assert applied is False
    assert sched["policy"].startswith("SCHED_")


def test_try_fifo_reports_success_and_asks_for_the_requested_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int, object]] = []

    def accept(pid: int, policy: int, param: object) -> None:
        calls.append((pid, policy, param))

    monkeypatch.setattr(realtime.os, "sched_setscheduler", accept, raising=False)
    monkeypatch.setattr(realtime.os, "sched_param", lambda priority: ("param", priority), raising=False)
    monkeypatch.setattr(realtime.os, "SCHED_FIFO", 1, raising=False)

    applied, _sched = realtime.try_fifo(25)

    assert applied is True
    assert calls == [(0, 1, ("param", 25))]


def test_drop_realtime_returns_to_sched_other(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, int, object]] = []

    def accept(pid: int, policy: int, param: object) -> None:
        calls.append((pid, policy, param))

    monkeypatch.setattr(realtime.os, "sched_setscheduler", accept, raising=False)
    monkeypatch.setattr(realtime.os, "sched_param", lambda priority: ("param", priority), raising=False)
    monkeypatch.setattr(realtime.os, "SCHED_OTHER", 0, raising=False)

    realtime.drop_realtime()

    assert calls == [(0, 0, ("param", 0))]


def test_drop_realtime_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(*_args: object) -> None:
        raise PermissionError("EPERM")

    monkeypatch.setattr(realtime.os, "sched_setscheduler", deny, raising=False)
    monkeypatch.setattr(realtime.os, "sched_param", lambda priority: priority, raising=False)
    monkeypatch.setattr(realtime.os, "SCHED_OTHER", 0, raising=False)

    assert realtime.drop_realtime()["policy"].startswith("SCHED_")


def test_nice_is_read_without_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: object) -> int:
        raise OSError("no nice here")

    monkeypatch.setattr(realtime.os, "nice", broken)

    assert realtime.current_schedule()["nice"] == 0
