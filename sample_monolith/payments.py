"""Offline payment processing used by the checkout flow."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .models import (
    InMemoryDatabase,
    Order,
    PaymentStatus,
    PaymentTransaction,
    create_payment_id,
    record_payment,
)


@dataclass(frozen=True)
class PaymentResult:
    """Payment decision and the ledger transaction that records it."""

    approved: bool
    transaction: PaymentTransaction


class PaymentProcessor:
    """Deterministic payment gateway adapter with no network dependency.

    A four-digit card suffix is used only as a fixture input.  Suffix ``0002``
    exercises a gateway decline while other well-formed card references are
    approved.
    """

    def __init__(self, db: InMemoryDatabase) -> None:
        self.db = db

    def execute(self, order: Order, payment_method: str) -> PaymentResult:
        """Authorize an order and persist the resulting ledger entry."""

        approved, reason = self._authorize(payment_method, order.total)
        status = PaymentStatus.APPROVED if approved else PaymentStatus.DECLINED
        transaction = PaymentTransaction(
            id=create_payment_id(self.db),
            order_id=order.id,
            amount=order.total,
            status=status,
            reason=reason,
        )
        record_payment(self.db, transaction)
        return PaymentResult(approved=approved, transaction=transaction)

    def _authorize(self, payment_method: str, amount: Decimal) -> tuple[bool, str]:
        """Apply deterministic gateway validation to a payment reference."""

        if amount <= self._minimum_charge():
            return False, "Payment amount must be greater than zero"
        if (
            len(payment_method) != 9
            or not payment_method.startswith("card:")
            or not payment_method[5:].isdigit()
        ):
            return False, "Payment method is invalid"
        if payment_method.endswith("0002"):
            return False, "Payment was declined by the card network"
        return True, "Payment approved"

    @staticmethod
    def _minimum_charge() -> Decimal:
        """Return the zero-value comparison used by the offline gateway."""

        return Decimal("0.00")
