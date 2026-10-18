"""Run the trusted static parser out-of-process, without application secrets."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

from pydantic import ValidationError

from app.core.errors import ApiError
from app.schemas.graph import CodeGraph

_MAX_OUTPUT_BYTES = 64 * 1024 * 1024


async def analyze_source(path: Path) -> CodeGraph:
    """A parser crash/timeout cannot crash or leave a running thread in the API."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-m",
        "app.services.parse_source",
        str(path),
        env={"PATH": os.defpath, "LANG": "C.UTF-8"},
        cwd="/",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        async with asyncio.timeout(35):
            assert process.stdout is not None
            output = bytearray()
            while chunk := await process.stdout.read(65536):
                output.extend(chunk)
                if len(output) > _MAX_OUTPUT_BYTES:
                    raise ApiError(
                        422, "AST_OUTPUT_TOO_LARGE", "Source graph is too large."
                    )
            if await process.wait() != 0:
                raise ApiError(422, "AST_PARSE_ERROR", "Python source analysis failed.")
            try:
                return CodeGraph.model_validate_json(bytes(output))
            except ValidationError:
                raise ApiError(
                    422, "AST_PARSE_ERROR", "Python source analysis failed."
                ) from None
    except TimeoutError:
        raise ApiError(
            504, "AST_PARSE_TIMEOUT", "Python source analysis timed out."
        ) from None
    finally:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        await process.wait()


def _main() -> None:
    """Only this installed module executes; target modules are never imported."""
    import resource

    from app.agents.static_analyzer import StaticAnalysisError, StaticAnalyzerAgent

    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if len(sys.argv) != 2:
        raise SystemExit(2)
    try:
        graph = StaticAnalyzerAgent().analyze(Path(sys.argv[1]))
        output = graph.model_dump_json().encode()
        if len(output) > _MAX_OUTPUT_BYTES:
            raise SystemExit(2)
        sys.stdout.buffer.write(output)
    except (StaticAnalysisError, MemoryError, RecursionError):
        raise SystemExit(2) from None


if __name__ == "__main__":
    _main()
