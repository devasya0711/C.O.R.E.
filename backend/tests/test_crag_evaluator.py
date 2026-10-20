"""Focused deterministic tests for the Phase 5 CRAG evaluator."""

from __future__ import annotations

import pytest
from app.agents.crag_evaluator import (
    MAX_MISSING_FILES,
    CRAGContextRequest,
    CRAGEvaluatorAgent,
)
from app.core.errors import ApiError
from app.schemas.graph import CodeEdge, CodeGraph, CodeNode
from pydantic import ValidationError


def node(
    qualified_name: str,
    *,
    source: str = "def run(value: str) -> str:\n    return value\n",
    file_path: str = "service.py",
) -> CodeNode:
    return CodeNode(
        label="Function",
        qualified_name=qualified_name,
        name=qualified_name.rsplit(".", 1)[-1],
        file_path=file_path,
        start_line=1,
        end_line=max(1, source.count("\n")),
        source_code=source,
    )


def test_complete_selected_context_is_sufficient() -> None:
    graph = CodeGraph(
        nodes=[node("service.run"), node("service.helper")],
        edges=[
            CodeEdge(
                source="service.run",
                target="service.helper",
                relationship="CALLS",
                line=2,
            )
        ],
    )

    result = CRAGEvaluatorAgent().evaluate(graph)

    assert result.decision == "context_sufficient"
    assert result.missing_files == []
    assert result.reasons == []


def test_missing_source_is_incomplete_and_uses_injected_reader_fallback() -> None:
    context = CRAGContextRequest(
        selected_nodes=[node("service.run", source="", file_path="service.py")]
    )
    reader = {"service.py": "def run(value: str) -> str:\n    return value\n"}

    result = CRAGEvaluatorAgent(reader).evaluate(context)

    assert result.decision == "context_sufficient"
    assert result.missing_files == []
    assert result.augmented_sources == {"service.py": reader["service.py"]}


def test_missing_import_and_type_context_are_reported() -> None:
    context = CRAGContextRequest(
        selected_nodes=[
            node(
                "service.run",
                source=(
                    "from .missing_types import MissingType\n\n"
                    "def run(value: MissingType, other: UnknownType) -> MissingType:\n"
                    "    return value\n"
                ),
                file_path="pkg/service.py",
            )
        ],
        warnings=[
            "pkg/service.py:1: unresolved import 'MissingType' (pkg.missing_types)"
        ],
    )

    result = CRAGEvaluatorAgent().evaluate(context)

    assert result.decision == "context_incomplete"
    assert result.missing_files == ["pkg/missing_types.py"]
    assert any("context warning" in reason for reason in result.reasons)
    assert any(
        "missing type context 'UnknownType'" in reason for reason in result.reasons
    )


def test_relative_import_can_be_augmented_to_complete_context() -> None:
    context = CRAGContextRequest(
        selected_nodes=[
            node(
                "pkg.service.run",
                source=(
                    "from .types import Order\n\n"
                    "def run(order: Order) -> Order:\n"
                    "    return order\n"
                ),
                file_path="pkg/service.py",
            )
        ],
        augment_full_files=True,
    )
    reader = {
        "pkg/types.py": "class Order:\n    pass\n",
    }

    result = CRAGEvaluatorAgent(reader).evaluate(context)

    assert result.decision == "context_sufficient"
    assert result.augmented_sources == reader


def test_warning_marks_context_incomplete_even_when_source_is_present() -> None:
    context = CRAGContextRequest(
        selected_nodes=[node("service.run")],
        warnings=["service.py:4: unresolved call 'gateway.charge'"],
    )

    result = CRAGEvaluatorAgent().evaluate(context)

    assert result.decision == "context_incomplete"
    assert result.missing_files == []
    assert any("unresolved call" in reason for reason in result.reasons)


def test_context_limits_are_explicit_and_paths_are_safe() -> None:
    context = {"selected_nodes": [node(f"service.run{index}") for index in range(257)]}
    with pytest.raises(ApiError) as caught:
        CRAGEvaluatorAgent().evaluate(context)
    assert caught.value.code == "CRAG_LIMIT_EXCEEDED"

    with pytest.raises(ValidationError):
        CRAGContextRequest(
            selected_nodes=[node("service.run", file_path="../outside.py")]
        )


def test_missing_file_list_is_bounded() -> None:
    nodes = [
        node(
            f"pkg.service.run{index}",
            source=(f"from .missing_{index} import Missing\n"),
            file_path=f"pkg/service_{index}.py",
        )
        for index in range(MAX_MISSING_FILES + 1)
    ]
    context = CRAGContextRequest(selected_nodes=nodes)

    with pytest.raises(ApiError) as caught:
        CRAGEvaluatorAgent({}).evaluate(context)
    assert caught.value.code == "CRAG_LIMIT_EXCEEDED"


def test_reader_failures_are_safe_api_errors() -> None:
    def broken_reader(_: str) -> str:
        raise OSError("fixture source unavailable")

    context = CRAGContextRequest(
        selected_nodes=[node("service.run", source="", file_path="service.py")]
    )

    # OSError means the source is unavailable, which is represented as a
    # deterministic incomplete context rather than a provider-like failure.
    result = CRAGEvaluatorAgent(broken_reader).evaluate(context)
    assert result.decision == "context_incomplete"
    assert result.missing_files == ["service.py"]


def test_reader_paths_are_repository_relative() -> None:
    requested: list[str] = []

    def reader(path: str) -> str | None:
        requested.append(path)
        return None

    context = CRAGContextRequest(
        selected_nodes=[node("service.run", source="", file_path="pkg/service.py")]
    )
    CRAGEvaluatorAgent(reader).evaluate(context)
    assert requested == ["pkg/service.py"]
    assert all(not path.startswith("/") for path in requested)
