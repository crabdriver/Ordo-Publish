from enum import Enum


class ErrorType(str, Enum):
    CONFIG_ERROR = "config_error"
    ENVIRONMENT_ERROR = "environment_error"
    LOGIN_REQUIRED = "login_required"
    PLATFORM_CHANGED = "platform_changed"
    CONTENT_REJECTED = "content_rejected"
    RATE_LIMITED = "rate_limited"
    TRANSIENT_ERROR = "transient_error"
    DUPLICATE_OR_SKIPPED = "duplicate_or_skipped"
    UNKNOWN_ERROR = "unknown_error"


RETRYABLE_ERRORS = {ErrorType.TRANSIENT_ERROR}
BLOCKING_ERRORS = {ErrorType.CONFIG_ERROR, ErrorType.ENVIRONMENT_ERROR}


def is_retryable_error(error_type):
    return error_type in RETRYABLE_ERRORS


def is_blocking_error(error_type):
    return error_type in BLOCKING_ERRORS
