"""Linux ``sysfs_notify`` event watcher for ``raspberrypi-hwmon``."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import select
import threading
from typing import TYPE_CHECKING, Final, Protocol, Self

from rpi_bad_power._sysfs import parse_alarm_value
from rpi_bad_power.exceptions import RpiBadPowerError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from types import TracebackType

_LOGGER = logging.getLogger("rpi_bad_power")

_DONE = object()
_JOIN_TIMEOUT_SECONDS: Final[float] = 5.0
_READ_CHUNK_SIZE: Final[int] = 64


class _Poller(Protocol):
    """Minimal protocol matching ``select.poll()``."""

    def register(self, fd: int, eventmask: int = ...) -> None:
        """Register a file descriptor with the poller."""

    def unregister(self, fd: int) -> None:
        """Unregister a file descriptor from the poller."""

    def poll(self, timeout: float | None = None) -> list[tuple[int, int]]:
        """Block until registered file descriptors have events."""


def supports_sysfs_poll() -> bool:
    """Return True if the current platform supports ``select.poll`` with ``POLLPRI``."""
    return hasattr(select, "poll") and hasattr(select, "POLLPRI")


def _read_alarm_fd(fd: int, path: Path) -> bool:
    """Seek to offset 0, read all bytes from ``fd``, and parse the alarm state."""
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, _READ_CHUNK_SIZE)
            if not chunk:
                break
            chunks.append(chunk)
    except OSError as err:
        raise RpiBadPowerError.from_os_error(
            "Failed to read under-voltage status",
            err,
            path,
        ) from err

    try:
        raw = b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as err:
        raise RpiBadPowerError(
            f"Failed to decode under-voltage status from {path}: {err}"
        ) from err

    return parse_alarm_value(raw, path)


class AsyncUnderVoltageWatcher:
    """Async iterator & context manager watching ``in0_lcrit_alarm`` via ``POLLPRI``.

    Uses a lightweight dedicated daemon thread with a self-pipe wakeup channel
    so it never occupies the asyncio executor pool and cancels immediately with
    zero file descriptor leaks.

    Recommended usage::

        async with under_voltage.async_watch() as watcher:
            async for is_under_voltage in watcher:
                ...
    """

    def __init__(
        self,
        alarm_path: Path,
        *,
        emit_initial: bool = True,
        poll_factory: Callable[[], _Poller] | None = None,
    ) -> None:
        """Initialize the sysfs event watcher (no OS resources allocated yet)."""
        self._alarm_path = alarm_path
        self._emit_initial = emit_initial
        self._poll_factory: Callable[[], _Poller] = poll_factory or select.poll

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._started = False
        self._closed = False
        self._wake_w: int | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[bool | BaseException | object] | None = None

    def _start(self) -> None:
        """Allocate wakeup pipe and start the background polling thread."""
        with self._lock:
            if self._closed:
                raise RuntimeError("Watcher has already been closed")
            if self._started:
                return

            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[bool | BaseException | object] = asyncio.Queue()
            wake_r, wake_w = os.pipe()
            os.set_blocking(wake_r, False)
            os.set_blocking(wake_w, False)

            thread = threading.Thread(
                target=self._watch_loop,
                args=(wake_r, loop, queue),
                name="rpi-bad-power-watch",
                daemon=True,
            )
            self._loop = loop
            self._queue = queue
            self._wake_w = wake_w
            self._thread = thread
            self._started = True
            thread.start()

    def _enqueue(
        self,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[bool | BaseException | object],
        item: bool | BaseException | object,
    ) -> bool:
        """Thread-safely push an item onto the consumer's asyncio queue."""
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:
            self._stop_event.set()
            return False
        return True

    def _watch_loop(
        self,
        wake_r: int,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[bool | BaseException | object],
    ) -> None:
        """Block on ``select.poll`` for ``POLLPRI | POLLERR`` until stopped."""
        alarm_fd: int | None = None
        poller: _Poller | None = None
        try:
            open_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            try:
                alarm_fd = os.open(self._alarm_path, open_flags)
            except OSError as err:
                self._enqueue(
                    loop,
                    queue,
                    RpiBadPowerError.from_os_error(
                        "Failed to open under-voltage alarm file",
                        err,
                        self._alarm_path,
                    ),
                )
                return

            try:
                last_value = _read_alarm_fd(alarm_fd, self._alarm_path)
            except RpiBadPowerError as err:
                self._enqueue(loop, queue, err)
                return

            if self._emit_initial and not self._enqueue(loop, queue, last_value):
                return

            poller = self._poll_factory()
            poller.register(alarm_fd, select.POLLPRI | select.POLLERR)
            poller.register(wake_r, select.POLLIN)

            while not self._stop_event.is_set():
                try:
                    events = poller.poll()
                except InterruptedError:
                    continue
                except OSError as err:
                    self._enqueue(
                        loop,
                        queue,
                        RpiBadPowerError.from_os_error(
                            "Failed while polling under-voltage status",
                            err,
                            self._alarm_path,
                        ),
                    )
                    return

                if self._stop_event.is_set():
                    break

                for fd, event_mask in events:
                    if fd == wake_r:
                        self._stop_event.set()
                        break
                    if fd == alarm_fd:
                        if event_mask & select.POLLNVAL:
                            self._enqueue(
                                loop,
                                queue,
                                RpiBadPowerError(
                                    f"Invalid file descriptor while watching "
                                    f"{self._alarm_path}"
                                ),
                            )
                            return
                        try:
                            value = _read_alarm_fd(alarm_fd, self._alarm_path)
                        except RpiBadPowerError as err:
                            self._enqueue(loop, queue, err)
                            return
                        if value != last_value:
                            last_value = value
                            if not self._enqueue(loop, queue, value):
                                return
        finally:
            if poller is not None and alarm_fd is not None:
                with contextlib.suppress(KeyError, OSError):
                    poller.unregister(alarm_fd)
                with contextlib.suppress(KeyError, OSError):
                    poller.unregister(wake_r)
            if alarm_fd is not None:
                os.close(alarm_fd)
            os.close(wake_r)
            self._enqueue(loop, queue, _DONE)

    def close(self) -> None:
        """Stop watching, join the background thread, and close file descriptors."""
        with self._lock:
            self._closed = True
            self._stop_event.set()
            wake_w = self._wake_w
            thread = self._thread

        if wake_w is not None:
            with contextlib.suppress(OSError):
                os.write(wake_w, b"\x01")

        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=_JOIN_TIMEOUT_SECONDS)

        with self._lock:
            if self._wake_w is not None:
                fd = self._wake_w
                self._wake_w = None
                os.close(fd)

    async def aclose(self) -> None:
        """Asynchronously close the watcher (idempotent)."""
        self.close()

    async def __aenter__(self) -> Self:
        """Start the watcher when entering an ``async with`` block."""
        self._start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Stop the watcher and release all OS resources on exit."""
        self.close()

    def __aiter__(self) -> Self:
        """Return self as an asynchronous iterator."""
        return self

    async def __anext__(self) -> bool:
        """Await the next under-voltage state change."""
        if not self._started:
            self._start()
        assert self._queue is not None

        try:
            item = await self._queue.get()
        except BaseException:
            self.close()
            raise

        if item is _DONE:
            self.close()
            raise StopAsyncIteration
        if isinstance(item, BaseException):
            self.close()
            raise item
        assert isinstance(item, bool)
        return item
