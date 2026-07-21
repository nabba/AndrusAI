"""Request-level sentience lifecycle envelope.

This is the canonical outer boundary for user interactions.  It emits one
full PRE_TASK/ON_COMPLETE pair around the complete request and carries a
stable request id through both halves.  Internal crews use compressed cycles;
therefore only this boundary performs full request consolidation.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SubjectiveRequestEnvelope:
    """Failure-isolated, idempotent lifecycle pair for one user request."""

    user_message: str
    request_id: str = field(
        default_factory=lambda: f"request:{uuid.uuid4().hex}",
    )
    agent_id: str = "commander"
    context: str = field(default="", init=False)
    _started: bool = field(default=False, init=False, repr=False)
    _finished: bool = field(default=False, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False,
    )

    def begin(self) -> str:
        """Emit PRE_TASK once and return SubIA's optional context block."""
        with self._lock:
            if self._started:
                return self.context
            self._started = True
            # Keep PRE_TASK atomic with respect to repeated begin/finalize
            # calls. Otherwise a concurrent caller can observe _started=True
            # before the live SubIA context has been produced, or emit
            # ON_COMPLETE before the PRE half-cycle finishes.
            try:
                from app.lifecycle_hooks import get_registry, HookContext, HookPoint

                ctx = HookContext(
                    hook_point=HookPoint.PRE_TASK,
                    agent_id=self.agent_id,
                    task_description=self.user_message,
                    metadata={
                        "task_id": self.request_id,
                        "request_id": self.request_id,
                        "operation_type": "user_interaction",
                        "subjective_scope": "request",
                    },
                )
                ctx = get_registry().execute(HookPoint.PRE_TASK, ctx)
                self.context = str(
                    ctx.get("subia_context_injection", "") or ""
                )
                return self.context
            except Exception:
                logger.debug("subjective request PRE_TASK failed", exc_info=True)
                return ""

    def finalize(self, result: object, *, success: bool = True) -> None:
        """Emit ON_COMPLETE once with the exact final response text."""
        with self._lock:
            if self._finished:
                return
            self._finished = True
        # Keep pairing deterministic even when a caller forgot begin().
        if not self._started:
            self.begin()
        try:
            from app.lifecycle_hooks import get_registry, HookContext, HookPoint

            ctx = HookContext(
                hook_point=HookPoint.ON_COMPLETE,
                agent_id=self.agent_id,
                task_description=self.user_message,
                data={"result": str(result), "success": bool(success)},
                metadata={
                    "task_id": self.request_id,
                    "request_id": self.request_id,
                    "operation_type": "user_interaction",
                    "subjective_scope": "request",
                },
            )
            get_registry().execute(HookPoint.ON_COMPLETE, ctx)
        except Exception:
            logger.debug("subjective request ON_COMPLETE failed", exc_info=True)


def begin_subjective_request(
    user_message: str,
    *,
    request_id: str | None = None,
) -> SubjectiveRequestEnvelope:
    """Construct and begin the canonical request envelope."""
    envelope = SubjectiveRequestEnvelope(
        user_message=user_message,
        request_id=request_id or f"request:{uuid.uuid4().hex}",
    )
    envelope.begin()
    return envelope
