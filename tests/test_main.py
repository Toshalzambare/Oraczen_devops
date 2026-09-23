import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from database import Base
import models
from main import app, get_db

# Create an in-memory SQLite database for testing
engine = create_engine(
    "sqlite:///:memory:", 
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

@pytest.fixture
def client():
    # Patch the wait_for_db function to bypass DB wait on app startup
    with patch('main.wait_for_db'), patch('main.engine', engine):
        Base.metadata.create_all(bind=engine)
        with TestClient(app) as c:
            yield c
        Base.metadata.drop_all(bind=engine)

def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["service"] == "notes-api"

def test_readyz_db_unavailable(client):
    # To test the failure, we mock the dependency specifically for this test
    from sqlalchemy.exc import OperationalError
    def override_get_db_fail():
        mock_db = MagicMock()
        mock_db.execute.side_effect = OperationalError("conn", {}, Exception("db down"))
        yield mock_db
    
    app.dependency_overrides[get_db] = override_get_db_fail
    r = client.get("/readyz")
    assert r.status_code == 503
    app.dependency_overrides[get_db] = override_get_db # Restore override

def test_create_note(client):
    r = client.post("/notes", json={"title": "hello", "content": "world"})
    assert r.status_code == 201
    data = r.json()
    assert data["title"] == "hello"
    assert data["content"] == "world"
    assert "id" in data

def test_list_notes_empty(client):
    r = client.get("/notes")
    assert r.status_code == 200
    assert r.json() == []
