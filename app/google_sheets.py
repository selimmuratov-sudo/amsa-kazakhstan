"""Minimal, server-side Google Sheets writer for approved committee reports."""
from __future__ import annotations

import re
from datetime import timezone
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.database import SessionLocal
from app.models import AuditEvent, CommitteeReport, GoogleSyncRun, utcnow

API_ROOT = "https://sheets.googleapis.com/v4/spreadsheets"
REPORT_HEADERS = [
    "AMSA report ID", "Committee", "Committee ID", "Period", "Executive summary",
    "Activities", "Impact", "Challenges", "Next steps", "Members count",
    "Events count", "Status", "Submitted at (UTC)", "Updated at (UTC)",
]


class GoogleSheetsError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _credentials(settings):
    if not settings.google_sheets_sync_configured:
        raise GoogleSheetsError("not_configured")
    credential_file = Path(settings.google_service_account_file).expanduser()
    if not credential_file.is_file():
        raise GoogleSheetsError("credentials_file_missing")
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,200}", settings.google_reports_spreadsheet_id.strip()):
        raise GoogleSheetsError("spreadsheet_id_invalid")
    try:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2.service_account import Credentials
    except ImportError as exc:
        raise GoogleSheetsError("google_auth_not_installed") from exc
    try:
        credentials = Credentials.from_service_account_file(
            str(credential_file), scopes=["https://www.googleapis.com/auth/spreadsheets"])
    except Exception as exc:
        raise GoogleSheetsError("credentials_invalid") from exc
    return AuthorizedSession(credentials), settings.google_reports_spreadsheet_id.strip()


def _request(session, method: str, url: str, *, expected=(200,), **kwargs):
    try:
        response = session.request(method, url, timeout=(5, 25), **kwargs)
    except Exception as exc:
        raise GoogleSheetsError("google_api_unreachable") from exc
    if response.status_code not in expected:
        if response.status_code in {401, 403}:
            code = "google_permission_denied"
        elif response.status_code == 404:
            code = "spreadsheet_not_found"
        elif response.status_code == 429 or response.status_code >= 500:
            code = "google_api_temporary_error"
        else:
            code = "google_api_rejected_request"
        raise GoogleSheetsError(code)
    try:
        return response.json()
    except Exception as exc:
        raise GoogleSheetsError("google_api_invalid_response") from exc


def _sheet_title(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 100 or any(ord(ch) < 32 for ch in value):
        raise GoogleSheetsError("sheet_name_invalid")
    return value


def _values_url(spreadsheet_id: str, a1_range: str) -> str:
    return f"{API_ROOT}/{spreadsheet_id}/values/{quote(a1_range, safe='')}"


def _ensure_tab(session, spreadsheet_id: str, title: str) -> None:
    metadata = _request(session, "GET", f"{API_ROOT}/{spreadsheet_id}",
                        params={"fields": "sheets.properties(sheetId,title)"})
    titles = [sheet.get("properties", {}).get("title") for sheet in metadata.get("sheets", [])]
    if title not in titles:
        _request(session, "POST", f"{API_ROOT}/{spreadsheet_id}:batchUpdate", expected=(200,),
            json={"requests": [{"addSheet": {"properties": {"title": title}}}]})
    escaped = title.replace("'", "''")
    header_range = f"'{escaped}'!A1:N1"
    current = _request(session, "GET", _values_url(spreadsheet_id, header_range), expected=(200,))
    values = current.get("values", [])
    if not values:
        _request(session, "PUT", _values_url(spreadsheet_id, header_range), expected=(200,),
            params={"valueInputOption": "RAW"}, json={"majorDimension": "ROWS", "values": [REPORT_HEADERS]})
    elif values[0] != REPORT_HEADERS:
        raise GoogleSheetsError("sheet_header_mismatch")


def _utc_iso(value) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def sync_approved_report(report: CommitteeReport) -> str:
    """Upsert one approved report using its immutable database id as the sheet key."""
    settings = get_settings()
    session, spreadsheet_id = _credentials(settings)
    try:
        title = _sheet_title(settings.google_reports_sheet_name)
        _ensure_tab(session, spreadsheet_id, title)
        escaped = title.replace("'", "''")
        column_range = f"'{escaped}'!A:A"
        columns = _request(session, "GET", _values_url(spreadsheet_id, column_range), expected=(200,))
        values = columns.get("values", [[]])
        row_number = next((index + 1 for index, row in enumerate(values)
                           if row and str(row[0]) == str(report.id)), None)
        row = [
            str(report.id), report.committee.name, report.committee_id, report.period,
            report.executive_summary, report.activities, report.impact, report.challenges,
            report.next_steps, report.members_count if report.members_count is not None else "",
            report.events_count if report.events_count is not None else "", report.status,
            _utc_iso(report.submitted_at), _utc_iso(report.updated_at),
        ]
        if row_number is None:
            result = _request(session, "POST", _values_url(spreadsheet_id, f"'{escaped}'!A:N") + ":append",
                expected=(200,), params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                json={"majorDimension": "ROWS", "values": [row]})
            updated_range = result.get("updates", {}).get("updatedRange", "")
            match = re.search(r"!A(\d+):N\d+$", updated_range)
            if not match:
                raise GoogleSheetsError("google_api_invalid_response")
            row_number = int(match.group(1))
        else:
            target = f"'{escaped}'!A{row_number}:N{row_number}"
            _request(session, "PUT", _values_url(spreadsheet_id, target), expected=(200,),
                params={"valueInputOption": "RAW"}, json={"majorDimension": "ROWS", "values": [row]})
        return f"'{title}'!A{row_number}:N{row_number}"
    finally:
        session.close()


def run_google_sync(report_id: int, run_id: int) -> None:
    """Background worker: perform the network call and persist a sanitized outcome."""
    with SessionLocal() as db:
        run = db.get(GoogleSyncRun, run_id)
        report = db.scalar(select(CommitteeReport).options(joinedload(CommitteeReport.committee))
                           .where(CommitteeReport.id == report_id))
        if not run or not report:
            return
        if report.status != "approved":
            run.status = "failed"
            run.error_code = "report_not_approved"
        else:
            try:
                run.sheet_range = sync_approved_report(report)
                run.status = "succeeded"
                run.error_code = None
            except GoogleSheetsError as exc:
                run.status = "not_configured" if exc.code == "not_configured" else "failed"
                run.error_code = exc.code
            except Exception:
                run.status = "failed"
                run.error_code = "unexpected_sync_error"
        run.finished_at = utcnow()
        db.add(AuditEvent(actor_user_id=run.attempted_by, action="report.google_sheets_sync",
            entity_type="committee_report", entity_id=str(report.id),
            details={"status": run.status, "error_code": run.error_code}))
        db.commit()


def enqueue_google_sync(db: Session, report: CommitteeReport, *, actor_user_id: int | None) -> GoogleSyncRun:
    run = GoogleSyncRun(report_id=report.id, attempted_by=actor_user_id,
                        status="queued" if get_settings().google_sheets_sync_configured else "not_configured")
    db.add(run)
    db.flush()
    if run.status == "not_configured":
        run.error_code = "not_configured"
        run.finished_at = utcnow()
    return run
