from dataclasses import dataclass
from typing import Optional

from .errors import ErrorType


@dataclass(frozen=True)
class ExecutionResult:
    platform: str
    stage: str
    status: str
    error_type: Optional[ErrorType] = None
    summary: str = ""
    stdout: str = ""
    stderr: str = ""
    current_url: str = ""
    page_state: str = ""
    smoke_step: str = ""
    retryable: bool = False

    def to_dict(self):
        return {
            "platform": self.platform,
            "stage": self.stage,
            "status": self.status,
            "error_type": self.error_type.value if self.error_type else None,
            "summary": self.summary,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "current_url": self.current_url,
            "page_state": self.page_state,
            "smoke_step": self.smoke_step,
            "retryable": self.retryable,
        }
