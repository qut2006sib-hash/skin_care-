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


class ValidationDomainError(CommerceDomainError):
    pass


class IdempotencyConflictError(CommerceDomainError):
    pass


class PriceChangedError(CommerceDomainError):
    def __init__(self, current_total_syp):
        self.current_total_syp = current_total_syp
        super().__init__("تغير السعر أو أجور التوصيل. راجع الإجمالي الجديد قبل تأكيد الطلب.")
