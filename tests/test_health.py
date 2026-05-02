"""H1–H2: App boot and root redirects."""
from tests.conftest import client as _client  # noqa: F401 — re-import for session scope
import pytest


def test_h1_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"


def test_h2_docs_root_redirects(client):
    r = client.get("/docs/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].endswith("/docs/quickstart")


def test_h3_root_redirects_to_globe(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert "globe" in r.headers["location"]
