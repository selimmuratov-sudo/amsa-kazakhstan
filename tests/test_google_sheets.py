from types import SimpleNamespace
import json

from app import google_sheets
from app.config import Settings


class FakeResponse:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


class FakeSession:
    def __init__(self, column_values):
        self.column_values = column_values
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if method == "GET" and url.endswith("/sheet-for-test"):
            return FakeResponse({"sheets": [{"properties": {"sheetId": 1, "title": "Committee Reports"}}]})
        if method == "GET" and "/values/" in url:
            if "A1%3AN1" in url:
                return FakeResponse({"values": [google_sheets.REPORT_HEADERS]})
            return FakeResponse({"values": self.column_values})
        if method == "POST" and url.endswith(":append"):
            return FakeResponse({"updates": {"updatedRange": "'Committee Reports'!A3:N3"}})
        return FakeResponse({})

    def close(self):
        pass


def sample_report():
    return SimpleNamespace(
        id=27, committee_id=4, committee=SimpleNamespace(name="Test LC"), period="2026-Q4",
        executive_summary="Summary", activities="Activities", impact="", challenges="", next_steps="",
        members_count=11, events_count=2, status="approved", submitted_at=None, updated_at=None,
    )


def test_sheets_sync_appends_a_report_using_raw_values(monkeypatch):
    session = FakeSession([["AMSA report ID"]])
    monkeypatch.setattr(google_sheets, "get_settings", lambda: Settings(
        google_reports_spreadsheet_id="s" * 24, google_service_account_file="/tmp/service-account.json"))
    monkeypatch.setattr(google_sheets, "_credentials", lambda settings: (session, "s" * 24))

    result = google_sheets.sync_approved_report(sample_report())

    assert result == "'Committee Reports'!A3:N3"
    append = next(call for call in session.calls if call[0] == "POST" and call[1].endswith(":append"))
    assert append[2]["params"]["valueInputOption"] == "RAW"
    assert append[2]["json"]["values"][0][:4] == ["27", "Test LC", 4, "2026-Q4"]


def test_sheets_sync_updates_existing_report_row(monkeypatch):
    session = FakeSession([["AMSA report ID"], ["27"]])
    monkeypatch.setattr(google_sheets, "get_settings", lambda: Settings(
        google_reports_spreadsheet_id="s" * 24, google_service_account_file="/tmp/service-account.json"))
    monkeypatch.setattr(google_sheets, "_credentials", lambda settings: (session, "s" * 24))

    result = google_sheets.sync_approved_report(sample_report())

    assert result == "'Committee Reports'!A2:N2"
    assert not any(call[0] == "POST" and call[1].endswith(":append") for call in session.calls)
    update = next(call for call in session.calls if call[0] == "PUT" and "/values/" in call[1])
    assert "A2%3AN2" in update[1]


def test_service_account_loads_with_only_sheets_scope(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key = key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    credential_path = tmp_path / "service-account.json"
    credential_path.write_text(json.dumps({
        "type": "service_account", "project_id": "amsa-test", "private_key_id": "test-key",
        "private_key": private_key, "client_email": "sheets@amsa-test.iam.gserviceaccount.com",
        "client_id": "123456789", "token_uri": "https://oauth2.googleapis.com/token",
    }))
    monkeypatch.setattr(google_sheets, "get_settings", lambda: Settings(
        google_reports_spreadsheet_id="s" * 24, google_service_account_file=str(credential_path)))

    session, spreadsheet_id = google_sheets._credentials(google_sheets.get_settings())
    try:
        assert spreadsheet_id == "s" * 24
        assert set(session.credentials.scopes) == {"https://www.googleapis.com/auth/spreadsheets"}
    finally:
        session.close()
