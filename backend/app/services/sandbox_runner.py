"""Run generated pytest artifacts in an ephemeral, bounded workspace."""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath, PureWindowsPath
from tempfile import TemporaryDirectory
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SandboxIsolation(StrEnum):
    """The execution boundary used for one sandbox run."""

    DOCKER = "docker"
    SUBPROCESS = "subprocess"


class SandboxRunnerConfig(BaseModel):
    """Bounded, injectable configuration for :class:`SandboxRunnerService`."""

    model_config = ConfigDict(extra="forbid")

    timeout_seconds: float = Field(default=30.0, gt=0, le=30.0)
    max_output_bytes: int = Field(default=64 * 1024, gt=0, le=16 * 1024 * 1024)
    max_artifact_bytes: int = Field(default=1024 * 1024, gt=0, le=16 * 1024 * 1024)
    python_executable: str = Field(default_factory=lambda: sys.executable, min_length=1)
    docker_executable: str | None = Field(default="docker", min_length=1)
    docker_image: str = Field(
        default="core-sandbox:latest", min_length=1, max_length=256
    )
    docker_memory_mb: int = Field(default=512, ge=128, le=2048)
    docker_cpus: float = Field(default=1.0, gt=0, le=4.0)
    docker_pids_limit: int = Field(default=256, ge=32, le=4096)
    docker_nofile_limit: int = Field(default=256, ge=64, le=65536)
    docker_workspace_root: Path | None = None
    require_docker: bool = False

    @field_validator("python_executable", "docker_executable", "docker_image")
    @classmethod
    def reject_unsafe_command_values(cls, value: str | None) -> str | None:
        """Command values are passed as argv entries, never through a shell."""
        if value is not None and (
            "\x00" in value or any(char.isspace() for char in value)
        ):
            raise ValueError("command values must not contain whitespace or NUL bytes")
        return value

    @field_validator("docker_workspace_root")
    @classmethod
    def validate_workspace_root(cls, value: Path | None) -> Path | None:
        """Require an explicit, dedicated absolute directory for Docker binds."""
        if value is None:
            return None
        if not value.expanduser().is_absolute():
            raise ValueError(
                "docker_workspace_root must be a dedicated absolute directory"
            )
        resolved = value.expanduser().resolve(strict=False)
        if not resolved.is_absolute() or resolved in {Path("/"), Path("/tmp")}:
            raise ValueError(
                "docker_workspace_root must be a dedicated absolute directory"
            )
        return resolved


class SandboxRequest(BaseModel):
    """Generated files to materialize before invoking pytest."""

    model_config = ConfigDict(extra="forbid")

    artifacts: dict[str, str] = Field(default_factory=dict)


class SandboxResult(BaseModel):
    """Safe, workflow-friendly output from one sandbox invocation."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    return_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool
    isolation: SandboxIsolation
    isolation_note: str


class SandboxValidationError(ValueError):
    """Raised when generated artifacts cannot be safely materialized."""


class SandboxIsolationUnavailable(RuntimeError):
    """Raised when a required Docker isolation boundary is unavailable."""

    code = "SANDBOX_ISOLATION_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class _CapturedProcess:
    return_code: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool


class SandboxRunnerService:
    """Materialize and execute generated pytest artifacts once, then clean up."""

    def __init__(self, config: SandboxRunnerConfig | None = None) -> None:
        self.config = config or SandboxRunnerConfig()
        self.source_root: Path | None = None
        self.source_subdirectory: str | None = None

    def with_source(
        self, source_root: Path, *, subdirectory: str | None = None
    ) -> SandboxRunnerService:
        """Return this runner configured to copy a trusted checkout per attempt."""
        self.source_root = source_root
        self.source_subdirectory = subdirectory
        return self

    async def run(self, artifacts: SandboxRequest | Mapping[str, str]) -> SandboxResult:
        """Run pytest from a fresh temporary workspace.

        A Docker executable is selected when it is configured and discoverable.
        The subprocess path deliberately reports reduced isolation because it
        cannot provide a kernel network namespace on its own.
        """
        request = (
            artifacts
            if isinstance(artifacts, SandboxRequest)
            else SandboxRequest(artifacts=dict(artifacts))
        )

        docker_executable = self._docker_executable()
        if docker_executable is not None and self.config.docker_workspace_root is None:
            if self.config.require_docker:
                raise SandboxIsolationUnavailable(
                    "Docker sandbox isolation requires an explicit shared "
                    "workspace root."
                )
            docker_executable = None
        workspace_root = (
            self.config.docker_workspace_root if docker_executable is not None else None
        )
        if workspace_root is not None and (
            not workspace_root.is_dir()
            or not os.access(workspace_root, os.W_OK | os.X_OK)
        ):
            raise SandboxIsolationUnavailable(
                "Docker sandbox shared workspace root is unavailable or not writable."
            )
        with TemporaryDirectory(
            prefix="core-sandbox-",
            dir=str(workspace_root) if workspace_root is not None else None,
        ) as temporary:
            workspace = Path(temporary)
            if self.source_root is not None:
                self._copy_source(self.source_root, workspace)
            self._materialize(workspace, request.artifacts)
            if docker_executable is None and self.config.require_docker:
                raise SandboxIsolationUnavailable(
                    "Docker sandbox isolation is required but unavailable."
                )
            isolation = (
                SandboxIsolation.DOCKER
                if docker_executable is not None
                else SandboxIsolation.SUBPROCESS
            )
            container_name = (
                f"core-sandbox-{uuid4().hex}" if docker_executable is not None else None
            )
            command = self._command(
                workspace, docker_executable, container_name=container_name
            )
            cleanup_command = self._cleanup_command(docker_executable, container_name)
            environment = self._environment(workspace, isolation)
            captured = await self._execute(
                command, workspace, environment, cleanup_command=cleanup_command
            )

        return SandboxResult(
            passed=captured.return_code == 0 and not captured.timed_out,
            return_code=captured.return_code,
            stdout=captured.stdout.decode("utf-8", errors="replace"),
            stderr=captured.stderr.decode("utf-8", errors="replace"),
            timed_out=captured.timed_out,
            stdout_truncated=captured.stdout_truncated,
            stderr_truncated=captured.stderr_truncated,
            isolation=isolation,
            isolation_note=(
                "Docker container with host networking disabled."
                if isolation is SandboxIsolation.DOCKER
                else (
                    "Subprocess fallback with reduced isolation; "
                    "host networking is shared."
                )
            ),
        )

    async def execute(
        self, artifacts: SandboxRequest | Mapping[str, str]
    ) -> SandboxResult:
        """Workflow-friendly alias for :meth:`run`."""
        return await self.run(artifacts)

    def _docker_executable(self) -> str | None:
        if self.config.docker_executable is None:
            return None
        return shutil.which(self.config.docker_executable)

    def _command(
        self,
        workspace: Path,
        docker_executable: str | None,
        *,
        container_name: str | None = None,
    ) -> list[str]:
        if docker_executable is None:
            return [self.config.python_executable, "-m", "pytest"]

        command = [
            docker_executable,
            "run",
            "--rm",
            f"--memory={self.config.docker_memory_mb}m",
            f"--memory-swap={self.config.docker_memory_mb}m",
            f"--cpus={self.config.docker_cpus:g}",
            f"--pids-limit={self.config.docker_pids_limit}",
            "--ulimit",
            f"nofile={self.config.docker_nofile_limit}:{self.config.docker_nofile_limit}",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--network=none",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--mount",
            f"type=bind,src={workspace},dst=/workspace,rw",
            "--workdir",
            "/workspace",
            "--env",
            "HOME=/tmp",
            "--env",
            "TMPDIR=/tmp",
            "--env",
            "LANG=C.UTF-8",
            "--env",
            "LC_ALL=C.UTF-8",
            "--env",
            "PYTHONNOUSERSITE=1",
            "--env",
            "PYTHONUNBUFFERED=1",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            self.config.docker_image,
            "python",
            "-m",
            "pytest",
        ]
        if container_name is not None:
            command[3:3] = ["--name", container_name]
        return command

    @staticmethod
    def _cleanup_command(
        docker_executable: str | None, container_name: str | None
    ) -> list[str] | None:
        if docker_executable is None or container_name is None:
            return None
        return [docker_executable, "rm", "--force", container_name]

    def _environment(
        self, workspace: Path, isolation: SandboxIsolation
    ) -> dict[str, str]:
        """Use an allowlist so app credentials and proxy settings never inherit."""
        home = workspace / ".home"
        cache = workspace / ".cache"
        home.mkdir(mode=0o700)
        cache.mkdir(mode=0o700)
        if isolation is SandboxIsolation.DOCKER:
            return {
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": "/tmp",
                "TMPDIR": "/tmp",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "PYTHONNOUSERSITE": "1",
                "PYTHONUNBUFFERED": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }

        executable_directory = str(Path(self.config.python_executable).parent)
        return {
            "PATH": os.pathsep.join((executable_directory, os.defpath)),
            "HOME": str(home),
            "TMPDIR": str(cache),
            "XDG_CACHE_HOME": str(cache),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def _materialize(self, workspace: Path, artifacts: Mapping[str, str]) -> None:
        """Validate every key before writing any generated file."""
        validated: list[tuple[PurePosixPath, str]] = []
        for raw_path, text in artifacts.items():
            path = self._validate_artifact_path(raw_path)
            if not isinstance(text, str):
                raise SandboxValidationError("artifact content must be text")
            try:
                encoded = text.encode("utf-8")
            except UnicodeEncodeError as error:
                raise SandboxValidationError(
                    "artifact content must be valid UTF-8"
                ) from error
            if len(encoded) > self.config.max_artifact_bytes:
                raise SandboxValidationError(
                    "artifact content exceeds the configured size"
                )
            validated.append((path, text))

        root = workspace.resolve()
        for path, text in sorted(validated, key=lambda item: item[0].as_posix()):
            target = workspace.joinpath(*path.parts)
            resolved = target.resolve(strict=False)
            if root not in resolved.parents:
                raise SandboxValidationError(
                    "artifact path escapes the sandbox workspace"
                )
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            target.chmod(0o600)

    def _copy_source(self, source_root: Path, workspace: Path) -> None:
        """Copy only a trusted acquired tree; reject links before execution."""
        source_root = source_root.resolve()
        if not source_root.is_dir():
            raise SandboxValidationError("sandbox source root is not a directory")
        destination = workspace
        if self.source_subdirectory is not None:
            prefix = self._validate_artifact_path(self.source_subdirectory)
            destination = workspace.joinpath(*prefix.parts)
            destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        for entry in source_root.iterdir():
            if entry.is_symlink():
                raise SandboxValidationError("sandbox source contains a symlink")
            target = destination / entry.name
            if entry.is_dir():
                shutil.copytree(entry, target, symlinks=False)
            else:
                shutil.copy2(entry, target, follow_symlinks=False)

    @staticmethod
    def _validate_artifact_path(raw_path: str) -> PurePosixPath:
        if not isinstance(raw_path, str) or not raw_path:
            raise SandboxValidationError(
                "artifact paths must be non-empty relative paths"
            )
        if (
            "\x00" in raw_path
            or "\\" in raw_path
            or raw_path.startswith("/")
            or raw_path.endswith("/")
        ):
            raise SandboxValidationError("artifact paths must be relative POSIX paths")

        windows_path = PureWindowsPath(raw_path)
        if windows_path.drive or windows_path.root:
            raise SandboxValidationError("artifact paths must be relative POSIX paths")

        parts = raw_path.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise SandboxValidationError("artifact paths contain an unsafe component")
        return PurePosixPath(*parts)

    async def _execute(
        self,
        command: list[str],
        workspace: Path,
        environment: dict[str, str],
        *,
        cleanup_command: list[str] | None = None,
    ) -> _CapturedProcess:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=workspace,
                env=environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError:
            return _CapturedProcess(
                return_code=None,
                stdout=b"",
                stderr=b"Sandbox process could not be started.",
                timed_out=False,
                stdout_truncated=False,
                stderr_truncated=False,
            )

        stdout_task = asyncio.create_task(
            self._read_capped(process.stdout, self.config.max_output_bytes)
        )
        stderr_task = asyncio.create_task(
            self._read_capped(process.stderr, self.config.max_output_bytes)
        )
        timed_out = False
        cleanup_complete = False

        async def cleanup_container() -> None:
            nonlocal cleanup_complete
            if cleanup_complete or cleanup_command is None:
                return
            cleanup_complete = True
            await self._remove_container(cleanup_command, environment)

        try:
            try:
                async with asyncio.timeout(self.config.timeout_seconds):
                    await process.wait()
                    stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
            except TimeoutError:
                timed_out = True
                await self._terminate_process_group(process)
                await cleanup_container()
                stdout, stderr = await self._finish_readers(stdout_task, stderr_task)
            except BaseException:
                await self._terminate_process_group(process)
                await cleanup_container()
                stdout_task.cancel()
                stderr_task.cancel()
                await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
                raise
        finally:
            if process.returncode is None:
                await self._terminate_process_group(process)
                await cleanup_container()
            elif process.returncode != 0:
                await cleanup_container()

        return _CapturedProcess(
            return_code=process.returncode,
            stdout=stdout[0],
            stderr=stderr[0],
            timed_out=timed_out,
            stdout_truncated=stdout[1],
            stderr_truncated=stderr[1],
        )

    async def _remove_container(
        self, command: list[str], environment: dict[str, str]
    ) -> None:
        """Force-remove a named Docker container after CLI cancellation."""
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                env=environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            return

        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except TimeoutError:
            await self._terminate_process_group(process)
        except BaseException:
            await self._terminate_process_group(process)
            raise

    async def _finish_readers(
        self,
        stdout_task: asyncio.Task[tuple[bytes, bool]],
        stderr_task: asyncio.Task[tuple[bytes, bool]],
    ) -> tuple[tuple[bytes, bool], tuple[bytes, bool]]:
        try:
            return await asyncio.wait_for(
                asyncio.gather(stdout_task, stderr_task), timeout=1.0
            )
        except TimeoutError:
            stdout_task.cancel()
            stderr_task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            return (b"", True), (b"", True)

    async def _read_capped(
        self, stream: asyncio.StreamReader | None, limit: int
    ) -> tuple[bytes, bool]:
        if stream is None:
            return b"", False
        output = bytearray()
        truncated = False
        while chunk := await stream.read(64 * 1024):
            if len(output) < limit:
                remaining = limit - len(output)
                output.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    truncated = True
            else:
                truncated = True
        return bytes(output), truncated

    @staticmethod
    async def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
        """Stop pytest and descendants, including when a timeout interrupted reads."""
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return

        try:
            await asyncio.wait_for(process.wait(), timeout=0.25)
        except (TimeoutError, ProcessLookupError):
            pass

        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


SandboxConfig = SandboxRunnerConfig
SandboxRunResult = SandboxResult
SandboxRunner = SandboxRunnerService
