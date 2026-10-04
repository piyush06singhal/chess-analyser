"""Phase 8 training API tests.

The deterministic tests seed a real imported game with **controlled stored move
analyses** (hand-specified engine evaluations) so the whole loop — generation,
eligibility, persistence, grading, spaced repetition, sessions, progress and
recommendations — is asserted without depending on Stockfish's opinion at test
time. That is deliberate: these tests must not become flaky when the engine's
depth or version changes. The real engine pipeline is exercised separately by
``TestTrainingWithRealEngine``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from argus_api.db.models import Game, MoveAnalysis
from argus_api.main import app
from tests.conftest import OPERA_GAME_PGN

# Two legal, distinct positions where the side to move can win a queen with
# Rxd5. The played move (Rd2) is a clear, measurable mistake in both.
POSITION_A_FEN = "4k3/8/8/3q4/8/8/8/3RK3 w - - 0 1"
POSITION_B_FEN = "4k3/8/8/3q4/8/8/8/3R1K2 w - - 0 1"


def _import_game(client: TestClient) -> str:
    response = client.post("/api/games/import", json={"pgn_text": OPERA_GAME_PGN, "run_analysis": False})
    assert response.status_code == 200, response.text
    return response.json()["game_id"]


def _player_id(client: TestClient, name: str) -> str:
    players = client.get("/api/players").json()["players"]
    match = next((entry for entry in players if entry["name"] == name), None)
    assert match is not None, f"player {name!r} not in {players}"
    return match["id"]


def _seed_analyses(game_id: str, player_id: int) -> None:
    """Insert controlled per-move engine analysis into the named game."""
    with app.state.session_factory.session_scope() as session:
        session.add(
            MoveAnalysis(
                game_id=game_id,
                ply=1,
                move_number=1,
                mover="white",
                played_move_uci="d1d2",
                played_move_san="Rd2",
                fen_before=POSITION_A_FEN,
                fen_after="4k3/8/8/3q4/8/8/3R4/4K3 b - - 1 1",
                best_move_uci="d1d5",
                best_move_san="Rxd5",
                evaluation_before_cp=800,
                centipawn_loss=130,
                played_eval_cp=670,
                played_eval_source="same_search",
                classification="mistake",
                is_best_move=False,
                phase="endgame",
                depth=12,
                principal_variation=["d1d5"],
                # MultiPV candidates from the same search: d1d4 is 100cp worse
                # (inside the 150cp acceptance tolerance) so it must be recorded
                # as an acceptable alternative, not a wrong move.
                candidate_moves=[
                    {"rank": 1, "uci": "d1d5", "san": "Rxd5", "cp": 800, "mate": None, "pv": ["d1d5"]},
                    {"rank": 2, "uci": "d1d4", "san": "Rd4", "cp": 700, "mate": None, "pv": ["d1d4"]},
                ],
                analysis_version="3.1",
                engine="stockfish",
                engine_version="test-engine",
            )
        )
        session.add(
            MoveAnalysis(
                game_id=game_id,
                ply=3,
                move_number=2,
                mover="white",
                played_move_uci="d1d2",
                played_move_san="Rd2",
                fen_before=POSITION_B_FEN,
                fen_after="4k3/8/8/3q4/8/8/3R4/5K2 b - - 1 2",
                best_move_uci="d1d5",
                best_move_san="Rxd5",
                evaluation_before_cp=800,
                centipawn_loss=1200,
                played_eval_cp=-400,
                played_eval_source="same_search",
                classification="blunder",
                is_best_move=False,
                phase="endgame",
                depth=12,
                principal_variation=["d1d5"],
                analysis_version="3.1",
                engine="stockfish",
                engine_version="test-engine",
            )
        )


@pytest.fixture()
def training_setup(client: TestClient) -> dict:
    """An imported game with two seeded, engine-backed training exercises."""
    game_id = _import_game(client)
    player_id = _player_id(client, "Paul Morphy")
    _seed_analyses(game_id, int(player_id))
    return {"game_id": game_id, "player_id": player_id}


#: A real six-ply opening, so the seeded rows below are legal in sequence and the
#: replay formats have a genuine continuation to read.
REPLAY_LINE = ("e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4", "Nf6", "O-O", "Be7")
REPLAY_MISTAKE_PLY = 3         # White played Nf3; the stored best move is Nc3.
REPLAY_MISTAKE_BEST = "b1c3"
REPLAY_MISTAKE_LOSS = 220


def _seed_replay_analyses(game_id: str) -> None:
    """Store one per-move analysis per ply of a real, continuous game line."""
    import chess

    with app.state.session_factory.session_scope() as session:
        board = chess.Board()
        for index, san in enumerate(REPLAY_LINE):
            ply = index + 1
            move = board.parse_san(san)
            fen_before = board.fen()
            best = move.uci()
            loss = 0
            if ply == REPLAY_MISTAKE_PLY:
                best, loss = REPLAY_MISTAKE_BEST, REPLAY_MISTAKE_LOSS
            board.push(move)
            session.add(
                MoveAnalysis(
                    game_id=game_id,
                    ply=ply,
                    move_number=index // 2 + 1,
                    mover="white" if index % 2 == 0 else "black",
                    played_move_uci=move.uci(),
                    played_move_san=san,
                    fen_before=fen_before,
                    fen_after=board.fen(),
                    best_move_uci=best,
                    best_move_san=None,
                    evaluation_before_cp=40,
                    centipawn_loss=loss,
                    played_eval_cp=40 - loss,
                    played_eval_source="same_search",
                    classification="mistake" if loss else "best",
                    is_best_move=not loss,
                    phase="opening",
                    depth=14,
                    principal_variation=[best],
                    candidate_moves=[],
                    analysis_version="3.1",
                    engine="stockfish",
                    engine_version="test-engine",
                )
            )


@pytest.fixture()
def replay_setup(client: TestClient) -> dict:
    """An imported game whose stored analyses form a real, replayable line."""
    game_id = _import_game(client)
    player_id = _player_id(client, "Paul Morphy")
    _seed_replay_analyses(game_id)
    return {"game_id": game_id, "player_id": player_id}


def _generate(client: TestClient, setup: dict) -> dict:
    response = client.post(
        f"/api/training/games/{setup['game_id']}/generate",
        json={"player_id": setup["player_id"], "data_source": "personalized"},
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestTrainingMeta:
    def test_meta_documents_everything(self, client: TestClient) -> None:
        body = client.get("/api/training/meta").json()
        assert body["methodology_version"] == "8.2"
        assert "tactical" in body["categories"]
        assert len(body["session_kinds"]) == 7
        assert body["acceptance_policy"]["near_best_cp"] == 150
        assert body["eligibility_policy"]["min_gap_to_played_cp"] == 120
        assert "PERSONALIZED" in body["privacy"]


class TestTrainingGeneration:
    def test_generation_accepts_and_persists_exercises(self, client: TestClient, training_setup: dict) -> None:
        body = _generate(client, training_setup)
        # Two single-move puzzles for the two seeded mistakes, plus the whole-game
        # review of the second one (methodology 8.2). The review shares the
        # position with the puzzle but carries the game's real aftermath, so it
        # is stored as its own exercise.
        assert body["accepted"] == 3, body
        assert body["rejected"] == 0
        assert len(body["generated"]) == 3
        reviews = [e for e in body["generated"] if e["position_type"] == "what_went_wrong"]
        puzzles = [e for e in body["generated"] if e["position_type"] != "what_went_wrong"]
        assert len(puzzles) == 2 and len(reviews) == 1, body
        assert body["replay"]["emitted"] == ["what_went_wrong"]
        # The review states what was played, what was better, and what really
        # followed — all read from this game's stored analyses.
        review = reviews[0]
        assert review["replay_context"]["kind"] == "what_went_wrong"
        assert review["replay_context"]["played"]["uci"] == review["played_move"]["uci"]
        assert review["replay_context"]["better"]["uci"] == review["solution"]["uci"]
        assert review["replay_context"]["methodology_version"] == "8.2"
        assert review["replay_context"]["evaluation_trajectory"]
        # A category belongs to the position: the review reuses the puzzle's.
        puzzle = next(p for p in puzzles if p["origin"]["ply"] == review["origin"]["ply"])
        assert review["category"] == puzzle["category"]
        # The MultiPV candidate within tolerance became a real acceptable move.
        with_candidates = next((e for e in puzzles if e["acceptable_moves"]), None)
        assert with_candidates is not None, body
        assert with_candidates["acceptable_moves"].get("d1d4") == "Rd4"
        for entry in body["generated"]:
            assert entry["data_source"] == "personalized"
            assert entry["origin"]["game_id"] == training_setup["game_id"]
            assert entry["origin"]["ply"] in (1, 3)
            assert entry["category"] in ("tactical", "positional", "calculation", "opening", "endgame")

    def test_generation_is_idempotent_on_duplicate_positions(self, client: TestClient, training_setup: dict) -> None:
        first = _generate(client, training_setup)
        assert first["accepted"] == 3
        # Re-running without regeneration must not create duplicates.
        before = client.get(f"/api/training/positions?player_id={training_setup['player_id']}").json()["count"]
        second = _generate(client, training_setup)
        assert second["accepted"] == 0
        assert second["replay"]["emitted"] == []
        after = client.get(f"/api/training/positions?player_id={training_setup['player_id']}").json()["count"]
        assert after == before == 3
        # The duplicate refusal carries a real reason.
        assert any(count for count in second["reasons"].values())

    def test_generation_without_analysis_is_honest(self, client: TestClient) -> None:
        game_id = _import_game(client)
        player_id = _player_id(client, "Paul Morphy")
        body = client.post(
            f"/api/training/games/{game_id}/generate", json={"player_id": player_id}
        ).json()
        assert body["accepted"] == 0
        assert body["seen"] == 0
        assert "no stored move analysis" in body["note"]

    def test_generation_rejects_a_non_participant(self, client: TestClient, training_setup: dict) -> None:
        # A real, tracked player who did not appear in THIS game must be refused:
        # training must never be generated for a player who did not play.
        other = client.post(
            "/api/games/import",
            json={"pgn_text": OPERA_GAME_PGN.replace('"Paul Morphy"', '"Someone Else"'), "run_analysis": False},
        ).json()
        outsider_id = _player_id(client, "Someone Else")
        assert other["game_id"] != training_setup["game_id"]
        response = client.post(
            f"/api/training/games/{training_setup['game_id']}/generate",
            json={"player_id": outsider_id},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"


class TestWholeGameReplayAPI:
    """Methodology 8.2, end to end: whole-game exercises from stored analyses."""

    def test_review_carries_the_real_aftermath(self, client: TestClient, replay_setup: dict) -> None:
        body = _generate(client, replay_setup)
        reviews = [
            entry for entry in body["generated"] if entry["position_type"] == "what_went_wrong"
        ]
        assert reviews, body
        # The same game also yields a reconstruction: the player handled the
        # plies after the mistake well enough to reproduce them.
        assert set(body["replay"]["emitted"]) == {"what_went_wrong", "reconstruction"}
        review = reviews[0]
        assert review["origin"]["ply"] == REPLAY_MISTAKE_PLY
        context = review["replay_context"]
        assert context["methodology_version"] == "8.2"
        assert context["played"]["uci"] == review["played_move"]["uci"]
        assert context["better"]["uci"] == REPLAY_MISTAKE_BEST
        # The aftermath is the game's own following plies, in order.
        aftermath = [entry["uci"] for entry in context["actual_continuation"]]
        assert aftermath[:3] == ["b8c6", "f1b5", "a7a6"]
        assert len(aftermath) == len(REPLAY_LINE) - REPLAY_MISTAKE_PLY
        assert context["played"]["loss_cp"] == REPLAY_MISTAKE_LOSS

    def test_a_review_and_its_puzzle_share_position_and_category(
        self, client: TestClient, replay_setup: dict
    ) -> None:
        body = _generate(client, replay_setup)
        review = next(
            entry for entry in body["generated"] if entry["position_type"] == "what_went_wrong"
        )
        puzzle = next(
            entry
            for entry in body["generated"]
            if entry["position_type"] != "what_went_wrong"
            and entry["origin"]["ply"] == REPLAY_MISTAKE_PLY
        )
        assert review["fen"] == puzzle["fen"]
        assert review["category"] == puzzle["category"]
        # They are distinct rows, each with its own traceable provenance.
        assert review["id"] != puzzle["id"]
        assert review["solution"]["uci"] == puzzle["solution"]["uci"] == REPLAY_MISTAKE_BEST

    def test_reconstruction_is_graded_against_the_stored_line(
        self, client: TestClient, replay_setup: dict
    ) -> None:
        body = _generate(client, replay_setup)
        exercise = next(
            entry for entry in body["generated"] if entry["position_type"] == "reconstruction"
        )
        line = exercise["continuation_line"]
        assert line
        # After the solution the line alternates: the opponent's stored reply,
        # then the move the solver must find.
        expected = line[1::2]
        assert len(expected) == exercise["replay_context"]["solver_moves"]
        response = client.post(
            f"/api/training/positions/{exercise['id']}/continue",
            json={"player_id": replay_setup["player_id"], "moves": expected},
        )
        assert response.status_code == 200, response.text
        graded = response.json()
        assert graded["type"] == "reconstruction"
        assert graded["outcome"] == "correct"
        assert graded["correct_moves"] == graded["expected_moves"] == len(expected)
        # An illegal submission is reported, never silently skipped.
        bad = client.post(
            f"/api/training/positions/{exercise['id']}/continue",
            json={"player_id": replay_setup["player_id"], "moves": ["a1a8"]},
        ).json()
        assert bad["outcome"] == "incorrect"
        assert bad["moves"][0]["legal"] is False

    def test_replay_can_be_switched_off(self, client: TestClient, replay_setup: dict) -> None:
        response = client.post(
            f"/api/training/games/{replay_setup['game_id']}/generate",
            json={"player_id": replay_setup["player_id"], "include_replay": False},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["replay"]["emitted"] == []
        assert all(
            entry["position_type"] != "what_went_wrong" for entry in body["generated"]
        )


class TestTrainingLibrary:
    def test_library_lists_exercises(self, client: TestClient, training_setup: dict) -> None:
        _generate(client, training_setup)
        body = client.get(f"/api/training/positions?player_id={training_setup['player_id']}").json()
        assert body["count"] == 3
        for entry in body["positions"]:
            assert "solution" not in entry  # withheld before an attempt
            assert entry["difficulty_factors"]

    def test_game_link_lists_only_that_games_exercises(self, client: TestClient, training_setup: dict) -> None:
        _generate(client, training_setup)
        body = client.get(
            f"/api/training/games/{training_setup['game_id']}?player_id={training_setup['player_id']}"
        ).json()
        assert body["count"] == 3
        assert all(p["origin"]["game_id"] == training_setup["game_id"] for p in body["positions"])

    def test_single_position_withholds_solution(self, client: TestClient, training_setup: dict) -> None:
        body = _generate(client, training_setup)
        position_id = body["generated"][0]["id"]
        detail = client.get(
            f"/api/training/positions/{position_id}?player_id={training_setup['player_id']}"
        ).json()
        assert "solution" not in detail
        assert "principal_variation" not in detail
        assert detail["fen"]

    def test_foreign_player_cannot_read_position(self, client: TestClient, training_setup: dict) -> None:
        body = _generate(client, training_setup)
        position_id = body["generated"][0]["id"]
        # A different tracked player (the opponent) must not see this exercise.
        opponent_id = _player_id(client, "Duke Karl / Count Isouard")
        response = client.get(f"/api/training/positions/{position_id}?player_id={opponent_id}")
        assert response.status_code == 404
        assert (
            client.get(
                f"/api/training/positions/{position_id}/explanation?player_id={opponent_id}"
            ).status_code
            == 404
        )


class TestTrainingHintsAndReveal:
    def test_hints_are_progressive_and_never_the_solution(self, client: TestClient, training_setup: dict) -> None:
        body = _generate(client, training_setup)
        position_id = body["generated"][0]["id"]
        hints = client.get(f"/api/training/positions/{position_id}/hints").json()
        assert hints["policy_version"] == "8.0"
        assert hints["revealed"] == []
        assert hints["hints"]
        assert hints["remaining"] == len(hints["hints"])
        step = client.get(f"/api/training/positions/{position_id}/hints?hint_index=1").json()
        assert step["revealed"] == hints["hints"][:1]

    def test_explanation_is_evidence_based_and_needs_no_llm(self, client: TestClient, training_setup: dict) -> None:
        body = _generate(client, training_setup)
        position_id = body["generated"][0]["id"]
        expl = client.get(
            f"/api/training/positions/{position_id}/explanation?player_id={training_setup['player_id']}"
        ).json()
        assert expl["solution"]["uci"] == "d1d5"
        assert "Rxd5" in expl["reason"]
        assert expl["board_facts"]  # Rxd5 is a capture, measured from the board
        assert any("captures a piece" in fact for fact in expl["board_facts"])
        assert expl["source"]["engine"]
        assert expl["methodology_version"] == "8.2"

    def test_reveal_returns_the_verified_solution(self, client: TestClient, training_setup: dict) -> None:
        body = _generate(client, training_setup)
        position_id = body["generated"][0]["id"]
        reveal = client.get(f"/api/training/positions/{position_id}/solution").json()
        assert reveal["solution"]["uci"] == "d1d5"
        assert reveal["principal_variation"] == ["d1d5"]
        assert reveal["played_move"]["uci"] == "d1d2"


class TestTrainingAttempts:
    def _position_id(self, client: TestClient, setup: dict, *, index: int) -> int:
        body = _generate(client, setup)
        return body["generated"][index]["id"]

    def test_recorded_equivalent_is_accepted(self, client: TestClient, training_setup: dict) -> None:
        # d1d4 (from the stored MultiPV window) must grade correct, not wrong.
        position_id = self._position_id(client, training_setup, index=0)
        body = client.post(
            f"/api/training/positions/{position_id}/attempt",
            json={"player_id": training_setup["player_id"], "submitted_uci": "d1d4"},
        ).json()
        assert body["outcome"] == "correct"
        assert "equivalent" in body["reason"]

    def test_revealed_attempt_returns_the_acceptable_moves(
        self, client: TestClient, training_setup: dict
    ) -> None:
        # The revealed alternatives are read from the same stored row the grader
        # used. Before this was wired, `reveal=True` referenced an out-of-scope
        # name and the whole request failed — a path no test exercised.
        position_id = self._position_id(client, training_setup, index=0)
        response = client.post(
            f"/api/training/positions/{position_id}/attempt",
            json={
                "player_id": training_setup["player_id"],
                "submitted_uci": "d1d5",
                "reveal": True,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["reveal"] is True
        assert body["acceptable_moves"].get("d1d4") == "Rd4"

    def test_correct_attempt(self, client: TestClient, training_setup: dict) -> None:
        position_id = self._position_id(client, training_setup, index=0)
        response = client.post(
            f"/api/training/positions/{position_id}/attempt",
            json={"player_id": training_setup["player_id"], "submitted_uci": "d1d5"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["outcome"] == "correct"
        assert body["correct"] is True
        assert body["state"] == "learning"
        assert body["streak"] == 1
        assert body["next_review_at"]

    def test_near_best_attempt(self, client: TestClient, training_setup: dict) -> None:
        position_id = self._position_id(client, training_setup, index=0)
        body = client.post(
            f"/api/training/positions/{position_id}/attempt",
            json={"player_id": training_setup["player_id"], "submitted_uci": "d1d2"},
        ).json()
        assert body["outcome"] == "near_best"
        assert body["evaluation_delta_cp"] == 130
        assert body["streak"] == 0

    def test_incorrect_attempt_when_the_played_move_was_a_blunder(
        self, client: TestClient, training_setup: dict
    ) -> None:
        position_id = self._position_id(client, training_setup, index=1)
        body = client.post(
            f"/api/training/positions/{position_id}/attempt",
            json={"player_id": training_setup["player_id"], "submitted_uci": "d1d2"},
        ).json()
        assert body["outcome"] == "incorrect"
        assert body["evaluation_delta_cp"] == 1200
        assert body["state"] == "failed"  # a NEW exercise that fails outright

    def test_illegal_move_is_rejected(self, client: TestClient, training_setup: dict) -> None:
        position_id = self._position_id(client, training_setup, index=0)
        response = client.post(
            f"/api/training/positions/{position_id}/attempt",
            json={"player_id": training_setup["player_id"], "submitted_uci": "e1e9"},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    def test_unknown_submitter_is_404(self, client: TestClient, training_setup: dict) -> None:
        position_id = self._position_id(client, training_setup, index=0)
        response = client.post(
            f"/api/training/positions/{position_id}/attempt",
            json={"player_id": "99999", "submitted_uci": "d1d5"},
        )
        assert response.status_code == 404

    def test_every_attempt_is_stored_forever(self, client: TestClient, training_setup: dict) -> None:
        position_id = self._position_id(client, training_setup, index=0)
        for move in ("d1d2", "d1d5", "d1d2"):
            client.post(
                f"/api/training/positions/{position_id}/attempt",
                json={"player_id": training_setup["player_id"], "submitted_uci": move},
            )
        history = client.get(
            f"/api/training/positions/{position_id}/attempts?player_id={training_setup['player_id']}"
        ).json()
        assert history["count"] == 3
        assert {row["submitted_uci"] for row in history["attempts"]} == {"d1d2", "d1d5"}


class TestTrainingReviewAndProgress:
    def test_review_queue_shape(self, client: TestClient, training_setup: dict) -> None:
        _generate(client, training_setup)
        body = client.get(f"/api/training/review-queue?player_id={training_setup['player_id']}").json()
        assert body["count"] == 0  # nothing is scheduled until an attempt is made
        assert "now" in body

    def test_progress_has_sample_sizes(self, client: TestClient, training_setup: dict) -> None:
        gen = _generate(client, training_setup)
        position_id = gen["generated"][0]["id"]
        client.post(
            f"/api/training/positions/{position_id}/attempt",
            json={"player_id": training_setup["player_id"], "submitted_uci": "d1d5"},
        )
        body = client.get(f"/api/training/progress?player_id={training_setup['player_id']}").json()
        assert body["attempts_total"] == 1
        assert body["correct_total"] == 1
        assert body["library_size"] == 3
        assert body["by_category"]
        for entry in body["by_category"].values():
            assert "decided" in entry and entry["decided"] >= 1

    def test_recommendations_refuse_to_guess_without_data(self, client: TestClient, training_setup: dict) -> None:
        _generate(client, training_setup)
        body = client.get(f"/api/training/recommendations?player_id={training_setup['player_id']}").json()
        # Only two exercises and no attempts: no evidence, so no recommendations.
        assert body["opportunities"] == []
        assert body["categories_without_evidence"]
        assert "too little data" in body["evidence_policy"]


class TestTrainingSessions:
    def test_session_lifecycle_and_resume(self, client: TestClient, training_setup: dict) -> None:
        _generate(client, training_setup)
        started = client.post(
            "/api/training/sessions",
            json={"player_id": training_setup["player_id"], "kind": "quick"},
        ).json()
        assert started["kind"] == "quick"
        assert started["status"] == "active"
        assert started["planned"] == 2
        assert started["remaining_position_ids"]

        session_id = started["id"]
        resumed = client.get(f"/api/training/sessions/{session_id}?player_id={training_setup['player_id']}").json()
        assert resumed["current"]["id"] == started["remaining_position_ids"][0]
        assert "solution" not in resumed["current"]

        # Solve the first exercise inside the session.
        solution = client.get(
            f"/api/training/positions/{resumed['current']['id']}/solution"
        ).json()["solution"]["uci"]
        attempt = client.post(
            f"/api/training/positions/{resumed['current']['id']}/attempt",
            json={"player_id": training_setup["player_id"], "submitted_uci": solution, "session_id": session_id},
        ).json()
        assert attempt["outcome"] == "correct"

        after = client.get(f"/api/training/sessions/{session_id}?player_id={training_setup['player_id']}").json()
        assert after["completed"] == 1
        assert after["counts"]["correct"] == 1
        assert after["remaining"] == 1

        cancelled = client.post(
            f"/api/training/sessions/{session_id}/cancel?player_id={training_setup['player_id']}"
        ).json()
        assert cancelled["status"] == "cancelled"

    def test_category_session_plans_exactly_its_category(self, client: TestClient, training_setup: dict) -> None:
        _generate(client, training_setup)
        library = client.get(
            f"/api/training/positions?player_id={training_setup['player_id']}"
        ).json()["positions"]
        by_id = {p["id"]: p for p in library}
        for kind in ("tactical", "endgame"):
            # One position can exist in two formats (a puzzle and its review); a
            # session serves a position once, preferring the richer format.
            expected = len(
                {
                    " ".join(p["fen"].split()[:4])
                    for p in library
                    if p["category"] == kind
                }
            )
            body = client.post(
                "/api/training/sessions",
                json={"player_id": training_setup["player_id"], "kind": kind},
            ).json()
            # A category session is filled from that category only, never padded.
            assert body["planned"] == expected, (kind, body)
            planned_fens = [
                " ".join(by_id[pid]["fen"].split()[:4])
                for pid in body["planned_position_ids"]
            ]
            assert len(planned_fens) == len(set(planned_fens)), body

    def test_unknown_session_kind_is_rejected(self, client: TestClient, training_setup: dict) -> None:
        response = client.post(
            "/api/training/sessions",
            json={"player_id": training_setup["player_id"], "kind": "not_a_kind"},
        )
        assert response.status_code == 422


class TestTrainingAgentTools:
    """The Phase 7 agent can reach the Phase 8 engine, read-only, with real evidence."""

    def _toolbox(self, client: TestClient):
        from argus.ai_agent.tools import build_agent_toolbox
        from argus_api.services import agent_service

        return client.app.state.session_factory, build_agent_toolbox, agent_service

    def test_training_tools_answer_from_stored_exercises(self, client: TestClient, training_setup: dict) -> None:
        from argus.ai_agent.core.context import AgentContext

        generated = _generate(client, training_setup)
        position_id = generated["generated"][0]["id"]
        solution_uci = generated["generated"][0]["solution"]["uci"]

        factory, build_toolbox, agent_service = self._toolbox(client)
        with factory.session_scope() as session:
            providers = agent_service.build_providers(session, engine=client.app.state.engine)
            box = build_toolbox(providers)
            ctx = AgentContext(player_id=training_setup["player_id"])

            # Recommendations and progress from real, measured data.
            rec = box.call("get_training_recommendations", {}, ctx)
            assert rec.ok, rec.error
            assert "opportunities" in rec.data
            progress = box.call("get_training_progress", {}, ctx)
            assert progress.ok, progress.error
            assert progress.data["library_size"] == 3
            queue = box.call("get_review_queue", {}, ctx)
            assert queue.ok, queue.error

            # The puzzle tool never returns the answer.
            puzzle = box.call("generate_training_position", {}, ctx)
            assert puzzle.ok, puzzle.error
            assert "solution" not in puzzle.data["position"]

            # The explanation tool does, because that is its explicit job.
            explain = box.call("generate_training_explanation", {"position_id": position_id}, ctx)
            assert explain.ok, explain.error
            assert explain.data["solution"]["uci"] == solution_uci

            # Evaluation is read-only: it grades but stores nothing.
            evaluation = box.call(
                "evaluate_training_attempt",
                {"position_id": position_id, "submitted_uci": solution_uci},
                ctx,
            )
            assert evaluation.ok, evaluation.error
            assert evaluation.data["outcome"] == "correct"
            assert evaluation.data["persisted"] is False
            history = client.get(
                f"/api/training/positions/{position_id}/attempts?player_id={training_setup['player_id']}"
            ).json()
            assert history["count"] == 0  # the agent did not write an attempt

            # The game → training link is reachable from the agent too.
            ctx_game = AgentContext(
                player_id=training_setup["player_id"],
                active_game_id=training_setup["game_id"],
                available_game_ids=[training_setup["game_id"]],
            )
            from_game = box.call("get_training_from_game", {}, ctx_game)
            assert from_game.ok, from_game.error
            assert from_game.data["count"] == 3

    def test_coach_ask_with_a_training_question_answers_without_an_llm(
        self, client: TestClient, training_setup: dict
    ) -> None:
        _generate(client, training_setup)
        response = client.post(
            "/api/coach/ask",
            json={
                "question": "What should I practise today?",
                "player_id": training_setup["player_id"],
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        # An honest answer exists even with no LLM configured.
        assert payload["message"]
        assert payload["deterministic"] is True


@pytest.mark.engine
class TestTrainingWithRealEngine:
    """End-to-end on the real pipeline: import → analyze → generate → solve."""

    @pytest.fixture()
    def analyzed_setup(self, client: TestClient) -> dict:
        game_id = _import_game(client)
        player_id = _player_id(client, "Paul Morphy")
        started = client.post(f"/api/analysis/games/{game_id}", json={"depth": 6, "multipv": 2})
        assert started.status_code == 202, started.text
        progress = client.get(f"/api/analysis/games/{game_id}/progress").json()
        assert progress["status"] == "completed", progress
        return {"game_id": game_id, "player_id": player_id}

    def test_real_analysis_generates_verified_exercises(self, client: TestClient, analyzed_setup: dict) -> None:
        body = client.post(
            f"/api/training/games/{analyzed_setup['game_id']}/generate",
            json={"player_id": analyzed_setup["player_id"]},
        ).json()
        assert body["seen"] > 0
        # The Opera Game contains real mistakes; at least one must qualify.
        assert body["accepted"] >= 1, body
        position_id = body["generated"][0]["id"]
        reveal = client.get(f"/api/training/positions/{position_id}/solution").json()
        # The solution's provenance is the real stored analysis, not a guess.
        assert reveal["engine"] == "stockfish"
        assert reveal["depth"] >= 6
        assert reveal["analysis_version"]
        # Solving the stored solution is graded correct through the real path.
        attempt = client.post(
            f"/api/training/positions/{position_id}/attempt",
            json={
                "player_id": analyzed_setup["player_id"],
                "submitted_uci": reveal["solution"]["uci"],
            },
        ).json()
        assert attempt["outcome"] == "correct"


def _seed_continue_analysis(game_id: str) -> None:
    """Seed one analysis row whose stored PV is long enough for CONTINUE_LINE."""
    with app.state.session_factory.session_scope() as session:
        session.add(
            MoveAnalysis(
                game_id=game_id,
                ply=1,
                move_number=1,
                mover="white",
                played_move_uci="d2d4",
                played_move_san="d4",
                fen_before="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                fen_after="rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq - 0 1",
                best_move_uci="e2e4",
                best_move_san="e4",
                evaluation_before_cp=30,
                centipawn_loss=130,
                played_eval_cp=-100,
                played_eval_source="same_search",
                classification="mistake",
                is_best_move=False,
                phase="opening",
                depth=12,
                # The engine's own line: a forced reply and a continuation to find.
                principal_variation=["e2e4", "e7e5", "g1f3", "b8c6"],
                analysis_version="3.1",
                engine="stockfish",
                engine_version="test-engine",
            )
        )


class TestContinueLine:
    """CONTINUE_LINE: a real multi-ply exercise graded against the stored PV."""

    @pytest.fixture()
    def continue_setup(self, client: TestClient) -> dict:
        game_id = _import_game(client)
        player_id = _player_id(client, "Paul Morphy")
        _seed_continue_analysis(game_id)
        return {"game_id": game_id, "player_id": player_id}

    def _generate(self, client: TestClient, setup: dict) -> dict:
        response = client.post(
            f"/api/training/games/{setup['game_id']}/generate",
            json={"player_id": setup["player_id"], "data_source": "personalized"},
        )
        assert response.status_code == 200, response.text
        return response.json()

    def test_long_pv_becomes_a_continue_line_exercise(self, client: TestClient, continue_setup: dict) -> None:
        body = self._generate(client, continue_setup)
        line_exercises = [e for e in body["generated"] if e["position_type"] == "continue_line"]
        assert line_exercises, body
        exercise = line_exercises[0]
        # The generation summary echoes the stored exercise (like every other
        # type); the continuation is the stored PV after the solution.
        assert exercise["continuation_line"] == ["e7e5", "g1f3", "b8c6"]
        reveal = client.get(f"/api/training/positions/{exercise['id']}/solution").json()
        assert reveal["continuation_line"] == ["e7e5", "g1f3", "b8c6"]
        # The serving view (no reveal) withholds it.
        served = client.get(f"/api/training/positions/{exercise['id']}").json()
        assert "continuation_line" not in served

    def test_continuation_grading_is_move_by_move(self, client: TestClient, continue_setup: dict) -> None:
        body = self._generate(client, continue_setup)
        exercise = next(e for e in body["generated"] if e["position_type"] == "continue_line")
        # The solver plays the engine's continuation move: correct.
        right = client.post(
            f"/api/training/positions/{exercise['id']}/continue",
            json={"player_id": continue_setup["player_id"], "moves": ["g1f3"]},
        ).json()
        assert right["outcome"] == "correct"
        assert right["correct_moves"] == 1 and right["expected_moves"] == 1
        # A different legal move is graded incorrect, not silently accepted.
        wrong = client.post(
            f"/api/training/positions/{exercise['id']}/continue",
            json={"player_id": continue_setup["player_id"], "moves": ["a2a3"]},
        ).json()
        assert wrong["outcome"] == "incorrect"
        assert wrong["moves"][0]["played_engine_move"] is False

    def test_continue_endpoint_refuses_a_plain_exercise(self, client: TestClient, continue_setup: dict) -> None:
        # A find_best_move exercise is not a line exercise.
        body = self._generate(client, continue_setup)
        plain = [e for e in body["generated"] if e["position_type"] != "continue_line"]
        if not plain:
            pytest.skip("no non-line exercise generated")
        response = client.post(
            f"/api/training/positions/{plain[0]['id']}/continue",
            json={"player_id": continue_setup["player_id"], "moves": ["e2e4"]},
        )
        assert response.status_code == 422, response.text


# ---------------------------------------------------------------------------
# Opponent preparation (Phase 9): exercises from the opponent's own games
# ---------------------------------------------------------------------------

PREP_START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
PREP_AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
PREP_AFTER_E4_E5 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"


def _seed_two_sided_opening(game_id: str) -> None:
    """Seed White's characteristic moves *and* Black's reply plies.

    White (the opponent we prepare against) plays 1.e4 and 2.Nf3 in every game.
    Black (the preparing player) faced 1.e4 and replied with a mistake at ply 2 —
    that reply position is the preparation exercise.
    """
    rows = [
        # White: the opponent's characteristic moves.
        dict(ply=1, move_number=1, mover="white", played_move_uci="e2e4", played_move_san="e4",
             fen_before=PREP_START, fen_after=PREP_AFTER_E4, best_move_uci="e2e4", best_move_san="e4",
             evaluation_before_cp=30, centipawn_loss=20, played_eval_cp=30, classification="good",
             is_best_move=True, phase="opening"),
        dict(ply=3, move_number=2, mover="white", played_move_uci="g1f3", played_move_san="Nf3",
             fen_before=PREP_AFTER_E4_E5, fen_after=PREP_AFTER_E4_E5, best_move_uci="g1f3",
             best_move_san="Nf3", evaluation_before_cp=30, centipawn_loss=15, played_eval_cp=30,
             classification="good", is_best_move=True, phase="opening"),
        # Black: the reply position after White's 1.e4, where Black erred.
        dict(ply=2, move_number=1, mover="black", played_move_uci="c7c5", played_move_san="c5",
             fen_before=PREP_AFTER_E4, fen_after=PREP_AFTER_E4_E5, best_move_uci="e7e5",
             best_move_san="e5", evaluation_before_cp=30, centipawn_loss=130, played_eval_cp=-100,
             classification="mistake", is_best_move=False, phase="opening"),
        # Black: the reply after 2.Nf3, where Black played the best move (skipped).
        dict(ply=4, move_number=2, mover="black", played_move_uci="b8c6", played_move_san="Nc6",
             fen_before=PREP_AFTER_E4_E5, fen_after=PREP_AFTER_E4_E5, best_move_uci="b8c6",
             best_move_san="Nc6", evaluation_before_cp=30, centipawn_loss=10, played_eval_cp=30,
             classification="good", is_best_move=True, phase="opening"),
    ]
    with app.state.session_factory.session_scope() as session:
        game = session.get(Game, game_id)
        if game is not None:
            game.analysis_status = "analyzed"
        for row in rows:
            session.add(
                MoveAnalysis(
                    game_id=game_id,
                    analysis_version="3.1",
                    engine="stockfish",
                    engine_version="test-engine",
                    depth=12,
                    principal_variation=[row["best_move_uci"]],
                    played_eval_source="same_search",
                    **row,
                )
            )


class TestOpponentPreparation:
    """Preparation exercises are built from the opponent's stored games."""

    @pytest.fixture()
    def prep_setup(self, client: TestClient) -> dict:
        game_ids = [_import_game(client) for _ in range(2)]
        for game_id in game_ids:
            _seed_two_sided_opening(game_id)
        return {
            "game_ids": game_ids,
            "opponent_id": _player_id(client, "Paul Morphy"),
            "player_id": _player_id(client, "Duke Karl / Count Isouard"),
        }

    def test_characteristic_move_yields_a_preparation_exercise(
        self, client: TestClient, prep_setup: dict
    ) -> None:
        response = client.post(
            f"/api/training/opponents/{prep_setup['opponent_id']}/prepare",
            json={"player_id": prep_setup["player_id"]},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["data_source"] == "opponent_preparation"
        assert body["accepted"] >= 1, body
        exercise = body["generated"][0]
        # It is preparation, not the player's own mistake.
        assert exercise["is_opponent_preparation"] is True
        assert exercise["is_personalized"] is False
        assert "Opponent preparation" in exercise["source_reason"]
        # The solution is the engine's stored reply at the next ply.
        assert exercise["solution"]["uci"] == "e7e5"
        # It traces back to the opponent's game.
        assert exercise["origin"]["game_id"] in prep_setup["game_ids"]

    def test_exercise_is_owned_by_the_preparing_player(
        self, client: TestClient, prep_setup: dict
    ) -> None:
        client.post(
            f"/api/training/opponents/{prep_setup['opponent_id']}/prepare",
            json={"player_id": prep_setup["player_id"]},
        )
        mine = client.get(
            f"/api/training/positions?player_id={prep_setup['player_id']}"
        ).json()
        assert mine["count"] >= 1
        assert all(p["is_opponent_preparation"] for p in mine["positions"])
        theirs = client.get(
            f"/api/training/positions?player_id={prep_setup['opponent_id']}"
        ).json()
        assert theirs["count"] == 0

    def test_high_occurrence_gate_refuses_to_prepare(
        self, client: TestClient, prep_setup: dict
    ) -> None:
        body = client.post(
            f"/api/training/opponents/{prep_setup['opponent_id']}/prepare",
            json={"player_id": prep_setup["player_id"], "min_occurrences": 50},
        ).json()
        assert body["accepted"] == 0
        assert "characteristic" in body["note"]

    def test_unknown_opponent_is_404(self, client: TestClient, prep_setup: dict) -> None:
        response = client.post(
            "/api/training/opponents/999999/prepare",
            json={"player_id": prep_setup["player_id"]},
        )
        assert response.status_code == 404

    def test_preparation_is_deterministic(self, client: TestClient, prep_setup: dict) -> None:
        first = client.post(
            f"/api/training/opponents/{prep_setup['opponent_id']}/prepare",
            json={"player_id": prep_setup["player_id"]},
        ).json()
        second = client.post(
            f"/api/training/opponents/{prep_setup['opponent_id']}/prepare",
            json={"player_id": prep_setup["player_id"]},
        ).json()
        # The same position is never stored twice; the second run accepts nothing.
        assert first["accepted"] >= 1
        assert second["accepted"] == 0
