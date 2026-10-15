"""Single-instance transport: a QLockFile decides who is first, a QLocalServer receives files.

The first VLClone process takes ``instance.lock`` in the config folder and
listens on a local socket (a named pipe on Windows). Later processes fail
to take the lock, send their command line to the socket (protocol in
``core/instance.py``) and exit.

A crash leaves the lock file and, on Linux/macOS, a socket file behind.
QLockFile treats a lock whose process no longer exists as stale and takes
it over; the new owner then removes the leftover socket before listening.
If the owner is alive but does not answer (hung), the new process gives
up after a few seconds and runs on its own instead of exiting silently.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import shiboken6
from PySide6.QtCore import QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from ..core.instance import HandoffMessage, decode_stream

log = logging.getLogger("vlclone.instance")


class InstanceGuard(QObject):
    received = Signal(object)  # HandoffMessage, on the Qt thread

    def __init__(self, name: str, lock_path: str | Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.name = name
        self.lock = QLockFile(str(lock_path))
        self.lock.setStaleLockTime(0)  # never stale by age: only when its process is gone
        self.server: QLocalServer | None = None
        self._buffers: dict[QLocalSocket, bytes] = {}

    @property
    def primary(self) -> bool:
        return self.server is not None

    def acquire(self) -> bool:
        """True if this process is the first one (it now owns the lock and listens)."""
        if not self.lock.tryLock(150):
            if self.lock.error() == QLockFile.LockError.LockFailedError:
                return False
            # PermissionError / UnknownError: a read-only config folder must not stop the player.
            log.warning("instance lock unavailable (%s); running without single-instance mode",
                        self.lock.error())
            return True
        QLocalServer.removeServer(self.name)  # a crashed owner's socket file (Unix); no-op on Windows
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(self.name):
            log.warning("could not listen on %s: %s", self.name, server.errorString())
            return True
        server.newConnection.connect(self._accept)
        self.server = server
        log.info("primary instance, listening on %s", self.name)
        return True

    # ------------------------------------------------------------ primary side
    def _accept(self) -> None:
        while self.server is not None and self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            self._buffers[sock] = b""
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(lambda s=sock: self._drop(s))
            if sock.bytesAvailable():
                self._read(sock)

    def _read(self, sock: QLocalSocket) -> None:
        messages, rest = decode_stream(self._buffers.get(sock, b"") + bytes(sock.readAll().data()))
        self._buffers[sock] = rest
        for msg in messages:
            sock.write(b'{"ok": true}\n')
            sock.flush()
            log.info("handoff: %d input(s), mode %s", len(msg.inputs), msg.mode)
            self.received.emit(msg)

    def _drop(self, sock: QLocalSocket) -> None:
        self._buffers.pop(sock, None)
        if shiboken6.isValid(sock):
            sock.deleteLater()

    def close(self) -> None:
        for sock in list(self._buffers):
            if shiboken6.isValid(sock):
                sock.readyRead.disconnect()
                sock.disconnected.disconnect()
                sock.abort()
        self._buffers.clear()
        if self.server is not None:
            self.server.newConnection.disconnect()
            self.server.close()
            self.server.deleteLater()
            self.server = None
        if self.lock.isLocked():
            self.lock.unlock()

    # ----------------------------------------------------------- secondary side
    def send(self, msg: HandoffMessage, timeout_s: float = 4.0) -> bool:
        """Hand ``msg`` to the running instance; True once it acknowledged."""
        _allow_foreground()
        deadline = time.monotonic() + timeout_s
        sock = QLocalSocket()
        while True:
            sock.connectToServer(self.name)
            if sock.waitForConnected(400):
                break
            if time.monotonic() > deadline:
                log.warning("running instance does not answer on %s", self.name)
                return False
            time.sleep(0.1)  # the first instance may still be starting its server
        sock.write(msg.encode())
        if not sock.waitForBytesWritten(1000):
            return False
        reply = b""
        while b"\n" not in reply and time.monotonic() < deadline:
            if not sock.waitForReadyRead(200) and sock.state() != QLocalSocket.LocalSocketState.ConnectedState:
                break
            reply += bytes(sock.readAll().data())
        sock.disconnectFromServer()
        return b'"ok"' in reply


def _allow_foreground() -> None:
    """Windows only lets the foreground process hand focus on: let the running window come to front."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
        except Exception:
            pass
