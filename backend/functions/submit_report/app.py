"""``POST /reports`` - the single writer for new reports."""

from __future__ import annotations

from typing import Any

from shared.config import load_settings
from shared.domain import build_new_report, request_fingerprint
from shared.errors import Conflict, InternalError
from shared.http import handle_api, parse_json_body
from shared.ids import new_public_ref
from shared.logging import log
from shared.repository import DuplicateReport, ReportRepository
from shared.responses import json_response
from shared.storage import EvidenceStorage
from shared.validation import ReportInput, validate_report_input
from shared.workflow import WorkflowClient


def _success(
    report: dict[str, Any],
    runtime: Any,
    event: dict[str, Any],
    status_code: int,
) -> dict[str, Any]:
    public_ref = report["publicRef"]
    return json_response(
        status_code,
        {
            "publicRef": public_ref,
            "status": report["status"],
            "createdAt": report["createdAt"],
            "trackingUrl": f"{runtime.web_url}/track/{public_ref}",
        },
        event=event,
        allowed_origins=runtime.allowed_origins,
    )


def _start_workflow(
    report: dict[str, Any],
    repository: ReportRepository,
    flow: WorkflowClient,
) -> None:
    """Start the workflow, leaving ``workflowStatus = PENDING`` if the call fails.

    The report is already durable at this point. A failed start must not turn a successful
    submission into a client error; an operator alarm watches for reports that stay
    ``PENDING``. Retrying the submission calls this again with the same execution name,
    and ``ExecutionAlreadyExists`` counts as success.
    """

    if not flow.configured or report.get("workflowStatus") != "PENDING":
        return
    try:
        execution_arn = flow.start(report["reportId"], report["publicRef"])
        repository.mark_workflow_started(report["reportId"], execution_arn)
        report["workflowStatus"] = "STARTED"
    except Exception as exc:  # noqa: BLE001 - submission is already committed
        log(
            "error",
            "workflow start failed",
            reportId=report["reportId"],
            errorType=type(exc).__name__,
        )


def _already_exists(
    value: ReportInput,
    repository: ReportRepository,
    runtime: Any,
    event: dict[str, Any],
    flow: WorkflowClient,
) -> dict[str, Any] | None:
    existing = repository.get_report(value.report_id)
    if existing is None:
        return None
    if existing.get("requestHash") == request_fingerprint(value):
        _start_workflow(existing, repository, flow)
        return _success(existing, runtime, event, 200)
    raise Conflict("A different report already uses this reportId.")


def handler(
    event: dict[str, Any],
    context: Any = None,
    *,
    settings: Any = None,
    repository: ReportRepository | None = None,
    storage: EvidenceStorage | None = None,
    workflow: WorkflowClient | None = None,
) -> dict[str, Any]:
    runtime = settings or load_settings()

    def operation() -> dict[str, Any]:
        value = validate_report_input(parse_json_body(event))

        repo = repository or ReportRepository(
            runtime.reports_table,
            runtime.events_table,
            region=runtime.region,
        )
        flow = workflow or WorkflowClient(runtime.state_machine_arn, region=runtime.region)
        retry_response = _already_exists(value, repo, runtime, event, flow)
        if retry_response is not None:
            return retry_response

        store = storage
        if value.evidence is not None and store is None:
            if not runtime.evidence_bucket:
                raise InternalError("Evidence storage is not configured.")
            store = EvidenceStorage(runtime.evidence_bucket, region=runtime.region)

        evidence_key = None
        if value.evidence is not None and store is not None:
            evidence_key = store.verify_and_copy(value.evidence, value.report_id)

        new_report = build_new_report(
            value,
            public_ref=new_public_ref(),
            evidence_key=evidence_key,
        )
        try:
            repo.create_report(new_report.report, new_report.event)
        except DuplicateReport as exc:
            retry_response = _already_exists(value, repo, runtime, event, flow)
            if retry_response is not None:
                return retry_response
            raise Conflict("The report could not be created because it already exists.") from exc

        _start_workflow(new_report.report, repo, flow)
        return _success(new_report.report, runtime, event, 201)

    return handle_api(
        event,
        context,
        runtime,
        operation,
        operation_name="submit_report",
    )
