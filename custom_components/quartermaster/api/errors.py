"""Exceptions raised by the Quartermaster API client."""


class QuartermasterError(Exception):
    """Base class for every error the client raises."""


class QuartermasterConnectionError(QuartermasterError):
    """The server could not be reached, timed out, or closed the connection."""


class QuartermasterInvalidResponseError(QuartermasterError):
    """The server answered, but not like a Quartermaster server would."""


class QuartermasterAuthError(QuartermasterError):
    """The token is unknown, revoked, or not a Home Assistant token (401/403)."""


class QuartermasterNotFoundError(QuartermasterError):
    """The item doesn't exist (any more)."""


class QuartermasterRequestError(QuartermasterError):
    """The server refused the request (other 4xx and 5xx answers)."""

    def __init__(self, status: int, message: str) -> None:
        """Initialize with the HTTP status and the server's message."""
        super().__init__(message)
        self.status = status
        self.message = message
