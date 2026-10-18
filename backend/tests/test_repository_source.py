"""Local Git integration/security checks; no external repository is contacted."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from app.core.errors import ApiError
from app.services.repository_source import RepositorySource
from pydantic import SecretStr

_URL = "https://github.com/example/project.git"
_TOKEN = "github_pat_test_only_credential"


def _git(path: Path, *arguments: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            *arguments,
        ],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        env={
            "PATH": os.defpath,
            "HOME": str(path),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        },
    )
    return result.stdout.strip()


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    repository = tmp_path / "fixture"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=main")
    (repository / "module.py").write_text("raise RuntimeError('MUST NEVER EXECUTE')\n")
    (repository / ".gitattributes").write_text("*.py filter=untrusted\n")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "fixture")
    return repository


class _GitHarness:
    """Redirect only the clone transport to a local fixture, using real Git.

    Production's HTTPS URL/protocol policy is asserted before this test-only
    replacement. All remaining Git commands run unchanged.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, repository: Path) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, str]]] = []
        self.roots: list[Path] = []
        self.processes: list[asyncio.subprocess.Process] = []
        self.mode = "local"
        original = asyncio.create_subprocess_exec

        async def spawn(
            *arguments: str,
            cwd: Path,
            env: dict[str, str],
            start_new_session: bool,
            stdin: int,
            stdout: int,
            stderr: int,
        ) -> asyncio.subprocess.Process:
            self.calls.append((arguments, dict(env)))
            self.roots.append(cwd)
            assert start_new_session
            assert stderr == asyncio.subprocess.DEVNULL
            assert _TOKEN not in " ".join(arguments)
            assert "http.followRedirects=false" in arguments
            assert "credential.helper=" in arguments
            assert "core.hooksPath=/dev/null" in arguments
            assert "GIT_TRACE" not in env and "HTTPS_PROXY" not in env
            modified = list(arguments)
            child_env = dict(env)
            if "clone" in arguments:
                assert env["GIT_ALLOW_PROTOCOL"] == "https"
                assert _URL in arguments
                assert "--bare" in arguments and "--single-branch" in arguments
                if "CORE_GITHUB_TOKEN" in env:
                    helper = Path(env["GIT_ASKPASS"])
                    assert _TOKEN not in helper.read_text()
                    assert helper.stat().st_mode & 0o777 == 0o700
                    # Exercise the ephemeral credential interface itself.
                    for prompt, expected in (
                        ("Username for 'https://github.com'", "x-access-token"),
                        ("Password for 'https://github.com'", _TOKEN),
                    ):
                        credential = subprocess.run(
                            [str(helper), prompt],
                            env=env,
                            capture_output=True,
                            text=True,
                            check=True,
                        )
                        assert credential.stdout.strip() == expected
                if self.mode == "local":
                    modified[modified.index(_URL)] = str(repository)
                    modified[modified.index("clone") : modified.index("clone")] = [
                        "-c",
                        "protocol.file.allow=always",
                    ]
                    child_env["GIT_ALLOW_PROTOCOL"] = "file"
                elif self.mode == "fail":
                    modified = [
                        sys.executable,
                        "-c",
                        "import os,sys; "
                        "sys.stderr.write(os.environ.get('CORE_GITHUB_TOKEN','')); "
                        "sys.exit(1)",
                    ]
                elif self.mode == "timeout":
                    modified = [sys.executable, "-c", "import time; time.sleep(60)"]
                elif self.mode == "descendant":
                    modified = [
                        sys.executable,
                        "-c",
                        "import pathlib,subprocess,sys,time; "
                        "child=subprocess.Popen([sys.executable,'-c',"
                        "'import time; time.sleep(60)']); "
                        "pathlib.Path('child-pid').write_text(str(child.pid)); "
                        "time.sleep(60)",
                    ]
                elif self.mode == "disk":
                    modified = [
                        sys.executable,
                        "-c",
                        "import pathlib,time; "
                        "pathlib.Path('overflow').write_bytes(b'x'*18000000); "
                        "time.sleep(60)",
                    ]
            else:
                assert "CORE_GITHUB_TOKEN" not in env
            process = await original(
                *modified,
                cwd=cwd,
                env=child_env,
                start_new_session=start_new_session,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
            )
            self.processes.append(process)
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://github.com/owner/repo", "https://github.com/owner/repo.git"),
        ("https://github.com/owner/repo.git", "https://github.com/owner/repo.git"),
        ("https://github.com/owner/.github", "https://github.com/owner/.github.git"),
        (
            "https://github.com/owner/data.zipper",
            "https://github.com/owner/data.zipper.git",
        ),
        ("local://sample_monolith", "local://sample_monolith"),
    ],
)
def test_valid_url(value: str, expected: str) -> None:
    assert RepositorySource.validate_repo_url(value) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a/b",
        "https://github.com:443/a/b",
        "https://token@github.com/a/b",
        "https://github.com.evil.test/a/b",
        "https://github.com/a/b?token=secret",
        "https://github.com/a/b#fragment",
        "https://github.com/a/b/",
        "https://github.com/a/b/extra",
        "https://github.com/a/..",
        "https://github.com/a/%2e%2e",
        "https://github.com/a/b\n",
        "https://github.com/a/b\x00",
        " https://github.com/a/b",
        "https://github.com/a/b?",
        "https://github.com/a/b#",
        "https://github.com/a/b.git:443",
        "ssh://git@github.com/a/b",
        "git@github.com:a/b.git",
        "file:///tmp/repo",
        "local:///tmp/repo",
        "local://sample_monolith/../private",
        "local://sample_monolith/",
        "--upload-pack=evil",
        "https://github.com/a/b.zip",
    ],
)
def test_unsafe_urls_are_rejected_without_reflecting_input(url: str) -> None:
    with pytest.raises(ApiError) as caught:
        RepositorySource.validate_repo_url(url)
    assert caught.value.status_code == 422
    assert url not in str(caught.value)
    if ".zip" in url:
        assert caught.value.code == "ZIP_NOT_SUPPORTED"


@pytest.mark.parametrize(
    "branch", ["main", "feature/source-acquisition", "release-1.2"]
)
def test_git_branch_validation_accepts_real_branches(branch: str) -> None:
    assert RepositorySource.validate_branch(branch) == branch


@pytest.mark.parametrize(
    "branch",
    [
        "",
        "-main",
        "--upload-pack=evil",
        "@{-1}",
        "main~1",
        "main^{}",
        "a..b",
        "a b",
        "a\nb",
        "refs/tags/main",
        "main:evil",
        "a.lock",
        "a\\b",
        "a\x00b",
        "a" * 129,
        "\ud800",
    ],
)
def test_unsafe_branch_is_rejected(branch: str) -> None:
    with pytest.raises(ApiError) as caught:
        RepositorySource.validate_branch(branch)
    assert caught.value.code == "INVALID_REPOSITORY_BRANCH"


@pytest.mark.asyncio
async def test_sample_copy_hash_is_deterministic_and_ephemeral(tmp_path: Path) -> None:
    sample = tmp_path / "configured-sample"
    sample.mkdir()
    (sample / "module.py").write_bytes(b"print('inert fixture')\n")
    cache = sample / "__pycache__"
    cache.mkdir()
    (cache / "unstable.pyc").write_bytes(b"runtime cache")
    service = RepositorySource(sample)
    async with service.acquire("local://sample_monolith", "main") as first:
        first_hash = first.commit_sha
        assert len(first_hash) == 64
        assert first.path != sample
        assert (first.path / "module.py").read_bytes() == (
            sample / "module.py"
        ).read_bytes()
        assert not (first.path / "__pycache__").exists()
        (first.path / "module.py").write_bytes(b"changes to the copy")
        root = first.path.parent
    assert not root.exists()
    (cache / "unstable.pyc").write_bytes(b"different cache")
    os.utime(sample / "module.py", (1, 1))
    async with service.acquire("local://sample_monolith", "other-branch") as second:
        assert second.commit_sha == first_hash
    (sample / "module.py").write_bytes(b"changed source")
    async with service.acquire("local://sample_monolith", "main") as changed:
        assert changed.commit_sha != first_hash


@pytest.mark.parametrize(
    "link_kind", ["file", "directory", "root", "ancestor", "hardlink"]
)
@pytest.mark.asyncio
async def test_sample_rejects_symlinks(tmp_path: Path, link_kind: str) -> None:
    sample = tmp_path / "sample"
    sample.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("outside-content")
    if link_kind == "ancestor":
        ancestor = tmp_path / "alias"
        ancestor.symlink_to(tmp_path, target_is_directory=True)
        configured = ancestor / "sample"
    elif link_kind == "root":
        configured = tmp_path / "linked-root"
        configured.symlink_to(sample, target_is_directory=True)
    elif link_kind == "hardlink":
        configured = sample
        os.link(outside / "secret", sample / "linked")
    else:
        configured = sample
        (sample / "linked").symlink_to(
            outside if link_kind == "directory" else outside / "secret",
            target_is_directory=link_kind == "directory",
        )
    with pytest.raises(ApiError) as caught:
        async with RepositorySource(configured).acquire(
            "local://sample_monolith", "main"
        ):
            pytest.fail("Symlinks must not be exposed")
    assert caught.value.code == "UNSAFE_REPOSITORY_SOURCE"


@pytest.mark.asyncio
async def test_sample_source_limit_and_consumer_exception_cleanup(
    tmp_path: Path,
) -> None:
    sample = tmp_path / "sample"
    sample.mkdir()
    (sample / "large.py").write_bytes(b"12345")
    with pytest.raises(ApiError) as caught:
        async with RepositorySource(sample, max_source_bytes=4).acquire(
            "local://sample_monolith", "main"
        ):
            pytest.fail("Oversized fixture must be rejected")
    assert caught.value.code == "REPOSITORY_SOURCE_TOO_LARGE"
    with pytest.raises(RuntimeError, match="consumer"):
        async with RepositorySource(sample).acquire(
            "local://sample_monolith", "main"
        ) as checkout:
            root = checkout.path.parent
            raise RuntimeError("consumer")
    assert not root.exists()


@pytest.mark.asyncio
async def test_real_git_export_credentials_and_clean_environment(
    monkeypatch: pytest.MonkeyPatch,
    fixture_repo: Path,
) -> None:
    monkeypatch.setenv("GIT_TRACE", "1")
    monkeypatch.setenv("HTTPS_PROXY", "https://malicious.example")
    harness = _GitHarness(monkeypatch, fixture_repo)
    async with RepositorySource(fixture_repo).acquire(
        _URL, "main", SecretStr(_TOKEN)
    ) as checkout:
        assert checkout.commit_sha == _git(fixture_repo, "rev-parse", "HEAD")
        assert (
            (checkout.path / "module.py").read_text().startswith("raise RuntimeError")
        )
        assert not (checkout.path / ".git").exists()
        assert not (checkout.path.parent / "askpass").exists()
        assert not (checkout.path.parent / "objects.git").exists()
        assert all(
            not (path.stat().st_mode & 0o111)
            for path in checkout.path.rglob("*")
            if path.is_file()
        )
    assert all(not root.exists() for root in harness.roots)
    assert all(process.returncode == 0 for process in harness.processes)


@pytest.mark.asyncio
async def test_built_in_fixture_is_copied_without_execution() -> None:
    sample = Path(__file__).parents[2] / "sample_monolith"
    service = RepositorySource(sample)
    async with service.acquire("local://sample_monolith", "main") as first:
        content_sha = first.commit_sha
        assert (first.path / "payments.py").read_bytes() == (
            sample / "payments.py"
        ).read_bytes()
    async with service.acquire("local://sample_monolith", "main") as second:
        assert second.commit_sha == content_sha


@pytest.mark.asyncio
async def test_cancellation_kills_the_entire_subprocess_group(
    monkeypatch: pytest.MonkeyPatch,
    fixture_repo: Path,
) -> None:
    harness = _GitHarness(monkeypatch, fixture_repo)
    harness.mode = "descendant"

    async def acquire() -> None:
        async with RepositorySource(fixture_repo).acquire(_URL, "main"):
            pytest.fail("Sleeping clone must not finish")

    task = asyncio.create_task(acquire())
    async with asyncio.timeout(5):
        pid_text = ""
        while not pid_text:
            if harness.roots and (harness.roots[0] / "child-pid").exists():
                pid_text = (harness.roots[0] / "child-pid").read_text()
            await asyncio.sleep(0.001)
        child_pid = int(pid_text)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # A killed orphan may briefly remain a zombie until the container's PID 1
    # reaps it. It must no longer be executing or holding source resources.
    child_status = Path(f"/proc/{child_pid}/stat")
    async with asyncio.timeout(5):
        while child_status.exists():
            try:
                if child_status.read_text().split()[2] == "Z":
                    break
            except FileNotFoundError:
                break
            await asyncio.sleep(0.001)
    assert all(process.returncode is not None for process in harness.processes)
    assert all(not root.exists() for root in harness.roots)


@pytest.mark.parametrize("mode", ["fail", "timeout", "disk"])
@pytest.mark.asyncio
async def test_failure_timeout_and_disk_limit_clean_up_processes_and_temps(
    monkeypatch: pytest.MonkeyPatch,
    fixture_repo: Path,
    mode: str,
) -> None:
    harness = _GitHarness(monkeypatch, fixture_repo)
    harness.mode = mode
    service = RepositorySource(
        fixture_repo,
        clone_timeout_seconds=0.15 if mode == "timeout" else 5,
        max_source_bytes=100,
    )
    with pytest.raises(ApiError) as caught:
        async with service.acquire(_URL, "main", SecretStr(_TOKEN)):
            pytest.fail("Acquisition must fail")
    assert (
        caught.value.code
        == {
            "fail": "REPOSITORY_SOURCE_FAILED",
            "timeout": "REPOSITORY_SOURCE_TIMEOUT",
            "disk": "REPOSITORY_SOURCE_TOO_LARGE",
        }[mode]
    )
    assert _TOKEN not in str(caught.value)
    assert all(not root.exists() for root in harness.roots)
    assert all(process.returncode is not None for process in harness.processes)


@pytest.mark.asyncio
async def test_cancellation_reaps_clone_and_cleans_temporary_directory(
    monkeypatch: pytest.MonkeyPatch,
    fixture_repo: Path,
) -> None:
    harness = _GitHarness(monkeypatch, fixture_repo)
    harness.mode = "timeout"

    async def acquire() -> None:
        async with RepositorySource(fixture_repo).acquire(_URL, "main"):
            pytest.fail("Sleeping clone must not finish")

    task = asyncio.create_task(acquire())
    async with asyncio.timeout(5):
        while not harness.processes:
            await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert all(process.returncode is not None for process in harness.processes)
    assert all(not root.exists() for root in harness.roots)


@pytest.mark.parametrize("unsafe", ["symlink", "submodule"])
@pytest.mark.asyncio
async def test_git_tree_rejects_symlinks_and_submodules(
    monkeypatch: pytest.MonkeyPatch,
    fixture_repo: Path,
    unsafe: str,
) -> None:
    if unsafe == "symlink":
        (fixture_repo / "escape").symlink_to("/etc/passwd")
        _git(fixture_repo, "add", "escape")
    else:
        commit = _git(fixture_repo, "rev-parse", "HEAD")
        _git(
            fixture_repo,
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{commit},nested",
        )
    _git(fixture_repo, "commit", "-m", "unsafe tree")
    harness = _GitHarness(monkeypatch, fixture_repo)
    with pytest.raises(ApiError) as caught:
        async with RepositorySource(fixture_repo).acquire(_URL, "main"):
            pytest.fail("Unsafe Git entries must be rejected")
    assert caught.value.code == "UNSAFE_REPOSITORY_SOURCE"
    assert all(not root.exists() for root in harness.roots)


@pytest.mark.asyncio
async def test_tag_cannot_impersonate_requested_branch(
    monkeypatch: pytest.MonkeyPatch,
    fixture_repo: Path,
) -> None:
    _git(fixture_repo, "tag", "not-a-branch")
    harness = _GitHarness(monkeypatch, fixture_repo)
    with pytest.raises(ApiError):
        async with RepositorySource(fixture_repo).acquire(_URL, "not-a-branch"):
            pytest.fail("A tag must never be accepted as a branch")
    assert all(not root.exists() for root in harness.roots)


@pytest.mark.parametrize(
    "failure", [OSError("sensitive"), subprocess.TimeoutExpired("git", 2)]
)
def test_branch_subprocess_errors_are_static(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise failure

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(ApiError) as caught:
        RepositorySource.validate_branch("main")
    assert caught.value.code == "INVALID_REPOSITORY_BRANCH"
    assert "sensitive" not in str(caught.value)


@pytest.mark.parametrize(
    "name", ["../outside", "/absolute", ".git/config", "a\\b", "a/../../escape"]
)
def test_export_rejects_malicious_tree_paths(tmp_path: Path, name: str) -> None:
    service = RepositorySource(tmp_path)
    tree = b"100644 blob " + b"a" * 40 + b" 1\t" + name.encode() + b"\0"
    with pytest.raises(ApiError):
        service._validate_tree(tree)


@pytest.mark.asyncio
async def test_git_source_size_limit_is_checked_before_export(
    monkeypatch: pytest.MonkeyPatch,
    fixture_repo: Path,
) -> None:
    harness = _GitHarness(monkeypatch, fixture_repo)
    with pytest.raises(ApiError) as caught:
        async with RepositorySource(fixture_repo, max_source_bytes=1).acquire(
            _URL, "main"
        ):
            pytest.fail("Oversized Git source must be rejected before export")
    assert caught.value.code == "REPOSITORY_SOURCE_TOO_LARGE"
    assert all("cat-file" not in arguments for arguments, _ in harness.calls)
    assert all(not root.exists() for root in harness.roots)
