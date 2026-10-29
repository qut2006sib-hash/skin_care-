class CommerceDomainError(Exception):
    """Base domain error for inventory, order, and financial invariants."""

class InsufficientStockError(CommerceDomainError):
    pass

class InvalidDeliveryZoneError(CommerceDomainError):
    pass

class HoldExpiredError(CommerceDomainError):
    pass

class InvalidStateTransitionError(CommerceDomainError):
    pass

class BatchEligibilityError(CommerceDomainError):
    pass

class ReturnLimitExceededError(CommerceDomainError):
    pass

class UnauthorizedStaffActionError(CommerceDomainError):
    pass
