class DomainException(Exception):
    """Base class for domain exceptions."""
    pass

class BusinessRuleViolation(DomainException):
    """Raised when a business rule is violated."""
    pass

class NotFoundException(DomainException):
    """Raised when an entity is not found."""
    pass

class DuplicateException(DomainException):
    """Raised when attempting to create an entity that already exists."""
    pass

class InvalidTransitionException(DomainException):
    """Raised when an invalid state transition is attempted."""
    pass


class UncertainWriteException(DomainException):
    """Jira may have applied the write; inspect the issue before retrying."""
