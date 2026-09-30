class DomainError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NotFound(DomainError):
    status_code = 404
    code = "not_found"


class InvalidRequest(DomainError):
    status_code = 422
    code = "invalid_request"


class SeatsUnavailable(DomainError):
    status_code = 409
    code = "seats_unavailable"

    def __init__(self, seat_ids: list[int]) -> None:
        super().__init__("One or more seats are no longer available", seat_ids=seat_ids)


class InvalidState(DomainError):
    status_code = 409
    code = "invalid_state"


class Overloaded(DomainError):
    status_code = 503
    code = "overloaded"


class InvalidSignature(DomainError):
    status_code = 401
    code = "invalid_signature"
