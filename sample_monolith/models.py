"""Domain objects and SQLAlchemy-like schema declarations for the fixture.

The fixture deliberately keeps persistence in memory so it can run without a
database service.  The table declarations retain the shape of a relational
e-commerce schema for later static analysis.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum


class Integer:
    """Marker type matching an integer database column."""


class String:
    """Marker type matching a string database column."""


class Numeric:
    """Marker type matching a decimal database column."""


class Boolean:
    """Marker type matching a boolean database column."""


class DateTime:
    """Marker type matching a timestamp database column."""


class Column:
    """Small schema object with the subset needed by the static fixture."""

    def __init__(
        self,
        name: str,
        column_type: type[object],
        *,
        primary_key: bool = False,
        nullable: bool = True,
    ) -> None:
        self.name = name
        self.column_type = column_type
        self.primary_key = primary_key
        self.nullable = nullable


class MetaData:
    """Registry for SQLAlchemy-like table declarations."""

    def __init__(self) -> None:
        self.tables: dict[str, Table] = {}

    def register(self, table: Table) -> None:
        self.tables[table.name] = table


class Table:
    """Static table definition that does not open a database connection."""

    def __init__(self, name: str, metadata: MetaData, *columns: Column) -> None:
        self.name = name
        self.columns = {column.name: column for column in columns}
        metadata.register(self)


metadata = MetaData()

users_table = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True, nullable=False),
    Column("email", String, nullable=False),
    Column("is_active", Boolean, nullable=False),
)

products_table = Table(
    "products",
    metadata,
    Column("id", String, primary_key=True, nullable=False),
    Column("sku", String, nullable=False),
    Column("name", String, nullable=False),
    Column("price", Numeric, nullable=False),
)

inventory_table = Table(
    "inventory",
    metadata,
    Column("product_id", String, primary_key=True, nullable=False),
    Column("quantity", Integer, nullable=False),
)

orders_table = Table(
    "orders",
    metadata,
    Column("id", String, primary_key=True, nullable=False),
    Column("user_id", String, nullable=False),
    Column("total", Numeric, nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", DateTime, nullable=False),
)

order_items_table = Table(
    "order_items",
    metadata,
    Column("id", Integer, primary_key=True, nullable=False),
    Column("order_id", String, nullable=False),
    Column("product_id", String, nullable=False),
    Column("quantity", Integer, nullable=False),
)

ledger_entries_table = Table(
    "ledger_entries",
    metadata,
    Column("id", String, primary_key=True, nullable=False),
    Column("order_id", String, nullable=False),
    Column("amount", Numeric, nullable=False),
    Column("status", String, nullable=False),
)


class OrderStatus(str, Enum):
    """Lifecycle state for an order."""

    PENDING = "pending"
    PAID = "paid"
    CANCELLED = "cancelled"


class PaymentStatus(str, Enum):
    """Result state stored in the payment ledger."""

    APPROVED = "approved"
    DECLINED = "declined"


@dataclass(frozen=True)
class User:
    """Authenticated customer record."""

    id: str
    email: str
    is_active: bool = True


@dataclass
class Product:
    """Product catalog record with its current available stock."""

    id: str
    sku: str
    name: str
    price: Decimal
    stock: int


@dataclass(frozen=True)
class CartItem:
    """A requested product quantity in a checkout."""

    product_id: str
    quantity: int


@dataclass(frozen=True)
class OrderLine:
    """Snapshot of a product included in an order."""

    product_id: str
    product_name: str
    quantity: int
    unit_price: Decimal

    @property
    def line_total(self) -> Decimal:
        """Return the line total from the immutable price snapshot."""

        return self.unit_price * self.quantity


@dataclass
class Order:
    """Order aggregate persisted by the in-memory database."""

    id: str
    user_id: str
    lines: tuple[OrderLine, ...]
    total: Decimal
    status: OrderStatus
    created_at: datetime


@dataclass(frozen=True)
class PaymentTransaction:
    """Payment ledger row produced by the offline payment processor."""

    id: str
    order_id: str
    amount: Decimal
    status: PaymentStatus
    reason: str


class InMemoryDatabase:
    """Deterministic repository used instead of a live SQLAlchemy session."""

    def __init__(self) -> None:
        self._users: dict[str, User] = {}
        self._products: dict[str, Product] = {}
        self._orders: dict[str, Order] = {}
        self._payments: dict[str, PaymentTransaction] = {}
        self._order_sequence = 0
        self._payment_sequence = 0

    def add_user(self, user: User) -> None:
        """Insert a user into the fixture database."""

        self._users[user.id] = user

    def get_user(self, user_id: str) -> User | None:
        """Read one user by primary key."""

        return self._users.get(user_id)

    def add_product(self, product: Product) -> None:
        """Insert a catalog product with its starting inventory."""

        self._products[product.id] = product

    def get_product(self, product_id: str) -> Product | None:
        """Read one product and its current stock."""

        return self._products.get(product_id)

    def save_order(self, order: Order) -> None:
        """Persist an order aggregate."""

        self._orders[order.id] = order

    def get_order(self, order_id: str) -> Order | None:
        """Read an order by primary key."""

        return self._orders.get(order_id)

    def list_orders(self) -> tuple[Order, ...]:
        """Return orders in insertion order for deterministic assertions."""

        return tuple(self._orders.values())

    def save_payment(self, payment: PaymentTransaction) -> None:
        """Persist a payment ledger entry."""

        self._payments[payment.id] = payment

    def get_payment(self, payment_id: str) -> PaymentTransaction | None:
        """Read one payment ledger entry by primary key."""

        return self._payments.get(payment_id)

    def list_payments(self) -> tuple[PaymentTransaction, ...]:
        """Return payment entries in insertion order."""

        return tuple(self._payments.values())

    def next_order_id(self) -> str:
        """Generate a deterministic order identifier."""

        self._order_sequence += 1
        return f"order-{self._order_sequence:04d}"

    def next_payment_id(self) -> str:
        """Generate a deterministic payment identifier."""

        self._payment_sequence += 1
        return f"payment-{self._payment_sequence:04d}"


def find_user(db: InMemoryDatabase, user_id: str) -> User | None:
    """Read a user through the model boundary."""

    return db.get_user(user_id)


def find_product(db: InMemoryDatabase, product_id: str) -> Product | None:
    """Read a product through the model boundary."""

    return db.get_product(product_id)


def create_order_id(db: InMemoryDatabase) -> str:
    """Allocate the next order identifier through the model boundary."""

    return db.next_order_id()


def save_order(db: InMemoryDatabase, order: Order) -> None:
    """Write an order through the model boundary."""

    db.save_order(order)


def update_order_status(
    db: InMemoryDatabase,
    order_id: str,
    status: OrderStatus,
) -> None:
    """Update a persisted order status."""

    order = db.get_order(order_id)
    if order is None:
        raise KeyError(f"Unknown order: {order_id}")
    order.status = status


def create_payment_id(db: InMemoryDatabase) -> str:
    """Allocate the next payment identifier through the model boundary."""

    return db.next_payment_id()


def record_payment(db: InMemoryDatabase, payment: PaymentTransaction) -> None:
    """Write a payment ledger entry through the model boundary."""

    db.save_payment(payment)
