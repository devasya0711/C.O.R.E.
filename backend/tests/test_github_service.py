"""GitHub PR service behavior using an injected provider facade."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

import pytest
from app.core.errors import ApiError
from app.services.github_service import (
    GitHubArtifact,
    GitHubBlobRecord,
    GitHubCommitRecord,
    GitHubFacade,
    GitHubPRRequest,
    GitHubPullRequestRecord,
    GitHubRefRecord,
    GitHubRepositoryFacade,
    GitHubService,
    GitHubTreeEntry,
    GitHubTreeRecord,
    sanitize_seam_name,
    validate_repo_url,
)
from pydantic import SecretStr, ValidationError

_TOKEN = "github_pat_fixture_token"
_BASE_SHA = "a" * 40
_BASE_TREE_SHA = "b" * 40
_PR_URL = "https://github.com/acme/legacy-shop/pull/17"


class ProviderError(Exception):
    """Minimal provider exception with the metadata used by the mapper."""

    def __init__(self, status: int) -> None:
        super().__init__("provider fixture failure")
        self.status = status


class FakeRepository(GitHubRepositoryFacade):
    """Stateful fake that models refs, Git objects, and open pull requests."""

    def __init__(self) -> None:
        self.refs: dict[str, str] = {"heads/main": _BASE_SHA}
        self.commits: dict[str, GitHubCommitRecord] = {
            _BASE_SHA: GitHubCommitRecord(
                _BASE_SHA, _BASE_TREE_SHA, "fixture base commit"
            )
        }
        self.blobs: list[str] = []
        self.tree_entries: list[GitHubTreeEntry] = []
        self.trees = 0
        self.commits_created = 0
        self.pull_requests: list[GitHubPullRequestRecord] = []
        self.pull_bodies: list[str] = []
        self.fail_create_pr_status: int | None = None
        self.calls: list[str] = []

    def get_git_ref(self, ref: str) -> GitHubRefRecord:
        self.calls.append(f"get_ref:{ref}")
        try:
            return GitHubRefRecord(self.refs[ref])
        except KeyError:
            raise ProviderError(404) from None

    def create_git_ref(self, ref: str, sha: str) -> GitHubRefRecord:
        self.calls.append(f"create_ref:{ref}")
        short_ref = ref.removeprefix("refs/")
        if short_ref in self.refs:
            raise ProviderError(422)
        self.refs[short_ref] = sha
        return GitHubRefRecord(sha)

    def update_git_ref(self, ref: str, sha: str) -> GitHubRefRecord:
        self.calls.append(f"update_ref:{ref}")
        self.refs[ref] = sha
        return GitHubRefRecord(sha)

    def get_git_commit(self, sha: str) -> GitHubCommitRecord:
        self.calls.append(f"get_commit:{sha}")
        try:
            return self.commits[sha]
        except KeyError:
            raise ProviderError(404) from None

    def create_git_blob(self, content: str) -> GitHubBlobRecord:
        self.calls.append("create_blob")
        sha = hashlib.sha256(content.encode()).hexdigest()
        self.blobs.append(sha)
        return GitHubBlobRecord(sha)

    def create_git_tree(
        self, entries: Sequence[GitHubTreeEntry], base_tree: GitHubCommitRecord
    ) -> GitHubTreeRecord:
        self.calls.append("create_tree")
        assert base_tree.tree_sha
        self.tree_entries = list(entries)
        self.trees += 1
        return GitHubTreeRecord(f"{self.trees:040x}")

    def create_git_commit(
        self,
        message: str,
        tree: GitHubTreeRecord,
        parent: GitHubCommitRecord,
    ) -> GitHubCommitRecord:
        self.calls.append("create_commit")
        assert tree.sha and parent.sha
        self.commits_created += 1
        sha = f"{self.commits_created + 100:040x}"
        commit = GitHubCommitRecord(sha, tree.sha, message)
        self.commits[sha] = commit
        return commit

    def get_open_pull_requests(
        self, head: str, base: str
    ) -> Sequence[GitHubPullRequestRecord]:
        self.calls.append(f"get_pulls:{head}:{base}")
        owner, branch = head.split(":", 1)
        assert owner == "acme"
        return tuple(
            pull
            for pull in self.pull_requests
            if pull.head_ref == branch and pull.base_ref == base
        )

    def create_pull_request(
        self, title: str, body: str, head: str, base: str
    ) -> GitHubPullRequestRecord:
        self.calls.append("create_pull")
        if self.fail_create_pr_status is not None:
            status = self.fail_create_pr_status
            self.fail_create_pr_status = None
            raise ProviderError(status)
        branch_sha = self.refs[f"heads/{head}"]
        pull = GitHubPullRequestRecord(
            number=17,
            url=_PR_URL,
            head_ref=head,
            base_ref=base,
            head_sha=branch_sha,
        )
        self.pull_requests.append(pull)
        self.pull_bodies.append(f"{title}\n{body}")
        return pull


class FakeFacade(GitHubFacade):
    def __init__(self, repository: FakeRepository) -> None:
        self.repository = repository
        self.tokens: list[SecretStr] = []
        self.closed = 0

    def get_repository(self, full_name: str) -> GitHubRepositoryFacade:
        assert full_name == "acme/legacy-shop"
        return self.repository

    def close(self) -> None:
        self.closed += 1


def request(token: str = _TOKEN) -> GitHubPRRequest:
    return GitHubPRRequest(
        repo_url="https://github.com/acme/legacy-shop.git",
        base_branch="main",
        seam_name="Payments Service",
        artifacts=[
            GitHubArtifact(
                path="services/payments.py", content="def pay() -> None:\n    pass\n"
            ),
            GitHubArtifact(path="services/__init__.py", content=""),
        ],
        dependency_diagram="graph TD\n  API-->Payments",
        blast_radius_summary="Risk score: 78; one API route is affected.",
        token=SecretStr(token),
    )


def service_for(
    repository: FakeRepository,
) -> tuple[GitHubService, FakeFacade]:
    facade = FakeFacade(repository)

    def factory(token: SecretStr) -> GitHubFacade:
        facade.tokens.append(token)
        return facade

    return GitHubService(facade_factory=factory), facade


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "https://github.com/acme/legacy-shop",
            "https://github.com/acme/legacy-shop.git",
        ),
        (
            "https://github.com/acme/legacy-shop.git",
            "https://github.com/acme/legacy-shop.git",
        ),
        ("https://github.com/acme/.github", "https://github.com/acme/.github.git"),
    ],
)
def test_supported_repository_urls_are_canonicalized(value: str, expected: str) -> None:
    assert validate_repo_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://github.com/acme/legacy-shop",
        "https://github.com:443/acme/legacy-shop",
        "https://token@github.com/acme/legacy-shop",
        "https://github.com.evil.test/acme/legacy-shop",
        "https://github.com/acme/legacy-shop?token=secret",
        "https://github.com/acme/legacy-shop/extra",
        "https://github.com/acme/legacy-shop/",
        "https://github.com/acme/../private",
        "https://github.com/acme/legacy-shop\n",
    ],
)
def test_unsupported_repository_urls_are_rejected_without_reflection(
    value: str,
) -> None:
    with pytest.raises(ApiError) as caught:
        validate_repo_url(value)
    assert caught.value.code == "INVALID_GITHUB_REPOSITORY_URL"
    assert value not in str(caught.value)


def test_artifact_paths_and_seams_reject_traversal() -> None:
    with pytest.raises(ValidationError):
        GitHubArtifact(path="../outside.py", content="safe")
    with pytest.raises(ValidationError):
        GitHubArtifact(path=".git/config", content="safe")
    with pytest.raises(ApiError) as caught:
        sanitize_seam_name("payments/../../secret")
    assert caught.value.code == "INVALID_GITHUB_SEAM"
    assert sanitize_seam_name("Payment Processor_v2") == "payment-processor-v2"


@pytest.mark.parametrize("base_branch", ["main.lock", "foo/.hidden", "foo/bar."])
def test_git_branch_names_reject_ref_unsafe_components(base_branch: str) -> None:
    values = request().model_dump()
    values["base_branch"] = base_branch
    with pytest.raises(ValidationError):
        GitHubPRRequest.model_validate(values)


@pytest.mark.asyncio
async def test_branch_one_commit_and_pr_include_verified_context() -> None:
    repository = FakeRepository()
    service, facade = service_for(repository)

    result = await service.create_pull_request(request())

    assert result.branch == "refactor/extract-payments-service"
    assert result.commit_sha == "0" * 38 + "65"
    assert result.pr_number == 17
    assert result.pr_url == _PR_URL
    assert result.created is True
    assert repository.commits_created == 1
    assert len(repository.blobs) == 2
    assert repository.trees == 1
    assert repository.calls.count("create_commit") == 1
    assert "```mermaid\ngraph TD\n  API-->Payments\n```" in repository.pull_bodies[0]
    assert "Risk score: 78" in repository.pull_bodies[0]
    assert facade.closed == 1


@pytest.mark.asyncio
async def test_repeated_request_reuses_existing_pr_without_second_commit() -> None:
    repository = FakeRepository()
    service, _ = service_for(repository)

    first = await service.create_pull_request(request())
    second = await service.create_pull_request(request())

    assert first.created is True
    assert second.created is False
    assert second.pr_url == first.pr_url == _PR_URL
    assert repository.commits_created == 1
    assert repository.calls.count("create_commit") == 1
    assert repository.calls.count("create_pull") == 1


@pytest.mark.asyncio
async def test_retry_after_commit_failure_reuses_branch_tip() -> None:
    repository = FakeRepository()
    repository.fail_create_pr_status = 500
    service, _ = service_for(repository)

    with pytest.raises(ApiError) as first_error:
        await service.create_pull_request(request())
    second = await service.create_pull_request(request())

    assert first_error.value.code == "GITHUB_PROVIDER_ERROR"
    assert second.created is True
    assert repository.commits_created == 1
    assert repository.calls.count("create_commit") == 1


@pytest.mark.asyncio
async def test_token_is_secretstr_and_never_reflected_or_sent_for_invalid_input() -> (
    None
):
    repository = FakeRepository()
    service, facade = service_for(repository)
    invalid = request("fixture-token!")

    with pytest.raises(ApiError) as caught:
        await service.create_pull_request(invalid)

    assert caught.value.code == "GITHUB_AUTH_FAILED"
    assert "fixture-token!" not in str(caught.value)
    assert "fixture-token!" not in repr(invalid)
    assert facade.tokens == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "GITHUB_AUTH_FAILED"),
        (404, "GITHUB_REPOSITORY_NOT_FOUND"),
        (429, "GITHUB_RATE_LIMITED"),
    ],
)
async def test_provider_failures_are_mapped_to_safe_api_errors(
    status: int, code: str
) -> None:
    def factory(_: SecretStr) -> GitHubFacade:
        raise ProviderError(status)

    service = GitHubService(facade_factory=factory)
    with pytest.raises(ApiError) as caught:
        await service.create_pull_request(request())

    assert caught.value.code == code
    assert _TOKEN not in str(caught.value)


@pytest.mark.asyncio
async def test_provider_timeout_is_mapped_without_live_credentials() -> None:
    def factory(_: SecretStr) -> GitHubFacade:
        raise TimeoutError

    service = GitHubService(facade_factory=factory)
    with pytest.raises(ApiError) as caught:
        await service.create_pull_request(request())

    assert caught.value.code == "GITHUB_TIMEOUT"
    assert _TOKEN not in str(caught.value)


@pytest.mark.asyncio
async def test_source_commit_must_match_the_current_github_base() -> None:
    repository = FakeRepository()
    service, _facade = service_for(repository)
    request_with_identity = request().model_copy(update={"source_commit_sha": "c" * 40})

    with pytest.raises(ApiError) as caught:
        await service.create_pull_request(request_with_identity)

    assert caught.value.code == "GITHUB_BASE_CHANGED"


@pytest.mark.asyncio
async def test_invalid_artifact_request_does_not_call_facade() -> None:
    called = False

    def factory(_: SecretStr) -> GitHubFacade:
        nonlocal called
        called = True
        raise AssertionError("invalid input must not reach GitHub")

    service = GitHubService(facade_factory=factory)
    invalid: Mapping[str, object] = {
        "repo_url": "https://github.com/acme/legacy-shop",
        "base_branch": "main",
        "seam_name": "payments",
        "artifacts": [{"path": "/absolute.py", "content": "safe"}],
        "dependency_diagram": "graph TD\n  A-->B",
        "blast_radius_summary": "safe",
        "token": SecretStr(_TOKEN),
    }

    with pytest.raises(ApiError) as caught:
        await service.create_pull_request(invalid)
    assert caught.value.code == "GITHUB_INPUT_INVALID"
    assert called is False
