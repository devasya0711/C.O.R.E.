"""Portable static checks for the Batch 11 deployment contract."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    path = ROOT / relative_path
    if not path.is_file():
        raise AssertionError(f"required deployment file is missing: {relative_path}")
    return path.read_text(encoding="utf-8")


def _require(text: str, pattern: str, description: str) -> None:
    if not re.search(pattern, text, flags=re.MULTILINE):
        raise AssertionError(f"missing {description}: {pattern}")


def validate_backend_dockerfile() -> None:
    dockerfile = _read("backend/Dockerfile")
    _require(dockerfile, r"^FROM .* AS builder$", "backend builder stage")
    _require(dockerfile, r"^FROM .* AS runtime$", "backend runtime stage")
    _require(
        dockerfile,
        r"COPY --from=builder /opt/venv /opt/venv",
        "venv transfer",
    )
    _require(dockerfile, r"USER core", "non-root backend runtime")
    _require(dockerfile, r"groupadd --gid 1000 core", "stable backend UID/GID")
    _require(dockerfile, r"ENTRYPOINT \[\"/app/docker-entrypoint\.sh\"\]", "entrypoint")
    _require(
        _read("backend/docker-entrypoint.sh"),
        r"^alembic upgrade head$",
        "startup migration",
    )


def validate_frontend_dockerfile() -> None:
    dockerfile = _read("frontend/Dockerfile")
    for stage in ("deps", "builder", "runner"):
        _require(dockerfile, rf"^FROM .* AS {stage}$", f"frontend {stage} stage")
    _require(dockerfile, r"npm ci", "lockfile-based frontend install")
    _require(dockerfile, r"USER node", "non-root frontend runtime")


def validate_sandbox_dockerfile() -> None:
    dockerfile = _read("backend/sandbox.Dockerfile")
    _require(dockerfile, r"USER sandbox", "non-root sandbox runtime")
    _require(dockerfile, r"groupadd --gid 1000 sandbox", "stable sandbox UID/GID")


def validate_compose() -> None:
    compose = _read("docker-compose.yml")
    for service in (
        "postgres:",
        "neo4j:",
        "redis:",
        "sandbox:",
        "backend:",
        "frontend:",
    ):
        _require(
            compose,
            rf"^  {re.escape(service)}$",
            f"Compose service {service[:-1]}",
        )
    for dependency in ("postgres", "neo4j", "redis", "sandbox"):
        _require(
            compose,
            rf"{re.escape(dependency)}:\n\s+condition: service_healthy",
            f"backend health dependency for {dependency}",
        )
    _require(compose, r"SANDBOX_DOCKER_IMAGE", "sandbox image setting")
    _require(compose, r"SANDBOX_DOCKER_NOFILE_LIMIT", "sandbox nofile setting")
    _require(
        compose,
        r"SANDBOX_DOCKER_WORKSPACE_ROOT",
        "sandbox workspace setting",
    )
    _require(
        compose,
        r"SANDBOX_WORKSPACE_ROOT.*SANDBOX_WORKSPACE_ROOT.*:rw",
        "same-path sandbox workspace bind",
    )
    _require(
        compose,
        r"SANDBOX_REQUIRE_DOCKER: \$\{SANDBOX_REQUIRE_DOCKER:-true\}",
        "fail-closed Compose sandbox setting",
    )
    _require(compose, r"/var/run/docker\.sock", "Docker socket mount")
    _require(compose, r"DOCKER_GID", "Docker socket group setting")
    _require(compose, r"redis-cli.*ping", "Redis healthcheck")
    sandbox_section = compose.split("  sandbox:", 1)[1].split("  backend:", 1)[0]
    if "/var/run/docker.sock" in sandbox_section:
        raise AssertionError("sandbox service must not receive the Docker socket")
    if re.search(r"(?m)^\s*-\s+[^\n]*:/tmp(?::|$)", compose):
        raise AssertionError("Compose must not mount the host /tmp directory")


def validate_sandbox_runner_contract() -> None:
    runner = _read("backend/app/services/sandbox_runner.py")
    _require(runner, r'"--network=none"', "sandbox network isolation")
    _require(runner, r'"--name", container_name', "named sandbox container")
    _require(runner, r'"--rm"', "automatic sandbox container cleanup")
    _require(runner, r'"--ulimit"', "sandbox file-descriptor cap")
    _require(runner, r'"--user",', "sandbox non-root UID mapping")
    _require(runner, r'"rm", "--force", container_name', "timeout container cleanup")
    _require(
        runner,
        r"TemporaryDirectory\(\s*prefix=\"core-sandbox-\",\s*dir=str\(workspace_root\)",
        "daemon-visible shared workspace creation",
    )
    if re.search(r"type=bind,src=/tmp", runner):
        raise AssertionError("sandbox runner must not bind the host /tmp directory")


def validate_sandbox_configuration() -> None:
    env = _read(".env.example")
    _require(
        env, r"^SANDBOX_WORKSPACE_ROOT=/absolute/path/", "sandbox root prerequisite"
    )
    _require(env, r"^SANDBOX_BACKEND_UID=", "sandbox backend UID setting")
    _require(env, r"^SANDBOX_BACKEND_GID=", "sandbox backend GID setting")


def validate_ci_and_readme() -> None:
    ci = _read(".github/workflows/ci.yml")
    _require(ci, r"scripts/validate_deployment\.py", "CI static deployment check")
    _require(
        ci,
        r"SANDBOX_WORKSPACE_ROOT: \$\{\{ runner\.temp \}\}/core-sandbox-workspaces",
        "CI sandbox workspace root",
    )
    _require(
        ci,
        r'mkdir -p "\$SANDBOX_WORKSPACE_ROOT"',
        "CI sandbox workspace preparation",
    )
    _require(ci, r"docker compose config --quiet", "CI Compose validation")
    _require(ci, r"docker compose build backend frontend sandbox", "CI image builds")

    readme = _read("README.md")
    _require(readme, r"```mermaid", "README architecture diagram")
    _require(readme, r"docker compose up --build", "README Compose quickstart")
    _require(
        readme,
        r"Docker.*unavailable|unavailable.*Docker",
        "Docker verification gap",
    )


def main() -> int:
    validators = (
        validate_backend_dockerfile,
        validate_frontend_dockerfile,
        validate_sandbox_dockerfile,
        validate_compose,
        validate_sandbox_runner_contract,
        validate_sandbox_configuration,
        validate_ci_and_readme,
    )
    try:
        for validator in validators:
            validator()
    except AssertionError as error:
        print(f"deployment validation failed: {error}", file=sys.stderr)
        return 1
    print("deployment descriptors passed static validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
