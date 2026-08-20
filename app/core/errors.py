"""Domain exceptions and their single mapping point to HTTP.

Routes never build error responses by hand: they raise a domain error and the
handler registered in app.main turns it into the one response shape used
everywhere - {"error": {"code", "message", "details"}}.
"""

from typing import Any


class AppError(Exception):
    """Base class for every error this service reports deliberately."""

    status_code: int = 500
    code: str = "internal_error"
    message: str = "Internal server error."

    def __init__(self, message: str | None = None, details: Any = None) -> None:
        self.message = message or self.message
        self.details = details
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            payload["details"] = self.details
        return {"error": payload}


class SessionNotFoundError(AppError):
    status_code = 404
    code = "session_not_found"
    message = "Chat session does not exist."


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"
    message = "Request payload is invalid."


class SessionLimitError(AppError):
    status_code = 409
    code = "session_message_limit"
    message = "This session reached its message limit."


class UnknownModelPricingError(AppError):
    """No tariff configured for the model.

    Deliberately fatal: silently charging zero would look like a working
    system while destroying the cost accounting this service exists for.
    """

    status_code = 500
    code = "unknown_model_pricing"
    message = "No pricing configured for the requested model."


class UpstreamUnavailableError(AppError):
    """OpenAI is rate limiting, erroring or timing out - retrying may help."""

    status_code = 503
    code = "upstream_unavailable"
    message = "The model provider is temporarily unavailable."


class UpstreamRejectedError(AppError):
    """OpenAI refused the request - bad key, no quota, bad model name."""

    status_code = 502
    code = "upstream_rejected"
    message = "The model provider rejected the request."


class EmptyReplyError(AppError):
    """The model returned no text, usually after exhausting the output budget.

    Reasoning models spend part of that budget before writing anything, so a
    tight cap can consume all of it. The call is still billed, which is why the
    usage behind this error is recorded rather than discarded.
    """

    status_code = 502
    code = "empty_reply"
    message = (
        "The model produced no text within its output budget. "
        "Try a shorter question or a different model."
    )


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"
    message = "Missing or invalid API key."
