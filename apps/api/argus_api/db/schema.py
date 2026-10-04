"""Additive schema upgrades.

Caissa still bootstraps its schema with ``Base.metadata.create_all`` (Alembic is
a known follow-up). ``create_all`` creates *missing tables* but never alters an
existing one, so a newly added column would silently never reach a database that
already exists — exactly the kind of difference that makes a local run pass and
a deployed run fail.

:func:`ensure_schema_upgrades` closes that gap for purely **additive** changes.
It is deliberately small and honest about what it can do:

* it only ever *adds* a column or an index — it never drops, renames, or
  back-fills, because those need real migration tooling and a decision;
* every statement is guarded by inspecting the live schema, so running it twice
  changes nothing;
* it works on both PostgreSQL (production/dev) and SQLite (tests), where
  ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` is not available.

When Alembic arrives, this module is deleted and its statements become a
migration.
"""

from __future__ import annotations

from sqlalchemy import Engine, inspect, text

from argus.shared.logging import get_logger

logger = get_logger(__name__)


def ensure_schema_upgrades(engine: Engine) -> list[str]:
    """Apply additive, idempotent schema upgrades. Returns what was applied."""
    applied: list[str] = []
    inspector = inspect(engine)
    if "games" not in inspector.get_table_names():
        return applied

    columns = {column["name"] for column in inspector.get_columns("games")}
    if "source_game_id" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE games ADD COLUMN source_game_id VARCHAR(300)"))
        applied.append("games.source_game_id")
    # Ownership (Phase 15): the caller who imported a game. Additive and NULL for
    # every existing row, so a pre-ownership database is the shared library and
    # nothing becomes invisible.
    if "owner" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE games ADD COLUMN owner VARCHAR(120)"))
        applied.append("games.owner")
        indexes = {index["name"] for index in inspector.get_indexes("games")}
        if "ix_games_owner" not in indexes:
            with engine.begin() as connection:
                connection.execute(text("CREATE INDEX ix_games_owner ON games (owner)"))
            applied.append("ix_games_owner")

    # move_analyses gained the played-move evaluation: accuracy is computed from
    # the played move's own score inside the same search as the best line, which
    # needs its own columns rather than being inferred from centipawn loss.
    if "move_analyses" in inspector.get_table_names():
        move_columns = {column["name"] for column in inspector.get_columns("move_analyses")}
        for name, ddl in (
            ("played_eval_cp", "INTEGER"),
            ("played_eval_mate", "INTEGER"),
            ("played_eval_source", "VARCHAR(24)"),
            # MultiPV root moves + scores, so alternative moves are evidence
            # rather than a guess (used by the training engine's acceptable set).
            ("candidate_moves", "JSON"),
        ):
            if name not in move_columns:
                with engine.begin() as connection:
                    connection.execute(
                        text(f"ALTER TABLE move_analyses ADD COLUMN {name} {ddl}")
                    )
                applied.append(f"move_analyses.{name}")

    # Player identity (Phase 5): name-only matching cannot carry a player
    # profile, so a normalized identity key plus the platform fields arrive as
    # additive columns. Duplicate rows are NOT merged here — merging is an
    # explicit, testable operation (repository.merge_duplicate_players), not a
    # side effect of starting the API.
    if "players" in inspector.get_table_names():
        player_columns = {column["name"] for column in inspector.get_columns("players")}
        for name, ddl in (
            ("identity_key", "VARCHAR(220)"),
            ("platform", "VARCHAR(32)"),
            ("platform_username", "VARCHAR(120)"),
            ("updated_at", "TIMESTAMP"),
        ):
            if name not in player_columns:
                with engine.begin() as connection:
                    connection.execute(text(f"ALTER TABLE players ADD COLUMN {name} {ddl}"))
                applied.append(f"players.{name}")
        player_indexes = {index["name"] for index in inspector.get_indexes("players")}
        if "ix_players_identity_key" not in player_indexes:
            with engine.begin() as connection:
                connection.execute(
                    text("CREATE INDEX ix_players_identity_key ON players (identity_key)")
                )
            applied.append("ix_players_identity_key")

    # Training exercises gained the CONTINUE_LINE continuation (methodology 8.1):
    # the stored PV after the solution. Additive and idempotent, like the rest.
    if "training_positions" in inspector.get_table_names():
        training_columns = {
            column["name"] for column in inspector.get_columns("training_positions")
        }
        if "continuation_line" not in training_columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE training_positions ADD COLUMN continuation_line JSON")
                )
            applied.append("training_positions.continuation_line")
        # Methodology 8.2 added the replay formats (WHAT_WENT_WRONG,
        # RECONSTRUCTION), whose whole-game provenance rides in `replay_context`.
        if "replay_context" not in training_columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE training_positions ADD COLUMN replay_context JSON")
                )
            applied.append("training_positions.replay_context")
        # `data_source` once shipped as VARCHAR(16), which is too short for
        # 'opponent_preparation' (20 chars): the opponent-preparation generator
        # could never store an exercise, and the endpoint answered 500. Widen it
        # on an existing database (create_all already builds the new width for a
        # fresh one). SQLite ignores VARCHAR length, so this is PostgreSQL-only.
        if engine.dialect.name == "postgresql":
            source_column = next(
                (
                    column
                    for column in inspector.get_columns("training_positions")
                    if column["name"] == "data_source"
                ),
                None,
            )
            source_length = getattr(source_column.get("type"), "length", None) if source_column else None
            if source_length is not None and source_length < 32:
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "ALTER TABLE training_positions "
                            "ALTER COLUMN data_source TYPE VARCHAR(32)"
                        )
                    )
                applied.append("training_positions.data_source_width")
        # Methodology 8.2 scopes exercise uniqueness to the format: a whole-game
        # review of a mistake and the puzzle for that mistake are different
        # exercises over the same position. The old (player, fen) constraint must
        # be replaced, not merely supplemented. Postgres-only (SQLite cannot
        # alter a constraint); the existing rows are already unique on the wider
        # key, so this can never fail on data.
        if engine.dialect.name == "postgresql":
            unique = {
                constraint["name"]
                for constraint in inspector.get_unique_constraints("training_positions")
            }
            if "uq_training_position_fen_type" not in unique:
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "ALTER TABLE training_positions "
                            "DROP CONSTRAINT IF EXISTS uq_training_position_fen"
                        )
                    )
                    connection.execute(
                        text(
                            "ALTER TABLE training_positions ADD CONSTRAINT "
                            "uq_training_position_fen_type UNIQUE "
                            "(player_id, source_fen_normalized, position_type)"
                        )
                    )
                applied.append("uq_training_position_fen_type")
        # Phase 9 added the 'opponent_preparation' data source and Phase 10 added
        # 'counterfactual'. The CHECK constraint must be widened on an existing
        # database; `create_all` only builds it correctly for a fresh one. SQLite
        # (tests) cannot alter a constraint, so this is PostgreSQL-only, and
        # guarded so it is a no-op once widened.
        if engine.dialect.name == "postgresql":
            constraints = {
                constraint["name"]: constraint.get("sqltext", "")
                for constraint in inspector.get_check_constraints("training_positions")
            }
            existing_check = constraints.get("ck_training_source", "")
            if existing_check and "counterfactual" not in existing_check:
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "ALTER TABLE training_positions "
                            "DROP CONSTRAINT IF EXISTS ck_training_source"
                        )
                    )
                    connection.execute(
                        text(
                            "ALTER TABLE training_positions ADD CONSTRAINT "
                            "ck_training_source CHECK (data_source IN "
                            "('personalized','general','opponent_preparation','counterfactual'))"
                        )
                    )
                applied.append("training_positions.ck_training_source")

    # The unique index spans ``(source, source_game_id)``, so both columns must
    # exist before it is created. ``source_game_id`` is handled above; ``source``
    # predates this module, but a database old enough to lack it must not make
    # startup crash — add it first (additive, idempotent, like the rest) rather
    # than assuming a shape.
    if "source" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE games ADD COLUMN source VARCHAR(32)"))
        applied.append("games.source")

    indexes = {index["name"] for index in inspector.get_indexes("games")}
    if "uq_games_source_game" not in indexes:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX uq_games_source_game "
                    "ON games (source, source_game_id)"
                )
            )
        applied.append("uq_games_source_game")

    if applied:
        logger.info("Applied additive schema upgrades: %s", ", ".join(applied))
    return applied
