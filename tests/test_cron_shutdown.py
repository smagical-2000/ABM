"""A finished cron leg must actually EXIT.

Each leg opens one psycopg ConnectionPool per repository, and each pool runs
worker threads. `sys.exit()` finalizes the interpreter, which tries to join
those threads and raises `PythonFinalizationError: cannot join thread at
interpreter shutdown` (reproducible on every local run of
scripts/run_linkedin_tofu.py) — or just blocks. The work is already committed
at that point, but the CONTAINER never exits, and Railway will not start the
next tick of a service whose previous invocation is still "running": the most
plausible cause of the Jul 24-27 silence.

So the entrypoints close their pools, flush by hand (os._exit skips the atexit
flush) and hard-exit.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from auto_search.ops.shutdown import close_pools, hard_exit

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


class _Repo:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _Rude:
    def close(self):
        raise RuntimeError("pool already broken")


class TestClosePools:
    def test_closes_everything_that_can_be_closed(self):
        a, b = _Repo(), _Repo()
        close_pools([a, b, object(), None])
        assert a.closed and b.closed

    def test_a_failing_close_never_propagates(self):
        good = _Repo()
        close_pools([_Rude(), good])           # the rude one must not stop the rest
        assert good.closed

    def test_empty_is_a_no_op(self):
        close_pools(None)
        close_pools([])


class TestHardExit:
    def test_closes_flushes_then_os_exits(self, monkeypatch):
        repo = _Repo()
        flushed: list[str] = []
        exited: list[int] = []
        monkeypatch.setattr("auto_search.ops.shutdown.os._exit",
                            lambda c: exited.append(c))
        monkeypatch.setattr("sys.stdout.flush", lambda: flushed.append("out"),
                            raising=False)
        hard_exit(3, repo)
        assert repo.closed                      # pools closed BEFORE the exit
        assert flushed == ["out"]               # os._exit skips the atexit flush
        assert exited == [3]

    def test_exit_code_survives_a_broken_pool(self, monkeypatch):
        exited: list[int] = []
        monkeypatch.setattr("auto_search.ops.shutdown.os._exit",
                            lambda c: exited.append(c))
        hard_exit(1, _Rude())
        assert exited == [1]


_LINGERING_THREAD = """
import sys, threading, time
sys.path.insert(0, {root!r})
from auto_search.ops.shutdown import hard_exit

# Stands in for a psycopg pool worker: a live non-daemon thread that the
# interpreter would try to join at shutdown.
threading.Thread(target=lambda: time.sleep(120), daemon=False).start()
print("work done", flush=True)
hard_exit(7)
"""


class TestHardExitReallyExits:
    def test_process_exits_despite_a_live_pool_thread(self, tmp_path):
        """The actual hang: sys.exit() would block joining the worker for 120s.
        hard_exit returns the code immediately."""
        import subprocess
        import sys as _sys

        script = tmp_path / "leg.py"
        script.write_text(_LINGERING_THREAD.format(
            root=str(Path(__file__).resolve().parent.parent)))
        p = subprocess.run([_sys.executable, str(script)], capture_output=True,
                           text=True, timeout=20)
        assert p.returncode == 7
        assert "work done" in p.stdout      # os._exit skipped the atexit flush


class TestEntrypointsHardExit:
    @pytest.mark.parametrize("script", ["run_linkedin_tofu.py", "run_daily.py"])
    def test_entrypoint_hard_exits_instead_of_sys_exit(self, script):
        src = (_SCRIPTS / script).read_text()
        assert re.search(r"hard_exit\s*\(\s*main\s*\(\s*\)", src), (
            f"{script} must end with hard_exit(main()) — sys.exit() can hang the "
            "container on psycopg pool threads")
        assert not re.search(r"^\s*sys\.exit\s*\(\s*main\s*\(\s*\)\s*\)", src,
                             re.MULTILINE)

    def test_tofu_leg_closes_the_pools_it_opened(self):
        src = (_SCRIPTS / "run_linkedin_tofu.py").read_text()
        assert "close_pools(" in src
