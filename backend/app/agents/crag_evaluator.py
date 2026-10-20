"""Deterministic structural evaluation of retrieved seam context.

The evaluator deliberately does not ask an LLM to grade a graph snapshot.  It
checks the same evidence that the static analyzer records: selected nodes have
source, selected edges stay inside the seam, import/type references have
repository context, and analyzer warnings do not leave an ambiguity hidden.
Missing files are returned in a bounded list.  A caller may opt into reading
full files through the injected reader; no source is invented when that reader
cannot provide it.
"""

from __future__ import annotations

import ast
import builtins
import re
from collections.abc import Callable, Mapping
from pathlib import PurePosixPath
from typing import Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.core.errors import ApiError
from app.schemas.graph import CodeEdge, CodeGraph, CodeNode

MAX_SELECTED_NODES = 256
MAX_SELECTED_EDGES = 1_024
MAX_WARNINGS = 512
MAX_WARNING_BYTES = 4_096
MAX_MISSING_FILES = 32
MAX_AUGMENTED_FILES = 32
MAX_SOURCE_BYTES = 1 * 1024 * 1024
MAX_AUGMENTED_BYTES = 4 * 1024 * 1024

_SOURCE_LABELS = frozenset({"File", "Class", "Function", "APIEndpoint"})
_CONTEXT_WARNING_MARKERS = (
    "unresolved import",
    "unresolved base class",
    "unresolved type",
    "missing type",
    "wildcard binding",
    "above analysis root",
    "unresolved call",
    "retrieve full source",
    "ambiguous definition",
)
_WARNING_IMPORT = re.compile(
    r"^(?P<path>[^:]+):\d+:\s*unresolved import\s+['\"](?P<module>[^'\"]+)"
    r"(?:['\"]\s+\((?P<resolved>[^)]+)\))?"
)


class SourceReader(Protocol):
    """Synchronous repository-source boundary used by CRAG fallback retrieval."""

    def read(self, file_path: str) -> str | None:
        """Return one repository-relative file or ``None`` when unavailable."""


SourceReaderLike = SourceReader | Callable[[str], str | None] | Mapping[str, str]
CRAGDecision = Literal["context_sufficient", "context_incomplete"]


def _safe_text(value: str, maximum: int) -> str:
    """Bound diagnostic/source text without reflecting control data."""
    if "\x00" in value or any(
        (ord(character) < 32 and character not in "\t\n\r") or ord(character) == 127
        for character in value
    ):
        raise ValueError("text contains unsupported control characters")
    if len(value.encode("utf-8")) > maximum:
        raise ValueError("text exceeds the configured limit")
    return value


def _relative_path(value: str) -> str:
    """Accept the repository-relative paths emitted by the static analyzer."""
    if (
        not value
        or "\x00" in value
        or "\\" in value
        or value.startswith("/")
        or value.endswith("/")
    ):
        raise ValueError("source paths must be relative POSIX paths")
    path = PurePosixPath(value)
    if path.is_absolute() or path.parts != tuple(value.split("/")):
        raise ValueError("source paths must be relative POSIX paths")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("source paths contain an unsafe component")
    return value


class CRAGContextRequest(BaseModel):
    """Validated subgraph selected for one deterministic CRAG decision."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    selected_nodes: list[CodeNode] = Field(
        default_factory=list, max_length=MAX_SELECTED_NODES
    )
    selected_edges: list[CodeEdge] = Field(
        default_factory=list, max_length=MAX_SELECTED_EDGES
    )
    warnings: list[str] = Field(default_factory=list, max_length=MAX_WARNINGS)
    augment_full_files: bool = False

    @field_validator("warnings")
    @classmethod
    def validate_warnings(cls, values: list[str]) -> list[str]:
        return [_safe_text(value, MAX_WARNING_BYTES) for value in values]

    @model_validator(mode="after")
    def validate_symbols(self) -> CRAGContextRequest:
        names = [node.qualified_name for node in self.selected_nodes]
        if len(names) != len(set(names)):
            raise ValueError("selected seam symbols must be unique")
        for node in self.selected_nodes:
            _relative_path(node.file_path)
        return self

    @classmethod
    def from_graph(
        cls,
        graph: CodeGraph,
        selected_names: set[str] | None = None,
        *,
        augment_full_files: bool = False,
    ) -> CRAGContextRequest:
        """Build a seam request from a validated graph without changing it."""
        selected = (
            set(selected_names)
            if selected_names is not None
            else {node.qualified_name for node in graph.nodes}
        )
        nodes = [node for node in graph.nodes if node.qualified_name in selected]
        edges = [
            edge
            for edge in graph.edges
            if edge.source in selected and edge.target in selected
        ]
        return cls(
            selected_nodes=nodes,
            selected_edges=edges,
            warnings=graph.warnings,
            augment_full_files=augment_full_files,
        )


class CRAGEvaluationResult(BaseModel):
    """Safe, bounded result consumed by the migration workflow."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    decision: CRAGDecision
    missing_files: list[str] = Field(default_factory=list, max_length=MAX_MISSING_FILES)
    reasons: list[str] = Field(default_factory=list, max_length=MAX_WARNINGS)
    augmented_sources: dict[str, str] = Field(default_factory=dict)

    @field_validator("missing_files")
    @classmethod
    def validate_missing_files(cls, values: list[str]) -> list[str]:
        return [_relative_path(value) for value in values]

    @field_validator("reasons")
    @classmethod
    def validate_reasons(cls, values: list[str]) -> list[str]:
        return [_safe_text(value, MAX_WARNING_BYTES) for value in values]

    @field_validator("augmented_sources")
    @classmethod
    def validate_augmented_sources(cls, values: dict[str, str]) -> dict[str, str]:
        total = 0
        for path, source in values.items():
            _relative_path(path)
            if not isinstance(source, str):
                raise ValueError("augmented source must be text")
            encoded = source.encode("utf-8")
            if len(encoded) > MAX_SOURCE_BYTES:
                raise ValueError("augmented source exceeds the configured limit")
            total += len(encoded)
        if len(values) > MAX_AUGMENTED_FILES or total > MAX_AUGMENTED_BYTES:
            raise ValueError("augmented source exceeds the configured limit")
        return values

    @property
    def context_decision(self) -> CRAGDecision:
        """Compatibility spelling for workflow state serializers."""
        return self.decision

    @property
    def full_file_augmentation(self) -> dict[str, str]:
        """Return retrieved full files using the architecture's terminology."""
        return self.augmented_sources


def _input_error() -> ApiError:
    return ApiError(422, "CRAG_INPUT_INVALID", "CRAG context validation failed.")


def _limit_error() -> ApiError:
    return ApiError(
        422,
        "CRAG_LIMIT_EXCEEDED",
        "CRAG context exceeds the bounded evaluation limits.",
    )


def _source_error() -> ApiError:
    return ApiError(
        503,
        "CRAG_SOURCE_UNAVAILABLE",
        "Repository source could not be read for CRAG evaluation.",
    )


def _reader_value(reader: SourceReaderLike, file_path: str) -> str | None:
    """Call one of the deliberately small reader forms used by integrations."""
    if isinstance(reader, Mapping):
        value = reader.get(file_path)
    else:
        method = getattr(reader, "read", None)
        if callable(method):
            value = method(file_path)
        else:
            method = getattr(reader, "read_source", None)
            if callable(method):
                value = method(file_path)
            elif callable(reader):
                value = reader(file_path)
            else:
                raise TypeError("source reader must be callable or expose read")
    if value is not None and not isinstance(value, str):
        raise TypeError("source reader returned a non-text value")
    return value


def _module_candidates(module: str, current_file: str, level: int = 0) -> list[str]:
    """Resolve import spellings to possible repository-relative Python files."""
    current_parts = PurePosixPath(current_file).parts[:-1]
    if level:
        base_length = max(0, len(current_parts) - (level - 1))
        parts = list(current_parts[:base_length])
    else:
        parts = []
    if module:
        parts.extend(part for part in module.split(".") if part)
    if not parts:
        return []
    stem = "/".join(parts)
    return [f"{stem}.py", f"{stem}/__init__.py"]


def _import_candidates(
    source: str, file_path: str
) -> list[tuple[tuple[str, ...], bool]]:
    """Return import candidates and whether each import is repository-relative."""
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        return []
    candidates: list[tuple[tuple[str, ...], bool]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                options = tuple(_module_candidates(alias.name, file_path))
                if options:
                    candidates.append((options, False))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            options = tuple(_module_candidates(module, file_path, node.level))
            if node.level:
                if options:
                    candidates.append((options, True))
            elif module:
                if options:
                    candidates.append((options, False))
    return candidates


def _type_names(source: str, file_path: str) -> tuple[set[str], set[str], set[str]]:
    """Collect annotation/base names without executing target source."""
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError:
        return set(), set(), set()
    defined: set[str] = set()
    imported: set[str] = set()
    references: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                annotations = [argument.annotation for argument in node.args.args]
                annotations.extend(
                    argument.annotation for argument in node.args.kwonlyargs
                )
                annotations.append(node.returns)
                for annotation in annotations:
                    if annotation is not None:
                        references.update(
                            name.id
                            for name in ast.walk(annotation)
                            if isinstance(name, ast.Name)
                        )
            else:
                for base in node.bases:
                    references.update(
                        name.id for name in ast.walk(base) if isinstance(name, ast.Name)
                    )
        elif isinstance(node, ast.Import):
            imported.update(
                alias.asname or alias.name.split(".")[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            imported.update(alias.asname or alias.name for alias in node.names)
    return defined, imported, references


def _warning_is_contextual(warning: str) -> bool:
    lowered = warning.casefold()
    return any(marker in lowered for marker in _CONTEXT_WARNING_MARKERS)


class CRAGEvaluatorAgent:
    """Evaluate one bounded seam using only graph/source evidence."""

    def __init__(self, source_reader: SourceReaderLike | None = None) -> None:
        self.source_reader = source_reader

    def evaluate(
        self,
        context: CRAGContextRequest | CodeGraph | Mapping[str, object],
        *,
        augment_full_files: bool | None = None,
    ) -> CRAGEvaluationResult:
        """Return a complete decision or an explicit list of missing context."""
        request = self._request(context, augment_full_files)
        if self.source_reader is None and any(
            node.label in _SOURCE_LABELS and not node.source_code
            for node in request.selected_nodes
        ):
            # This is a deterministic incomplete result, not an implicit local
            # filesystem lookup.  Repository access must be injected by callers.
            reader = None
        else:
            reader = self.source_reader

        missing: set[str] = set()
        reasons: list[str] = []
        inspected: dict[str, str] = {}
        augmented: dict[str, str] = {}
        read_cache: dict[str, str | None] = {}
        selected_files = {
            node.file_path for node in request.selected_nodes if node.file_path
        }

        def add_missing(path: str) -> None:
            normalized = _relative_path(path)
            if normalized not in missing and len(missing) >= MAX_MISSING_FILES:
                raise _limit_error()
            missing.add(normalized)

        def read_source(path: str) -> str | None:
            if reader is None:
                return None
            if path in read_cache:
                return read_cache[path]
            try:
                value = _reader_value(reader, path)
            except (OSError, UnicodeError, LookupError):
                read_cache[path] = None
                return None
            except Exception as error:
                raise _source_error() from error
            if value is not None:
                try:
                    encoded_size = len(value.encode("utf-8"))
                except UnicodeEncodeError as error:
                    raise _source_error() from error
                if encoded_size > MAX_SOURCE_BYTES:
                    raise ApiError(
                        422,
                        "CRAG_SOURCE_TOO_LARGE",
                        "Repository source exceeds the CRAG file limit.",
                    )
            read_cache[path] = value
            return value

        def ensure_file(path: str, *, required: bool, augment: bool) -> None:
            normalized = _relative_path(path)
            if normalized in inspected and not augment:
                return
            value = read_source(normalized)
            if value is None:
                if required:
                    add_missing(normalized)
                return
            inspected[normalized] = value
            if augment:
                if normalized not in augmented:
                    if len(augmented) >= MAX_AUGMENTED_FILES:
                        raise _limit_error()
                    augmented[normalized] = value
            missing.discard(normalized)

        for node in request.selected_nodes:
            if node.label not in _SOURCE_LABELS:
                continue
            if node.source_code:
                inspected.setdefault(node.file_path, node.source_code)
            else:
                ensure_file(node.file_path, required=True, augment=True)
                if node.file_path not in inspected:
                    reasons.append(f"missing source for {node.qualified_name}")

        if request.augment_full_files:
            for file_path in sorted(selected_files):
                ensure_file(file_path, required=False, augment=True)

        if not request.selected_nodes:
            reasons.append("no seam nodes were selected")

        selected_names = {node.qualified_name for node in request.selected_nodes}
        for edge in request.selected_edges:
            if edge.source not in selected_names or edge.target not in selected_names:
                reasons.append(
                    f"edge context is incomplete for {edge.source!r}->{edge.target!r}"
                )

        for file_path, source in list(inspected.items()):
            if not source:
                continue
            try:
                ast.parse(source, filename=file_path)
            except SyntaxError:
                reasons.append(f"source could not be parsed for {file_path}")
            for candidates, relative in _import_candidates(source, file_path):
                if any(
                    candidate in selected_files or candidate in inspected
                    for candidate in candidates
                ):
                    continue
                # Import module.py and module/__init__.py are alternatives, not
                # two required files.  Prefer a reader-confirmed option and
                # otherwise report one stable candidate.
                if relative:
                    resolved = next(
                        (
                            candidate
                            for candidate in candidates
                            if read_source(candidate) is not None
                        ),
                        None,
                    )
                    ensure_file(
                        resolved or candidates[0],
                        required=True,
                        augment=request.augment_full_files,
                    )
                elif reader is not None:
                    resolved = next(
                        (
                            candidate
                            for candidate in candidates
                            if read_source(candidate) is not None
                        ),
                        None,
                    )
                    if resolved is not None:
                        ensure_file(
                            resolved,
                            required=True,
                            augment=request.augment_full_files,
                        )

            defined, imported, references = _type_names(source, file_path)
            known = defined | imported | set(dir(builtins))
            for reference in sorted(references - known):
                reasons.append(f"missing type context {reference!r} in {file_path}")

        for warning in request.warnings:
            if not _warning_is_contextual(warning):
                continue
            match = _WARNING_IMPORT.match(warning)
            if match is not None:
                module = match.group("module")
                module = match.group("resolved") or module
                warning_path = match.group("path")
                warning_candidates = _module_candidates(module, warning_path)
                if warning_candidates and not any(
                    candidate in selected_files or candidate in inspected
                    for candidate in warning_candidates
                ):
                    resolved = next(
                        (
                            candidate
                            for candidate in warning_candidates
                            if read_source(candidate) is not None
                        ),
                        None,
                    )
                    ensure_file(
                        resolved or warning_candidates[0],
                        required=True,
                        augment=request.augment_full_files,
                    )
            reasons.append(f"context warning: {warning}")

        # A source reader can resolve a missing relative import only when the
        # caller opted into augmentation.  Reinspect newly retrieved files so
        # their own imports/types are part of the same deterministic decision.
        pending = [
            (path, source)
            for path, source in augmented.items()
            if path not in selected_files
        ]
        for file_path, source in pending:
            for candidates, relative in _import_candidates(source, file_path):
                if relative and not any(
                    candidate in inspected for candidate in candidates
                ):
                    resolved = next(
                        (
                            candidate
                            for candidate in candidates
                            if read_source(candidate) is not None
                        ),
                        candidates[0],
                    )
                    ensure_file(resolved, required=True, augment=True)

        if missing:
            reasons.append("one or more required source files are missing")
        decision: CRAGDecision = (
            "context_sufficient"
            if not missing and not reasons
            else "context_incomplete"
        )
        try:
            return CRAGEvaluationResult(
                decision=decision,
                missing_files=sorted(missing),
                reasons=list(dict.fromkeys(reasons)),
                augmented_sources=dict(sorted(augmented.items())),
            )
        except ValidationError as error:
            raise _limit_error() from error

    def evaluate_context(
        self,
        context: CRAGContextRequest | CodeGraph | Mapping[str, object],
        *,
        augment_full_files: bool | None = None,
    ) -> CRAGEvaluationResult:
        """Explicit alias used by workflow integrations."""
        return self.evaluate(context, augment_full_files=augment_full_files)

    @staticmethod
    def _request(
        context: CRAGContextRequest | CodeGraph | Mapping[str, object],
        augment_full_files: bool | None,
    ) -> CRAGContextRequest:
        try:
            if isinstance(context, CRAGContextRequest):
                request = context
            elif isinstance(context, CodeGraph):
                request = CRAGContextRequest.from_graph(context)
            else:
                request = CRAGContextRequest.model_validate(context)
            if augment_full_files is not None:
                request = request.model_copy(
                    update={"augment_full_files": augment_full_files}
                )
            return request
        except (ValidationError, TypeError, ValueError) as error:
            if isinstance(error, ValidationError) and any(
                issue.get("type") == "too_long" for issue in error.errors()
            ):
                raise _limit_error() from error
            raise _input_error() from error


CRAGEvaluator = CRAGEvaluatorAgent
CRAGContext = CRAGContextRequest
CRAGRequest = CRAGContextRequest
CRAGResult = CRAGEvaluationResult


__all__ = [
    "CRAGContextRequest",
    "CRAGContext",
    "CRAGDecision",
    "CRAGEvaluationResult",
    "CRAGEvaluator",
    "CRAGEvaluatorAgent",
    "CRAGRequest",
    "CRAGResult",
    "MAX_AUGMENTED_BYTES",
    "MAX_AUGMENTED_FILES",
    "MAX_MISSING_FILES",
    "MAX_SELECTED_EDGES",
    "MAX_SELECTED_NODES",
    "MAX_SOURCE_BYTES",
    "MAX_WARNINGS",
    "SourceReader",
]
