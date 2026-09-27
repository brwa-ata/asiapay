"""Exceptions raised by the AsiaPay client.

Kept dependency-free so ``client.py`` can be copied into any project.
"""


class AsiaPayError(Exception):
    """Base class for every AsiaPay error."""


class AsiaPayAuthError(AsiaPayError):
    """Raised when obtaining an access token from AsiaPay fails."""


class AsiaPayAPIError(AsiaPayError):
    """Raised when an AsiaPay API call returns an error response."""

    def __init__(self, message, status_code=None, payload=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload
