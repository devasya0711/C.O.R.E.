"""Acquire inert source snapshots; never check out or execute downloaded code."""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

from pydantic import SecretStr

from app.core.errors import ApiError

_SAMPLE_URL = "local://sample_monolith"
_MAX_ENTRIES = 100_000
_MAX_METADATA_BYTES = 16 * 1024 * 1024
_IGNORED = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
_GIT = shutil.which("git", path=os.defpath) or "/usr/bin/git"
_LIMIT_AND_EXEC = (
    "import os,resource,sys; "
    "n=int(sys.argv[1]); resource.setrlimit(resource.RLIMIT_FSIZE,(n,n)); "
    "os.execv(sys.argv[2],sys.argv[2:])"
)
# Only this trusted helper runs. Its file contains no credential; the private
# child environment exists only for the lifetime of acquisition.
_ASKPASS = (
    f"#!{sys.executable}\n"
    "import os, sys\n"
    "prompt = sys.argv[1] if len(sys.argv) == 2 else ''\n"
    "if prompt.startswith('Username'):\n"
    "    print('x-access-token')\n"
    "elif prompt.startswith('Password'):\n"
    "    print(os.environ.get('CORE_GITHUB_TOKEN', ''))\n"
    "else:\n"
    "    sys.exit(1)\n"
)


def _error(code: str = "REPOSITORY_SOURCE_FAILED") -> ApiError:
    return ApiError(422, code, "Repository source could not be acquired safely.")


def _environment(home: Path) -> dict[str, str]:
    """Do not inherit proxies, Git configuration, tracing, or application keys."""
    return {
        "PATH": os.defpath,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/bin/false",
        "GIT_ALLOW_PROTOCOL": "https",
        "GIT_PROTOCOL_FROM_USER": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_LFS_SKIP_SMUDGE": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }


@dataclass(frozen=True, slots=True)
class SourceCheckout:
    """An ephemeral, metadata-free source tree and its immutable identity."""

    path: Path
    commit_sha: str


class RepositorySource:
    """Bounded GitHub snapshots and the single configured built-in fixture."""

    def __init__(
        self,
        sample_path: Path,
        clone_timeout_seconds: float = 30,
        max_source_bytes: int = 104857600,
    ) -> None:
        if (
            not math.isfinite(clone_timeout_seconds)
            or not 0 < clone_timeout_seconds <= 300
            or type(max_source_bytes) is not int
            or max_source_bytes <= 0
        ):
            raise _error("REPOSITORY_SOURCE_CONFIGURATION_ERROR")
        # Normalize trusted configuration lexically, without following links.
        self.sample_path = Path(os.path.abspath(sample_path))
        self.clone_timeout_seconds = clone_timeout_seconds
        self.max_source_bytes = max_source_bytes
        # Allow a packed Git copy plus the exported tree and bounded metadata.
        self._disk_limit = 2 * max_source_bytes + _MAX_METADATA_BYTES

    @staticmethod
    def validate_repo_url(repo_url: str) -> str:
        """Accept a literal GitHub HTTPS URL or the one built-in sample URL."""
        if repo_url == _SAMPLE_URL:
            return repo_url
        if re.search(r"\.zip(?:$|[?#/])", repo_url, flags=re.IGNORECASE):
            raise ApiError(422, "ZIP_NOT_SUPPORTED", "ZIP ingestion is not supported.")
        match = re.fullmatch(
            r"https://github\.com/([A-Za-z0-9][A-Za-z0-9-]{0,38})/"
            r"([A-Za-z0-9_.-]{1,104})",
            repo_url,
            flags=re.ASCII,
        )
        if match is None:
            raise _error("INVALID_REPOSITORY_URL")
        owner, name = match.groups()
        name = name.removesuffix(".git")
        if name in {"", ".", ".."} or len(name) > 100 or owner.endswith("-"):
            raise _error("INVALID_REPOSITORY_URL")
        return f"https://github.com/{owner}/{name}.git"

    @staticmethod
    def validate_branch(branch: str) -> str:
        """Use Git's own grammar, with no option or contextual ref expansion."""
        try:
            size = len(branch.encode("utf-8"))
        except UnicodeError:
            raise _error("INVALID_REPOSITORY_BRANCH") from None
        if (
            not branch
            or size > 128
            or branch.startswith(("-", "refs/"))
            or "@{" in branch
            or any(ord(char) < 33 or ord(char) == 127 for char in branch)
        ):
            raise _error("INVALID_REPOSITORY_BRANCH")
        try:
            result = subprocess.run(
                [_GIT, "check-ref-format", "--branch", branch],
                cwd="/",
                env=_environment(Path("/")),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise _error("INVALID_REPOSITORY_BRANCH") from None
        if result.returncode:
            raise _error("INVALID_REPOSITORY_BRANCH")
        return branch

    @asynccontextmanager
    async def acquire(
        self, repo_url: str, branch: str, token: SecretStr | None = None
    ) -> AsyncIterator[SourceCheckout]:
        """Clean up all processes and temporary files, including on cancellation."""
        url = self.validate_repo_url(repo_url)
        branch = self.validate_branch(branch)
        if token is not None and not re.fullmatch(
            r"[A-Za-z0-9_]{1,255}", token.get_secret_value(), flags=re.ASCII
        ):
            raise _error("GITHUB_AUTH_FAILED")
        with TemporaryDirectory(prefix="core-source-") as temporary:
            root = Path(temporary)
            operation = asyncio.create_task(self._acquire(root, url, branch, token))
            monitor = asyncio.create_task(self._monitor_disk(root))
            try:
                async with asyncio.timeout(self.clone_timeout_seconds):
                    done, _ = await asyncio.wait(
                        {operation, monitor}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if monitor in done:
                        await monitor
                    checkout = await operation
            except TimeoutError:
                raise ApiError(
                    504,
                    "REPOSITORY_SOURCE_TIMEOUT",
                    "Repository acquisition timed out.",
                ) from None
            except (OSError, ValueError, asyncio.IncompleteReadError):
                raise _error() from None
            finally:
                operation.cancel()
                monitor.cancel()
                await asyncio.gather(operation, monitor, return_exceptions=True)
            yield checkout

    async def _monitor_disk(self, root: Path) -> None:
        while True:
            total = 0
            count = 0
            pending = [root]
            while pending:
                directory = pending.pop()
                try:
                    entries = os.scandir(directory)
                except FileNotFoundError:
                    continue
                with entries:
                    for entry in entries:
                        try:
                            info = entry.stat(follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        count += 1
                        total += max(info.st_size, info.st_blocks * 512)
                        if stat.S_ISLNK(info.st_mode):
                            raise _error("UNSAFE_REPOSITORY_SOURCE")
                        if stat.S_ISDIR(info.st_mode):
                            pending.append(Path(entry.path))
                        if count > _MAX_ENTRIES or total > self._disk_limit:
                            raise _error("REPOSITORY_SOURCE_TOO_LARGE")
            await asyncio.sleep(0.01)

    async def _acquire(
        self, root: Path, url: str, branch: str, token: SecretStr | None
    ) -> SourceCheckout:
        source = root / "source"
        source.mkdir(mode=0o700)
        if url == _SAMPLE_URL:
            digest = hashlib.sha256(b"core-source-v1\0")
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            descriptor = os.open("/", flags)
            try:
                # Resolve every component using descriptors, so a concurrently
                # replaced ancestor cannot redirect the copy through a symlink.
                for part in self.sample_path.parts[1:]:
                    try:
                        child = os.open(part, flags, dir_fd=descriptor)
                    except OSError:
                        raise _error("UNSAFE_REPOSITORY_SOURCE") from None
                    os.close(descriptor)
                    descriptor = child
                await self._copy_sample(descriptor, source, "", digest, [0, 0])
            finally:
                os.close(descriptor)
            return SourceCheckout(source, digest.hexdigest())

        env = _environment(root)
        template = root / "empty-template"
        template.mkdir(mode=0o700)
        if token is not None:
            helper = root / "askpass"
            helper.write_text(_ASKPASS, encoding="utf-8")
            helper.chmod(0o700)
            env["GIT_ASKPASS"] = str(helper)
            env["CORE_GITHUB_TOKEN"] = token.get_secret_value()
        git_dir = root / "objects.git"
        settings = [
            "core.hooksPath=/dev/null",
            "core.attributesFile=/dev/null",
            "core.fsmonitor=false",
            "credential.helper=",
            "credential.useHttpPath=true",
            "http.followRedirects=false",
            "http.proxy=",
            "http.sslVerify=true",
            "protocol.allow=never",
            "protocol.https.allow=always",
            "submodule.recurse=false",
            "transfer.fsckObjects=true",
            "fetch.fsckObjects=true",
            "gc.auto=0",
            "maintenance.auto=false",
        ]
        options = [argument for setting in settings for argument in ("-c", setting)]
        await self._git(
            root,
            env,
            options
            + [
                "clone",
                "--bare",
                "--depth=1",
                "--single-branch",
                "--no-tags",
                f"--template={template}",
                "--branch",
                branch,
                "--",
                url,
                str(git_dir),
            ],
        )
        # Credentials are not available to any subsequent Git invocation.
        env.pop("CORE_GITHUB_TOKEN", None)
        env["GIT_ASKPASS"] = "/bin/false"
        (root / "askpass").unlink(missing_ok=True)
        local_options = options + [f"--git-dir={git_dir}"]
        commit = (
            await self._git(
                root,
                env,
                local_options
                + ["rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}"],
            )
        ).strip()
        if re.fullmatch(rb"[0-9a-f]{40}|[0-9a-f]{64}", commit) is None:
            raise _error()
        tree = await self._git(
            root,
            env,
            local_options + ["ls-tree", "-r", "-z", "-l", commit.decode("ascii")],
        )
        blobs = self._validate_tree(tree)
        async with self._process(
            root, env, local_options + ["cat-file", "--batch"], pipe_input=True
        ) as process:
            assert process.stdin is not None and process.stdout is not None
            for name, oid, size in blobs:
                process.stdin.write(oid + b"\n")
                await process.stdin.drain()
                header = await process.stdout.readline()
                if header != oid + b" blob " + str(size).encode("ascii") + b"\n":
                    raise _error()
                destination = source.joinpath(*PurePosixPath(name).parts)
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with destination.open("xb") as output:
                    remaining = size
                    while remaining:
                        chunk = await process.stdout.readexactly(min(65536, remaining))
                        output.write(chunk)
                        remaining -= len(chunk)
                if await process.stdout.readexactly(1) != b"\n":
                    raise _error()
            process.stdin.close()
            if await process.wait():
                raise _error()
        shutil.rmtree(git_dir)
        return SourceCheckout(source, commit.decode("ascii"))

    def _validate_tree(self, tree: bytes) -> list[tuple[str, bytes, int]]:
        blobs: list[tuple[str, bytes, int]] = []
        total = 0
        for record in tree.split(b"\0"):
            if not record:
                continue
            metadata, raw_name = record.split(b"\t", 1)
            mode, kind, oid, raw_size = metadata.split()
            name = raw_name.decode("utf-8", errors="strict")
            parts = name.split("/")
            if (
                mode not in {b"100644", b"100755"}
                or kind != b"blob"
                or re.fullmatch(rb"[0-9a-f]{40}|[0-9a-f]{64}", oid) is None
                or any(
                    part in {"", ".", ".."} or part.casefold() == ".git"
                    for part in parts
                )
                or "\\" in name
                or any(ord(char) < 32 or ord(char) == 127 for char in name)
            ):
                raise _error("UNSAFE_REPOSITORY_SOURCE")
            size = int(raw_size)
            total += size
            if size < 0 or total > self.max_source_bytes or len(blobs) >= _MAX_ENTRIES:
                raise _error("REPOSITORY_SOURCE_TOO_LARGE")
            blobs.append((name, oid, size))
        return blobs

    async def _copy_sample(
        self,
        descriptor: int,
        destination: Path,
        prefix: str,
        digest: hashlib._Hash,
        budget: list[int],
    ) -> None:
        # dir_fd + O_NOFOLLOW also prevent directory replacement races from
        # redirecting a copy outside the configured fixture.
        names = sorted(os.listdir(descriptor))
        if len(names) > _MAX_ENTRIES:
            raise _error("REPOSITORY_SOURCE_TOO_LARGE")
        for name in names:
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise _error("UNSAFE_REPOSITORY_SOURCE")
            if name in _IGNORED or name.endswith((".pyc", ".pyo")):
                continue
            budget[1] += 1
            if budget[1] > _MAX_ENTRIES:
                raise _error("REPOSITORY_SOURCE_TOO_LARGE")
            relative = f"{prefix}{name}"
            path = destination / name
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if stat.S_ISDIR(info.st_mode):
                flags |= os.O_DIRECTORY
            child = os.open(name, flags, dir_fd=descriptor)
            try:
                actual = os.fstat(child)
                if stat.S_IFMT(actual.st_mode) != stat.S_IFMT(info.st_mode) or (
                    stat.S_ISREG(actual.st_mode) and actual.st_nlink != 1
                ):
                    raise _error("UNSAFE_REPOSITORY_SOURCE")
                encoded = relative.encode("utf-8")
                digest.update(len(encoded).to_bytes(8) + encoded)
                if stat.S_ISDIR(actual.st_mode):
                    digest.update(b"D")
                    path.mkdir(mode=0o700)
                    await self._copy_sample(child, path, relative + "/", digest, budget)
                else:
                    budget[0] += actual.st_size
                    if budget[0] > self.max_source_bytes:
                        raise _error("REPOSITORY_SOURCE_TOO_LARGE")
                    digest.update(b"F" + actual.st_size.to_bytes(8))
                    copied = 0
                    with path.open("xb") as output:
                        while chunk := os.read(child, 65536):
                            copied += len(chunk)
                            if copied > actual.st_size:
                                raise _error("UNSAFE_REPOSITORY_SOURCE")
                            digest.update(chunk)
                            output.write(chunk)
                            await asyncio.sleep(0)
                    after = os.fstat(child)
                    if (
                        copied != actual.st_size
                        or after.st_size != actual.st_size
                        or after.st_mtime_ns != actual.st_mtime_ns
                        or after.st_ctime_ns != actual.st_ctime_ns
                    ):
                        raise _error("UNSAFE_REPOSITORY_SOURCE")
            finally:
                os.close(child)

    @asynccontextmanager
    async def _process(
        self,
        root: Path,
        env: dict[str, str],
        arguments: list[str],
        pipe_input: bool = False,
    ) -> AsyncIterator[asyncio.subprocess.Process]:
        spawn = asyncio.create_task(
            asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                _LIMIT_AND_EXEC,
                str(self._disk_limit),
                _GIT,
                *arguments,
                cwd=root,
                env=env,
                start_new_session=True,
                stdin=asyncio.subprocess.PIPE
                if pipe_input
                else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        )
        process: asyncio.subprocess.Process | None = None
        try:
            try:
                process = await asyncio.shield(spawn)
            except asyncio.CancelledError:
                process = await spawn
                raise
            yield process
        finally:
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()

    async def _git(
        self, root: Path, env: dict[str, str], arguments: list[str]
    ) -> bytes:
        async with self._process(root, env, arguments) as process:
            assert process.stdout is not None
            output = bytearray()
            while chunk := await process.stdout.read(65536):
                output.extend(chunk)
                if len(output) > _MAX_METADATA_BYTES:
                    raise _error("REPOSITORY_SOURCE_TOO_LARGE")
            if await process.wait():
                # Discard stderr entirely: Git may echo credentials or remote text.
                raise _error()
            return bytes(output)
