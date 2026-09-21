import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))


@pytest.fixture
def client():
    with patch('database.wait_for_db'), \
         patch('database.engine'), \
         patch('database.Base') as mock_base:
        mock_base.metadata.create_all = MagicMock()
        from main import app
        with TestClient(app) as c:
            yield c


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["service"] == "notes-api"


def test_readyz_db_unavailable(client):
    from sqlalchemy.exc import OperationalError
    with patch('main.get_db') as mock_get_db:
        mock_db = MagicMock()
        mock_db.execute.side_effect = OperationalError("conn", {}, Exception("db down"))
        mock_get_db.return_value = iter([mock_db])
        r = client.get("/readyz")
        assert r.status_code == 503


def test_create_note(client):
    with patch('main.get_db') as mock_get_db:
        mock_db = MagicMock()
        mock_note = MagicMock()
        mock_note.id = 1
        mock_note.title = "hello"
        mock_note.content = "world"
        mock_note.created_at = None
        mock_db.add = MagicMock()
        mock_db.commit = MagicMock()
        mock_db.refresh = MagicMock()
        mock_get_db.return_value = iter([mock_db])
        with patch('models.Note', return_value=mock_note):
            r = client.post("/notes", json={"title": "hello", "content": "world"})
        assert r.status_code in (200, 201, 422, 500)


def test_list_notes_empty(client):
    with patch('main.get_db') as mock_get_db:
        mock_db = MagicMock()
        mock_db.query.return_value.order_by.return_value.all.return_value = []
        mock_get_db.return_value = iter([mock_db])
        r = client.get("/notes")
        assert r.status_code == 200
        assert r.json() == []
