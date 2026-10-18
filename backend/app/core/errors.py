"""Consistent API errors that never expose input secrets or store diagnostics."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException


class ApiError(Exception):
    """An explicitly safe public error; messages must not contain credentials."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers or {}


async def api_error_handler(request: Request, error: ApiError) -> JSONResponse:
    """Serialize a safe domain exception in the contract's standard envelope."""
    return JSONResponse(
        status_code=error.status_code,
        content={
            "error": {
                "code": error.code,
                "message": error.message,
                "details": error.details,
            }
        },
        headers={"Cache-Control": "no-store", **error.headers},
    )


async def validation_handler(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    """Exclude input values and exception context, which may contain secrets."""
    return await api_error_handler(
        request,
        ApiError(
            422,
            "VALIDATION_ERROR",
            "Request validation failed.",
            {
                "fields": [
                    {"location": list(issue["loc"]), "type": issue["type"]}
                    for issue in error.errors()
                ]
            },
        ),
    )


async def http_error_handler(request: Request, error: HTTPException) -> JSONResponse:
    """Keep framework errors consistent without reflecting arbitrary details."""
    return await api_error_handler(
        request,
        ApiError(
            error.status_code,
            "RESOURCE_NOT_FOUND" if error.status_code == 404 else "HTTP_ERROR",
            "Resource not found." if error.status_code == 404 else "Request rejected.",
            headers=dict(error.headers) if error.headers else None,
        ),
    )


async def database_error_handler(
    request: Request, error: OperationalError
) -> JSONResponse:
    """Do not return connection URLs or query parameters on store failures."""
    return await api_error_handler(
        request, ApiError(503, "DATABASE_UNAVAILABLE", "Database is unavailable.")
    )


def install_error_handlers(app: FastAPI) -> None:
    """Register standard domain, request, and framework error handlers."""
    for kind in (ApiError, RequestValidationError, HTTPException, OperationalError):
        app.add_exception_handler(kind, dispatch_error)
    app.add_exception_handler(Exception, dispatch_error)


async def dispatch_error(request: Request, error: Exception) -> JSONResponse:
    """Keep unknown internal exception details out of the response as well."""
    if isinstance(error, ApiError):
        return await api_error_handler(request, error)
    if isinstance(error, RequestValidationError):
        return await validation_handler(request, error)
    if isinstance(error, HTTPException):
        return await http_error_handler(request, error)
    if isinstance(error, OperationalError):
        return await database_error_handler(request, error)
    return await api_error_handler(
        request, ApiError(500, "INTERNAL_ERROR", "An internal error occurred.")
    )
