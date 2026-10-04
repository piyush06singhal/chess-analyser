"""Tests for the additive schema-upgrade path (``ensure_schema_upgrades``).

A database that already exists must gain the columns and indexes added after it
was created, and running the upgrade twice must change nothing. These tests pin
both, and pin the fragility that a database old enough to lack a *core* column
(e.g. ``games.source``) does not make startup crash while building the unique
index that spans it.
"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from argus_api.db.models import Base
from argus_api.db.schema import ensure_schema_upgrades


def _fresh_engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


def test_fresh_schema_needs_no_upgrades_and_is_idempotent():
    engine = _fresh_engine()
    assert ensure_schema_upgrades(engine) == []
    assert ensure_schema_upgrades(engine) == []


def test_upgrade_adds_missing_columns_to_an_old_database():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE games (id INTEGER PRIMARY KEY, created_at TIMESTAMP)"))
        connection.execute(text("CREATE TABLE players (id INTEGER PRIMARY KEY, name VARCHAR(120))"))
        connection.execute(text("CREATE TABLE move_analyses (id INTEGER PRIMARY KEY)"))
        connection.execute(
            text("CREATE TABLE training_positions (id INTEGER PRIMARY KEY, data_source VARCHAR(16))")
        )

    applied = set(ensure_schema_upgrades(engine))

    assert "games.source" in applied
    assert "games.source_game_id" in applied
    assert "players.identity_key" in applied
    assert "move_analyses.candidate_moves" in applied
    assert "training_positions.replay_context" in applied
    assert "uq_games_source_game" in applied

    inspector = inspect(engine)
    games_columns = {column["name"] for column in inspector.get_columns("games")}
    assert {"source", "source_game_id", "owner"} <= games_columns

    # A second pass must find nothing left to do.
    assert ensure_schema_upgrades(engine) == []
