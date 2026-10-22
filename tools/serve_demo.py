"""Run a dedicated temporary SIH workspace and optional free Quick Tunnel.

Only a backend that confirms public_demo=true is ever exposed. Closing this
launcher stops both owned child processes. Never points at the private port.
"""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


class WindowsJob:
    """Keep owned Windows children tied to this launcher's lifetime.

    Windows does not reliably deliver Python's finally block when the console is
    closed. The OS closes this non-inherited handle on process exit and then
    terminates all processes assigned to the job, including their descendants.
    """

    def __init__(self):
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
        kernel.SetHandleInformation.restype = wintypes.BOOL
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self._kernel = kernel
        self._handle = kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            # No child may inherit a second handle that keeps the job alive.
            if not kernel.SetHandleInformation(self._handle, 1, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            limits = ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise

    def assign(self, process):
        if not self._kernel.AssignProcessToJobObject(self._handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self._handle:
            self._kernel.CloseHandle(self._handle)
            self._handle = None


def stop_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


class ChildSupervisor:
    def __init__(self):
        self.children = []
        self.job = None
        if sys.platform == "win32":
            try:
                self.job = WindowsJob()
            except OSError as exc:
                raise RuntimeError("Cannot safely supervise the demo processes on Windows; no server or public tunnel was started.") from exc

    def start(self, *args, **kwargs):
        if self.job:
            kwargs["close_fds"] = True
        process = subprocess.Popen(*args, **kwargs)
        self.children.append(process)
        if self.job:
            try:
                self.job.assign(process)
            except OSError as exc:
                # Do not continue to readiness checks or expose an unowned app.
                stop_process(process)
                raise RuntimeError("Cannot safely supervise the demo process; the public tunnel will not be started.") from exc
        return process

    def close(self):
        try:
            for process in reversed(self.children):
                stop_process(process)
        finally:
            if self.job:
                self.job.close()


def download_tunnel():
    if sys.platform != "win32":
        raise RuntimeError("Install cloudflared from https://developers.cloudflare.com/tunnel/downloads/ and pass --cloudflared PATH.")
    target = ROOT / ".tools" / "cloudflared.exe"
    if target.is_file():
        return target
    # Resolve the official release and verify the vendor's SHA256 digest.
    request = Request("https://api.github.com/repos/cloudflare/cloudflared/releases/latest", headers={"User-Agent": "DroneMap-SIH-demo"})
    with urlopen(request, timeout=30) as response:
        release = json.load(response)
    asset = next(a for a in release["assets"] if a["name"] == "cloudflared-windows-amd64.exe")
    digest = asset.get("digest", "")
    if not digest.startswith("sha256:"):
        raise RuntimeError("Official download has no SHA256 digest. Install cloudflared from the official downloads page instead.")
    target.parent.mkdir(exist_ok=True)
    with urlopen(Request(asset["browser_download_url"],headers={"User-Agent":"DroneMap-SIH-demo"}), timeout=120) as response:
        contents = response.read(100 * 1024 * 1024 + 1)
    if len(contents) > 100 * 1024 * 1024 or hashlib.sha256(contents).hexdigest() != digest.split(":",1)[1]:
        raise RuntimeError("Cloudflared download checksum did not match the official release.")
    target.write_bytes(contents)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8003)
    parser.add_argument("--cloudflared", type=Path)
    parser.add_argument("--download-tunnel", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port between 1024 and 65535.")
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError:
            parser.error("That port is already in use. Stop the previous demo or choose another port.")
    tunnel_binary = args.cloudflared or (download_tunnel() if args.download_tunnel else None)
    runtime = ROOT / ".demo-runtime"
    runtime.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.update(DRONEMAP_PUBLIC_DEMO="1", DRONEMAP_DATA_DIR=str(runtime / "visitor-data"), PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", YOLO_AUTOINSTALL="false")
    # Keep a previous private base URL from leaking into this dedicated demo.
    env.pop("DRONEMAP_PUBLIC_BASE_URL", None)
    supervisor = None
    # A URL from a previous run is not evidence that its tunnel is still alive.
    (runtime / "public-url.txt").unlink(missing_ok=True)
    try:
        supervisor = ChildSupervisor()
        app = supervisor.start([sys.executable,"-m","uvicorn","main:app","--host","127.0.0.1","--port",str(args.port),"--proxy-headers","--forwarded-allow-ips","127.0.0.1"], cwd=ROOT, env=env)
        local = f"http://127.0.0.1:{args.port}"
        for _ in range(120):
            if app.poll() is not None:
                raise RuntimeError("Demo server stopped during startup. Read the error above.")
            try:
                with urlopen(local + "/api/status", timeout=2) as response:
                    status = json.load(response)
                if status.get("public_demo") is not True:
                    raise RuntimeError("Refusing to expose a backend that is not in public demo mode.")
                break
            except OSError:
                time.sleep(0.5)
        else:
            raise RuntimeError("Demo server did not become ready.")
        print(f"\nIsolated SIH demo: {local}\nUse sample data only. Sessions expire after two hours.", flush=True)
        if not tunnel_binary:
            print("Local-only demo. Add --cloudflared PATH or --download-tunnel for a public link.", flush=True)
            return app.wait()
        tunnel = supervisor.start([str(tunnel_binary.resolve()),"tunnel","--no-autoupdate","--url",local], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        for line in tunnel.stdout:
            print(line, end="", flush=True)
            match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if match:
                url = match.group(0)
                (runtime / "public-url.txt").write_text(url + "\n", encoding="utf-8")
                print(f"\nPUBLIC SIH DEMO: {url}\nKeep this window, laptop and internet running. Closing it stops the link.\n", flush=True)
        if tunnel.wait() != 0:
            raise RuntimeError("The tunnel stopped. Restart this launcher to get a new public URL.")
    except KeyboardInterrupt:
        print("\nStopping the temporary demo.")
    finally:
        if supervisor:
            supervisor.close()
        (runtime / "public-url.txt").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
