from __future__ import annotations

import asyncio
import errno
import os
from pathlib import Path
import queue
import select
import threading
from unittest.mock import patch

import pytest

from rpi_bad_power import (
    SYSFILE_HWMON_FILE,
    AsyncUnderVoltageWatcher,
    RpiBadPowerError,
    UnderVoltageLegacy,
    UnderVoltageNew,
)
from rpi_bad_power.watcher import _read_alarm_fd


def _count_open_fds() -> int:
    return len(list(Path("/proc/self/fd").iterdir()))


class _FakePoller:
    """Deterministic poller supporting simulated POLLPRI events and real wake_r."""

    def __init__(self) -> None:
        self.registered: dict[int, int] = {}
        self.alarm_fd: int | None = None
        self.wake_fd: int | None = None
        self.ready = threading.Event()
        self._events: queue.Queue[list[tuple[int, int]] | BaseException] = queue.Queue()

    def register(self, fd: int, eventmask: int = 0) -> None:
        self.registered[fd] = eventmask
        if eventmask & select.POLLPRI:
            self.alarm_fd = fd
        if eventmask & select.POLLIN:
            self.wake_fd = fd
        if self.alarm_fd is not None and self.wake_fd is not None:
            self.ready.set()

    def unregister(self, fd: int) -> None:
        self.registered.pop(fd, None)

    def push_alarm_event(self, mask: int = select.POLLPRI | select.POLLERR) -> None:
        assert self.alarm_fd is not None
        self._events.put([(self.alarm_fd, mask)])

    def push_error(self, err: BaseException) -> None:
        self._events.put(err)

    def poll(self, _timeout: float | None = None) -> list[tuple[int, int]]:
        real_poller = select.poll()
        assert self.wake_fd is not None
        real_poller.register(self.wake_fd, select.POLLIN)
        while True:
            try:
                item = self._events.get_nowait()
            except queue.Empty:
                pass
            else:
                if isinstance(item, BaseException):
                    raise item
                return item
            wake_events = real_poller.poll(10)
            if wake_events:
                return wake_events


def test_supports_push_flags(tmp_path: Path) -> None:
    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / SYSFILE_HWMON_FILE).write_text("0\n", encoding="utf-8")

    modern = UnderVoltageNew(hwmon)
    assert modern.supports_push is True

    legacy = UnderVoltageLegacy(tmp_path / "get_throttled")
    assert legacy.supports_push is False
    with pytest.raises(NotImplementedError, match="UnderVoltageLegacy"):
        legacy.async_watch()

    with patch("rpi_bad_power.supports_sysfs_poll", return_value=False):
        modern_no_poll = UnderVoltageNew(hwmon)
        assert modern_no_poll.supports_push is False
        with pytest.raises(NotImplementedError, match="POLLPRI"):
            modern_no_poll.async_watch()


async def test_watcher_real_select_poll_initial_and_clean_shutdown(
    tmp_path: Path,
) -> None:
    hwmon = tmp_path / "hwmon0"
    hwmon.mkdir()
    (hwmon / SYSFILE_HWMON_FILE).write_text("0\n", encoding="utf-8")

    before_fds = _count_open_fds()
    modern = UnderVoltageNew(hwmon)

    async with modern.async_watch() as watcher:
        initial = await asyncio.wait_for(anext(watcher), timeout=2.0)
        assert initial is False

    assert _count_open_fds() == before_fds


async def test_watcher_state_transitions_and_deduplication(tmp_path: Path) -> None:
    alarm_file = tmp_path / SYSFILE_HWMON_FILE
    alarm_file.write_text("0\n", encoding="utf-8")
    poller = _FakePoller()
    before_fds = _count_open_fds()

    watcher = AsyncUnderVoltageWatcher(alarm_file, poll_factory=lambda: poller)
    seen: list[bool] = []

    async with watcher:
        assert await asyncio.wait_for(anext(watcher), timeout=2.0) is False
        assert poller.ready.wait(timeout=2.0)
        assert poller.alarm_fd is not None
        assert poller.registered[poller.alarm_fd] == (select.POLLPRI | select.POLLERR)

        # Transition 0 -> 1
        alarm_file.write_text("1\n", encoding="utf-8")
        poller.push_alarm_event()
        seen.append(await asyncio.wait_for(anext(watcher), timeout=2.0))

        # Duplicate notification with same value (should be deduplicated)
        poller.push_alarm_event()
        # Followed by transition 1 -> 0
        await asyncio.sleep(0.02)
        alarm_file.write_text("0\n", encoding="utf-8")
        poller.push_alarm_event()
        seen.append(await asyncio.wait_for(anext(watcher), timeout=2.0))

    assert seen == [True, False]
    assert _count_open_fds() == before_fds


async def test_watcher_emit_initial_false(tmp_path: Path) -> None:
    alarm_file = tmp_path / SYSFILE_HWMON_FILE
    alarm_file.write_text("0\n", encoding="utf-8")
    poller = _FakePoller()

    watcher = AsyncUnderVoltageWatcher(
        alarm_file,
        emit_initial=False,
        poll_factory=lambda: poller,
    )
    async with watcher:
        assert poller.ready.wait(timeout=2.0)
        alarm_file.write_text("1\n", encoding="utf-8")
        poller.push_alarm_event()
        value = await asyncio.wait_for(anext(watcher), timeout=2.0)
        assert value is True


async def test_watcher_direct_iteration_and_task_cancellation(
    tmp_path: Path,
) -> None:
    alarm_file = tmp_path / SYSFILE_HWMON_FILE
    alarm_file.write_text("1\n", encoding="utf-8")
    poller = _FakePoller()
    before_fds = _count_open_fds()

    watcher = AsyncUnderVoltageWatcher(alarm_file, poll_factory=lambda: poller)
    received: list[bool] = []
    got_first = asyncio.Event()

    async def consumer() -> None:
        async for state in watcher:
            received.append(state)
            got_first.set()

    task = asyncio.create_task(consumer())
    await asyncio.wait_for(got_first.wait(), timeout=2.0)
    assert received == [True]

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert _count_open_fds() == before_fds


async def test_watcher_close_idempotency_and_reuse_error(tmp_path: Path) -> None:
    alarm_file = tmp_path / SYSFILE_HWMON_FILE
    alarm_file.write_text("0\n", encoding="utf-8")

    # Closing before starting is safe and allocates no fds.
    before_fds = _count_open_fds()
    watcher = AsyncUnderVoltageWatcher(alarm_file)
    await watcher.aclose()
    watcher.close()
    assert _count_open_fds() == before_fds

    with pytest.raises(RuntimeError, match="already been closed"):
        async with watcher:
            pass


async def test_watcher_open_and_initial_read_errors(tmp_path: Path) -> None:
    missing = tmp_path / "missing_alarm"
    watcher = AsyncUnderVoltageWatcher(missing)
    async with watcher:
        with pytest.raises(RpiBadPowerError) as exc_info:
            await asyncio.wait_for(anext(watcher), timeout=2.0)
    assert exc_info.value.errno == errno.ENOENT

    corrupt = tmp_path / "corrupt_alarm"
    corrupt.write_bytes(b"\xff\xfe")
    watcher2 = AsyncUnderVoltageWatcher(corrupt)
    async with watcher2:
        with pytest.raises(RpiBadPowerError, match="Failed to decode"):
            await asyncio.wait_for(anext(watcher2), timeout=2.0)


async def test_watcher_runtime_event_errors(tmp_path: Path) -> None:
    # 1. POLLNVAL event
    alarm_file = tmp_path / SYSFILE_HWMON_FILE
    alarm_file.write_text("0\n", encoding="utf-8")
    poller = _FakePoller()
    watcher = AsyncUnderVoltageWatcher(alarm_file, poll_factory=lambda: poller)
    async with watcher:
        assert await asyncio.wait_for(anext(watcher), timeout=2.0) is False
        assert poller.ready.wait(timeout=2.0)
        poller.push_alarm_event(select.POLLNVAL)
        with pytest.raises(RpiBadPowerError, match="Invalid file descriptor"):
            await asyncio.wait_for(anext(watcher), timeout=2.0)

    # 2. Invalid content after notification
    poller2 = _FakePoller()
    watcher2 = AsyncUnderVoltageWatcher(alarm_file, poll_factory=lambda: poller2)
    async with watcher2:
        assert await asyncio.wait_for(anext(watcher2), timeout=2.0) is False
        assert poller2.ready.wait(timeout=2.0)
        alarm_file.write_text("bad\n", encoding="utf-8")
        poller2.push_alarm_event()
        with pytest.raises(RpiBadPowerError, match="Unexpected under-voltage value"):
            await asyncio.wait_for(anext(watcher2), timeout=2.0)

    # 3. InterruptedError retried, then OSError propagated
    alarm_file.write_text("0\n", encoding="utf-8")
    poller3 = _FakePoller()
    watcher3 = AsyncUnderVoltageWatcher(alarm_file, poll_factory=lambda: poller3)
    async with watcher3:
        assert await asyncio.wait_for(anext(watcher3), timeout=2.0) is False
        assert poller3.ready.wait(timeout=2.0)
        poller3.push_error(InterruptedError())
        poller3.push_error(OSError(errno.EIO, os.strerror(errno.EIO)))
        with pytest.raises(RpiBadPowerError) as exc_info:
            await asyncio.wait_for(anext(watcher3), timeout=2.0)
        assert exc_info.value.errno == errno.EIO


async def test_watcher_stop_async_iteration_when_closed(tmp_path: Path) -> None:
    alarm_file = tmp_path / SYSFILE_HWMON_FILE
    alarm_file.write_text("0\n", encoding="utf-8")
    poller = _FakePoller()

    watcher = AsyncUnderVoltageWatcher(alarm_file, poll_factory=lambda: poller)
    async with watcher:
        assert await asyncio.wait_for(anext(watcher), timeout=2.0) is False
        assert poller.ready.wait(timeout=2.0)
        watcher.close()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(watcher), timeout=2.0)


def test_read_alarm_fd_os_error(tmp_path: Path) -> None:
    path = tmp_path / SYSFILE_HWMON_FILE
    with (
        patch("os.lseek", side_effect=OSError(errno.EIO, os.strerror(errno.EIO))),
        pytest.raises(RpiBadPowerError) as exc_info,
    ):
        _read_alarm_fd(999, path)
    assert exc_info.value.errno == errno.EIO


async def test_watcher_start_idempotent_and_closed_loop_enqueue(
    tmp_path: Path,
) -> None:
    alarm_file = tmp_path / SYSFILE_HWMON_FILE
    alarm_file.write_text("0\n", encoding="utf-8")
    poller = _FakePoller()

    watcher = AsyncUnderVoltageWatcher(alarm_file, poll_factory=lambda: poller)
    async with watcher:
        watcher._start()  # idempotent when already started
        assert await asyncio.wait_for(anext(watcher), timeout=2.0) is False

    # _enqueue handles closed loop gracefully without raising
    closed_loop = asyncio.new_event_loop()
    closed_loop.close()
    q: asyncio.Queue[bool | BaseException | object] = asyncio.Queue()
    watcher2 = AsyncUnderVoltageWatcher(alarm_file)
    assert watcher2._enqueue(closed_loop, q, True) is False
