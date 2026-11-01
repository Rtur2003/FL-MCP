class ApiError(Exception):
    """A failure the API reports as application/problem+json (RFC 9457)."""

    def __init__(self, status: int, code: str, detail: str, **extra: object) -> None:
        super().__init__(detail)
        self.status, self.code, self.detail, self.extra = status, code, detail, extra
