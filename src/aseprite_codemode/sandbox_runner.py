"""Parent-side subprocess orchestrator (cold or warm) for running Code Mode safely."""

from __future__ import annotations

import atexit
import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
import weakref
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import RuntimeConfig, clamp_code_timeout
from .security import ASTSafetyChecker, UnsafeCodeError, build_sanitized_env


@dataclass
class PreviewImage:
    """In-memory rendered preview PNG returned from a worker run."""

    index: int
    label: str
    source_tool: str
    text: str
    png_bytes: bytes

    def metadata_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "label": self.label,
            "source_tool": self.source_tool,
            "text": self.text,
            "bytes": len(self.png_bytes),
        }


@dataclass
class ExecutionResult:
    """Structured outcome from `SandboxRunner.run`."""

    status: str
    stdout: str
    stderr: str
    duration_ms: int
    return_value: Any = None
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    previews: list[PreviewImage] = field(default_factory=list)
    tool_warnings: list[str] = field(default_factory=list)
    error: str | None = None
    error_type: str | None = None
    traceback: str | None = None

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "duration_ms": self.duration_ms,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "return_value": self.return_value,
            "files_created": self.files_created,
            "files_modified": self.files_modified,
            "previews": [p.metadata_dict() for p in self.previews],
            "tool_warnings": self.tool_warnings,
            "error": self.error,
            "error_type": self.error_type,
            "traceback": self.traceback,
        }


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    marker = "\n...[truncated]"
    keep = max(0, max_chars - len(marker))
    return text[:keep] + marker


def _snapshot_workspace(workspace_dir: Path) -> dict[str, tuple[int, int]]:
    snap: dict[str, tuple[int, int]] = {}
    if not workspace_dir.is_dir():
        return snap
    for path in workspace_dir.rglob("*"):
        if path.is_file():
            try:
                st = path.stat()
                rel = path.relative_to(workspace_dir).as_posix()
                snap[rel] = (st.st_mtime_ns, st.st_size)
            except OSError:
                continue
    return snap


def _diff_snapshots(
    before: dict[str, tuple[int, int]],
    after: dict[str, tuple[int, int]],
) -> tuple[list[str], list[str]]:
    created = sorted(k for k in after if k not in before)
    modified = sorted(k for k in after if k in before and after[k] != before[k])
    return created, modified


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    """Terminate the worker process along with any spawned child `aseprite.exe` processes."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass


def _popen_kwargs(config: RuntimeConfig, *, bufsize: int = -1) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "cwd": str(config.workspace_dir),
        "env": build_sanitized_env(config),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": bufsize,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return kwargs


def _read_lines_into_queue(stream: Any, q: queue.Queue[str | None]) -> None:
    try:
        for line in stream:
            q.put(line)
    except Exception:
        pass
    finally:
        q.put(None)


def _drain_stderr_lines(stream: Any, buf: deque[str]) -> None:
    try:
        for line in stream:
            buf.append(line)
    except Exception:
        pass


class SandboxRunner:
    """Runs Python scripts inside a sanitized child process bound to `config.workspace_dir`."""

    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.config.workspace_dir.mkdir(parents=True, exist_ok=True)
        self._warm_lock = threading.Lock()
        self._warm_proc: subprocess.Popen[str] | None = None
        self._warm_queue: queue.Queue[str | None] | None = None
        self._warm_stderr: deque[str] = deque(maxlen=200)

        self_ref = weakref.ref(self)
        atexit.register(lambda: (r := self_ref()) is not None and r.close())

    def close(self) -> None:
        """Terminate any active warm worker process cleanly."""
        with self._warm_lock:
            self._reset_warm_worker()

    def _reset_warm_worker(self) -> None:
        proc = self._warm_proc
        self._warm_proc = None
        self._warm_queue = None
        if proc is not None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except OSError:
                pass
            _kill_process_tree(proc)

    def _ensure_warm_worker(self) -> tuple[subprocess.Popen[str], queue.Queue[str | None]]:
        if self._warm_proc is not None and self._warm_proc.poll() is None and self._warm_queue is not None:
            return self._warm_proc, self._warm_queue
        self._reset_warm_worker()
        self._warm_stderr.clear()
        cmd = [sys.executable, "-m", "aseprite_codemode.worker", "--serve"]
        kwargs = _popen_kwargs(self.config, bufsize=1)
        kwargs["stdin"] = subprocess.PIPE
        proc = subprocess.Popen(cmd, **kwargs)
        q: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=_read_lines_into_queue, args=(proc.stdout, q), daemon=True).start()
        threading.Thread(target=_drain_stderr_lines, args=(proc.stderr, self._warm_stderr), daemon=True).start()
        self._warm_proc = proc
        self._warm_queue = q
        return proc, q

    def _build_job_spec(
        self,
        *,
        code: str,
        preview_dir: Path,
        result_path: Path,
        effective_timeout: float,
        auto_view: str | None,
        view_options: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "code": code,
            "workspace_dir": str(self.config.workspace_dir),
            "preview_dir": str(preview_dir),
            "result_path": str(result_path),
            "aseprite_path": self.config.aseprite_path,
            "aseprite_timeout_seconds": self.config.aseprite_timeout_seconds,
            "code_timeout_seconds": effective_timeout,
            "upstream_repo_path": str(self.config.upstream_repo_path),
            "allow_raw_lua": self.config.allow_raw_lua,
            "allow_preview_server": self.config.allow_preview_server,
            "max_output_chars": self.config.max_output_chars,
            "max_preview_images": self.config.max_preview_images,
            "auto_view": auto_view or "",
            "view_options": view_options or {},
        }

    def _collect_result_payload(
        self,
        *,
        result_path: Path,
        elapsed: int,
        created: list[str],
        modified: list[str],
        extra_stdout: str,
        extra_stderr: str,
        returncode: int | None,
    ) -> ExecutionResult:
        if not result_path.is_file():
            return ExecutionResult(
                status="error",
                stdout=_truncate(extra_stdout, self.config.max_output_chars),
                stderr=_truncate(extra_stderr, self.config.max_output_chars),
                duration_ms=elapsed,
                files_created=created,
                files_modified=modified,
                error=f"Worker exited with code {returncode} without writing result.json",
                error_type="WorkerCrashError",
            )

        data = json.loads(result_path.read_text(encoding="utf-8"))
        previews: list[PreviewImage] = []
        for meta in data.get("previews", []):
            p_file = Path(meta["path"])
            if p_file.is_file():
                previews.append(
                    PreviewImage(
                        index=int(meta["index"]),
                        label=str(meta.get("label", "")),
                        source_tool=str(meta.get("source_tool", "")),
                        text=str(meta.get("text", "")),
                        png_bytes=p_file.read_bytes(),
                    )
                )

        combined_stderr = str(data.get("stderr", ""))
        if extra_stderr:
            combined_stderr = (combined_stderr + "\n" + extra_stderr).strip()

        return ExecutionResult(
            status=str(data.get("status", "error")),
            stdout=_truncate(str(data.get("stdout", "")), self.config.max_output_chars),
            stderr=_truncate(combined_stderr, self.config.max_output_chars),
            duration_ms=elapsed,
            return_value=data.get("return_value"),
            files_created=created,
            files_modified=modified,
            previews=previews,
            tool_warnings=list(data.get("tool_warnings", [])),
            error=data.get("error"),
            error_type=data.get("error_type"),
            traceback=data.get("traceback"),
        )

    def _run_cold(
        self,
        *,
        job_spec_path: Path,
        result_path: Path,
        effective_timeout: float,
        before_snap: dict[str, tuple[int, int]],
        t0: float,
    ) -> ExecutionResult:
        cmd = [sys.executable, "-m", "aseprite_codemode.worker", str(job_spec_path)]
        kwargs = _popen_kwargs(self.config)
        kwargs["stdin"] = subprocess.DEVNULL
        proc = subprocess.Popen(cmd, **kwargs)
        try:
            proc_stdout, proc_stderr = proc.communicate(timeout=effective_timeout)
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            try:
                proc_stdout, proc_stderr = proc.communicate(timeout=2)
            except (subprocess.TimeoutExpired, OSError):
                proc_stdout, proc_stderr = "", ""
            elapsed = int((time.monotonic() - t0) * 1000)
            after_snap = _snapshot_workspace(self.config.workspace_dir)
            created, modified = _diff_snapshots(before_snap, after_snap)
            return ExecutionResult(
                status="timeout",
                stdout=_truncate(proc_stdout or "", self.config.max_output_chars),
                stderr=_truncate(proc_stderr or "", self.config.max_output_chars),
                duration_ms=elapsed,
                files_created=created,
                files_modified=modified,
                error=f"Execution timed out after {effective_timeout:g}s",
                error_type="TimeoutExpired",
            )

        elapsed = int((time.monotonic() - t0) * 1000)
        after_snap = _snapshot_workspace(self.config.workspace_dir)
        created, modified = _diff_snapshots(before_snap, after_snap)
        return self._collect_result_payload(
            result_path=result_path,
            elapsed=elapsed,
            created=created,
            modified=modified,
            extra_stdout=proc_stdout or "",
            extra_stderr=proc_stderr or "",
            returncode=proc.returncode,
        )

    def _run_warm(
        self,
        *,
        job_spec_path: Path,
        result_path: Path,
        effective_timeout: float,
        before_snap: dict[str, tuple[int, int]],
        t0: float,
    ) -> ExecutionResult:
        with self._warm_lock:
            payload_line = json.dumps({"job_spec_path": str(job_spec_path)}) + "\n"
            proc: subprocess.Popen[str] | None = None
            q: queue.Queue[str | None] | None = None

            for attempt in range(2):
                proc, q = self._ensure_warm_worker()
                self._warm_stderr.clear()
                try:
                    assert proc.stdin is not None
                    proc.stdin.write(payload_line)
                    proc.stdin.flush()
                    break
                except OSError:
                    self._reset_warm_worker()
                    if attempt == 1:
                        elapsed = int((time.monotonic() - t0) * 1000)
                        return ExecutionResult(
                            status="error",
                            stdout="",
                            stderr="",
                            duration_ms=elapsed,
                            error="Warm worker failed to accept job request",
                            error_type="WorkerCrashError",
                        )

            assert proc is not None and q is not None
            try:
                reply = q.get(timeout=effective_timeout)
            except queue.Empty:
                self._reset_warm_worker()
                elapsed = int((time.monotonic() - t0) * 1000)
                after_snap = _snapshot_workspace(self.config.workspace_dir)
                created, modified = _diff_snapshots(before_snap, after_snap)
                return ExecutionResult(
                    status="timeout",
                    stdout="",
                    stderr=_truncate("".join(self._warm_stderr), self.config.max_output_chars),
                    duration_ms=elapsed,
                    files_created=created,
                    files_modified=modified,
                    error=f"Execution timed out after {effective_timeout:g}s",
                    error_type="TimeoutExpired",
                )

            if reply is None:
                rc = proc.poll()
                self._reset_warm_worker()
                elapsed = int((time.monotonic() - t0) * 1000)
                after_snap = _snapshot_workspace(self.config.workspace_dir)
                created, modified = _diff_snapshots(before_snap, after_snap)
                return ExecutionResult(
                    status="error",
                    stdout="",
                    stderr=_truncate("".join(self._warm_stderr), self.config.max_output_chars),
                    duration_ms=elapsed,
                    files_created=created,
                    files_modified=modified,
                    error=f"Warm worker exited unexpectedly (code={rc})",
                    error_type="WorkerCrashError",
                )

            elapsed = int((time.monotonic() - t0) * 1000)
            after_snap = _snapshot_workspace(self.config.workspace_dir)
            created, modified = _diff_snapshots(before_snap, after_snap)
            return self._collect_result_payload(
                result_path=result_path,
                elapsed=elapsed,
                created=created,
                modified=modified,
                extra_stdout="",
                extra_stderr="".join(self._warm_stderr),
                returncode=0,
            )

    def run(
        self,
        code: str,
        *,
        timeout_seconds: float | None = None,
        auto_view: str | None = None,
        view_options: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        t0 = time.monotonic()

        # Pre-flight AST check before spawning a child process.
        try:
            ASTSafetyChecker.check(code)
        except UnsafeCodeError as exc:
            elapsed = int((time.monotonic() - t0) * 1000)
            return ExecutionResult(
                status="security_error",
                stdout="",
                stderr="",
                duration_ms=elapsed,
                error=str(exc),
                error_type=type(exc).__name__,
            )

        effective_timeout = clamp_code_timeout(
            self.config.code_timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        )
        before_snap = _snapshot_workspace(self.config.workspace_dir)

        with tempfile.TemporaryDirectory(prefix="ase_codemode_") as tmp:
            tmp_dir = Path(tmp)
            job_spec_path = tmp_dir / "job_spec.json"
            result_path = tmp_dir / "result.json"
            preview_dir = tmp_dir / "previews"
            preview_dir.mkdir(parents=True, exist_ok=True)

            job_spec = self._build_job_spec(
                code=code,
                preview_dir=preview_dir,
                result_path=result_path,
                effective_timeout=effective_timeout,
                auto_view=auto_view,
                view_options=view_options,
            )
            job_spec_path.write_text(json.dumps(job_spec), encoding="utf-8")

            if self.config.warm_worker:
                return self._run_warm(
                    job_spec_path=job_spec_path,
                    result_path=result_path,
                    effective_timeout=effective_timeout,
                    before_snap=before_snap,
                    t0=t0,
                )
            return self._run_cold(
                job_spec_path=job_spec_path,
                result_path=result_path,
                effective_timeout=effective_timeout,
                before_snap=before_snap,
                t0=t0,
            )
