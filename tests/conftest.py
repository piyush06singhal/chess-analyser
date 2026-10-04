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

# Small, verifiably legal games used by the Phase 2 chess-correctness tests.
CASTLING_PGN = """[Event "Castling test"]
[Result "*"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. O-O Nf6 5. d3 O-O *
"""

EN_PASSANT_PGN = """[Event "En passant test"]
[Result "*"]

1. e4 Nf6 2. e5 d5 3. exd6 *
"""

PROMOTION_PGN = """[Event "Promotion test"]
[Result "*"]

1. a4 b5 2. axb5 a6 3. bxa6 Bb7 4. axb7 Nc6 5. bxa8=Q *
"""

CHECK_PGN = """[Event "Check test"]
[Result "*"]

1. e4 d5 2. Bb5+ c6 *
"""

SCHOLARS_MATE_PGN = """[Event "Scholars mate"]
[Result "1-0"]

1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0
"""

DRAW_PGN = """[Event "Drawn test"]
[Result "1/2-1/2"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 1/2-1/2
"""

# 3. Nf6 is illegal: the white knight on f3 cannot jump straight to f6.
ILLEGAL_MOVE_PGN = """[Event "Illegal move"]
[Result "*"]

1. e4 e5 2. Nf3 Nc6 3. Nf6 *
"""

INCOMPLETE_PGN = """[Event "Headers only"]
[White "Nobody"]
[Black "Nobody"]
[Result "*"]
"""

MALFORMED_PGN = "this is not a chess game at all"


@pytest.fixture(autouse=True)
def _test_settings(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Point settings at a per-test SQLite database and fast engine.

    The process-wide rate limiter and engine gate are reset too: they are
    intentionally global (so a caller cannot dodge a limit by opening a new
    connection), but the whole suite acts as one caller hammering one API, so
    without a reset a later test would inherit an earlier test's budget.
    """
    from argus_api.rate_limit import LIMITER
    from argus_api.services.resource_gate import GATE

    monkeypatch.setenv("ARGUS_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("ARGUS_ENGINE_DEPTH", "6")
    monkeypatch.setenv("ARGUS_ENGINE_MULTIPV", "2")
    monkeypatch.setenv("ARGUS_LOG_LEVEL", "WARNING")
    # The suite must be hermetic: a developer's ``.env`` may point at a real LLM
    # provider (e.g. Groq), and inheriting that would make tests hit the network,
    # cost quota and pass or fail on someone else's uptime. Neutralise it here;
    # a test that wants a provider opts in explicitly (usually ``echo``).
    monkeypatch.setenv("ARGUS_LLM_PROVIDER", "")
    monkeypatch.setenv("ARGUS_LLM_API_KEY", "")
    get_settings.cache_clear()
    LIMITER.reset()
    GATE.reset()
    yield
    LIMITER.reset()
    GATE.reset()
    get_settings.cache_clear()


def build_fixture_pgn(
    game_count: int,
    *,
    players: int = 8,
    seed: int = 7,
    max_plies: int = 40,
    start_year: int = 2020,
) -> str:
    """A PGN corpus used **only** to exercise pipeline mechanics.

    These are legal random games with a generated result header and generated
    ratings. They exist so that splitting, deduplication, leakage checks and the
    experiment plumbing can be tested at a size where the mechanics matter.

    They are not real chess records and must never be presented as data: the
    Event header says ``generated fixture`` precisely so a fixture can never be
    mistaken for a corpus, and nothing in this repository measures a model on
    them outside a test.
    """
    import random

    import chess
    import chess.pgn

    rng = random.Random(seed)
    names = [f"Fixture Player {index:02d}" for index in range(players)]
    games: list[str] = []
    for index in range(game_count):
        board = chess.Board()
        game = chess.pgn.Game()
        white = names[index % players]
        black = names[(index + 1 + index % 3) % players] or names[(index + 2) % players]
        if black == white:
            black = names[(index + 3) % players]
        game.headers["Event"] = "generated fixture"
        game.headers["White"] = white
        game.headers["Black"] = black
        game.headers["WhiteElo"] = str(1000 + (index * 37) % 1800)
        game.headers["BlackElo"] = str(1000 + (index * 53) % 1800)
        # Spread the games across dates so a temporal split has chronology to keep.
        game.headers["Date"] = f"{start_year + (index // 4) % 3}.{1 + index % 12:02d}.{1 + index % 28:02d}"
        game.headers["TimeControl"] = ["180+0", "600+5", "60+0", "1800+30"][index % 4]
        node = game
        plies = 4 + rng.randrange(max_plies - 4)
        for _ in range(plies):
            moves = list(board.legal_moves)
            if not moves or board.is_game_over():
                break
            move = rng.choice(moves)
            node = node.add_variation(move)
            board.push(move)
        if board.is_checkmate():
            result = "1-0" if board.turn == chess.BLACK else "0-1"
        elif board.is_game_over():
            result = "1/2-1/2"
        else:
            # Unfinished random games still need a result header for the plumbing
            # tests to have something to label.
            result = ["1-0", "0-1", "1/2-1/2"][index % 3]
        game.headers["Result"] = result
        games.append(str(game) + "\n")
    return "\n".join(games)


@pytest.fixture()
def corpus_pgn() -> str:
    """A 240-game generated fixture corpus (mechanics tests only)."""
    return build_fixture_pgn(240)


@pytest.fixture()
def client() -> TestClient:
    """Test client with the app lifespan applied (tables + engine ready)."""
    with TestClient(app) as test_client:
        yield test_client
