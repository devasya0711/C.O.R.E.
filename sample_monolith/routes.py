"""API route handlers for the sample e-commerce monolith."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .auth import AuthenticationError, authenticate_request
from .models import CartItem, InMemoryDatabase
from .orders import place_order


@dataclass(frozen=True)
class CheckoutRequest:
    """Input payload accepted by the checkout endpoint."""

    authorization: str | None
    items: tuple[CartItem, ...]
    payment_method: str


@dataclass(frozen=True)
class CheckoutResponse:
    """Serializable route result with an HTTP-like status code."""

    status_code: int
    order_id: str | None = None
    payment_id: str | None = None
    payment_status: str | None = None
    error_code: str | None = None
    message: str | None = None


RouteHandler = Callable[[CheckoutRequest, InMemoryDatabase], CheckoutResponse]


class RouteRegistry:
    """Minimal decorator registry that avoids a web-framework dependency."""

    def __init__(self) -> None:
        self.post_routes: dict[str, RouteHandler] = {}

    def post(self, path: str) -> Callable[[RouteHandler], RouteHandler]:
        """Register a POST handler while preserving the original function."""

        def decorator(handler: RouteHandler) -> RouteHandler:
            self.post_routes[path] = handler
            return handler

        return decorator


api = RouteRegistry()


@api.post("/checkout")
def checkout_endpoint(
    request: CheckoutRequest,
    db: InMemoryDatabase,
) -> CheckoutResponse:
    """Authenticate a request and translate order service results to HTTP."""

    try:
        user = authenticate_request(request.authorization, db)
    except AuthenticationError as error:
        return CheckoutResponse(
            status_code=401,
            error_code=error.code,
            message=error.message,
        )

    result = place_order(db, user.id, request.items, request.payment_method)
    if result.success and result.order is not None and result.payment is not None:
        return CheckoutResponse(
            status_code=201,
            order_id=result.order.id,
            payment_id=result.payment.transaction.id,
            payment_status=result.payment.transaction.status.value,
            message="Checkout completed",
        )

    status_code = 402 if result.error_code == "PAYMENT_DECLINED" else 409
    payment_id = result.payment.transaction.id if result.payment is not None else None
    payment_status = (
        result.payment.transaction.status.value if result.payment is not None else None
    )
    return CheckoutResponse(
        status_code=status_code,
        order_id=result.order.id if result.order is not None else None,
        payment_id=payment_id,
        payment_status=payment_status,
        error_code=result.error_code,
        message=result.error_message,
    )
