"""Order orchestration across auth, inventory, payments, and models."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from . import auth, inventory, payments
from .models import (
    CartItem,
    InMemoryDatabase,
    Order,
    OrderLine,
    OrderStatus,
    PaymentTransaction,
    User,
    create_order_id,
    save_order,
    update_order_status,
)


@dataclass(frozen=True)
class CheckoutResult:
    """Stable application result returned by the order service."""

    success: bool
    order: Order | None = None
    payment: payments.PaymentResult | None = None
    error_code: str | None = None
    error_message: str | None = None


def _failure(
    code: str,
    message: str,
    *,
    order: Order | None = None,
    payment: payments.PaymentResult | None = None,
) -> CheckoutResult:
    """Build a failed checkout result without raising expected business errors."""

    return CheckoutResult(
        success=False,
        order=order,
        payment=payment,
        error_code=code,
        error_message=message,
    )


def _build_order(
    db: InMemoryDatabase,
    user: User,
    reservation: inventory.InventoryReservation,
) -> Order:
    """Create and persist a pending order from a successful reservation."""

    lines = tuple(
        OrderLine(
            product_id=item.product_id,
            product_name=item.product_name,
            quantity=item.quantity,
            unit_price=item.unit_price,
        )
        for item in reservation.items
    )
    total = sum((line.line_total for line in lines), Decimal("0.00"))
    order = Order(
        id=create_order_id(db),
        user_id=user.id,
        lines=lines,
        total=total,
        status=OrderStatus.PENDING,
        created_at=datetime.now(UTC),
    )
    save_order(db, order)
    return order


def place_order(
    db: InMemoryDatabase,
    user_id: str,
    items: Sequence[CartItem],
    payment_method: str,
) -> CheckoutResult:
    """Reserve inventory, charge payment, and finalize a customer order."""

    user = auth.authorize_checkout(db, user_id)
    try:
        reservation = inventory.reserve_items(db, items)
    except inventory.InventoryError as error:
        return _failure(error.code, error.message)

    order = _build_order(db, user, reservation)
    payment = payments.PaymentProcessor(db).execute(order, payment_method)
    if not payment.approved:
        inventory.release_items(db, reservation)
        update_order_status(db, order.id, OrderStatus.CANCELLED)
        return _failure(
            "PAYMENT_DECLINED",
            payment.transaction.reason,
            order=order,
            payment=payment,
        )

    update_order_status(db, order.id, OrderStatus.PAID)
    return CheckoutResult(success=True, order=order, payment=payment)


def find_order(db: InMemoryDatabase, order_id: str) -> Order | None:
    """Read an order for a route-level lookup."""

    return db.get_order(order_id)


def payment_for_order(
    db: InMemoryDatabase,
    order: Order,
) -> PaymentTransaction | None:
    """Find the ledger transaction associated with an order."""

    for payment in db.list_payments():
        if payment.order_id == order.id:
            return payment
    return None
