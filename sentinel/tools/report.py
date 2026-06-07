"""The terminal output tool. The agent calls this once to submit its structured
RootCauseReport; the eval harness intercepts the call, validates the report, and
ends the run. Keeping the answer in a typed tool call avoids parsing free text.
"""

from __future__ import annotations

from sentinel.registry import tool
from sentinel.tools.models import ReportAck, RootCauseReport

REPORT_TOOL = "report_root_cause"


@tool(namespace="report")
def report_root_cause(params: RootCauseReport, store: object) -> ReportAck:
    """Submit the final incident report and end the investigation.

    Call this exactly once, after you have traced the failure to its origin and
    correlated it to a change. Provide the root cause (kind=service with the
    faulting service, or kind=edge with caller and callee, plus the failure
    type), the culprit change id, the change ids you ruled out, and the evidence
    that supports your conclusion.
    """
    return ReportAck(accepted=True)
