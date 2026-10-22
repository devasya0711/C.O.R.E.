"""Security and behavior checks for the Phase 5 pytest sandbox."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from app.services.sandbox_runner import (
    SandboxIsolation,
    SandboxIsolationUnavailable,
    SandboxRequest,
    SandboxRunnerConfig,
    SandboxRunnerService,
    SandboxValidationError,
)
from pydantic import ValidationError


def _runner(**overrides: object) -> SandboxRunnerService:
    config = SandboxRunnerConfig(
        docker_executable="core-test-docker-does-not-exist",
        **overrides,
    )
    return SandboxRunnerService(config)


@pytest.mark.asyncio
async def test_passing_artifacts_use_explicit_subprocess_fallback() -> None:
    result = await _runner().run(
        SandboxRequest(
            artifacts={
                "tests/test_pass.py": (
                    "def test_pass() -> None:\n    assert 2 + 2 == 4\n"
                )
            }
        )
    )

    assert result.passed is True
    assert result.return_code == 0
    assert result.isolation is SandboxIsolation.SUBPROCESS
    assert result.timed_out is False


@pytest.mark.asyncio
async def test_required_docker_isolation_fails_closed_without_docker() -> None:
    runner = _runner(require_docker=True)

    with pytest.raises(
        SandboxIsolationUnavailable,
        match="Docker sandbox isolation is required but unavailable",
    ):
        await runner.run({})


@pytest.mark.asyncio
async def test_docker_requires_a_daemon_visible_shared_workspace(
    tmp_path: Path,
) -> None:
    fake_docker = tmp_path / "docker"
    fake_docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_docker.chmod(0o700)
    runner = SandboxRunnerService(
        SandboxRunnerConfig(
            docker_executable=str(fake_docker),
            require_docker=True,
        )
    )

    with pytest.raises(
        SandboxIsolationUnavailable,
        match="explicit shared workspace root",
    ):
        await runner.run({})


@pytest.mark.asyncio
async def test_sample_monolith_checkout_runs_in_isolated_copy() -> None:
    source = Path(__file__).resolve().parents[2] / "sample_monolith"
    runner = _runner().with_source(source, subdirectory="sample_monolith")

    result = await runner.run({})

    assert result.passed is True
    assert result.isolation is SandboxIsolation.SUBPROCESS


@pytest.mark.asyncio
async def test_failure_returns_pytest_stderr() -> None:
    result = await _runner().run(
        {
            "pytest.ini": "[pytest]\naddopts = -s\n",
            "tests/test_failure.py": (
                "import sys\n\n"
                "def test_failure() -> None:\n"
                "    sys.stderr.write('fixture failure stderr\\n')\n"
                "    raise RuntimeError('fixture failure')\n"
            ),
        }
    )

    assert result.passed is False
    assert result.return_code != 0
    assert "fixture failure stderr" in result.stderr


@pytest.mark.parametrize(
    "artifact_path",
    [
        "../outside.py",
        "tests/../../outside.py",
        "/absolute.py",
        "C:/outside.py",
        "tests\\test.py",
    ],
)
@pytest.mark.asyncio
async def test_traversal_and_absolute_artifact_paths_are_rejected(
    artifact_path: str,
) -> None:
    with pytest.raises(SandboxValidationError):
        await _runner().run({artifact_path: "print('must not be written')\n"})


@pytest.mark.asyncio
async def test_artifact_text_size_is_bounded() -> None:
    runner = _runner(max_artifact_bytes=8)

    with pytest.raises(SandboxValidationError):
        await runner.run({"tests/test_too_large.py": "0123456789"})


@pytest.mark.asyncio
async def test_timeout_kills_process_group_and_cleans_workspace(tmp_path: Path) -> None:
    marker = tmp_path / "child-survived.txt"
    runner = _runner(timeout_seconds=0.25)
    child_code = (
        "import pathlib, time; time.sleep(2); "
        f"pathlib.Path({str(marker)!r}).write_text('alive')"
    )
    result = await runner.run(
        {
            "tests/test_timeout.py": (
                "import pathlib\n"
                "import subprocess\n"
                "import sys\n"
                "import time\n\n"
                f"child = subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
                "time.sleep(2)\n"
                "assert child.poll() is None\n"
            )
        }
    )

    assert result.passed is False
    assert result.timed_out is True
    await asyncio.sleep(0.1)
    assert not marker.exists()


@pytest.mark.asyncio
async def test_environment_is_allowlisted_without_secrets_or_proxies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "application-secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid")
    result = await _runner().run(
        {
            "tests/test_environment.py": (
                "import os\n"
                "def test_environment() -> None:\n"
                "    assert os.environ.get('OPENAI_API_KEY') is None\n"
                "    assert os.environ.get('HTTPS_PROXY') is None\n"
                "    assert os.environ.get('HTTP_PROXY') is None\n"
                "    assert os.environ.get('CORE_GITHUB_TOKEN') is None\n"
            )
        }
    )

    assert result.passed is True
    assert "application-secret" not in result.stdout + result.stderr
    assert "proxy.invalid" not in result.stdout + result.stderr
    assert "reduced isolation" in result.isolation_note


@pytest.mark.asyncio
async def test_stdout_and_stderr_are_independently_capped() -> None:
    runner = _runner(max_output_bytes=128)
    result = await runner.run(
        {
            "pytest.ini": "[pytest]\naddopts = -s\n",
            "tests/test_output.py": (
                "import sys\n"
                "def test_output() -> None:\n"
                "    print('out-' + 'x' * 10000)\n"
                "    print('err-' + 'y' * 10000, file=sys.stderr)\n"
                "    assert False\n"
            ),
        }
    )

    assert result.passed is False
    assert len(result.stdout.encode()) <= 128
    assert len(result.stderr.encode()) <= 128
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True


def test_subprocess_fallback_does_not_require_docker() -> None:
    runner = _runner()
    assert runner._docker_executable() is None


def test_docker_command_applies_bounded_resource_flags(tmp_path: Path) -> None:
    runner = _runner(
        docker_memory_mb=384,
        docker_cpus=0.5,
        docker_pids_limit=96,
        docker_nofile_limit=128,
    )

    command = runner._command(tmp_path, "/usr/bin/docker")

    assert "--memory=384m" in command
    assert "--memory-swap=384m" in command
    assert "--cpus=0.5" in command
    assert "--pids-limit=96" in command
    assert "nofile=128:128" in command
    assert "--user" in command


@pytest.mark.asyncio
async def test_docker_workspace_uses_shared_root_and_force_removes_on_timeout(
    tmp_path: Path,
) -> None:
    shared_root = tmp_path / "shared-workspaces"
    shared_root.mkdir()
    run_marker = tmp_path / "run-seen"
    remove_marker = tmp_path / "remove-seen"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys, time\n"
        f"run_marker = pathlib.Path({str(run_marker)!r})\n"
        f"remove_marker = pathlib.Path({str(remove_marker)!r})\n"
        "args = sys.argv[1:]\n"
        "if args[0] == 'run':\n"
        "    mount = next(value for value in args if value.startswith('type=bind'))\n"
        "    source = pathlib.Path(mount.split('src=', 1)[1].split(',', 1)[0])\n"
        f"    assert source.parent == pathlib.Path({str(shared_root)!r})\n"
        "    assert source.is_dir()\n"
        "    run_marker.write_text(str(source), encoding='utf-8')\n"
        "    time.sleep(30)\n"
        "elif args[0] == 'rm':\n"
        "    remove_marker.write_text(' '.join(args), encoding='utf-8')\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o700)

    runner = SandboxRunnerService(
        SandboxRunnerConfig(
            docker_executable=str(fake_docker),
            docker_workspace_root=shared_root,
            require_docker=True,
            timeout_seconds=0.1,
        )
    )
    result = await runner.run(
        {"tests/test_timeout.py": "def test_timeout() -> None:\n    assert True\n"}
    )

    assert result.timed_out is True
    assert run_marker.read_text(encoding="utf-8").startswith(str(shared_root))
    assert remove_marker.read_text(encoding="utf-8").startswith(
        "rm --force core-sandbox-"
    )
    assert not any(shared_root.iterdir())


@pytest.mark.asyncio
async def test_docker_cancellation_force_removes_named_container(
    tmp_path: Path,
) -> None:
    shared_root = tmp_path / "shared-workspaces"
    shared_root.mkdir()
    run_marker = tmp_path / "run-seen"
    remove_marker = tmp_path / "remove-seen"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib, sys, time\n"
        f"run_marker = pathlib.Path({str(run_marker)!r})\n"
        f"remove_marker = pathlib.Path({str(remove_marker)!r})\n"
        "args = sys.argv[1:]\n"
        "if args[0] == 'run':\n"
        "    mount = next(value for value in args if value.startswith('type=bind'))\n"
        "    source = pathlib.Path(mount.split('src=', 1)[1].split(',', 1)[0])\n"
        "    run_marker.write_text(str(source), encoding='utf-8')\n"
        "    time.sleep(30)\n"
        "elif args[0] == 'rm':\n"
        "    remove_marker.write_text(' '.join(args), encoding='utf-8')\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o700)
    runner = SandboxRunnerService(
        SandboxRunnerConfig(
            docker_executable=str(fake_docker),
            docker_workspace_root=shared_root,
            require_docker=True,
            timeout_seconds=30,
        )
    )

    task = asyncio.create_task(runner.run({}))
    for _ in range(20):
        if run_marker.exists():
            break
        await asyncio.sleep(0.01)
    else:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        pytest.fail("fake Docker run did not start")

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert remove_marker.read_text(encoding="utf-8").startswith(
        "rm --force core-sandbox-"
    )
    assert not any(shared_root.iterdir())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("docker_memory_mb", 127),
        ("docker_memory_mb", 2049),
        ("docker_cpus", 0),
        ("docker_cpus", 4.1),
        ("docker_pids_limit", 31),
        ("docker_pids_limit", 4097),
        ("docker_nofile_limit", 63),
        ("docker_nofile_limit", 65537),
    ],
)
def test_docker_resource_configuration_is_bounded(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        SandboxRunnerConfig(**{field: value})
