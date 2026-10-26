import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402


@pytest.fixture
def client():
    return create_app().test_client()


@pytest.mark.parametrize("path,text", [
    ("/", "Will Your Flight"), ("/how-it-works", "How AeroPredict Works"), ("/faq", "Frequently Asked Questions"),
    ("/privacy", "Privacy Policy"), ("/terms", "Terms of Use"), ("/contact", "Get in Touch"),
    ("/faq.html", "Frequently Asked Questions"),
])
def test_pages_are_served(client, path, text):
    r = client.get(path)
    assert r.status_code == 200
    assert text in r.get_data(as_text=True)


def test_unknown_pages_and_traversal_are_404(client):
    assert client.get("/no-such-page").status_code == 404
    assert client.get("/../backend/app/__init__.py").status_code == 404


def test_contact_email_comes_from_environment(client, monkeypatch):
    assert client.get("/api/site-config").get_json()["contact_email"] is None
    monkeypatch.setenv("CONTACT_EMAIL", "hello@example.com")
    assert client.get("/api/site-config").get_json()["contact_email"] == "hello@example.com"


def test_coverage_numbers(client):
    cov = client.get("/api/analytics/global-stats").get_json()["coverage"]
    assert cov["countries"] > 50 and cov["airports"] > 100 and cov["airlines"] > 20
