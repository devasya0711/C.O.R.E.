"""Source-driven regression and adversarial checks for Python graph extraction."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from textwrap import dedent

import pytest
from app.agents.static_analyzer import (
    MAX_FILE_BYTES,
    MAX_PYTHON_FILES,
    StaticAnalysisError,
    StaticAnalyzerAgent,
)
from app.schemas.graph import CodeGraph

SAMPLE = Path(__file__).resolve().parents[2] / "sample_monolith"


def write_sources(root: Path, sources: dict[str, str]) -> None:
    for relative, source in sources.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(source).lstrip("\n"), encoding="utf-8")


def links(graph: CodeGraph, relation: str) -> set[tuple[str, str]]:
    return {
        (edge.source, edge.target)
        for edge in graph.edges
        if edge.relationship == relation
    }


def test_sample_exact_graph_regression() -> None:
    agent = StaticAnalyzerAgent()
    graph = agent.analyze(SAMPLE)
    assert len(graph.nodes) == 73
    assert Counter(node.label for node in graph.nodes) == {
        "File": 6,
        "Class": 19,
        "Function": 41,
        "APIEndpoint": 1,
        "DBTable": 6,
    }
    calls = [edge for edge in graph.edges if edge.relationship == "CALLS"]
    assert len(calls) == 39
    assert len(graph.edges) == 132
    assert ("orders.place_order", "payments.PaymentProcessor.execute") in links(
        graph, "CALLS"
    )
    assert (
        "payments.PaymentProcessor.execute",
        "payments.PaymentProcessor._authorize",
    ) in links(graph, "CALLS")
    assert (
        "payments.PaymentProcessor._authorize",
        "payments.PaymentProcessor._minimum_charge",
    ) in links(graph, "CALLS")
    payment_call = next(
        edge
        for edge in calls
        if edge.source == "orders.place_order"
        and edge.target == "payments.PaymentProcessor.execute"
    )
    assert payment_call.line == 98
    assert links(graph, "ROUTES_TO") == {
        ("api:POST:routes.checkout_endpoint:/checkout", "routes.checkout_endpoint"),
    }
    assert {node.name for node in graph.nodes if node.label == "DBTable"} == {
        "users",
        "products",
        "inventory",
        "orders",
        "order_items",
        "ledger_entries",
    }
    assert ("models.InMemoryDatabase.get_user", "db:users") in links(
        graph, "READS_FROM"
    )
    assert ("models.InMemoryDatabase.save_order", "db:orders") in links(
        graph, "WRITES_TO"
    )
    assert ("models.record_payment", "models.InMemoryDatabase.save_payment") in links(
        graph, "CALLS"
    )
    assert ("inventory.reserve_items", "db:products") in links(graph, "WRITES_TO")
    assert all(
        not node.qualified_name.startswith("sample_monolith.") for node in graph.nodes
    )
    assert len(graph.edges) == len(set(graph.edges))
    assert all(edge.line is not None for edge in calls)
    assert graph.model_dump() == agent.analyze(SAMPLE).model_dump()


def test_imports_reexports_inheritance_receivers_and_decorators(tmp_path: Path) -> None:
    write_sources(
        tmp_path,
        {
            "pkg/__init__.py": "from .base import Base as Exported\n",
            "pkg/base.py": """
            def decorate(function):
                return function

            class Base:
                def __init__(self):
                    pass

                def run(self):
                    return self.helper()

                @staticmethod
                def helper():
                    return 1

                @classmethod
                def build(cls):
                    return cls.helper()
        """,
            "pkg/child.py": """
            from . import Exported
            from .base import decorate as wrapped
            import pkg.base as module

            class Child(Exported):
                @wrapped
                def run(self):
                    return super().run()

                class Nested(module.Base):
                    def work(self):
                        return self.helper()
        """,
            "pkg/use.py": """
            from .child import Child as Worker
            from . import base as api

            def use(worker: Worker, other: "api.Base"):
                worker.run()
                other.run()
                api.Base.build()
                Worker().run()
                local = Worker()
                local.run()
        """,
            "pkg/sub/entry.py": """
            from ..child import Child
            def entry(value: Child | None):
                return value.run()
        """,
        },
    )
    graph = StaticAnalyzerAgent().analyze(tmp_path)
    calls = links(graph, "CALLS")
    assert {
        ("pkg.base.Base.run", "pkg.base.Base.helper"),
        ("pkg.base.Base.build", "pkg.base.Base.helper"),
        ("pkg.child.Child.run", "pkg.base.Base.run"),
        ("pkg.child.Child.Nested.work", "pkg.base.Base.helper"),
        ("pkg.use.use", "pkg.child.Child.run"),
        ("pkg.use.use", "pkg.base.Base.run"),
        ("pkg.use.use", "pkg.base.Base.build"),
        ("pkg.use.use", "pkg.base.Base.__init__"),
        ("pkg.sub.entry.entry", "pkg.child.Child.run"),
    } <= calls
    assert links(graph, "INHERITS") == {
        ("pkg.child.Child", "pkg.base.Base"),
        ("pkg.child.Child.Nested", "pkg.base.Base"),
    }
    assert ("pkg.child.Child", "pkg.base.decorate") not in calls
    assert any(
        "initialization calls 'pkg.base.decorate'" in warning
        for warning in graph.warnings
    )
    assert not any("unresolved import" in warning for warning in graph.warnings)


def test_nested_definitions_source_docstrings_and_routes(tmp_path: Path) -> None:
    source = '''
        """Module documentation."""
        from fastapi import APIRouter
        router = APIRouter()

        @router.get("/one")
        @router.api_route(path="/many", methods=["post", "GET"])
        async def endpoint():
            """Handle a route.

            More detail.
            """
            def nested():
                """Nested documentation."""
                class Local:
                    def method(self):
                        return 1
                return Local().method()
            nested(); nested()
    '''
    write_sources(tmp_path, {"routes.py": source})
    graph = StaticAnalyzerAgent().analyze(tmp_path)
    nodes = {node.qualified_name: node for node in graph.nodes}
    assert nodes["file:routes.py"].docstring == "Module documentation."
    endpoint = nodes["routes.endpoint"]
    assert endpoint.start_line == 5
    assert endpoint.end_line == 18
    assert endpoint.docstring == "Handle a route.\n\nMore detail."
    assert endpoint.source_code.startswith('@router.get("/one")')
    assert "async def endpoint" in endpoint.source_code
    assert ("routes.endpoint.nested", "routes.endpoint.nested.Local.method") in links(
        graph, "CALLS"
    )
    assert ("file:routes.py", "routes.endpoint.nested") in links(graph, "CONTAINS")
    assert ("file:routes.py", "routes.endpoint.nested.Local") in links(
        graph, "CONTAINS"
    )
    assert (
        len(
            [
                edge
                for edge in graph.edges
                if edge.source == "routes.endpoint" and edge.relationship == "CALLS"
            ]
        )
        == 1
    )
    assert links(graph, "ROUTES_TO") == {
        ("api:GET:routes.endpoint:/one", "routes.endpoint"),
        ("api:GET:routes.endpoint:/many", "routes.endpoint"),
        ("api:POST:routes.endpoint:/many", "routes.endpoint"),
    }


def test_typed_fields_return_values_and_static_parameter_shadowing(
    tmp_path: Path,
) -> None:
    write_sources(
        tmp_path,
        {
            "receivers.py": """
                class Worker:
                    def run(self): pass

                def worker_factory() -> Worker:
                    return Worker()

                class Service:
                    def __init__(self, worker: Worker):
                        self.worker = worker

                    @property
                    def selected(self) -> Worker:
                        return self.worker

                    def execute(self):
                        self.worker.run()
                        self.selected.run()
                        worker_factory().run()

                    @staticmethod
                    def unknown(self):
                        self.execute()
            """,
        },
    )
    graph = StaticAnalyzerAgent().analyze(tmp_path)
    calls = links(graph, "CALLS")
    assert ("receivers.Service.execute", "receivers.Worker.run") in calls
    assert ("receivers.Service.execute", "receivers.worker_factory") in calls
    assert not any(source == "receivers.Service.unknown" for source, _ in calls)
    assert not any(target.endswith(".__init__") for _, target in calls)
    worker_calls = [
        edge
        for edge in graph.edges
        if edge.target == "receivers.Worker.run" and edge.relationship == "CALLS"
    ]
    assert len(worker_calls) == 3


def test_real_sqlalchemy_tables_crud_queries_and_literal_sql(tmp_path: Path) -> None:
    write_sources(
        tmp_path,
        {
            "schema.py": """
            import sqlalchemy as sa
            from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
            class Base(DeclarativeBase):
                pass
            class Account(Base):
                __tablename__ = "accounts"
                id: Mapped[int] = mapped_column(primary_key=True)
                balance: Mapped[int]
            events = sa.Table("events", sa.MetaData(), sa.Column("id", sa.Integer))
            audit = sa.Table("audit", sa.MetaData(), schema="ops")
        """,
            "storage.py": """
            from sqlalchemy import select as read, insert, update, delete, text
            from sqlalchemy.orm import Session
            from schema import Account as Customer, events, audit

            def reads(session: Session):
                session.execute(read(Customer).where(Customer.id == 1))
                session.query(Customer).filter(Customer.id == 2).all()
                session.execute(events.select())
                session.execute(read(audit))

            def writes(session: Session, customer: Customer):
                session.execute(insert(events).values(id=1))
                session.execute(update(Customer).values(balance=4))
                session.execute(delete(audit))
                session.execute(events.insert())
                session.query(Customer).filter(Customer.id == 1).update({"balance": 3})
                session.query(Customer).delete()
                session.add(customer)

            def sql(connection):
                connection.execute(text(
                    'SELECT a.id FROM "accounts" a JOIN events e ON a.id=e.id'
                ))
                connection.execute("INSERT INTO ops.audit (id) VALUES (1)")
                connection.execute("UPDATE accounts SET balance=1")
                connection.execute("DELETE FROM events WHERE id=1")
                connection.execute("SELECT 'FROM imaginary' FROM accounts -- JOIN fake")
                connection.execute("/* FROM fake */ SELECT id FROM accounts")

            def dynamic(connection, table):
                connection.execute(f"SELECT * FROM {table}")

            def complex_sql(connection):
                connection.execute(
                    "WITH recent AS (SELECT * FROM accounts) SELECT * FROM recent"
                )
                connection.execute("SELECT * FROM generate_series(1, 10)")
        """,
        },
    )
    graph = StaticAnalyzerAgent().analyze(tmp_path)
    assert {node.qualified_name for node in graph.nodes if node.label == "DBTable"} == {
        "db:accounts",
        "db:events",
        "db:ops.audit",
    }
    assert {
        target
        for source, target in links(graph, "READS_FROM")
        if source == "storage.reads"
    } == {"db:accounts", "db:events", "db:ops.audit"}
    assert {
        target
        for source, target in links(graph, "WRITES_TO")
        if source == "storage.writes"
    } == {"db:accounts", "db:events", "db:ops.audit"}
    assert {
        target
        for source, target in links(graph, "READS_FROM")
        if source == "storage.sql"
    } == {"db:accounts", "db:events"}
    assert {
        target
        for source, target in links(graph, "WRITES_TO")
        if source == "storage.sql"
    } == {"db:accounts", "db:events", "db:ops.audit"}
    assert not any(
        source == "storage.dynamic" for source, _ in links(graph, "READS_FROM")
    )
    assert any("unresolved SQL" in warning for warning in graph.warnings)


def test_domain_metadata_attribute_and_typed_collection_access(tmp_path: Path) -> None:
    write_sources(
        tmp_path,
        {
            "domain.py": """
        from typing import ClassVar
        from dataclasses import dataclass

        @dataclass
        class Record:
            __tablename__: ClassVar[str] = "records"
            id: str
            total: int

        class Store:
            def __init__(self):
                self.rows: dict[str, Record] = {}

            def save(self, row: Record):
                self.rows[row.id] = row

            def fetch(self, key: str) -> Record | None:
                return self.rows.get(key)

        def change(store: Store):
            record = store.fetch("one")
            record.total += 1
    """
        },
    )
    graph = StaticAnalyzerAgent().analyze(tmp_path)
    assert ("domain.Store.fetch", "db:records") in links(graph, "READS_FROM")
    assert ("domain.Store.save", "db:records") in links(graph, "WRITES_TO")
    assert ("domain.change", "db:records") in links(graph, "READS_FROM")
    assert ("domain.change", "db:records") in links(graph, "WRITES_TO")
    assert ("domain.change", "domain.Store.fetch") in links(graph, "CALLS")


def test_session_constructors_query_aliases_and_local_query_methods(
    tmp_path: Path,
) -> None:
    write_sources(
        tmp_path,
        {
            "queries.py": """
                from sqlalchemy.orm import Session
                class Row:
                    __tablename__ = "rows"

                def persist(row: Row):
                    session = Session()
                    session.add(row)

                def revise(session: Session):
                    query = session.query(Row)
                    alias = query.filter_by(id=1)
                    alias.update({"value": 2})

                def remove(session: Session):
                    query = session.query(Row)
                    query.delete()

                class Repository:
                    def query(self, model): pass
                def ordinary(repository: Repository):
                    repository.query(Row)

                def cycle(session: Session):
                    query = session.query(Row)
                    query = query.filter_by(id=1)
                    query.delete()
            """,
        },
    )
    graph = StaticAnalyzerAgent().analyze(tmp_path)
    assert ("queries.persist", "db:rows") in links(graph, "WRITES_TO")
    assert ("queries.revise", "db:rows") in links(graph, "WRITES_TO")
    assert ("queries.remove", "db:rows") in links(graph, "WRITES_TO")
    assert ("queries.revise", "db:rows") in links(graph, "READS_FROM")
    assert ("queries.ordinary", "db:rows") not in links(graph, "READS_FROM")
    assert ("queries.ordinary", "queries.Repository.query") in links(graph, "CALLS")
    assert not any(source == "queries.cycle" for source, _ in links(graph, "WRITES_TO"))


def test_no_fake_table_recognition_or_execution(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    write_sources(
        tmp_path,
        {
            "danger.py": f'''
        import missing_dependency
        from pathlib import Path
        Path({str(marker)!r}).write_text("executed")
        raise RuntimeError("must never run")
        class Table:
            def __init__(self, name, metadata):
                pass
        fake = Table("not_a_table", None)
        def function():
            """__import__('os').system('false')"""
            return missing_dependency.call()
    '''
        },
    )
    graph = StaticAnalyzerAgent().analyze(tmp_path)
    assert not marker.exists()
    assert not any(node.label == "DBTable" for node in graph.nodes)
    assert any(
        "unresolved import 'missing_dependency'" in warning
        for warning in graph.warnings
    )
    assert any(
        "unresolved call 'missing_dependency.call'" in warning
        for warning in graph.warnings
    )
    assert (
        next(node for node in graph.nodes if node.name == "function").docstring
        == "__import__('os').system('false')"
    )


def test_ambiguous_receivers_shadowing_and_duplicate_definitions(
    tmp_path: Path,
) -> None:
    write_sources(
        tmp_path,
        {
            "ambiguous.py": """
        class A:
            def run(self): pass
        class B:
            def run(self): pass
        class Both(A, B): pass
        def target(): pass
        def duplicate(): pass
        def duplicate(): pass

        def unknown(receiver):
            receiver.run()
        def union(receiver: A | B):
            receiver.run()
        def inherited(receiver: Both):
            receiver.run()
        def shadow(target):
            target()
        def branches(flag):
            if flag:
                receiver = A()
            else:
                receiver = B()
            receiver.run()
        def duplicate_caller():
            duplicate()
        def destructured(values):
            target, other = values
            target()
        def comprehension(values):
            return [target() for target in values]
        def good(receiver: A):
            receiver.run()
    """
        },
    )
    graph = StaticAnalyzerAgent().analyze(tmp_path)
    assert links(graph, "CALLS") == {("ambiguous.good", "ambiguous.A.run")}
    duplicates = [node for node in graph.nodes if node.name == "duplicate"]
    assert len(duplicates) == 2
    assert any("ambiguous definition" in warning for warning in graph.warnings)
    assert any(
        "unresolved call 'receiver.run'" in warning for warning in graph.warnings
    )


@pytest.mark.parametrize("broken", ["def broken(:\n", "def broken():\n    return (\n"])
def test_parse_errors_are_atomic_and_agent_is_reusable(
    tmp_path: Path, broken: str
) -> None:
    agent = StaticAnalyzerAgent()
    write_sources(tmp_path, {"a_good.py": "def good(): pass\n"})
    assert len(agent.analyze(tmp_path).nodes) == 2
    write_sources(tmp_path, {"z_broken.py": broken})
    with pytest.raises(StaticAnalysisError, match=r"AST_PARSE_ERROR: z_broken.py:\d+:"):
        agent.analyze(tmp_path)
    (tmp_path / "z_broken.py").unlink()
    assert len(agent.analyze(tmp_path).nodes) == 2


def test_file_count_limit_is_inclusive(tmp_path: Path) -> None:
    write_sources(
        tmp_path, {f"m{index:03}.py": "# empty\n" for index in range(MAX_PYTHON_FILES)}
    )
    assert len(StaticAnalyzerAgent().analyze(tmp_path).nodes) == 500
    write_sources(tmp_path, {"overflow.py": "# one too many\n"})
    with pytest.raises(StaticAnalysisError, match="AST_LIMIT_ERROR: more than 500"):
        StaticAnalyzerAgent().analyze(tmp_path)


def test_byte_limit_is_inclusive(tmp_path: Path) -> None:
    path = tmp_path / "large.py"
    path.write_bytes(b"#" + b"x" * (MAX_FILE_BYTES - 1))
    assert len(StaticAnalyzerAgent().analyze(tmp_path).nodes) == 1
    path.write_bytes(b"#" + b"x" * MAX_FILE_BYTES)
    with pytest.raises(StaticAnalysisError, match="AST_LIMIT_ERROR: large.py exceeds"):
        StaticAnalyzerAgent().analyze(tmp_path)


def test_skips_tests_environments_and_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    write_sources(
        root,
        {
            "good.py": "def good(): pass\n",
            "tests/broken.py": "def (",
            "test/broken.py": "def (",
            "test_module.py": "def (",
            "module_test.py": "def (",
            ".venv/broken.py": "def (",
            "venv/broken.py": "def (",
            ".git/broken.py": "def (",
            "node_modules/broken.py": "def (",
        },
    )
    outside = tmp_path / "outside"
    write_sources(outside, {"broken.py": "def ("})
    (root / "linked.py").symlink_to(outside / "broken.py")
    (root / "linked_dir").symlink_to(outside, target_is_directory=True)
    (root / "cycle").symlink_to(root, target_is_directory=True)
    (root / "dangling.py").symlink_to(outside / "missing.py")
    graph = StaticAnalyzerAgent().analyze(root)
    assert {node.qualified_name for node in graph.nodes} == {
        "file:good.py",
        "good.good",
    }
    with pytest.raises(StaticAnalysisError, match="AST_INPUT_ERROR"):
        StaticAnalyzerAgent().analyze(root / "linked_dir")


def test_empty_invalid_root_and_non_utf8_source(tmp_path: Path) -> None:
    agent = StaticAnalyzerAgent()
    assert agent.analyze(tmp_path) == CodeGraph(nodes=[], edges=[])
    with pytest.raises(StaticAnalysisError, match="AST_INPUT_ERROR"):
        agent.analyze(tmp_path / "missing")
    (tmp_path / "latin.py").write_bytes(
        b'# coding: latin-1\n"""Caf\xe9."""\ndef caf():\n    return 1\n'
    )
    graph = agent.analyze(tmp_path)
    assert (
        next(node for node in graph.nodes if node.label == "File").docstring
        == "Caf\u00e9."
    )


def test_import_cycles_missing_exports_and_wildcards_warn(tmp_path: Path) -> None:
    write_sources(
        tmp_path,
        {
            "a.py": "from b import function\ndef call(): function()\n",
            "b.py": "from a import function\n",
            "missing.py": (
                "from a import absent\nfrom a import *\ndef use(): absent()\n"
            ),
        },
    )
    graph = StaticAnalyzerAgent().analyze(tmp_path)
    assert not links(graph, "CALLS")
    assert any("wildcard binding" in warning for warning in graph.warnings)
    assert any("unresolved import 'absent'" in warning for warning in graph.warnings)
