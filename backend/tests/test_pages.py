import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402


@pytest.fixture
def client():
    return create_app().test_client()


@pytest.mark.parametrize("path,text", [
    ("/", "Will your flight be on time?"), ("/how-it-works", "How AeroPredict works"), ("/faq", "Frequently asked questions"),
    ("/privacy", "Privacy policy"), ("/terms", "Terms of use"), ("/contact", "Get in touch"),
    ("/faq.html", "Frequently asked questions"),
])
def test_pages_are_served(client, path, text):
    r = client.get(path)
    assert r.status_code == 200
    assert text in r.get_data(as_text=True)


def test_unknown_pages_and_traversal_are_404(client):
    assert client.get("/no-such-page").status_code == 404
    assert client.get("/../backend/app/__init__.py").status_code == 404


def test_contact_details_come_from_environment(client, monkeypatch):
    monkeypatch.delenv("CONTACT_EMAIL", raising=False)
    monkeypatch.delenv("CONTACT_WHATSAPP", raising=False)
    cfg = client.get("/api/site-config").get_json()
    assert cfg["contact_email"] is None and cfg["contact_whatsapp"] is None
    monkeypatch.setenv("CONTACT_EMAIL", "hello@example.com")
    monkeypatch.setenv("CONTACT_WHATSAPP", "+90 555 123 45 67")
    cfg = client.get("/api/site-config").get_json()
    assert cfg["contact_email"] == "hello@example.com" and cfg["contact_whatsapp"] == "+905551234567"


def test_coverage_numbers(client):
    cov = client.get("/api/analytics/global-stats").get_json()["coverage"]
    assert cov["countries"] > 50 and cov["airports"] > 100 and cov["airlines"] > 20
