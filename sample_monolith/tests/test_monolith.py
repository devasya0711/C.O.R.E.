"""Deterministic end-to-end tests for the six-module sample monolith."""

from __future__ import annotations

from decimal import Decimal

import pytest

from sample_monolith.models import (
    CartItem,
    InMemoryDatabase,
    OrderStatus,
    PaymentStatus,
    Product,
    User,
)
from sample_monolith.orders import place_order
from sample_monolith.routes import CheckoutRequest, checkout_endpoint


@pytest.fixture
def database() -> InMemoryDatabase:
    """Build the same small catalog for every isolated test."""

    db = InMemoryDatabase()
    db.add_user(User(id="alice", email="alice@example.test"))
    db.add_product(
        Product(
            id="sku-lamp",
            sku="LAMP-001",
            name="Desk Lamp",
            price=Decimal("24.50"),
            stock=3,
        )
    )
    db.add_product(
        Product(
            id="sku-chair",
            sku="CHAIR-001",
            name="Desk Chair",
            price=Decimal("120.00"),
            stock=1,
        )
    )
    return db


def test_successful_checkout_reserves_inventory_and_records_payment(
    database: InMemoryDatabase,
) -> None:
    """A valid checkout creates a paid order and approved ledger entry."""

    result = place_order(
        database,
        "alice",
        (CartItem(product_id="sku-lamp", quantity=2),),
        "card:4242",
    )

    assert result.success is True
    assert result.order is not None
    assert result.order.status is OrderStatus.PAID
    assert result.order.total == Decimal("49.00")
    assert result.payment is not None
    assert result.payment.transaction.status is PaymentStatus.APPROVED
    lamp = database.get_product("sku-lamp")
    assert lamp is not None
    assert lamp.stock == 1


def test_insufficient_inventory_does_not_create_order_or_payment(
    database: InMemoryDatabase,
) -> None:
    """A stock shortage fails before the payment boundary is called."""

    response = checkout_endpoint(
        CheckoutRequest(
            authorization="Bearer user:alice",
            items=(CartItem(product_id="sku-chair", quantity=2),),
            payment_method="card:4242",
        ),
        database,
    )

    assert response.status_code == 409
    assert response.error_code == "INSUFFICIENT_INVENTORY"
    assert response.payment_id is None
    chair = database.get_product("sku-chair")
    assert chair is not None
    assert chair.stock == 1
    assert database.list_orders() == ()
    assert database.list_payments() == ()


def test_payment_failure_releases_inventory_and_cancels_order(
    database: InMemoryDatabase,
) -> None:
    """A gateway decline rolls back the reservation and keeps its ledger row."""

    result = place_order(
        database,
        "alice",
        (CartItem(product_id="sku-lamp", quantity=1),),
        "card:0002",
    )

    assert result.success is False
    assert result.error_code == "PAYMENT_DECLINED"
    assert result.order is not None
    assert result.order.status is OrderStatus.CANCELLED
    assert result.payment is not None
    assert result.payment.transaction.status is PaymentStatus.DECLINED
    lamp = database.get_product("sku-lamp")
    assert lamp is not None
    assert lamp.stock == 3


def test_checkout_route_returns_payment_transaction_details(
    database: InMemoryDatabase,
) -> None:
    """The API route exposes the payment decision from the order workflow."""

    response = checkout_endpoint(
        CheckoutRequest(
            authorization="Bearer user:alice",
            items=(CartItem(product_id="sku-lamp", quantity=1),),
            payment_method="card:4242",
        ),
        database,
    )

    assert response.status_code == 201
    assert response.order_id == "order-0001"
    assert response.payment_id == "payment-0001"
    assert response.payment_status == PaymentStatus.APPROVED.value
