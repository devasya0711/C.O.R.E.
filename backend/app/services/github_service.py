"""Create idempotent, execution-verified GitHub pull requests."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from app.core.errors import ApiError

_GITHUB_HOST = "github.com"
_MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
_MAX_ARTIFACTS = 1_000
_MAX_DIAGRAM_BYTES = 64 * 1024
_MAX_SUMMARY_BYTES = 128 * 1024
_MAX_SEAM_BYTES = 128
_MAX_BRANCH_BYTES = 255
_SAFE_TOKEN = re.compile(r"[A-Za-z0-9_]{1,255}", re.ASCII)
_SAFE_SHA = re.compile(r"[0-9a-f]{7,64}", re.ASCII)


def _invalid_input() -> ValueError:
    """Return a non-sensitive validation error for Pydantic validators."""
    return ValueError("invalid GitHub request input")


def _validate_text(value: str, maximum: int) -> str:
    """Reject control characters and invalid Unicode in text sent to GitHub."""
    if "\x00" in value or any(
        ord(character) < 32 and character not in "\t\n\r" or ord(character) == 127
        for character in value
    ):
        raise _invalid_input()
    try:
        if len(value.encode("utf-8")) > maximum:
            raise _invalid_input()
    except UnicodeEncodeError:
        raise _invalid_input() from None
    return value


def _validate_relative_path(value: str) -> str:
    """Accept only a relative, slash-separated GitHub tree path."""
    if not value or len(value.encode("utf-8")) > 512:
        raise _invalid_input()
    if value.startswith("/") or "\\" in value or "//" in value:
        raise _invalid_input()
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise _invalid_input()
    try:
        path = PurePosixPath(value)
    except (TypeError, ValueError):
        raise _invalid_input() from None
    if path.is_absolute() or path.parts != tuple(value.split("/")):
        raise _invalid_input()
    if any(part in {"", ".", ".."} or part.casefold() == ".git" for part in path.parts):
        raise _invalid_input()
    return value


def _validate_branch_syntax(value: str) -> str:
    """Validate a branch name without invoking Git or expanding a ref."""
    try:
        encoded_size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise _invalid_input() from None
    if (
        not value
        or encoded_size > _MAX_BRANCH_BYTES
        or value.startswith(("-", "/", "refs/"))
        or value.endswith(("/", "."))
        or ".." in value
        or "@{" in value
        or "//" in value
        or any(
            ord(character) < 33
            or ord(character) == 127
            or character in {"~", "^", ":", "?", "*", "[", "\\"}
            for character in value
        )
        or any(
            not part
            or part.startswith(".")
            or part.endswith(".")
            or part.casefold().endswith(".lock")
            for part in value.split("/")
        )
    ):
        raise _invalid_input()
    return value


class GitHubArtifact(BaseModel):
    """A generated text file to add to the refactor commit."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    path: str = Field(min_length=1, max_length=512)
    content: str = Field(max_length=_MAX_ARTIFACT_BYTES)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _validate_relative_path(value)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        return _validate_text(value, _MAX_ARTIFACT_BYTES)


class GitHubPRRequest(BaseModel):
    """Validated input for one C.O.R.E. GitHub extraction."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    repo_url: str = Field(min_length=1, max_length=512)
    base_branch: str = Field(default="main", min_length=1, max_length=255)
    seam_name: str = Field(min_length=1, max_length=128)
    artifacts: list[GitHubArtifact] = Field(min_length=1, max_length=_MAX_ARTIFACTS)
    dependency_diagram: str = Field(min_length=1, max_length=_MAX_DIAGRAM_BYTES)
    blast_radius_summary: str = Field(min_length=1, max_length=_MAX_SUMMARY_BYTES)
    token: SecretStr = Field(repr=False)
    title: str | None = Field(default=None, max_length=256)
    source_commit_sha: str | None = Field(default=None, max_length=64)

    @field_validator("artifacts", mode="before")
    @classmethod
    def normalize_artifacts(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return [
                {"path": path, "content": content} for path, content in value.items()
            ]
        return value

    @field_validator("base_branch")
    @classmethod
    def validate_base_branch(cls, value: str) -> str:
        try:
            return _validate_branch_syntax(value)
        except ValueError:
            raise _invalid_input() from None

    @field_validator("dependency_diagram")
    @classmethod
    def validate_dependency_diagram(cls, value: str) -> str:
        return _validate_text(value, _MAX_DIAGRAM_BYTES)

    @field_validator("blast_radius_summary")
    @classmethod
    def validate_blast_radius_summary(cls, value: str) -> str:
        return _validate_text(value, _MAX_SUMMARY_BYTES)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_text(value, 256)

    @field_validator("source_commit_sha")
    @classmethod
    def validate_source_commit_sha(cls, value: str | None) -> str | None:
        if value is not None and _SAFE_SHA.fullmatch(value) is None:
            raise _invalid_input()
        return value

    @model_validator(mode="after")
    def validate_unique_artifact_paths(self) -> GitHubPRRequest:
        paths = [artifact.path for artifact in self.artifacts]
        if len(paths) != len(set(paths)):
            raise _invalid_input()
        return self


class GitHubPRResult(BaseModel):
    """Safe result returned after a branch and pull request are available."""

    model_config = ConfigDict(extra="forbid", strict=True)

    branch: str
    commit_sha: str
    pr_number: int = Field(gt=0)
    pr_url: str = Field(min_length=1)
    created: bool

    @property
    def branch_name(self) -> str:
        """Compatibility name for callers that use the explicit field form."""
        return self.branch

    @property
    def pull_request_number(self) -> int:
        """Compatibility name for callers that use the explicit field form."""
        return self.pr_number

    @property
    def pull_request_url(self) -> str:
        """Compatibility name for callers that use the explicit field form."""
        return self.pr_url


@dataclass(frozen=True, slots=True)
class GitHubRefRecord:
    """The immutable portion of a Git reference used by the service."""

    sha: str


@dataclass(frozen=True, slots=True)
class GitHubCommitRecord:
    """A commit plus its tree identity used for one low-level commit."""

    sha: str
    tree_sha: str
    message: str
    native: object | None = None
    native_tree: object | None = None


@dataclass(frozen=True, slots=True)
class GitHubBlobRecord:
    """A Git blob identity returned by the provider."""

    sha: str


@dataclass(frozen=True, slots=True)
class GitHubTreeEntry:
    """A safe text blob entry for a Git tree."""

    path: str
    mode: str
    type: str
    sha: str


@dataclass(frozen=True, slots=True)
class GitHubTreeRecord:
    """A Git tree identity and optional provider-native object."""

    sha: str
    native: object | None = None


@dataclass(frozen=True, slots=True)
class GitHubPullRequestRecord:
    """Provider PR data required to return a real URL without synthesizing it."""

    number: int
    url: str
    head_ref: str
    base_ref: str
    head_sha: str


class GitHubRepositoryFacade(Protocol):
    """The synchronous repository operations needed by the PR workflow."""

    def get_git_ref(self, ref: str) -> GitHubRefRecord: ...

    def create_git_ref(self, ref: str, sha: str) -> GitHubRefRecord: ...

    def update_git_ref(self, ref: str, sha: str) -> GitHubRefRecord: ...

    def get_git_commit(self, sha: str) -> GitHubCommitRecord: ...

    def create_git_blob(self, content: str) -> GitHubBlobRecord: ...

    def create_git_tree(
        self, entries: Sequence[GitHubTreeEntry], base_tree: GitHubCommitRecord
    ) -> GitHubTreeRecord: ...

    def create_git_commit(
        self,
        message: str,
        tree: GitHubTreeRecord,
        parent: GitHubCommitRecord,
    ) -> GitHubCommitRecord: ...

    def get_open_pull_requests(
        self, head: str, base: str
    ) -> Sequence[GitHubPullRequestRecord]: ...

    def create_pull_request(
        self, title: str, body: str, head: str, base: str
    ) -> GitHubPullRequestRecord: ...


class GitHubFacade(Protocol):
    """Injectable synchronous facade boundary for PyGithub or a test fake."""

    def get_repository(self, full_name: str) -> GitHubRepositoryFacade: ...

    def close(self) -> None: ...


GitHubFacadeFactory = Callable[[SecretStr], GitHubFacade]


@dataclass(frozen=True, slots=True)
class _ParsedRepository:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True, slots=True)
class _PreparedRequest:
    repository: _ParsedRepository
    base_branch: str
    seam: str
    branch: str
    artifacts: tuple[GitHubArtifact, ...]
    dependency_diagram: str
    blast_radius_summary: str
    title: str
    token: SecretStr
    commit_message: str
    source_commit_sha: str | None


class _PyGithubFacade:
    """Small adapter keeping PyGithub objects out of the service contract."""

    def __init__(self, token: SecretStr) -> None:
        try:
            from github import Auth, Github
        except ImportError:
            raise ApiError(
                503,
                "GITHUB_PROVIDER_UNAVAILABLE",
                "GitHub integration is unavailable.",
            ) from None
        self._client = Github(
            auth=Auth.Token(token.get_secret_value()), timeout=15, per_page=100
        )

    def get_repository(self, full_name: str) -> GitHubRepositoryFacade:
        return _PyGithubRepositoryFacade(self._client.get_repo(full_name))

    def close(self) -> None:
        self._client.close()


class _PyGithubRepositoryFacade:
    """Translate the low-level PyGithub Git data APIs to typed records."""

    def __init__(self, repository: object) -> None:
        self._repository = repository

    def get_git_ref(self, ref: str) -> GitHubRefRecord:
        result = self._repository.get_git_ref(ref)  # type: ignore[attr-defined]
        return GitHubRefRecord(sha=result.object.sha)

    def create_git_ref(self, ref: str, sha: str) -> GitHubRefRecord:
        result = self._repository.create_git_ref(ref=ref, sha=sha)  # type: ignore[attr-defined]
        return GitHubRefRecord(sha=result.object.sha)

    def update_git_ref(self, ref: str, sha: str) -> GitHubRefRecord:
        result = self._repository.get_git_ref(ref)  # type: ignore[attr-defined]
        result.edit(sha=sha, force=False)
        return GitHubRefRecord(sha=sha)

    def get_git_commit(self, sha: str) -> GitHubCommitRecord:
        result = self._repository.get_git_commit(sha)  # type: ignore[attr-defined]
        message = getattr(result, "message", None)
        if not isinstance(message, str):
            commit_data = getattr(result, "commit", None)
            message = getattr(commit_data, "message", "")
        tree = result.tree
        return GitHubCommitRecord(
            sha=result.sha,
            tree_sha=tree.sha,
            message=message if isinstance(message, str) else "",
            native=result,
            native_tree=tree,
        )

    def create_git_blob(self, content: str) -> GitHubBlobRecord:
        result = self._repository.create_git_blob(  # type: ignore[attr-defined]
            content, "utf-8"
        )
        return GitHubBlobRecord(sha=result.sha)

    def create_git_tree(
        self, entries: Sequence[GitHubTreeEntry], base_tree: GitHubCommitRecord
    ) -> GitHubTreeRecord:
        from github import InputGitTreeElement

        native_entries = [
            InputGitTreeElement(
                entry.path,
                mode=entry.mode,
                type=entry.type,
                sha=entry.sha,
            )
            for entry in entries
        ]
        result = self._repository.create_git_tree(  # type: ignore[attr-defined]
            native_entries, base_tree=base_tree.native_tree
        )
        return GitHubTreeRecord(sha=result.sha, native=result)

    def create_git_commit(
        self,
        message: str,
        tree: GitHubTreeRecord,
        parent: GitHubCommitRecord,
    ) -> GitHubCommitRecord:
        result = self._repository.create_git_commit(  # type: ignore[attr-defined]
            message, tree.native, [parent.native]
        )
        return GitHubCommitRecord(
            sha=result.sha,
            tree_sha=tree.sha,
            message=message,
            native=result,
            native_tree=tree.native,
        )

    def get_open_pull_requests(
        self, head: str, base: str
    ) -> Sequence[GitHubPullRequestRecord]:
        pulls = self._repository.get_pulls(  # type: ignore[attr-defined]
            state="open", head=head, base=base
        )
        return tuple(
            GitHubPullRequestRecord(
                number=pull.number,
                url=pull.html_url,
                head_ref=pull.head.ref,
                base_ref=pull.base.ref,
                head_sha=pull.head.sha,
            )
            for pull in pulls
        )

    def create_pull_request(
        self, title: str, body: str, head: str, base: str
    ) -> GitHubPullRequestRecord:
        pull = self._repository.create_pull(  # type: ignore[attr-defined]
            title=title, body=body, head=head, base=base
        )
        return GitHubPullRequestRecord(
            number=pull.number,
            url=pull.html_url,
            head_ref=pull.head.ref,
            base_ref=pull.base.ref,
            head_sha=pull.head.sha,
        )


def _default_facade_factory(token: SecretStr) -> GitHubFacade:
    """Build the production PyGithub facade lazily so tests need no provider SDK."""
    return _PyGithubFacade(token)


def _provider_status(error: BaseException) -> int | None:
    """Read only numeric provider metadata; never stringify provider exceptions."""
    for attribute in ("status", "status_code"):
        value = getattr(error, attribute, None)
        if type(value) is int:
            return value
    return None


def _provider_is_rate_limited(error: BaseException, status: int | None) -> bool:
    class_name = type(error).__name__.casefold()
    if "ratelimit" in class_name or status == 429:
        return True
    if status != 403:
        return False
    headers = getattr(error, "headers", None)
    if isinstance(headers, Mapping):
        remaining = headers.get("X-RateLimit-Remaining")
        return remaining == "0" or remaining == 0
    return False


def _map_provider_error(error: BaseException) -> ApiError:
    """Map PyGithub/network failures to static, credential-free API errors."""
    if (
        isinstance(error, (TimeoutError, asyncio.TimeoutError))
        or "timeout" in type(error).__name__.casefold()
    ):
        return ApiError(504, "GITHUB_TIMEOUT", "GitHub request timed out.")
    if isinstance(error, ApiError):
        return error
    if (
        isinstance(error, ImportError)
        or "unavailable" in type(error).__name__.casefold()
    ):
        return ApiError(
            503, "GITHUB_PROVIDER_UNAVAILABLE", "GitHub integration is unavailable."
        )
    status = _provider_status(error)
    if _provider_is_rate_limited(error, status):
        return ApiError(429, "GITHUB_RATE_LIMITED", "GitHub API rate limit reached.")
    class_name = type(error).__name__.casefold()
    if status == 404 or "unknownobject" in class_name or "notfound" in class_name:
        return ApiError(
            404,
            "GITHUB_REPOSITORY_NOT_FOUND",
            "GitHub repository was not found or access was denied.",
        )
    if status in {401, 403} or "badcredentials" in class_name:
        return ApiError(401, "GITHUB_AUTH_FAILED", "GitHub authentication failed.")
    return ApiError(502, "GITHUB_PROVIDER_ERROR", "GitHub request failed.")


def _parse_repository_url(value: str) -> _ParsedRepository:
    """Parse only canonical GitHub HTTPS repository URLs."""
    if any(ord(character) < 33 or ord(character) == 127 for character in value):
        raise ApiError(
            422, "INVALID_GITHUB_REPOSITORY_URL", "GitHub repository URL is invalid."
        )
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ApiError(
            422, "INVALID_GITHUB_REPOSITORY_URL", "GitHub repository URL is invalid."
        ) from None
    if (
        parsed.scheme != "https"
        or parsed.hostname != _GITHUB_HOST
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path
        or parsed.path.endswith("/")
    ):
        raise ApiError(
            422, "INVALID_GITHUB_REPOSITORY_URL", "GitHub repository URL is invalid."
        )
    match = re.fullmatch(
        r"/([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_.-]{1,104})",
        parsed.path,
        flags=re.ASCII,
    )
    if match is None:
        raise ApiError(
            422, "INVALID_GITHUB_REPOSITORY_URL", "GitHub repository URL is invalid."
        )
    owner, name = match.groups()
    name = name.removesuffix(".git")
    if (
        not name
        or name in {".", ".."}
        or len(name) > 100
        or name.endswith(".")
        or owner.endswith("-")
    ):
        raise ApiError(
            422, "INVALID_GITHUB_REPOSITORY_URL", "GitHub repository URL is invalid."
        )
    return _ParsedRepository(owner=owner, name=name)


def validate_repo_url(value: str) -> str:
    """Validate and canonicalize a supported GitHub HTTPS repository URL."""
    repository = _parse_repository_url(value)
    return f"https://github.com/{repository.full_name}.git"


def _sanitize_seam(value: str) -> str:
    """Turn a display seam name into a bounded branch-safe slug."""
    try:
        encoded_size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        raise ApiError(422, "INVALID_GITHUB_SEAM", "Seam name is invalid.") from None
    if not value.strip() or encoded_size > _MAX_SEAM_BYTES:
        raise ApiError(422, "INVALID_GITHUB_SEAM", "Seam name is invalid.")
    if (
        any(ord(character) < 32 or ord(character) == 127 for character in value)
        or "/" in value
        or "\\" in value
        or ".." in value
    ):
        raise ApiError(422, "INVALID_GITHUB_SEAM", "Seam name is invalid.")
    sanitized = re.sub(r"[^A-Za-z0-9]+", "-", value, flags=re.ASCII).strip("-").lower()
    if not sanitized or len(sanitized.encode("ascii")) > 96:
        raise ApiError(422, "INVALID_GITHUB_SEAM", "Seam name is invalid.")
    return sanitized


def sanitize_seam_name(value: str) -> str:
    """Return the branch-safe seam slug used by the extraction branch."""
    return _sanitize_seam(value)


def _validate_token(token: SecretStr) -> None:
    """Validate a PAT without ever placing its value in an exception message."""
    try:
        value = token.get_secret_value()
    except (AttributeError, TypeError):
        raise ApiError(
            401, "GITHUB_AUTH_FAILED", "GitHub authentication failed."
        ) from None
    if _SAFE_TOKEN.fullmatch(value) is None:
        raise ApiError(401, "GITHUB_AUTH_FAILED", "GitHub authentication failed.")


def _digest_request(request: GitHubPRRequest, branch: str) -> str:
    digest = hashlib.sha256()
    digest.update(branch.encode("ascii"))
    for artifact in sorted(request.artifacts, key=lambda item: item.path):
        path = artifact.path.encode("utf-8")
        content = artifact.content.encode("utf-8")
        digest.update(len(path).to_bytes(8, "big"))
        digest.update(path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    digest.update(request.dependency_diagram.encode("utf-8"))
    digest.update(request.blast_radius_summary.encode("utf-8"))
    digest.update((request.title or "").encode("utf-8"))
    digest.update((request.source_commit_sha or "").encode("ascii"))
    return digest.hexdigest()


def _prepare_request(request: GitHubPRRequest) -> _PreparedRequest:
    repository = _parse_repository_url(request.repo_url)
    _validate_token(request.token)
    seam = _sanitize_seam(request.seam_name)
    branch = f"refactor/extract-{seam}"
    _validate_branch_syntax(request.base_branch)
    if any(
        request.token.get_secret_value() in value
        for value in (
            request.dependency_diagram,
            request.blast_radius_summary,
            request.title or "",
            *(artifact.content for artifact in request.artifacts),
        )
    ):
        raise ApiError(
            422,
            "GITHUB_SECRET_IN_INPUT",
            "GitHub credential cannot be included in generated content.",
        )
    title = request.title or f"Extract {seam}"
    digest = _digest_request(request, branch)
    commit_message = "\n\n".join(
        (f"refactor: extract {seam}", f"C.O.R.E. migration marker: {branch}:{digest}")
    )
    return _PreparedRequest(
        repository=repository,
        base_branch=request.base_branch,
        seam=seam,
        branch=branch,
        artifacts=tuple(sorted(request.artifacts, key=lambda item: item.path)),
        dependency_diagram=request.dependency_diagram,
        blast_radius_summary=request.blast_radius_summary,
        title=title,
        token=request.token,
        commit_message=commit_message,
        source_commit_sha=request.source_commit_sha,
    )


def _validate_pr_record(
    pull_request: GitHubPullRequestRecord,
    branch: str,
    base_branch: str,
) -> None:
    if (
        type(pull_request.number) is not int
        or pull_request.number <= 0
        or pull_request.head_ref != branch
        or pull_request.base_ref != base_branch
        or not _SAFE_SHA.fullmatch(pull_request.head_sha)
        or not re.fullmatch(
            r"https://github\.com/[^\s/]+/[^\s/]+/pull/\d+",
            pull_request.url,
        )
    ):
        raise ApiError(502, "GITHUB_PROVIDER_ERROR", "GitHub returned invalid PR data.")


def _result(
    pull_request: GitHubPullRequestRecord,
    branch: str,
    base_branch: str,
    commit_sha: str,
    created: bool,
) -> GitHubPRResult:
    _validate_pr_record(pull_request, branch, base_branch)
    return GitHubPRResult(
        branch=branch,
        commit_sha=commit_sha,
        pr_number=pull_request.number,
        pr_url=pull_request.url,
        created=created,
    )


def _find_open_pr(
    repository: GitHubRepositoryFacade,
    prepared: _PreparedRequest,
    commit_sha: str,
) -> GitHubPRResult | None:
    head = f"{prepared.repository.owner}:{prepared.branch}"
    for pull_request in repository.get_open_pull_requests(head, prepared.base_branch):
        if (
            pull_request.head_ref == prepared.branch
            and pull_request.base_ref == prepared.base_branch
            and (not commit_sha or pull_request.head_sha == commit_sha)
        ):
            return _result(
                pull_request,
                prepared.branch,
                prepared.base_branch,
                commit_sha or pull_request.head_sha,
                False,
            )
    return None


def _build_body(prepared: _PreparedRequest) -> str:
    return (
        "## Dependency diagram\n\n"
        f"```mermaid\n{prepared.dependency_diagram}\n```\n\n"
        "## Blast-radius summary\n\n"
        f"{prepared.blast_radius_summary}\n"
    )


def _publish_sync(
    prepared: _PreparedRequest, facade_factory: GitHubFacadeFactory
) -> GitHubPRResult:
    facade = facade_factory(prepared.token)
    try:
        repository = facade.get_repository(prepared.repository.full_name)
        base_ref = repository.get_git_ref(f"heads/{prepared.base_branch}")
        if (
            prepared.source_commit_sha is not None
            and base_ref.sha != prepared.source_commit_sha
        ):
            raise ApiError(
                409,
                "GITHUB_BASE_CHANGED",
                "The GitHub base branch changed; re-ingest before publishing.",
            )
        try:
            branch_ref = repository.get_git_ref(f"heads/{prepared.branch}")
            branch_is_new = False
        except Exception as error:
            if (
                _provider_status(error) != 404
                and "notfound" not in type(error).__name__.casefold()
            ):
                raise
            try:
                branch_ref = repository.create_git_ref(
                    f"refs/heads/{prepared.branch}", base_ref.sha
                )
                branch_is_new = True
            except Exception as create_error:
                # A concurrent retry may have won branch creation. Reuse its ref.
                if _provider_status(create_error) != 422:
                    raise
                branch_ref = repository.get_git_ref(f"heads/{prepared.branch}")
                branch_is_new = False

        if not branch_is_new:
            tip = repository.get_git_commit(branch_ref.sha)
            open_prs = repository.get_open_pull_requests(
                f"{prepared.repository.owner}:{prepared.branch}",
                prepared.base_branch,
            )
            if tip.message == prepared.commit_message:
                commit_sha = tip.sha
                existing = _find_open_pr(repository, prepared, commit_sha)
                if existing is not None:
                    return existing
                if open_prs:
                    raise ApiError(
                        409,
                        "GITHUB_BRANCH_CONFLICT",
                        "An open GitHub branch contains different migration content.",
                    )
            else:
                if open_prs:
                    raise ApiError(
                        409,
                        "GITHUB_BRANCH_CONFLICT",
                        "An open GitHub branch contains different migration content.",
                    )
                raise ApiError(
                    409,
                    "GITHUB_BRANCH_CONFLICT",
                    "The extraction branch is not owned by this migration.",
                )
        else:
            commit_sha = ""

        if not commit_sha:
            parent = repository.get_git_commit(branch_ref.sha)
            blobs = [
                GitHubTreeEntry(
                    path=artifact.path,
                    mode="100644",
                    type="blob",
                    sha=repository.create_git_blob(artifact.content).sha,
                )
                for artifact in prepared.artifacts
            ]
            tree = repository.create_git_tree(blobs, parent)
            commit = repository.create_git_commit(prepared.commit_message, tree, parent)
            repository.update_git_ref(f"heads/{prepared.branch}", commit.sha)
            commit_sha = commit.sha

        existing = _find_open_pr(repository, prepared, commit_sha)
        if existing is not None:
            return existing
        try:
            pull_request = repository.create_pull_request(
                prepared.title,
                _build_body(prepared),
                prepared.branch,
                prepared.base_branch,
            )
        except Exception as error:
            # GitHub returns 422 when another idempotent attempt opened the PR.
            if _provider_status(error) != 422:
                raise
            existing = _find_open_pr(repository, prepared, commit_sha)
            if existing is None:
                raise
            return existing
        return _result(
            pull_request, prepared.branch, prepared.base_branch, commit_sha, True
        )
    finally:
        facade.close()


class GitHubService:
    """Async GitHub PR service with a replaceable synchronous provider facade."""

    def __init__(
        self,
        facade_factory: GitHubFacadeFactory | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        if not 0 < timeout_seconds <= 15:
            raise ValueError("GitHub timeout must be between 0 and 15 seconds")
        self._facade_factory = facade_factory or _default_facade_factory
        self._timeout_seconds = timeout_seconds

    @staticmethod
    def validate_repo_url(value: str) -> str:
        """Validate and canonicalize a GitHub repository URL."""
        return validate_repo_url(value)

    @staticmethod
    def sanitize_seam_name(value: str) -> str:
        """Sanitize the seam name used in the extraction branch."""
        return sanitize_seam_name(value)

    async def create_pull_request(
        self, request: GitHubPRRequest | Mapping[str, object]
    ) -> GitHubPRResult:
        """Publish validated artifacts and return the provider's real PR URL."""
        try:
            if not isinstance(request, GitHubPRRequest):
                try:
                    request = GitHubPRRequest.model_validate(request)
                except ValidationError:
                    raise ApiError(
                        422,
                        "GITHUB_INPUT_INVALID",
                        "GitHub request validation failed.",
                    ) from None
            prepared = _prepare_request(request)
            async with asyncio.timeout(self._timeout_seconds):
                return await asyncio.to_thread(
                    _publish_sync, prepared, self._facade_factory
                )
        except TimeoutError:
            raise ApiError(504, "GITHUB_TIMEOUT", "GitHub request timed out.") from None
        except ApiError:
            raise
        except Exception as error:
            raise _map_provider_error(error) from None

    async def publish(
        self, request: GitHubPRRequest | Mapping[str, object]
    ) -> GitHubPRResult:
        """Alias for workflow callers that describe the operation as publishing."""
        return await self.create_pull_request(request)


GitHubPRService = GitHubService


__all__ = [
    "GitHubArtifact",
    "GitHubBlobRecord",
    "GitHubCommitRecord",
    "GitHubFacade",
    "GitHubFacadeFactory",
    "GitHubPRRequest",
    "GitHubPRResult",
    "GitHubPRService",
    "GitHubPullRequestRecord",
    "GitHubRefRecord",
    "GitHubRepositoryFacade",
    "GitHubService",
    "GitHubTreeEntry",
    "GitHubTreeRecord",
    "sanitize_seam_name",
    "validate_repo_url",
]
