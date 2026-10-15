"""Single-instance mode across real processes: handoff, a crashed owner's stale lock, a hung owner."""

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtNetwork")

from PySide6.QtCore import QCoreApplication  # noqa: E402

from vlclone.core.instance import HandoffMessage, server_name  # noqa: E402
from vlclone.ui.instance import InstanceGuard  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def where(tmp_path):
    return server_name(str(tmp_path), "test-user"), tmp_path / "instance.lock"


def run_py(code: str, *args, wait=True):
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONPATH": str(ROOT)}
    cmd = [sys.executable, "-c", textwrap.dedent(code), *map(str, args)]
    if wait:
        return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60)
    return subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


SENDER = """
    import sys
    from PySide6.QtCore import QCoreApplication
    from vlclone.core.instance import HandoffMessage
    from vlclone.ui.instance import InstanceGuard
    app = QCoreApplication([])
    g = InstanceGuard(sys.argv[1], sys.argv[2])
    if g.acquire():
        print("PRIMARY"); sys.exit(3)
    ok = g.send(HandoffMessage(sys.argv[4:], sys.argv[3] or None, "/home/x"), timeout_s=float(sys.argv[3] == "slow" or 4))
    print("SENT" if ok else "NOANSWER")
    sys.exit(0 if ok else 4)
"""

CRASHER = """
    import os, sys
    from PySide6.QtCore import QCoreApplication
    from vlclone.ui.instance import InstanceGuard
    app = QCoreApplication([])
    g = InstanceGuard(sys.argv[1], sys.argv[2])
    assert g.acquire() and g.primary
    print("OWNER", flush=True)
    if sys.argv[3] == "crash":
        os._exit(1)        # no cleanup: lock file and socket file stay behind
    import time
    time.sleep(30)          # hung: holds the lock, never runs the event loop
"""


def pump(app, cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not cond():
        app.processEvents()
        time.sleep(0.01)
    return cond()


def test_second_launch_hands_files_to_the_first(app, where):
    name, lock = where
    primary = InstanceGuard(name, lock)
    assert primary.acquire() and primary.primary
    got = []
    primary.received.connect(got.append)
    proc = run_py(SENDER, name, lock, "enqueue", "D:/Films/a é.mkv", "rel/b.mkv", wait=False)
    assert pump(app, lambda: got)
    out, err = proc.communicate(timeout=30)
    assert proc.returncode == 0 and "SENT" in out, err
    msg = got[0]
    assert msg.mode == "enqueue" and msg.inputs == ["D:/Films/a é.mkv", "rel/b.mkv"]
    assert msg.resolved_inputs()[1] == os.path.normpath("/home/x/rel/b.mkv")
    primary.close()
    app.processEvents()
    # Once the first instance quits, the next launch becomes the primary.
    again = InstanceGuard(name, lock)
    assert again.acquire() and again.primary
    again.close()


def test_stale_lock_after_crash_is_taken_over(app, where):
    name, lock = where
    result = run_py(CRASHER, name, lock, "crash")
    assert "OWNER" in result.stdout, result.stderr
    assert lock.exists()  # left behind by the crash
    guard = InstanceGuard(name, lock)
    assert guard.acquire() and guard.primary  # stale lock + stale socket recovered
    got = []
    guard.received.connect(got.append)
    proc = run_py(SENDER, name, lock, "", "/m/x.mkv", wait=False)
    assert pump(app, lambda: got)
    proc.communicate(timeout=30)
    assert got[0].inputs == ["/m/x.mkv"] and got[0].mode is None
    guard.close()
    app.processEvents()


def test_hung_owner_does_not_swallow_the_launch(app, where):
    name, lock = where
    owner = run_py(CRASHER, name, lock, "hang", wait=False)
    try:
        assert owner.stdout.readline().strip() == "OWNER"
        t = time.monotonic()
        result = run_py(SENDER, name, lock, "slow", "/m/x.mkv")
        assert "NOANSWER" in result.stdout and result.returncode == 4, result.stderr
        assert time.monotonic() - t < 20  # gives up, so the app can open the file itself
    finally:
        owner.kill()
        owner.wait()
