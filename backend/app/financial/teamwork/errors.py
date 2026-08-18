"""Teamwork API errors. Messages never include credentials."""

from __future__ import annotations


class TeamworkError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


NOT_CONFIGURED_MESSAGE = "Teamwork credentials are not configured"


class TeamworkAuthError(TeamworkError):
    pass


class TeamworkForbiddenError(TeamworkError):
    pass


class TeamworkNotFoundError(TeamworkError):
    pass


class TeamworkRateLimitError(TeamworkError):
    pass


class TeamworkServerError(TeamworkError):
    pass


class TeamworkTimeoutError(TeamworkError):
    pass


class TeamworkResponseError(TeamworkError):
    pass
