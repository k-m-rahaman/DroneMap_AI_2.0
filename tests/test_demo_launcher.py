"""The public-demo launcher owns its children, even on Windows console exit."""
import ctypes
import io
import json
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import MagicMock, Mock

import pytest

from tools import serve_demo


def native_job(monkeypatch, **results):
    kernel = Mock()
    for name, default in {
        "CreateJobObjectW": 321,
        "SetHandleInformation": 1,
        "SetInformationJobObject": 1,
        "AssignProcessToJobObject": 1,
        "CloseHandle": 1,
    }.items():
        getattr(kernel, name).return_value = results.get(name, default)
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=kernel), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda code: OSError(code, "Windows job error"), raising=False)
    return kernel


def child(handle=42):
    process = Mock(_handle=handle)
    process.poll.return_value = None
    process.wait.return_value = 0
    process.terminate.side_effect = lambda: setattr(process.poll, "return_value", 0)
    return process


def test_job_handle_is_not_inherited_and_kills_children_on_close(monkeypatch):
    kernel = native_job(monkeypatch)
    job = serve_demo.WindowsJob()
    kernel.CreateJobObjectW.assert_called_once_with(None, None)
    kernel.SetHandleInformation.assert_called_once_with(321, 1, 0)
    handle, kind, pointer, size = kernel.SetInformationJobObject.call_args.args
    assert handle == 321 and kind == 9
    assert pointer._obj.BasicLimitInformation.LimitFlags == 0x2000
    assert size == ctypes.sizeof(pointer._obj)
    job.assign(child(57))
    kernel.AssignProcessToJobObject.assert_called_once_with(321, 57)
    job.close()
    job.close()
    kernel.CloseHandle.assert_called_once_with(321)


@pytest.mark.parametrize("failed_api", ["SetHandleInformation", "SetInformationJobObject"])
def test_job_configuration_failure_releases_handle(monkeypatch, failed_api):
    kernel = native_job(monkeypatch, **{failed_api: 0})
    with pytest.raises(OSError, match="Windows job error"):
        serve_demo.WindowsJob()
    kernel.CloseHandle.assert_called_once_with(321)


def test_job_creation_failure_does_not_close_invalid_handle(monkeypatch):
    kernel = native_job(monkeypatch, CreateJobObjectW=0)
    with pytest.raises(OSError):
        serve_demo.WindowsJob()
    kernel.CloseHandle.assert_not_called()


def test_windows_supervision_failure_starts_no_process(monkeypatch):
    monkeypatch.setattr(serve_demo.sys, "platform", "win32")
    monkeypatch.setattr(serve_demo, "WindowsJob", Mock(side_effect=OSError("denied")))
    launch = Mock()
    monkeypatch.setattr(serve_demo.subprocess, "Popen", launch)
    with pytest.raises(RuntimeError, match="no server or public tunnel was started"):
        serve_demo.ChildSupervisor()
    launch.assert_not_called()


def test_both_windows_children_are_assigned_and_handles_not_inherited(monkeypatch):
    monkeypatch.setattr(serve_demo.sys, "platform", "win32")
    job = Mock()
    monkeypatch.setattr(serve_demo, "WindowsJob", Mock(return_value=job))
    app, tunnel = child(41), child(42)
    launch = Mock(side_effect=[app, tunnel])
    monkeypatch.setattr(serve_demo.subprocess, "Popen", launch)
    supervisor = serve_demo.ChildSupervisor()
    supervisor.start(["app"])
    supervisor.start(["tunnel"])
    assert [call.args for call in job.assign.call_args_list] == [(app,), (tunnel,)]
    assert all(call.kwargs["close_fds"] is True for call in launch.call_args_list)
    supervisor.close()
    app.terminate.assert_called_once()
    tunnel.terminate.assert_called_once()
    job.close.assert_called_once()


def test_failed_assignment_terminates_child_and_cannot_continue(monkeypatch):
    monkeypatch.setattr(serve_demo.sys, "platform", "win32")
    job = Mock()
    job.assign.side_effect = OSError("job rejected child")
    monkeypatch.setattr(serve_demo, "WindowsJob", Mock(return_value=job))
    app = child()
    launch = Mock(return_value=app)
    monkeypatch.setattr(serve_demo.subprocess, "Popen", launch)
    supervisor = serve_demo.ChildSupervisor()
    with pytest.raises(RuntimeError, match="tunnel will not be started"):
        supervisor.start(["app"])
    app.terminate.assert_called_once()
    supervisor.close()
    launch.assert_called_once()
    job.close.assert_called_once()


def test_non_windows_cleanup_remains_available(monkeypatch):
    monkeypatch.setattr(serve_demo.sys, "platform", "linux")
    create_job = Mock(side_effect=AssertionError("Windows-only API used"))
    monkeypatch.setattr(serve_demo, "WindowsJob", create_job)
    process = child()
    monkeypatch.setattr(serve_demo.subprocess, "Popen", Mock(return_value=process))
    supervisor = serve_demo.ChildSupervisor()
    supervisor.start(["app"])
    supervisor.close()
    process.terminate.assert_called_once()
    create_job.assert_not_called()


def test_cleanup_kills_and_reaps_unresponsive_child():
    process = child()
    process.wait.side_effect = [subprocess.TimeoutExpired("app", 10), 0]
    serve_demo.stop_process(process)
    process.kill.assert_called_once()
    assert process.wait.call_count == 2


def test_main_never_tunnels_private_backend_and_cleans_stale_url(monkeypatch, tmp_path):
    monkeypatch.setattr(serve_demo, "ROOT", tmp_path)
    monkeypatch.setattr(serve_demo.sys, "argv", ["serve_demo.py", "--cloudflared", str(tmp_path / "cloudflared.exe")])
    monkeypatch.setattr(serve_demo.socket, "socket", MagicMock())
    monkeypatch.setattr(serve_demo, "urlopen", lambda *args, **kwargs: io.BytesIO(json.dumps({"public_demo": False}).encode()))
    monkeypatch.setenv("DRONEMAP_PUBLIC_BASE_URL", "https://private.example.test")
    runtime = tmp_path / ".demo-runtime"
    runtime.mkdir()
    (runtime / "public-url.txt").write_text("https://stale.trycloudflare.com")
    app = child()
    supervisor = Mock()
    supervisor.start.return_value = app
    monkeypatch.setattr(serve_demo, "ChildSupervisor", Mock(return_value=supervisor))
    with pytest.raises(RuntimeError, match="not in public demo mode"):
        serve_demo.main()
    supervisor.start.assert_called_once()
    env = supervisor.start.call_args.kwargs["env"]
    assert env["DRONEMAP_PUBLIC_DEMO"] == "1"
    assert Path(env["DRONEMAP_DATA_DIR"]) == runtime / "visitor-data"
    assert "DRONEMAP_PUBLIC_BASE_URL" not in env
    supervisor.close.assert_called_once()
    assert not (runtime / "public-url.txt").exists()


def test_tunnel_api_error_is_never_advertised_as_public_demo(tmp_path, capsys):
    assert serve_demo.relay_tunnel_output([
        'failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel": timeout\n',
        'Registered tunnel connection\n',
    ], tmp_path) is False
    assert not (tmp_path / "public-url.txt").exists()
    assert "PUBLIC SIH DEMO:" not in capsys.readouterr().out


def test_candidate_url_is_not_published_before_registration(tmp_path):
    assert serve_demo.relay_tunnel_output([
        '| https://example-demo.trycloudflare.com |\n',
    ], tmp_path) is False
    assert not (tmp_path / "public-url.txt").exists()


def test_registered_tunnel_publishes_visitor_url_only_once(tmp_path, capsys):
    def logs():
        yield '| https://example-demo.trycloudflare.com |\n'
        assert not (tmp_path / "public-url.txt").exists()
        yield 'INF Registered tunnel connection connIndex=0\n'
        assert (tmp_path / "public-url.txt").read_text().strip() == "https://example-demo.trycloudflare.com"
        yield 'INF Registered tunnel connection connIndex=0\n'
    assert serve_demo.relay_tunnel_output(logs(), tmp_path) is True
    assert capsys.readouterr().out.count("PUBLIC SIH DEMO:") == 1


@pytest.mark.parametrize("logs", [
    ['| https://example-demo.trycloudflare.com |\n', 'ERR Unauthorized: Tunnel not found\n'],
    ['| https://example-demo.trycloudflare.com |\n'],
    ['| https://example-demo.trycloudflare.com |\n', 'INF Registered tunnel connection\n'],
    ['| https://example-demo.trycloudflare.com |\n', 'INF Registered tunnel connection\n', 'ERR Unauthorized: Tunnel not found\n'],
])
def test_tunnel_failure_or_eof_stops_children_and_clears_url(monkeypatch, tmp_path, logs):
    monkeypatch.setattr(serve_demo, "ROOT", tmp_path)
    monkeypatch.setattr(serve_demo.sys, "argv", ["serve_demo.py", "--cloudflared", str(tmp_path / "cloudflared.exe")])
    monkeypatch.setattr(serve_demo.socket, "socket", MagicMock())
    monkeypatch.setattr(serve_demo, "urlopen", lambda *args, **kwargs: io.BytesIO(b'{"public_demo":true}'))
    app, tunnel = child(), child()
    tunnel.stdout = iter(logs)
    tunnel.wait.return_value = 0
    supervisor = Mock()
    supervisor.start.side_effect = [app, tunnel]
    monkeypatch.setattr(serve_demo, "ChildSupervisor", Mock(return_value=supervisor))
    with pytest.raises(RuntimeError, match="expired|stopped"):
        serve_demo.main()
    supervisor.close.assert_called_once()
    assert not (tmp_path / ".demo-runtime" / "public-url.txt").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job lifetime integration")
def test_windows_parent_termination_kills_owned_child(tmp_path):
    """Force-exit bypasses Python cleanup, like closing its console window."""
    from ctypes import wintypes

    marker = tmp_path / "owned-child.pid"
    script = (
        "from tools.serve_demo import ChildSupervisor\n"
        "from pathlib import Path\n"
        "import sys, time\n"
        "supervisor = ChildSupervisor()\n"
        "child = supervisor.start([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"Path({str(marker)!r}).write_text(str(child.pid))\n"
        "while True: time.sleep(1)\n"
    )
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    helper = subprocess.Popen([sys.executable, "-u", "-c", script], cwd=serve_demo.ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
    owned_handle = None
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and helper.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists(), "The child supervisor failed to start: " + (
            helper.stderr.read().decode(errors="replace") if helper.poll() is not None else "timed out")
        owned_handle = kernel.OpenProcess(0x00100001, False, int(marker.read_text()))
        assert owned_handle, ctypes.WinError(ctypes.get_last_error())
        assert kernel.WaitForSingleObject(owned_handle, 0) == 258  # WAIT_TIMEOUT: alive
        helper.kill()
        helper.wait(timeout=10)
        assert kernel.WaitForSingleObject(owned_handle, 10000) == 0  # WAIT_OBJECT_0: exited
    finally:
        if helper.poll() is None:
            helper.kill()
            helper.wait(timeout=10)
        if owned_handle:
            if kernel.WaitForSingleObject(owned_handle, 0) == 258:
                kernel.TerminateProcess(owned_handle, 1)
                kernel.WaitForSingleObject(owned_handle, 10000)
            kernel.CloseHandle(owned_handle)
        helper.communicate(timeout=10)
