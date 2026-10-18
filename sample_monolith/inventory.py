"""Inventory reservation and release operations for checkout."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from .models import CartItem, InMemoryDatabase, find_product


@dataclass(frozen=True)
class ReservedItem:
    """Product snapshot held by a checkout reservation."""

    product_id: str
    product_name: str
    quantity: int
    unit_price: Decimal


@dataclass
class InventoryReservation:
    """Reservation that can be released exactly once after payment failure."""

    items: tuple[ReservedItem, ...]
    released: bool = False


class InventoryError(Exception):
    """Expected inventory validation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def reserve_items(
    db: InMemoryDatabase,
    requested_items: Sequence[CartItem],
) -> InventoryReservation:
    """Atomically reserve every requested product in the in-memory catalog."""

    if not requested_items:
        raise InventoryError("EMPTY_CART", "At least one item is required")

    quantities: dict[str, int] = {}
    for item in requested_items:
        if item.quantity <= 0:
            raise InventoryError(
                "INVALID_QUANTITY",
                f"Quantity for {item.product_id} must be positive",
            )
        quantities[item.product_id] = quantities.get(item.product_id, 0) + item.quantity

    reserved_items: list[ReservedItem] = []
    for product_id, quantity in quantities.items():
        product = find_product(db, product_id)
        if product is None:
            raise InventoryError("PRODUCT_NOT_FOUND", f"Unknown product: {product_id}")
        if product.stock < quantity:
            raise InventoryError(
                "INSUFFICIENT_INVENTORY",
                f"{product.name} has {product.stock} units available, "
                f"but {quantity} were requested",
            )
        reserved_items.append(
            ReservedItem(
                product_id=product.id,
                product_name=product.name,
                quantity=quantity,
                unit_price=product.price,
            )
        )

    for item in reserved_items:
        product = find_product(db, item.product_id)
        if product is None:
            raise InventoryError(
                "PRODUCT_NOT_FOUND", f"Unknown product: {item.product_id}"
            )
        product.stock -= item.quantity

    return InventoryReservation(items=tuple(reserved_items))


def release_items(db: InMemoryDatabase, reservation: InventoryReservation) -> None:
    """Return reserved stock after a downstream payment failure."""

    if reservation.released:
        return

    for item in reservation.items:
        product = find_product(db, item.product_id)
        if product is None:
            raise InventoryError(
                "PRODUCT_NOT_FOUND", f"Unknown product: {item.product_id}"
            )
        product.stock += item.quantity
    reservation.released = True


def available_stock(db: InMemoryDatabase, product_id: str) -> int:
    """Return current stock for a product, or zero when it is not cataloged."""

    product = find_product(db, product_id)
    return product.stock if product is not None else 0
