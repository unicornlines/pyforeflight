class ForeflightException(Exception):
    """Base exception for all Foreflight client errors."""


class AuthenticationException(ForeflightException):
    """An authentication error occured."""


class APIException(ForeflightException):
    """A Foreflight API request returned an error response."""
