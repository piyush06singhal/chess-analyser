"""Shared pytest fixtures.

The test database is a file-based SQLite database (per-test tmp path) so the
persistence layer is exercised realistically without PostgreSQL. Environment
variables are set before the settings cache is created.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from argus_api.config import get_settings
from argus_api.main import app

# Real, well-documented game: Opera Game (Morphy, 1858).
OPERA_GAME_PGN = """[Event "Paris Opera House"]
[Site "Paris FRA"]
[Date "1858.11.02"]
[Round "?"]
[Result "1-0"]
[White "Paul Morphy"]
[Black "Duke Karl / Count Isouard"]
[ECO "C41"]
[TimeControl "600+5"]

1. e4 e5 2. Nf3 d6 3. d4 Bg4 4. dxe5 Bxf3 5. Qxf3 dxe5 6. Bc4 Nf6 7. Qb3 Qe7
8. Nc3 c6 9. Bg5 b5 10. Nxb5 cxb5 11. Bxb5+ Nbd7 12. O-O-O Rd8 13. Rxd7 Rxd7
14. Rd1 Qe6 15. Bxd7+ Nxd7 16. Qb8+ Nxb8 17. Rd8# 1-0
"""

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


@pytest.fixture(autouse=True)
def _test_settings(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Point settings at a per-test SQLite database and fast engine."""
    monkeypatch.setenv("ARGUS_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("ARGUS_ENGINE_DEPTH", "6")
    monkeypatch.setenv("ARGUS_ENGINE_MULTIPV", "2")
    monkeypatch.setenv("ARGUS_LOG_LEVEL", "WARNING")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def client() -> TestClient:
    """Test client with the app lifespan applied (tables + engine ready)."""
    with TestClient(app) as test_client:
        yield test_client
