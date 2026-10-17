"""Stream HTTP and WebSockets between network namespaces over shared Unix sockets.

Only the two provisioned local services are exposed. No arbitrary destinations
or host command execution are accepted by the relay.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import tempfile
import threading
from pathlib import Path

_logger = logging.getLogger(__name__)

# macOS sun_path is 104 bytes; Linux is 108. A longer path makes bind return
# ENOENT on Darwin instead of creating the socket.
_SUN_PATH_LIMIT = 104 if sys.platform == "darwin" else 108


@contextlib.contextmanager
def _socket_address(path: Path):
    """Bind *path* even when it is longer than the platform sun_path limit.

    Linux can name the parent through ``/proc/self/fd``. macOS has no proc
    filesystem, so a short symlink in ``/tmp`` points at the same directory
    and the socket file still lands at *path*.
    """
    path = path.resolve()
    if len(os.fsencode(path)) < _SUN_PATH_LIMIT:
        yield str(path)
        return
    if sys.platform.startswith("linux"):
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            yield f"/proc/self/fd/{fd}/{path.name}"
        finally:
            os.close(fd)
        return
    short_dir = Path(tempfile.mkdtemp(prefix="omni-sock-", dir="/tmp"))
    link = short_dir / "d"
    try:
        link.symlink_to(path.parent)
        yield str(link / path.name)
    finally:
        link.unlink(missing_ok=True)
        short_dir.rmdir()


class Relay:
    def __init__(
        self,
        *,
        unix_listener: Path | None = None,
        tcp_target: tuple[str, int] | None = None,
        unix_target: Path | None = None,
    ):
        self.unix_listener = unix_listener
        self.tcp_target = tcp_target
        self.unix_target = unix_target
        self._owns_socket = False
        self.port = 0
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._connection_tasks: set[asyncio.Task] = set()

    async def _connect(self, reader, writer):
        # Half-closed transports can lose their event-loop references while
        # the handler is still waiting for the response in the other direction.
        task = asyncio.current_task()
        assert task is not None
        self._connection_tasks.add(task)
        task.add_done_callback(self._connection_tasks.discard)
        remote = None
        tasks = []
        try:
            if self.unix_target:
                with _socket_address(self.unix_target) as address:
                    other, remote = await asyncio.open_unix_connection(address)
            else:
                other, remote = await asyncio.open_connection(*self.tcp_target)

            async def copy(source, destination):
                while data := await source.read(65536):
                    destination.write(data)
                    await destination.drain()
                if destination.can_write_eof():
                    destination.write_eof()
                    await destination.drain()

            tasks = [
                asyncio.create_task(copy(reader, remote)),
                asyncio.create_task(copy(other, writer)),
            ]
            await asyncio.gather(*tasks)
        except (OSError, ConnectionError):
            # Peer disconnects are expected; close both streams without killing the relay.
            _logger.debug("Relay connection closed", exc_info=True)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for stream in (writer, remote):
                if stream:
                    stream.close()
                    with contextlib.suppress(OSError):
                        await stream.wait_closed()

    async def _start(self):
        if self.unix_listener:
            if self.unix_listener.parent.stat().st_mode & 0o077:
                raise ValueError("Unix relay sockets require an owner-only parent directory")
            with _socket_address(self.unix_listener) as address:
                self.server = await asyncio.start_unix_server(self._connect, address)
            self._owns_socket = True
            self.unix_listener.chmod(0o600)
        else:
            self.server = await asyncio.start_server(self._connect, "127.0.0.1", 0)
            self.port = self.server.sockets[0].getsockname()[1]

    async def _close(self):
        if hasattr(self, "server"):
            self.server.close()
        # Python 3.12 waits for accepted streams too; close their handlers first.
        tasks = asyncio.all_tasks() - {asyncio.current_task()}
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if hasattr(self, "server"):
            await self.server.wait_closed()

    def __enter__(self):
        self.thread.start()
        try:
            asyncio.run_coroutine_threadsafe(self._start(), self.loop).result(timeout=10)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_):
        try:
            asyncio.run_coroutine_threadsafe(self._close(), self.loop).result(timeout=10)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=10)
            self.loop.close()
            if self._owns_socket:
                self.unix_listener.unlink(missing_ok=True)
