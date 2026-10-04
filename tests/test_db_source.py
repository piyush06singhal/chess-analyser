"""Tests for the database game source and PGN re-serialisation.

The database source exists so a Caissa database can feed the *same* pipeline as a
PGN file. These tests use a temporary SQLite database built from the fixture
games, so no real data or running service is required, and they pin the two
properties that matter: a re-rendered game reproduces the stored game exactly, and
private games cannot become a dataset without an explicit opt-in.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "argus"))

from argus.chess_core.pgn import parse_games  # noqa: E402
from argus.datasets.db_source import (  # noqa: E402
    DatabaseGameSource,
    DatasetScope,
    assert_scope_allowed,
    render_pgn,
)
from argus.datasets.importer import DatasetImporter  # noqa: E402
from argus.shared.errors import ValidationError  # noqa: E402
from tests.conftest import OPERA_GAME_PGN, SCHOLARS_MATE_PGN  # noqa: E402

DDL = [
    """
    CREATE TABLE games (
        id TEXT PRIMARY KEY,
        white_player_name TEXT NOT NULL,
        black_player_name TEXT NOT NULL,
        white_rating INTEGER,
        black_rating INTEGER,
        result TEXT NOT NULL,
        date TEXT,
        event TEXT,
        site TEXT,
        time_control TEXT,
        eco_code TEXT,
        opening_name TEXT,
        initial_position TEXT NOT NULL,
        final_position TEXT NOT NULL DEFAULT '',
        move_count INTEGER NOT NULL DEFAULT 0,
        source TEXT NOT NULL DEFAULT 'test',
        source_game_id TEXT,
        created_at TEXT
    )
    """,
    """
    CREATE TABLE game_moves (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        game_id TEXT NOT NULL,
        ply INTEGER NOT NULL,
        move_number INTEGER NOT NULL,
        color TEXT NOT NULL,
        san TEXT NOT NULL
    )
    """,
]

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _load(path: Path, pgns: list[tuple[str, str, dict]]) -> Path:
    """Build a small SQLite database holding the supplied games."""
    url = f"sqlite+pysqlite:///{path}"
    engine = create_engine(url)
    with engine.begin() as connection:
        for statement in DDL:
            connection.execute(text(statement))
        for game_id, pgn, metadata in pgns:
            game = parse_games(pgn)[0]
            connection.execute(
                text(
                    "INSERT INTO games (id, white_player_name, black_player_name, white_rating,"
                    " black_rating, result, date, event, site, time_control, eco_code,"
                    " opening_name, initial_position, move_count, source_game_id, created_at)"
                    " VALUES (:id, :w, :b, :wr, :br, :res, :date, :event, :site, :tc, :eco,"
                    " :opening, :initial, :moves, :src, :created)"
                ),
                {
                    "id": game_id,
                    "w": metadata.get("white") or game.white_player.name,
                    "b": metadata.get("black") or game.black_player.name,
                    "wr": metadata.get("white_rating"),
                    "br": metadata.get("black_rating"),
                    "res": metadata.get("result") or game.result.value,
                    "date": metadata.get("date"),
                    "event": metadata.get("event", "Test"),
                    "site": metadata.get("site", "Test"),
                    "tc": metadata.get("time_control", "600"),
                    "eco": metadata.get("eco"),
                    "opening": metadata.get("opening"),
                    "initial": START,
                    "moves": len(game.moves),
                    "src": metadata.get("source_game_id", game_id),
                    "created": metadata.get("created_at"),
                },
            )
            for index, move in enumerate(game.moves):
                connection.execute(
                    text(
                        "INSERT INTO game_moves (game_id, ply, move_number, color, san)"
                        " VALUES (:id, :ply, :number, :color, :san)"
                    ),
                    {
                        "id": game_id,
                        "ply": index,
                        "number": index // 2 + 1,
                        "color": "white" if index % 2 == 0 else "black",
                        "san": move.san,
                    },
                )
    engine.dispose()
    return path


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    return _load(
        tmp_path / "corpus.db",
        [
            ("g1", OPERA_GAME_PGN, {"white_rating": 1500, "black_rating": 1520}),
            ("g2", SCHOLARS_MATE_PGN, {"white_rating": 900, "black_rating": 950}),
        ],
    )


class TestRenderPgn:
    def test_moves_are_numbered_correctly_for_white_first(self) -> None:
        rendered = render_pgn(
            headers=[
                '[Event "T"]',
                '[White "A"]',
                '[Black "B"]',
                '[Result "1-0"]',
            ],
            moves=[("white", 1, "e4"), ("black", 1, "e5"), ("white", 2, "Nf3")],
        )
        assert "1. e4 e5 2. Nf3 1-0" in rendered

    def test_black_to_move_from_a_custom_position_is_marked(self) -> None:
        rendered = render_pgn(
            headers=['[White "A"]', '[Black "B"]', '[Result "*"]'],
            moves=[("black", 1, "d5"), ("white", 2, "e4")],
            starting_fen="fnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1",
        )
        assert "1... d5 2. e4" in rendered
        assert '[SetUp "1"]' in rendered

    def test_a_header_containing_a_quote_cannot_break_the_pgn(self) -> None:
        rendered = render_pgn(
            headers=['[White "Bob "The Boss""]', '[Result "*"]'],
            moves=[("white", 1, "e4")],
        )
        # The stray quote must not break the header or the parse: the name has to
        # survive intact on a single header line.
        parsed = parse_games(rendered)[0]
        assert "Bob" in parsed.white_player.name
        assert "The Boss" in parsed.white_player.name


class TestDatabaseGameSource:
    def test_it_reads_every_game_as_one_record(self, corpus: Path) -> None:
        source = DatabaseGameSource(f"sqlite+pysqlite:///{corpus}")
        games = list(source.iter_games())
        source.close()
        assert [game.metadata["argus_game_id"] for game in games] == ["g1", "g2"]

    def test_the_limit_is_a_limit(self, corpus: Path) -> None:
        source = DatabaseGameSource(f"sqlite+pysqlite:///{corpus}")
        games = list(source.iter_games(limit=1))
        source.close()
        assert len(games) == 1

    def test_a_round_trip_reproduces_the_stored_moves_exactly(self, corpus: Path) -> None:
        """The property that matters: what we write back must re-parse to the same game.

        This is what a mis-numbered move list silently breaks, so it is asserted
        move by move rather than by ply count.
        """
        source = DatabaseGameSource(f"sqlite+pysqlite:///{corpus}")
        for game in source.iter_games():
            parsed = parse_games(game.pgn_text)[0]
            original = parse_games(
                OPERA_GAME_PGN if game.metadata["argus_game_id"] == "g1" else SCHOLARS_MATE_PGN
            )[0]
            assert [move.san for move in parsed.moves] == [move.san for move in original.moves]
            assert parsed.white_player.name == original.white_player.name
            assert parsed.black_player.name == original.black_player.name
            assert parsed.result == original.result
        source.close()

    def test_ratings_and_dates_survive_the_round_trip(self, corpus: Path) -> None:
        importer = DatasetImporter(source="test")
        source = DatabaseGameSource(f"sqlite+pysqlite:///{corpus}")
        result = source.ingest(importer, allow_user_scope=True)
        assert result.stats.games_seen == 2
        ratings = sorted(
            (record.white_rating, record.black_rating) for record in result.records
        )
        assert ratings == [(900, 950), (1500, 1520)]

    def test_scope_is_recorded_and_provenance_is_described(self, corpus: Path) -> None:
        source = DatabaseGameSource(
            f"sqlite+pysqlite:///{corpus}", scope=DatasetScope.USER, owner_key="piyush"
        )
        described = source.describe()
        source.close()
        assert described["kind"] == "database"
        assert described["scope"] == "user"
        assert described["owner_key"] == "piyush"
        # The URL carries credentials, so it must never be written into a manifest.
        assert "sqlite" not in str(described)


class TestScopeGuard:
    def test_private_games_are_refused_without_the_opt_in(self) -> None:
        with pytest.raises(ValidationError, match="allow_user_scope=True"):
            assert_scope_allowed(DatasetScope.USER, allow_user_scope=False)

    def test_the_opt_in_permits_them(self) -> None:
        assert_scope_allowed(DatasetScope.USER, allow_user_scope=True)

    def test_a_global_source_needs_no_opt_in(self) -> None:
        assert_scope_allowed(DatasetScope.GLOBAL, allow_user_scope=False)

    def test_ingesting_refuses_before_reading_a_single_row(self, corpus: Path) -> None:
        source = DatabaseGameSource(f"sqlite+pysqlite:///{corpus}", scope=DatasetScope.USER)
        with pytest.raises(ValidationError):
            source.ingest(DatasetImporter(source="test"), allow_user_scope=False)
        source.close()


class TestDatasetScopeReporting:
    def test_both_ingestion_paths_produce_the_same_records(self, corpus: Path) -> None:
        """The two paths must agree, or every statistic would depend on provenance.

        A game read from the database carries the same moves hash, players, result
        and ply count as the same game read from a PGN file — which is what makes
        the database a source rather than a second pipeline.
        """
        from argus.datasets.importer import DatasetImporter as Importer

        file_importer = Importer(source="pgn")
        file_result = file_importer.ingest_text(OPERA_GAME_PGN)

        database_importer = Importer(source="db")
        database_source = DatabaseGameSource(f"sqlite+pysqlite:///{corpus}")
        database_source.ingest(database_importer, allow_user_scope=True, limit=1)
        database_source.close()

        file_record = file_result.records[0]
        database_record = database_importer.result().records[0]
        assert file_record.moves_hash == database_record.moves_hash
        assert file_record.white.original_name == database_record.white.original_name
        assert file_record.black.original_name == database_record.black.original_name
        assert file_record.result == database_record.result
        assert file_record.ply_count == database_record.ply_count

    def test_scope_and_provenance_reach_the_manifest(self, corpus: Path) -> None:
        """A dataset must say where it came from and who it belongs to.

        Without this the rule "private games stay out of global training" is a
        promise; with it, the manifest can be checked after the fact.
        """
        from argus.ml.experiment_runner import build_dataset_from_ingestion

        source = DatabaseGameSource(
            f"sqlite+pysqlite:///{corpus}",
            scope=DatasetScope.USER,
            owner_key="piyush",
            label="argus-db",
        )
        importer = DatasetImporter(source="Caissa database")
        ingestion = source.ingest(importer, allow_user_scope=True)
        built = build_dataset_from_ingestion(
            ingestion,
            dataset_id="scope_test",
            source="Caissa database",
            scope=DatasetScope.USER.value,
            provenance=source.describe(),
            write=False,
        )
        manifest = built.manifests[0]
        assert manifest.scope == "user"
        assert manifest.provenance["kind"] == "database"
        assert manifest.provenance["owner_key"] == "piyush"
        assert manifest.number_of_games == 2
        assert manifest.rating_coverage == 1.0
        assert manifest.date_range == (None, None)
        assert any("never merged into a global training set" in note for note in manifest.notes)

    def test_a_global_manifest_is_labelled_global(self, corpus: Path) -> None:
        from argus.ml.experiment_runner import build_dataset_from_ingestion

        source = DatabaseGameSource(f"sqlite+pysqlite:///{corpus}", scope=DatasetScope.GLOBAL)
        ingestion = source.ingest(DatasetImporter(source="global corpus"), allow_user_scope=False)
        built = build_dataset_from_ingestion(
            ingestion, dataset_id="global_test", source="global corpus", write=False
        )
        assert built.manifests[0].scope == "global"
        assert any("scope 'global'" in note for note in built.manifests[0].notes)
