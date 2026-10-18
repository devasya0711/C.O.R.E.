"""Bounded Python analysis using Tree-sitter, without importing target code.

Resolution is deliberately flow-insensitive: conflicting bindings, multiple
possible receiver types, and ambiguous inherited members never produce CALLS.
Constructors link only to an explicit source-defined __init__, not to invented
dataclass methods. Warnings retain source locations for CRAG fallback retrieval.
"""

from __future__ import annotations

import ast
import builtins
import io
import os
import re
import tokenize
from collections.abc import Iterator
from dataclasses import dataclass, field
from inspect import cleandoc
from pathlib import Path
from typing import Literal

import tree_sitter_python
from tree_sitter import Language, Node, Parser, Tree

from app.schemas.graph import CodeEdge, CodeGraph, CodeNode, NodeLabel, Relationship

MAX_PYTHON_FILES = 500
MAX_FILE_BYTES = 1024 * 1024
_SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "ENV",
    "node_modules",
    ".tox",
    ".nox",
    "site-packages",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "tests",
    "test",
}
_DEFINITIONS = {"class_definition", "function_definition"}
_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE"}
_BUILTINS = frozenset(dir(builtins))
_IDENTIFIER = r'(?:[A-Za-z_][\w$]*|"[^"\n]+"|`[^`\n]+`|\[[^\]\n]+\])'
_SQL_TABLE = rf"{_IDENTIFIER}(?:\s*\.\s*{_IDENTIFIER})?"
_SQL_OPERATIONS = re.compile(
    rf"\b(?P<op>INSERT\s+INTO|DELETE\s+FROM|UPDATE|FROM|JOIN)\s+(?P<table>{_SQL_TABLE})",
    re.IGNORECASE,
)


class StaticAnalysisError(ValueError):
    """Analysis failed; callers must not mark a partial graph READY."""


@dataclass(frozen=True)
class _Value:
    kind: Literal[
        "symbol",
        "module",
        "external",
        "instance",
        "container",
        "table",
        "export",
        "unknown",
    ]
    name: str = ""


_UNKNOWN = _Value("unknown")


@dataclass
class _Binding:
    scope: _Scope
    expression: Node | None = None
    annotation: Node | None = None
    value: _Value | None = None


@dataclass
class _Unit:
    path: str
    module: str
    package: str
    source: bytes
    tree: Node
    parsed_tree: Tree


@dataclass
class _Scope:
    unit: _Unit
    node: Node
    qname: str
    kind: Literal["file", "class", "function"]
    parent: _Scope | None = None
    bindings: dict[str, list[_Binding]] = field(default_factory=dict)
    fields: dict[str, list[_Binding]] = field(default_factory=dict)
    receiver: str | None = None

    def bind(self, name: str, binding: _Binding) -> None:
        self.bindings.setdefault(name, []).append(binding)


def _text(unit: _Unit, node: Node) -> str:
    return unit.source[node.start_byte : node.end_byte].decode("utf-8")


def _field(node: Node, name: str) -> Node | None:
    return node.child_by_field_name(name)


def _walk(node: Node) -> Iterator[Node]:
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.named_children))


def _owned(scope: _Scope) -> Iterator[Node]:
    """Visit this execution scope, including nested definition headers only."""
    body = scope.node if scope.kind == "file" else _field(scope.node, "body")
    if body is None:
        return
    stack = list(reversed(body.named_children))
    while stack:
        node = stack.pop()
        yield node
        children = node.named_children
        if node.type in _DEFINITIONS:
            children = [child for child in children if child != _field(node, "body")]
        stack.extend(reversed(children))


def _literal(unit: _Unit, node: Node | None) -> str | None:
    if node is None:
        return None
    if node.type == "concatenated_string":
        parts = [_literal(unit, child) for child in node.named_children]
        return (
            "".join(part for part in parts if part is not None)
            if all(part is not None for part in parts)
            else None
        )
    if node.type != "string" or any(n.type == "interpolation" for n in _walk(node)):
        return None
    try:
        value: object = ast.literal_eval(_text(unit, node))
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, str) else None


def _docstring(unit: _Unit, node: Node) -> str:
    body = node if node.type == "module" else _field(node, "body")
    if body is not None:
        statements = [child for child in body.named_children if child.type != "comment"]
        if statements and statements[0].type == "expression_statement":
            value = _literal(unit, statements[0].named_children[0])
            if value is not None:
                return cleandoc(value)
    return ""


def _one(values: set[_Value]) -> _Value:
    return next(iter(values)) if len(values) == 1 else _UNKNOWN


class StaticAnalyzerAgent:
    """Extract a deterministic, repository-relative graph from Python files."""

    def analyze(self, root: Path) -> CodeGraph:
        """Analyze all eligible files or raise StaticAnalysisError atomically.

        Limits are inclusive: 500 Python files, 1 MiB of raw bytes per file.
        Tests, environments, dependency directories and symlinks are excluded.
        No imports, decorators, SQL, or other target expressions are executed.
        """
        return _Analysis(root).run()


class _Analysis:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.parser = Parser(Language(tree_sitter_python.language()))
        self.nodes: dict[str, CodeNode] = {}
        self.edges: set[CodeEdge] = set()
        self.warnings: set[str] = set()
        self.scopes: dict[str, _Scope] = {}
        self.modules: dict[str, _Scope] = {}
        self.namespaces: set[str] = {""}
        self.class_tables: dict[str, str] = {}
        self.table_calls: dict[int, str] = {}
        self.imports: list[tuple[_Scope, Node, str, _Value]] = []

    def run(self) -> CodeGraph:
        try:
            units = self._parse_files()
            for unit in units:
                self._collect(unit)
            for scope in list(self.scopes.values()):
                self._bindings(scope)
            self._tables()
            for scope, node, name, value in self.imports:
                resolved = self._expand(value, frozenset())
                if resolved == _UNKNOWN or resolved.kind == "external":
                    if not value.name.startswith("__future__."):
                        self._warn(
                            scope, node, f"unresolved import {name!r} ({value.name})"
                        )
            for scope in list(self.scopes.values()):
                self._relationships(scope)
        except (OSError, UnicodeError, SyntaxError, RecursionError) as exc:
            raise StaticAnalysisError(f"AST_ANALYSIS_ERROR: {exc}") from exc
        return CodeGraph(
            nodes=sorted(self.nodes.values(), key=lambda node: node.qualified_name),
            edges=sorted(
                self.edges,
                key=lambda edge: (
                    edge.source,
                    edge.target,
                    edge.relationship,
                    edge.line or 0,
                ),
            ),
            warnings=sorted(self.warnings),
        )

    def _parse_files(self) -> list[_Unit]:
        if self.root.is_symlink() or not self.root.is_dir():
            raise StaticAnalysisError(
                "AST_INPUT_ERROR: root must be a non-symlink directory"
            )
        paths: list[Path] = []

        def walk_error(error: OSError) -> None:
            raise error

        for directory, dirs, files in os.walk(
            self.root, followlinks=False, onerror=walk_error
        ):
            dirs[:] = sorted(
                name
                for name in dirs
                if name not in _SKIP_DIRS and not (Path(directory) / name).is_symlink()
            )
            for name in sorted(files):
                path = Path(directory) / name
                if (
                    path.suffix == ".py"
                    and not path.is_symlink()
                    and not name.startswith("test_")
                    and not name.endswith("_test.py")
                ):
                    paths.append(path)
                    if len(paths) > MAX_PYTHON_FILES:
                        raise StaticAnalysisError(
                            f"AST_LIMIT_ERROR: more than {MAX_PYTHON_FILES} "
                            "Python files"
                        )
        units: list[_Unit] = []
        for path in sorted(paths):
            relative = path.relative_to(self.root).as_posix()
            # O_NOFOLLOW also closes the file-symlink race after directory traversal.
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
            if len(raw) > MAX_FILE_BYTES:
                raise StaticAnalysisError(
                    f"AST_LIMIT_ERROR: {relative} exceeds {MAX_FILE_BYTES} bytes"
                )
            encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
            source = raw.decode(encoding).encode("utf-8")
            parsed_tree = self.parser.parse(source)
            tree = parsed_tree.root_node
            if tree.has_error:
                error = next(
                    (node for node in _walk(tree) if node.is_error or node.is_missing),
                    tree,
                )
                raise StaticAnalysisError(
                    f"AST_PARSE_ERROR: {relative}:{error.start_point.row + 1}:"
                    f"{error.start_point.column + 1} ({error.type})"
                )
            parts = list(path.relative_to(self.root).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
                package = ".".join(parts)
            else:
                package = ".".join(parts[:-1])
            units.append(
                _Unit(relative, ".".join(parts), package, source, tree, parsed_tree)
            )
        return units

    def _add_node(
        self,
        scope: _Scope,
        node: Node,
        label: NodeLabel,
        qname: str,
        name: str,
        *,
        source: Node | None = None,
    ) -> None:
        extent = source or node
        self.nodes.setdefault(
            qname,
            CodeNode(
                label=label,
                qualified_name=qname,
                name=name,
                file_path=scope.unit.path,
                start_line=extent.start_point.row + 1,
                end_line=max(
                    extent.start_point.row + 1,
                    extent.end_point.row + (extent.end_point.column > 0),
                ),
                docstring=_docstring(scope.unit, node),
                source_code=_text(scope.unit, extent),
            ),
        )

    def _edge(
        self, source: str, target: str, relation: Relationship, line: int | None = None
    ) -> None:
        if relation == "CALLS" and self.nodes[source].label != "Function":
            # DATABASE.md permits Function -> Function CALLS only. Preserve
            # definition-time/module-initialization dependencies as diagnostics,
            # not as an invented File/Class CALLS relationship.
            node = self.nodes[source]
            self.warnings.add(
                f"{node.file_path}:{line or node.start_line}: "
                f"module/class initialization calls {target[:512]!r}; "
                "retrieve full source"
            )
            return
        self.edges.add(
            CodeEdge(source=source, target=target, relationship=relation, line=line)
        )

    def _warn(self, scope: _Scope, node: Node, message: str) -> None:
        self.warnings.add(
            f"{scope.unit.path}:{node.start_point.row + 1}: {message[:512]}"
        )

    def _collect(self, unit: _Unit) -> None:
        if unit.module in self.modules:
            raise StaticAnalysisError(
                f"AST_SYMBOL_ERROR: ambiguous module {unit.module!r}"
            )
        root = _Scope(unit, unit.tree, f"file:{unit.path}", "file")
        self.modules[unit.module] = root
        self.scopes[root.qname] = root
        for length in range(1, len(unit.module.split(".")) + 1):
            self.namespaces.add(".".join(unit.module.split(".")[:length]))
        self._add_node(root, unit.tree, "File", root.qname, unit.path)
        stack = [(child, root) for child in reversed(unit.tree.named_children)]
        while stack:
            node, parent = stack.pop()
            current = parent
            if node.type in _DEFINITIONS:
                name_node = _field(node, "name")
                assert name_node is not None
                name = _text(unit, name_node)
                prefix = unit.module if parent.kind == "file" else parent.qname
                qname = f"{prefix}.{name}" if prefix else name
                if qname in self.scopes:
                    self._warn(parent, node, f"ambiguous definition {qname!r}")
                    qname += f"@{node.start_point.row + 1}"
                kind: Literal["class", "function"] = (
                    "class" if node.type == "class_definition" else "function"
                )
                current = _Scope(unit, node, qname, kind, parent)
                self.scopes[qname] = current
                parent.bind(name, _Binding(parent, value=_Value("symbol", qname)))
                extent = (
                    node.parent
                    if node.parent and node.parent.type == "decorated_definition"
                    else node
                )
                self._add_node(
                    current,
                    node,
                    "Class" if kind == "class" else "Function",
                    qname,
                    name,
                    source=extent,
                )
                container = parent.qname
                if parent.kind == "function" or (
                    parent.kind == "class" and kind == "class"
                ):
                    container = f"file:{unit.path}"
                self._edge(container, qname, "CONTAINS")
            stack.extend((child, current) for child in reversed(node.named_children))

    def _bindings(self, scope: _Scope) -> None:
        if scope.kind == "function":
            self._parameters(scope)
        for node in _owned(scope):
            if node.type in {"import_statement", "import_from_statement"}:
                self._import(scope, node)
            elif node.type == "assignment":
                left = _field(node, "left")
                if left is None:
                    continue
                binding = _Binding(scope, _field(node, "right"), _field(node, "type"))
                if left.type == "identifier":
                    name = _text(scope.unit, left)
                    scope.bind(name, binding)
                    if scope.kind == "class":
                        scope.fields.setdefault(name, []).append(binding)
                elif left.type == "attribute" and scope.receiver:
                    obj, attr = _field(left, "object"), _field(left, "attribute")
                    if (
                        obj is not None
                        and attr is not None
                        and _text(scope.unit, obj) == scope.receiver
                    ):
                        assert scope.parent is not None
                        scope.parent.fields.setdefault(
                            _text(scope.unit, attr), []
                        ).append(binding)
                elif left.type in {"pattern_list", "tuple_pattern", "list_pattern"}:
                    self._unknown_targets(scope, left)
            elif node.type == "for_statement":
                left, right = _field(node, "left"), _field(node, "right")
                if left is not None and left.type == "identifier" and right is not None:
                    # The binding expression is evaluated as an iterable's element.
                    scope.bind(
                        _text(scope.unit, left), _Binding(scope, expression=node)
                    )
            elif node.type in {"augmented_assignment", "named_expression"}:
                left = _field(node, "left") or _field(node, "name")
                if left is not None and left.type == "identifier":
                    scope.bind(_text(scope.unit, left), _Binding(scope))
            elif node.type in {"for_in_clause", "as_pattern"}:
                target = _field(node, "left") or _field(node, "alias")
                if target is not None:
                    self._unknown_targets(scope, target)
            elif node.type == "delete_statement":
                for target in node.named_children:
                    if target.type == "identifier":
                        scope.bind(_text(scope.unit, target), _Binding(scope))

    def _unknown_targets(self, scope: _Scope, node: Node) -> None:
        for child in _walk(node):
            if child.type == "identifier":
                scope.bind(_text(scope.unit, child), _Binding(scope))

    def _parameters(self, scope: _Scope) -> None:
        params = _field(scope.node, "parameters")
        if params is None:
            return
        parameters = [
            child
            for child in params.named_children
            if child.type not in {"positional_separator", "keyword_separator"}
        ]
        decorators = self._decorators(scope)
        static = any(
            _text(scope.unit, node).lstrip("@") == "staticmethod" for node in decorators
        )
        class_method = any(
            _text(scope.unit, node).lstrip("@") == "classmethod" for node in decorators
        )
        for index, param in enumerate(parameters):
            name_node = _field(param, "name")
            if param.type == "identifier":
                name_node = param
            elif name_node is None:
                name_node = next(
                    (n for n in param.named_children if n.type == "identifier"), None
                )
            if name_node is None:
                continue
            name = _text(scope.unit, name_node)
            value = None
            if (
                index == 0
                and scope.parent
                and scope.parent.kind == "class"
                and not static
            ):
                scope.receiver = name
                value = _Value(
                    "symbol" if class_method else "instance", scope.parent.qname
                )
            scope.bind(
                name, _Binding(scope, annotation=_field(param, "type"), value=value)
            )

    def _import(self, scope: _Scope, node: Node) -> None:
        module_node = _field(node, "module_name")
        module = _text(scope.unit, module_node) if module_node else ""
        if module.startswith("."):
            dots = len(module) - len(module.lstrip("."))
            parts = scope.unit.package.split(".") if scope.unit.package else []
            if dots - 1 > len(parts):
                self._warn(
                    scope, node, f"unresolved import {module!r}: above analysis root"
                )
                module = "<outside-root>"
            else:
                module = ".".join(
                    parts[: len(parts) - dots + 1] + [module[dots:]]
                ).strip(".")
        for child in node.named_children:
            if child == module_node:
                continue
            if child.type == "wildcard_import":
                self._warn(
                    scope, node, f"unresolved import {module}.*: wildcard binding"
                )
                continue
            alias_node = _field(child, "alias")
            imported_node = (
                _field(child, "name") if child.type == "aliased_import" else child
            )
            if imported_node is None:
                continue
            imported = _text(scope.unit, imported_node)
            path = f"{module}.{imported}".lstrip(".") if module_node else imported
            name = (
                _text(scope.unit, alias_node) if alias_node else imported.split(".")[0]
            )
            if module_node:
                name = _text(scope.unit, alias_node) if alias_node else imported
                local = module in self.namespaces or path in self.namespaces
                value = _Value("export" if local else "external", path)
            else:
                target = path if alias_node else imported.split(".")[0]
                value = _Value(
                    "module" if target in self.namespaces else "external", target
                )
            scope.bind(name, _Binding(scope, value=value))
            self.imports.append((scope, node, name, value))

    def _lookup(self, scope: _Scope, name: str, seen: frozenset[str]) -> _Value:
        key = f"{scope.qname}:{name}"
        if key in seen or len(seen) > 48:
            return _UNKNOWN
        if name in scope.bindings:
            return _one(
                {
                    self._binding(binding, seen | {key})
                    for binding in scope.bindings[name]
                }
            )
        parent = scope.parent
        # Class namespaces are not lexical closures for methods or nested classes.
        while parent and parent.kind == "class":
            parent = parent.parent
        if parent:
            return self._lookup(parent, name, seen | {key})
        return _Value("external", f"builtins.{name}") if name in _BUILTINS else _UNKNOWN

    def _binding(self, binding: _Binding, seen: frozenset[str]) -> _Value:
        if binding.value is not None:
            return self._expand(binding.value, seen)
        if binding.annotation is not None:
            return self._annotation(binding.scope, binding.annotation, seen)
        return self._expr(binding.scope, binding.expression, seen)

    def _expand(self, value: _Value, seen: frozenset[str]) -> _Value:
        if value.kind != "export":
            return value
        if value.name in self.namespaces:
            return _Value("module", value.name)
        module, _, name = value.name.rpartition(".")
        scope = self.modules.get(module)
        return self._lookup(scope, name, seen) if scope else _UNKNOWN

    def _annotation(self, scope: _Scope, node: Node, seen: frozenset[str]) -> _Value:
        if len(seen) > 48:
            return _UNKNOWN
        if node.type == "type" and node.named_children:
            return self._annotation(scope, node.named_children[0], seen)
        if node.type == "string":
            text = _literal(scope.unit, node)
            if text is None:
                return _UNKNOWN
            source = f"x: {text}".encode()
            parsed_tree = self.parser.parse(source)
            parsed = parsed_tree.root_node
            if parsed.has_error:
                return _UNKNOWN
            annotation = next(
                (n for n in _walk(parsed) if n.type == "assignment"), None
            )
            if annotation is None or _field(annotation, "type") is None:
                return _UNKNOWN
            unit = _Unit(
                scope.unit.path,
                scope.unit.module,
                scope.unit.package,
                source,
                parsed,
                parsed_tree,
            )
            temporary = _Scope(
                unit,
                scope.node,
                scope.qname,
                scope.kind,
                scope.parent,
                scope.bindings,
                scope.fields,
                scope.receiver,
            )
            child = _field(annotation, "type")
            assert child is not None
            return self._annotation(temporary, child, seen | {f"annotation:{text}"})
        if node.type == "binary_operator":
            values = {
                self._annotation(scope, child, seen)
                for child in node.named_children
                if child.type != "none" and _text(scope.unit, child) != "None"
            }
            return _one(values)
        if node.type in {"generic_type", "subscript"}:
            children = node.named_children
            if not children:
                return _UNKNOWN
            base = _text(scope.unit, children[0]).split(".")[-1]
            args = (
                children[1].named_children
                if node.type == "generic_type"
                else children[1:]
            )
            if base in {"Optional", "ClassVar", "Mapped", "Annotated"} and args:
                return self._annotation(scope, args[0], seen)
            if base == "Union":
                return _one(
                    {
                        self._annotation(scope, child, seen)
                        for child in args
                        if _text(scope.unit, child) not in {"None", "NoneType"}
                    }
                )
            if base in {"dict", "Dict", "Mapping", "MutableMapping"} and len(args) == 2:
                value = self._annotation(scope, args[1], seen)
            elif (
                base
                in {
                    "list",
                    "List",
                    "tuple",
                    "Tuple",
                    "set",
                    "Set",
                    "Sequence",
                    "Iterable",
                    "Iterator",
                }
                and args
            ):
                value = self._annotation(scope, args[0], seen)
            else:
                return _UNKNOWN
            return (
                _Value("container", value.name)
                if value.kind == "instance"
                else _UNKNOWN
            )
        value = self._expr(scope, node, seen)
        if value.kind == "symbol" and self.scopes[value.name].kind == "class":
            return _Value("instance", value.name)
        return value

    def _member(self, class_name: str, name: str, seen: frozenset[str]) -> _Value:
        key = f"member:{class_name}:{name}"
        if key in seen or len(seen) > 48:
            return _UNKNOWN
        seen = seen | {key}
        scope = self.scopes.get(class_name)
        if scope is None or scope.kind != "class":
            return _UNKNOWN
        if name == "__table__" and class_name in self.class_tables:
            return _Value("table", self.class_tables[class_name])
        if name in scope.fields:
            return _one(
                {self._binding(binding, seen) for binding in scope.fields[name]}
            )
        if name in scope.bindings:
            return self._lookup(scope, name, seen)
        bases = _field(scope.node, "superclasses")
        if bases is None:
            return _UNKNOWN
        candidates: set[_Value] = set()
        for base in bases.named_children:
            value = self._expr(scope.parent or scope, base, seen)
            if value.kind == "symbol":
                member = self._member(value.name, name, seen)
                if member != _UNKNOWN:
                    candidates.add(member)
            else:
                # An external base could supply or override this member.
                candidates.add(_UNKNOWN)
        return _one(candidates)

    def _expr(
        self, scope: _Scope, node: Node | None, seen: frozenset[str] = frozenset()
    ) -> _Value:
        if node is None or len(seen) > 48:
            return _UNKNOWN
        seen = seen | {f"expr:{node.id}"}
        if node.type in {"identifier", "dotted_name"}:
            return self._lookup(scope, _text(scope.unit, node), seen)
        if (
            node.type in {"type", "parenthesized_expression", "await"}
            and node.named_children
        ):
            return self._expr(scope, node.named_children[0], seen)
        if node.type == "attribute":
            obj, attr = _field(node, "object"), _field(node, "attribute")
            if obj is None or attr is None:
                return _UNKNOWN
            receiver = self._expr(scope, obj, seen)
            name = _text(scope.unit, attr)
            if receiver.kind == "module":
                return self._expand(
                    _Value("export", f"{receiver.name}.{name}".lstrip(".")), seen
                )
            if receiver.kind == "external":
                return _Value("external", f"{receiver.name}.{name}")
            if receiver.kind in {"instance", "symbol"}:
                value = self._member(receiver.name, name, seen)
                if value.kind == "symbol" and self._is_property(value.name):
                    return self._return_type(value.name, seen)
                return value
            return _UNKNOWN
        if node.type == "call":
            if node.id in self.table_calls:
                return _Value("table", self.table_calls[node.id])
            func = _field(node, "function")
            value = self._expr(scope, func, seen)
            if value.kind == "symbol":
                target = self.scopes[value.name]
                return (
                    _Value("instance", value.name)
                    if target.kind == "class"
                    else self._return_type(value.name, seen)
                )
            if (
                value.kind == "external"
                and value.name.startswith("sqlalchemy.")
                and value.name.rsplit(".", 1)[-1] in {"Session", "AsyncSession"}
            ):
                return value
            if func is not None and func.type == "attribute":
                receiver = self._expr(scope, _field(func, "object"), seen)
                attr = _field(func, "attribute")
                name = _text(scope.unit, attr) if attr else ""
                if receiver.kind == "container":
                    if name in {"get", "pop", "setdefault"}:
                        return _Value("instance", receiver.name)
                    if name in {"values", "copy"}:
                        return receiver
            if value == _Value("external", "builtins.super"):
                owner = scope.parent
                if owner and owner.kind == "class":
                    bases = _field(owner.node, "superclasses")
                    if bases and len(bases.named_children) == 1:
                        base = self._expr(
                            owner.parent or owner, bases.named_children[0], seen
                        )
                        if base.kind == "symbol":
                            return _Value("instance", base.name)
            return _UNKNOWN
        if node.type in {"subscript", "for_statement"}:
            value = self._expr(
                scope,
                _field(node, "value")
                if node.type == "subscript"
                else _field(node, "right"),
                seen,
            )
            return (
                _Value("instance", value.name)
                if value.kind == "container"
                else _UNKNOWN
            )
        if node.type == "conditional_expression":
            children = node.named_children
            return _one(
                {
                    self._expr(scope, child, seen)
                    for child in (children[0], children[-1])
                }
            )
        return _UNKNOWN

    def _return_type(self, qname: str, seen: frozenset[str]) -> _Value:
        target = self.scopes[qname]
        annotation = _field(target.node, "return_type")
        return self._annotation(target, annotation, seen) if annotation else _UNKNOWN

    def _is_property(self, qname: str) -> bool:
        return any(
            _text(self.scopes[qname].unit, node) == "@property"
            for node in self._decorators(self.scopes[qname])
        )

    def _decorators(self, scope: _Scope) -> list[Node]:
        parent = scope.node.parent
        return (
            [child for child in parent.named_children if child.type == "decorator"]
            if parent and parent.type == "decorated_definition"
            else []
        )

    def _arguments(self, node: Node) -> tuple[list[Node], dict[str, Node]]:
        args = _field(node, "arguments")
        positional: list[Node] = []
        keywords: dict[str, Node] = {}
        if args is not None:
            for child in args.named_children:
                if child.type == "keyword_argument":
                    name, value = _field(child, "name"), _field(child, "value")
                    if name is not None and value is not None:
                        keywords[bytes(name.text or b"").decode()] = value
                elif child.type != "comment":
                    positional.append(child)
        return positional, keywords

    def _table(self, scope: _Scope, node: Node, name: str) -> str:
        qname = f"db:{name}"
        self._add_node(scope, node, "DBTable", qname, name)
        return qname

    def _tables(self) -> None:
        # Imports and definitions must be complete before recognizing aliases.
        for scope in self.scopes.values():
            for node in _owned(scope):
                if node.type != "assignment":
                    continue
                left, right = _field(node, "left"), _field(node, "right")
                if left is None or right is None:
                    continue
                if scope.kind == "class" and _text(scope.unit, left) == "__tablename__":
                    name = _literal(scope.unit, right)
                    if name:
                        self.class_tables[scope.qname] = self._table(scope, node, name)
                if right.type == "call":
                    function = self._expr(scope, _field(right, "function"))
                    if (
                        function.kind == "external"
                        and function.name.startswith("sqlalchemy.")
                        and function.name.endswith(".Table")
                    ):
                        args, keywords = self._arguments(right)
                        name = (
                            _literal(scope.unit, args[0])
                            if args
                            else _literal(scope.unit, keywords.get("name"))
                        )
                        schema = _literal(scope.unit, keywords.get("schema"))
                        if name and ("schema" not in keywords or schema is not None):
                            table = self._table(
                                scope, node, f"{schema}.{name}" if schema else name
                            )
                            self.table_calls[right.id] = table
                            if (
                                scope.kind == "class"
                                and _text(scope.unit, left) == "__table__"
                            ):
                                self.class_tables[scope.qname] = table
                        else:
                            self._warn(
                                scope,
                                node,
                                "unresolved dynamic SQLAlchemy table name/schema",
                            )

    def _relationships(self, scope: _Scope) -> None:
        if scope.kind == "class":
            bases = _field(scope.node, "superclasses")
            if bases is not None:
                for base in bases.named_children:
                    if base.type == "keyword_argument":
                        continue
                    value = self._expr(scope.parent or scope, base)
                    if (
                        value.kind == "symbol"
                        and self.scopes[value.name].kind == "class"
                    ):
                        self._edge(scope.qname, value.name, "INHERITS")
                    else:
                        self._warn(
                            scope,
                            base,
                            f"unresolved base class {_text(scope.unit, base)!r}",
                        )
        if scope.kind == "function":
            self._routes(scope)
        for node in _owned(scope):
            if node.type == "call":
                self._call(scope, node)
                if scope.kind == "function":
                    self._data_call(scope, node)
            elif node.type == "decorator" and node.named_children:
                expression = node.named_children[0]
                if expression.type != "call":
                    value = self._expr(scope, expression)
                    if (
                        value.kind == "symbol"
                        and self.scopes[value.name].kind == "function"
                    ):
                        self._edge(
                            scope.qname, value.name, "CALLS", node.start_point.row + 1
                        )
            elif scope.kind == "function" and node.type in {"attribute", "subscript"}:
                obj = (
                    _field(node, "object")
                    if node.type == "attribute"
                    else _field(node, "value")
                )
                value = self._expr(scope, obj)
                tables = self._value_tables(value)
                # Typed in-memory collections also expose their element schema.
                if node.type == "attribute":
                    result = self._expr(scope, node)
                    if result.kind == "container":
                        tables |= self._value_tables(result)
                relation: Relationship = (
                    "WRITES_TO" if self._written(node) else "READS_FROM"
                )
                for table in tables:
                    self._edge(scope.qname, table, relation)
                    if relation == "WRITES_TO" and self._augmented(node):
                        self._edge(scope.qname, table, "READS_FROM")

    def _call(self, scope: _Scope, node: Node) -> None:
        func = _field(node, "function")
        value = self._expr(scope, func)
        if value.kind == "symbol":
            target = self.scopes[value.name]
            if target.kind == "class":
                initializer = self._member(value.name, "__init__", frozenset())
                if initializer.kind == "symbol":
                    self._edge(
                        scope.qname, initializer.name, "CALLS", node.start_point.row + 1
                    )
            elif not self._is_property(value.name):
                self._edge(scope.qname, value.name, "CALLS", node.start_point.row + 1)
            return
        if value.kind == "external" and value.name.startswith("builtins."):
            return
        if func is not None and func.type == "attribute":
            receiver = self._expr(scope, _field(func, "object"))
            if receiver.kind == "container":
                return
        if func is not None:
            self._warn(scope, node, f"unresolved call {_text(scope.unit, func)!r}")

    def _routes(self, scope: _Scope) -> None:
        for decorator in self._decorators(scope):
            call = next(
                (child for child in decorator.named_children if child.type == "call"),
                None,
            )
            if call is None:
                continue
            func = _field(call, "function")
            if func is None or func.type != "attribute":
                continue
            method_node = _field(func, "attribute")
            method = _text(scope.unit, method_node).upper() if method_node else ""
            args, kwargs = self._arguments(call)
            path = (
                _literal(scope.unit, args[0])
                if args
                else _literal(scope.unit, kwargs.get("path") or kwargs.get("rule"))
            )
            methods: set[str] = set()
            if method in _HTTP_METHODS:
                methods.add(method)
            elif method in {"ROUTE", "API_ROUTE"}:
                configured = kwargs.get("methods")
                if configured is None:
                    methods.add("GET")
                elif configured.type in {"list", "tuple", "set"}:
                    values = [
                        _literal(scope.unit, child)
                        for child in configured.named_children
                    ]
                    if all(
                        value is not None and value.upper() in _HTTP_METHODS
                        for value in values
                    ):
                        methods.update(
                            value.upper() for value in values if value is not None
                        )
            else:
                continue
            if path is None or not methods:
                self._warn(scope, decorator, "unresolved dynamic route path/methods")
                continue
            for http_method in sorted(methods):
                qname = f"api:{http_method}:{scope.qname}:{path}"
                self._add_node(
                    scope,
                    scope.node,
                    "APIEndpoint",
                    qname,
                    f"{http_method} {path}",
                    source=decorator,
                )
                self._edge(qname, scope.qname, "ROUTES_TO")

    def _value_tables(self, value: _Value) -> set[str]:
        if value.kind == "table":
            return {value.name}
        if value.kind in {"symbol", "instance", "container"}:
            table = self.class_tables.get(value.name)
            return {table} if table else set()
        return set()

    def _expression_tables(self, scope: _Scope, node: Node) -> set[str]:
        tables: set[str] = set()
        for child in _walk(node):
            tables |= self._value_tables(self._expr(scope, child))
        return tables

    def _query_tables(
        self, scope: _Scope, node: Node | None, seen: frozenset[str] = frozenset()
    ) -> set[str]:
        """Only follow a syntactically established SQLAlchemy query chain."""
        if node is None or len(seen) > 48:
            return set()
        if node.type == "identifier":
            name = _text(scope.unit, node)
            key = f"query:{scope.qname}:{name}"
            if key in seen:
                return set()
            if name in scope.bindings:
                candidates = [
                    self._query_tables(binding.scope, binding.expression, seen | {key})
                    for binding in scope.bindings[name]
                ]
                if candidates and all(value == candidates[0] for value in candidates):
                    return candidates[0]
                return set()
            parent = scope.parent
            while parent and parent.kind == "class":
                parent = parent.parent
            return self._query_tables(parent, node, seen | {key}) if parent else set()
        if node.type != "call":
            return set()
        func = _field(node, "function")
        value = self._expr(scope, func)
        args, _ = self._arguments(node)
        if value.kind == "symbol":
            # A source-defined method named query/select is not SQLAlchemy.
            return set()
        if (
            value.kind == "external"
            and value.name.startswith("sqlalchemy.")
            and value.name.rsplit(".", 1)[-1] in {"select", "query"}
        ):
            return set().union(*(self._expression_tables(scope, arg) for arg in args))
        if func is not None and func.type == "attribute":
            attr = _field(func, "attribute")
            name = _text(scope.unit, attr) if attr else ""
            receiver = _field(func, "object")
            if name == "query":
                return set().union(
                    *(self._expression_tables(scope, arg) for arg in args)
                )
            if name == "select":
                return self._value_tables(self._expr(scope, receiver))
            if name in {
                "filter",
                "filter_by",
                "where",
                "join",
                "outerjoin",
                "select_from",
                "order_by",
                "limit",
                "offset",
                "options",
                "with_for_update",
            }:
                tables = self._query_tables(scope, receiver, seen)
                if tables:
                    for arg in args:
                        tables |= self._expression_tables(scope, arg)
                return tables
        return set()

    def _data_call(self, scope: _Scope, node: Node) -> None:
        func = _field(node, "function")
        if func is None:
            return
        value = self._expr(scope, func)
        args, _ = self._arguments(node)
        reads = self._query_tables(scope, node)
        writes: set[str] = set()
        name = value.name.rsplit(".", 1)[-1] if value.kind == "external" else ""
        if (
            value.kind == "external"
            and value.name.startswith("sqlalchemy.")
            and name in {"insert", "update", "delete"}
            and args
        ):
            writes |= self._expression_tables(scope, args[0])
        if func.type == "attribute":
            receiver_node = _field(func, "object")
            receiver = self._expr(scope, receiver_node)
            attr = _field(func, "attribute")
            name = _text(scope.unit, attr) if attr else ""
            if name in {"insert", "update", "delete"}:
                writes |= (
                    self._value_tables(receiver)
                    if receiver.kind == "table"
                    else self._query_tables(scope, receiver_node)
                )
            if (
                receiver.kind == "external"
                and receiver.name.startswith("sqlalchemy.")
                and receiver.name.rsplit(".", 1)[-1] in {"Session", "AsyncSession"}
                and name in {"add", "add_all", "merge", "delete", "bulk_save_objects"}
            ):
                for arg in args:
                    writes |= self._expression_tables(scope, arg)
            if name in {"execute", "executemany", "exec_driver_sql"} and args:
                sql_node = args[0]
                if sql_node.type == "call":
                    wrapper = self._expr(scope, _field(sql_node, "function"))
                    wrapped_args, _ = self._arguments(sql_node)
                    if (
                        wrapper.kind == "external"
                        and wrapper.name.startswith("sqlalchemy.")
                        and wrapper.name.endswith(".text")
                        and wrapped_args
                    ):
                        sql_node = wrapped_args[0]
                sql = _literal(scope.unit, sql_node)
                if sql is not None:
                    self._sql(scope, sql_node, sql)
                elif sql_node.type in {
                    "string",
                    "binary_operator",
                    "call",
                } and not self._query_tables(scope, sql_node):
                    self._warn(
                        scope, sql_node, "unresolved SQL/statement passed to execute"
                    )
        for table in reads:
            self._edge(scope.qname, table, "READS_FROM")
        for table in writes:
            self._edge(scope.qname, table, "WRITES_TO")

    def _sql(self, scope: _Scope, node: Node, sql: str) -> None:
        # Discard comments and string values before matching table identifiers.
        sql = re.sub(r"--[^\n]*|/\*[\s\S]*?\*/|'(?:''|[^'])*'", " ", sql)
        if re.search(r"\bWITH\b", sql, re.IGNORECASE):
            # A CTE name is not a persistent table. Defer complex/dialect-specific
            # WITH clauses instead of manufacturing database nodes for aliases.
            self._warn(
                scope, node, "unresolved SQL WITH clause; retrieve full statement"
            )
            return
        for match in _SQL_OPERATIONS.finditer(sql):
            operation = match.group("op").upper()
            tail = sql[match.end() :].lstrip()
            if operation in {"FROM", "JOIN"} and tail.startswith("("):
                self._warn(scope, node, "unresolved SQL table-valued expression")
                continue
            name = ".".join(
                part.strip().strip('"`[]') for part in match.group("table").split(".")
            )
            if name.upper() in {"SET", "SELECT"}:
                continue
            table = f"db:{name}"
            if table not in self.nodes:
                self._table(scope, node, name)
            self._edge(
                scope.qname,
                table,
                "READS_FROM" if operation in {"FROM", "JOIN"} else "WRITES_TO",
            )

    def _written(self, node: Node) -> bool:
        current = node
        while current.parent and current.parent.type in {
            "attribute",
            "subscript",
            "pattern_list",
            "tuple_pattern",
            "list_pattern",
        }:
            current = current.parent
        parent = current.parent
        return bool(
            parent
            and parent.type
            in {"assignment", "augmented_assignment", "delete_statement"}
            and (parent.type == "delete_statement" or _field(parent, "left") == current)
        )

    def _augmented(self, node: Node) -> bool:
        current = node
        while current.parent and current.parent.type in {"attribute", "subscript"}:
            current = current.parent
        return bool(current.parent and current.parent.type == "augmented_assignment")
