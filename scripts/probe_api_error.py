"""Probe: reproduce the API re-analysis 500 and print the traceback."""
import os
import sys
import tempfile

sys.path.insert(0, ".")

os.environ["ARGUS_DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ["ARGUS_ENGINE_DEPTH"] = "6"
os.environ["ARGUS_ENGINE_MULTIPV"] = "2"
os.environ["ARGUS_LOG_LEVEL"] = "WARNING"

from fastapi.testclient import TestClient

from argus_api.config import get_settings
from argus_api.main import create_app
from tests.test_api import OPERA_GAME_PGN

get_settings.cache_clear()
app = create_app()
with TestClient(app, raise_server_exceptions=True) as client:
    imported = client.post(
        "/api/games/import", json={"pgn_text": OPERA_GAME_PGN, "depth": 6, "multipv": 2}
    )
    print("import status:", imported.status_code)
    game_id = imported.json().get("game_id")
    print("game_id:", game_id)
    try:
        reanalysis = client.post("/api/analysis/game", json={"game_id": game_id, "depth": 6})
        print("reanalysis status:", reanalysis.status_code)
        print("body:", repr(reanalysis.text[:500]))
    except Exception as exc:
        import traceback

        print("EXCEPTION during reanalysis:")
        traceback.print_exc()
    get_settings.cache_clear()

