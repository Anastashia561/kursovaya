import json
import httpx
import pytest
from scripts import smoke


def test_incomplete_http_scenario_does_not_report_success(
    tmp_path, monkeypatch
):
    """A healthy app followed by an auth outage is a failed scenario."""
    (tmp_path / "credentials.json").write_text(
        json.dumps(
            {"admin_password": "synthetic", "auditor_password": "synthetic"}
        )
    )
    real_client = httpx.Client

    def respond(request):
        if request.url.path == "/api/v1/records":
            return httpx.Response(200, json=[])
        raise httpx.ConnectError("auth service unavailable", request=request)

    monkeypatch.setattr(smoke, "ROOT", tmp_path)
    monkeypatch.setattr(
        smoke.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond)),
    )
    with pytest.raises(httpx.ConnectError):
        smoke.verify(tmp_path)
    report = json.loads(
        (tmp_path / "test-results" / "local-smoke.json").read_text()
    )
    assert report["checks"][0]["passed"] is True
    assert report["passed"] is False
