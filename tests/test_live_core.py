"""Phase 12 unit tests: the live-game domain, its clock, and fair play.

These pin the properties that make live play safe, none of which need a database,
a WebSocket or an engine:

* the state machine refuses illegal transitions;
* a move is validated server-side (turn, legality, version) and the clock is
  charged from server time, not the client's;
* a flag fall ends the game with the right result and nobody can move after it;
* the fair-play clamp forces competitive games to no analysis and a hint-only
  coach, while training and sandbox games may enable analysis;
* a reconnecting client can tell whether it missed an event.
"""

from __future__ import annotations

import chess
from datetime import datetime, timedelta, timezone

import pytest

from argus.live import (
    AnalysisMode,
    ClockConfig,
    ClockState,
    CoachLevel,
    EventType,
    GameMode,
    GameResult,
    LiveGame,
    LiveGameConfig,
    LiveGameError,
    LivePlayer,
    LiveStatus,
    Side,
    apply_move,
    build_state,
    claimable_draws,
    coach_permissions,
    coaching_reply,
    detect_terminal,
    events_after,
    is_engine_request_permitted,
    refuse_if_not_permitted,
    resolve_analysis_mode,
)

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def new_game(*, mode: GameMode = GameMode.PRIVATE_MATCH, clock: ClockConfig | None = None) -> LiveGame:
    state = build_state(
        game_id="live-1",
        config=LiveGameConfig(mode=mode, clock=clock or ClockConfig(base_ms=300_000, increment_ms=0)),
        current_fen=chess.STARTING_FEN,
    )
    game = LiveGame(state=state)
    game.join(LivePlayer(player_id=1, name="Alice", side=Side.WHITE))
    game.join(LivePlayer(player_id=2, name="Bob", side=Side.BLACK))
    return game


# ---------------------------------------------------------------------------
# state machine
# ---------------------------------------------------------------------------


class TestStateMachine:
    def test_join_seats_two_players_and_readies_the_game(self) -> None:
        state = build_state(
            game_id="g", config=LiveGameConfig(mode=GameMode.PRIVATE_MATCH),
            current_fen=chess.STARTING_FEN,
        )
        game = LiveGame(state=state)
        assert game.state.status is LiveStatus.WAITING
        game.join(LivePlayer(player_id=1, name="A", side=Side.WHITE))
        assert game.state.status is LiveStatus.WAITING  # one seat alone is not ready
        transition = game.join(LivePlayer(player_id=2, name="B", side=Side.BLACK))
        assert transition.state.status is LiveStatus.READY
        assert [event.event_type for event in transition.events] == [EventType.GAME_READY]

    def test_a_third_player_is_refused(self) -> None:
        game = new_game()
        with pytest.raises(LiveGameError) as exc:
            game.join(LivePlayer(player_id=3, name="C", side=Side.WHITE))
        assert exc.value.code == "game_full"

    def test_start_requires_two_players(self) -> None:
        state = build_state(
            game_id="g", config=LiveGameConfig(mode=GameMode.PRIVATE_MATCH),
            current_fen=chess.STARTING_FEN,
        )
        game = LiveGame(state=state)
        game.join(LivePlayer(player_id=1, name="A", side=Side.WHITE))
        with pytest.raises(LiveGameError) as exc:
            game.start()
        assert exc.value.code == "not_enough_players"

    def test_an_illegal_transition_is_refused(self) -> None:
        game = new_game()
        game.abort()
        assert game.state.status is LiveStatus.ABORTED
        # A terminal game cannot be started.
        with pytest.raises(LiveGameError) as exc:
            game.start()
        assert exc.value.code == "illegal_transition"

    def test_finishing_is_terminal_and_stops_the_clock(self) -> None:
        game = new_game()
        game.start(now=T0)
        game.resign(player_id=1, now=T0)
        assert game.state.status is LiveStatus.RESIGNED
        assert game.state.result is GameResult.BLACK_WIN
        assert game.state.clock.running is False
        with pytest.raises(LiveGameError):
            game.play(player_id=2, uci="e7e5", now=T0)


# ---------------------------------------------------------------------------
# the move pipeline
# ---------------------------------------------------------------------------


class TestMovePipeline:
    def test_a_legal_move_is_applied_with_san_and_clock_events(self) -> None:
        game = new_game()
        game.start(now=T0)
        transition = game.play(player_id=1, uci="e2e4", now=T0 + timedelta(seconds=3))
        assert transition.move is not None and transition.move.san == "e4"
        assert transition.state.side_to_move is Side.BLACK
        assert transition.state.version > 0
        kinds = [event.event_type for event in transition.events]
        assert EventType.MOVE_MADE in kinds and EventType.CLOCK_UPDATED in kinds
        # White was charged 3s: 300_000 - 3000.
        assert transition.state.clock.white_ms == 297_000

    def test_an_illegal_move_is_refused(self) -> None:
        game = new_game()
        game.start(now=T0)
        with pytest.raises(LiveGameError) as exc:
            game.play(player_id=1, uci="a1a8", now=T0)
        assert exc.value.code == "illegal_move"

    def test_playing_out_of_turn_is_refused(self) -> None:
        game = new_game()
        game.start(now=T0)
        with pytest.raises(LiveGameError) as exc:
            game.play(player_id=2, uci="e7e5", now=T0)
        assert exc.value.code == "not_your_turn"

    def test_a_stale_version_is_refused_and_reports_the_current_one(self) -> None:
        game = new_game()
        game.start(now=T0)
        game.play(player_id=1, uci="e2e4", now=T0)
        with pytest.raises(LiveGameError) as exc:
            game.play(player_id=2, uci="e7e5", expected_version=1, now=T0)
        assert exc.value.code == "stale_version"
        assert exc.value.details["current_version"] == game.state.version

    def test_a_non_player_cannot_move(self) -> None:
        game = new_game()
        game.start(now=T0)
        with pytest.raises(LiveGameError) as exc:
            game.play(player_id=99, uci="e2e4", now=T0)
        assert exc.value.code == "not_a_player"

    def test_the_client_fen_is_never_trusted(self) -> None:
        # There is no parameter for a client FEN at all; the board is rebuilt from
        # the stored FEN, so a forged position cannot be injected.
        game = new_game()
        game.start(now=T0)
        game.play(player_id=1, uci="e2e4", now=T0)
        assert game.board.fen() == game.state.current_fen


# ---------------------------------------------------------------------------
# clock
# ---------------------------------------------------------------------------


class TestClock:
    def test_remaining_is_derived_from_server_time(self) -> None:
        clock = ClockState(white_ms=60_000, black_ms=60_000).start(Side.WHITE, now=T0)
        from argus.live import remaining_ms

        assert remaining_ms(clock, Side.WHITE, now=T0 + timedelta(seconds=10)) == 50_000
        # The opponent's bank is untouched while it is not their turn.
        assert remaining_ms(clock, Side.BLACK, now=T0 + timedelta(seconds=10)) == 60_000

    def test_increment_is_credited_on_a_move(self) -> None:
        clock = ClockState(white_ms=60_000, black_ms=60_000).start(Side.WHITE, now=T0)
        config = ClockConfig(base_ms=60_000, increment_ms=2_000)
        updated = apply_move(clock, mover=Side.WHITE, config=config, now=T0 + timedelta(seconds=5))
        # 60_000 - 5_000 + 2_000
        assert updated.white_ms == 57_000
        assert updated.turn_started_at == T0 + timedelta(seconds=5)

    def test_a_move_after_the_flag_is_a_timeout(self) -> None:
        clock = ClockState(white_ms=1_000, black_ms=1_000).start(Side.WHITE, now=T0)
        config = ClockConfig(base_ms=1_000, increment_ms=0)
        with pytest.raises(LiveGameError) as exc:
            apply_move(clock, mover=Side.WHITE, config=config, now=T0 + timedelta(seconds=5))
        assert exc.value.code == "timeout"

    def test_a_latency_grace_window_protects_the_round_trip(self) -> None:
        clock = ClockState(white_ms=1_000, black_ms=1_000).start(Side.WHITE, now=T0)
        config = ClockConfig(base_ms=1_000, increment_ms=0)
        # 1.1s used against 1.0s bank: within the 250ms grace, so it is accepted.
        updated = apply_move(clock, mover=Side.WHITE, config=config, now=T0 + timedelta(milliseconds=1100))
        assert updated.white_ms == 0

    def test_flag_fall_ends_the_game_with_the_right_winner(self) -> None:
        game = new_game(clock=ClockConfig(base_ms=1_000, increment_ms=0))
        game.start(now=T0)
        transition = game.check_timeout(now=T0 + timedelta(seconds=2))
        assert transition is not None
        assert transition.state.status is LiveStatus.TIMEOUT
        # White was to move and flagged: Black wins.
        assert transition.state.result is GameResult.BLACK_WIN
        assert transition.state.clock.white_ms == 0

    def test_no_timeout_before_the_clock_runs_out(self) -> None:
        game = new_game(clock=ClockConfig(base_ms=60_000, increment_ms=0))
        game.start(now=T0)
        assert game.check_timeout(now=T0 + timedelta(seconds=1)) is None


# ---------------------------------------------------------------------------
# chess endings
# ---------------------------------------------------------------------------


class TestChessEndings:
    def test_fools_mate_is_detected(self) -> None:
        board = chess.Board()
        for uci in ("f2f3", "e7e5", "g2g4", "d8h4"):
            board.push(chess.Move.from_uci(uci))
        info = detect_terminal(board)
        assert info is not None
        assert info.status is LiveStatus.FINISHED
        assert info.result is GameResult.BLACK_WIN
        assert info.reason == "checkmate"

    def test_stalemate_is_a_draw(self) -> None:
        # Black to move, only K on h8, White queen g6 + K on h6: stalemate.
        board = chess.Board("7k/8/6QK/8/8/8/8/8 b - - 0 1")
        info = detect_terminal(board)
        assert info is not None and info.result is GameResult.DRAW and info.reason == "stalemate"

    def test_insufficient_material_is_a_draw(self) -> None:
        board = chess.Board("8/8/8/4k3/8/4K3/8/8 w - - 0 1")
        info = detect_terminal(board)
        assert info is not None and info.reason == "insufficient_material"

    def test_play_ends_on_checkmate(self) -> None:
        state = build_state(
            game_id="g", config=LiveGameConfig(mode=GameMode.TRAINING, clock=ClockConfig(base_ms=600_000)),
            current_fen=chess.STARTING_FEN,
        )
        game = LiveGame(state=state)
        game.join(LivePlayer(player_id=1, name="A", side=Side.WHITE))
        game.join(LivePlayer(player_id=2, name="B", side=Side.BLACK))
        game.start(now=T0)
        now = T0
        # Scholar's mate.
        for player, uci in ((1, "e2e4"), (2, "e7e5"), (1, "f1c4"), (2, "b8c6"), (1, "d1h5"), (2, "g8f6"), (1, "h5f7")):
            now += timedelta(seconds=1)
            transition = game.play(player_id=player, uci=uci, now=now)
        assert transition.state.status is LiveStatus.FINISHED
        assert transition.state.result is GameResult.WHITE_WIN
        assert transition.state.result_reason == "checkmate"

    def test_threefold_repetition_is_claimable_not_automatic(self) -> None:
        board = chess.Board()
        # Knights shuffle to repeat the start position three times.
        for uci in ("g1f3", "g8f6", "f3g1", "f6g8") * 2:
            board.push(chess.Move.from_uci(uci))
        assert detect_terminal(board) is None  # not automatic at threefold
        assert "threefold_repetition" in claimable_draws(board)

    def test_a_claimable_draw_can_be_claimed(self) -> None:
        state = build_state(
            game_id="g", config=LiveGameConfig(mode=GameMode.TRAINING, clock=ClockConfig(base_ms=600_000)),
            current_fen=chess.STARTING_FEN,
        )
        game = LiveGame(state=state)
        game.join(LivePlayer(player_id=1, name="A", side=Side.WHITE))
        game.join(LivePlayer(player_id=2, name="B", side=Side.BLACK))
        game.start(now=T0)
        now = T0
        for player, uci in (
            (1, "g1f3"), (2, "g8f6"), (1, "f3g1"), (2, "f6g8"),
            (1, "g1f3"), (2, "g8f6"), (1, "f3g1"), (2, "f6g8"),
        ):
            now += timedelta(seconds=1)
            game.play(player_id=player, uci=uci, now=now)
        transition = game.claim_draw(player_id=1, rule="threefold_repetition", now=now)
        assert transition.state.status is LiveStatus.FINISHED
        assert transition.state.result is GameResult.DRAW


# ---------------------------------------------------------------------------
# draws and resignations
# ---------------------------------------------------------------------------


class TestDrawsAndResignation:
    def test_a_draw_offer_must_be_answered_by_the_opponent(self) -> None:
        game = new_game()
        game.start(now=T0)
        game.offer_draw(player_id=1)
        assert game.state.draw_offer is Side.WHITE
        with pytest.raises(LiveGameError) as exc:
            game.accept_draw(player_id=1, now=T0)  # cannot accept your own offer
        assert exc.value.code == "own_draw_offer"
        transition = game.accept_draw(player_id=2, now=T0)
        assert transition.state.status is LiveStatus.DRAW_AGREED
        assert transition.state.result is GameResult.DRAW

    def test_a_move_declines_a_pending_offer(self) -> None:
        game = new_game()
        game.start(now=T0)
        game.offer_draw(player_id=1)
        game.play(player_id=1, uci="e2e4", now=T0)
        assert game.state.draw_offer is None

    def test_resignation_gives_the_opponent_the_win(self) -> None:
        game = new_game()
        game.start(now=T0)
        transition = game.resign(player_id=2, now=T0)
        assert transition.state.result is GameResult.WHITE_WIN
        assert transition.state.status is LiveStatus.RESIGNED


# ---------------------------------------------------------------------------
# fair play
# ---------------------------------------------------------------------------


class TestFairPlay:
    def test_competitive_games_are_forced_to_no_analysis(self) -> None:
        for mode in (GameMode.LOCAL, GameMode.PRIVATE_MATCH):
            assert resolve_analysis_mode(mode, AnalysisMode.SANDBOX_ANALYSIS) is AnalysisMode.NO_ANALYSIS
            assert resolve_analysis_mode(mode, AnalysisMode.TRAINING_ANALYSIS) is AnalysisMode.NO_ANALYSIS

    def test_competitive_coach_is_hint_only(self) -> None:
        state = build_state(
            game_id="g", config=LiveGameConfig(mode=GameMode.PRIVATE_MATCH), current_fen=chess.STARTING_FEN
        )
        assert state.coach_level is CoachLevel.HINTS
        permissions = coach_permissions(state)
        assert permissions["may_give_engine_moves"] is False
        assert permissions["may_give_hints"] is True
        assert permissions["refusal"]

    def test_training_and_sandbox_may_enable_full_analysis(self) -> None:
        training = build_state(
            game_id="g", config=LiveGameConfig(mode=GameMode.TRAINING), current_fen=chess.STARTING_FEN
        )
        sandbox = build_state(
            game_id="g", config=LiveGameConfig(mode=GameMode.SANDBOX), current_fen=chess.STARTING_FEN
        )
        assert is_engine_request_permitted(training) is True
        assert is_engine_request_permitted(sandbox) is True
        assert is_engine_request_permitted(
            build_state(game_id="g", config=LiveGameConfig(mode=GameMode.PRIVATE_MATCH), current_fen=chess.STARTING_FEN)
        ) is False

    def test_an_engine_request_in_a_competitive_game_is_refused(self) -> None:
        state = build_state(
            game_id="g", config=LiveGameConfig(mode=GameMode.PRIVATE_MATCH), current_fen=chess.STARTING_FEN
        )
        with pytest.raises(LiveGameError) as exc:
            refuse_if_not_permitted(state)
        assert exc.value.code == "analysis_not_permitted"

    def test_the_safe_reply_never_contains_a_move(self) -> None:
        state = build_state(
            game_id="g", config=LiveGameConfig(mode=GameMode.PRIVATE_MATCH), current_fen=chess.STARTING_FEN
        )
        reply = coaching_reply(state, question="what's the best move?")
        assert reply["kind"] == "hint_only"
        assert reply["hint"]
        # A hint is a heuristic, never a named move or an evaluation.
        assert not any(token[0] in "abcdefgh" and token[-1].isdigit() for token in reply["hint"].split())

    def test_an_explicit_coach_off_is_respected(self) -> None:
        state = build_state(
            game_id="g",
            config=LiveGameConfig(mode=GameMode.TRAINING, coach_level=CoachLevel.OFF),
            current_fen=chess.STARTING_FEN,
        )
        assert state.coach_level is CoachLevel.OFF
        assert coaching_reply(state)["kind"] == "coach_off"


# ---------------------------------------------------------------------------
# events and reconnection
# ---------------------------------------------------------------------------


class TestEvents:
    def test_sequence_numbers_are_monotonic(self) -> None:
        game = new_game()
        game.start(now=T0)
        game.play(player_id=1, uci="e2e4", now=T0)
        game.play(player_id=2, uci="e7e5", now=T0)
        sequences = [event.sequence_number for event in game.events]
        assert sequences == sorted(sequences)
        assert len(set(sequences)) == len(sequences)

    def test_a_client_can_retrieve_exactly_what_it_missed(self) -> None:
        game = new_game()
        game.start(now=T0)
        game.play(player_id=1, uci="e2e4", now=T0)
        game.play(player_id=2, uci="e7e5", now=T0)
        last = game.events[0].sequence_number
        missed, need_resync = events_after(game.events, last)
        assert need_resync is False
        assert all(event.sequence_number > last for event in missed)

    def test_a_gap_is_detected_and_asks_for_a_resync(self) -> None:
        game = new_game()
        game.start(now=T0)
        game.play(player_id=1, uci="e2e4", now=T0)
        # Pretend the client only holds a much older sequence that has been pruned.
        pruned = game.events[2:]
        _, need_resync = events_after(pruned, after_sequence=0)
        assert need_resync is True

    def test_every_event_carries_the_envelope(self) -> None:
        game = new_game()
        transition = game.start(now=T0)
        payload = transition.events[0].to_payload()
        for key in ("event_id", "game_id", "event_type", "sequence_number", "game_version", "timestamp", "payload"):
            assert key in payload

    def test_a_move_event_advances_the_game_version(self) -> None:
        game = new_game()
        game.start(now=T0)
        before = game.state.version
        transition = game.play(player_id=1, uci="e2e4", now=T0)
        assert transition.state.version > before
        # A single move emits several events (MOVE_MADE and CLOCK_UPDATED), each
        # carrying the version it was emitted at; the last one reflects the final
        # state, which is the invariant a client relies on.
        versions = [event.game_version for event in transition.events]
        assert versions == sorted(versions)
        assert transition.events[-1].game_version == transition.state.version


# ---------------------------------------------------------------------------
# PGN export
# ---------------------------------------------------------------------------


class TestPgn:
    def test_pgn_is_standards_shaped_and_does_not_leak_analysis(self) -> None:
        game = new_game()
        game.start(now=T0)
        game.play(player_id=1, uci="e2e4", now=T0)
        game.play(player_id=2, uci="e7e5", now=T0)
        game.resign(player_id=2, now=T0)
        pgn = game.to_pgn()
        assert '[Result "1-0"]' in pgn
        assert "1. e4 e5" in pgn
        # No engine evaluation or analysis annotation is written into the PGN.
        assert "eval" not in pgn.lower()
        assert "stockfish" not in pgn.lower()
