"""Phase 11 workspace unit tests: evidence, collections, search, prep, progress.

These pin the five honesty rules the workspace adds on top of the coaching core:

* an evidence reference Caissa cannot follow is a stated gap, not a silent drop;
* a collection is typed pointers with rules, never copied data;
* search distinguishes "nothing stored" from "nothing matched", and explains rank;
* a match brief describes stored play and refuses a section below its gate;
* a progress comparison refuses a thin sample and always carries the causality note.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "argus"))

from argus.coaching import (  # noqa: E402
    CAUSALITY_NOTE,
    CollectionError,
    CollectionKind,
    ItemKind,
    KnowledgeKind,
    SearchCandidate,
    SearchKind,
    StudyItem,
    add_item,
    build_collection,
    build_evidence_packet,
    build_match_preparation,
    build_reference,
    build_snapshot,
    compare_periods,
    evidence_method,
    match_brief,
    match_prep_method,
    remove_item,
    search,
    search_method,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# evidence / Show me why
# ---------------------------------------------------------------------------


class TestEvidencePacket:
    def test_engine_facts_and_interpretations_are_labelled_apart(self) -> None:
        packet = build_evidence_packet(
            claim="d4 was a mistake",
            references=[
                {
                    "kind": "engine",
                    "label": "Engine evaluation",
                    "statement": "The engine preferred Bc4.",
                    "game_id": "g1",
                    "ply": 7,
                    "value": 320,
                    "unit": "cp",
                },
                {
                    "kind": "interpretation",
                    "label": "Classification",
                    "statement": "Caissa classified this as a mistake.",
                    "game_id": "g1",
                    "ply": 7,
                },
            ],
        )
        kinds = {item.kind for item in packet.items}
        assert kinds == {KnowledgeKind.ENGINE_FACT, KnowledgeKind.INTERPRETATION}
        assert packet.has_engine_fact
        assert packet.items[0].followable
        assert packet.items[0].href == "/game/g1?ply=7"

    def test_an_unrecognised_kind_is_a_gap_not_a_fact(self) -> None:
        item = build_reference(kind="vibes", label="Vibes", statement="felt bad")
        assert item.kind is KnowledgeKind.REFUSAL
        assert item.followable is False
        assert "does not recognise" in (item.unavailable_reason or "")

    def test_an_unfollowable_reference_becomes_a_stated_gap(self) -> None:
        packet = build_evidence_packet(
            claim="this pattern recurs",
            references=[{"kind": "interpretation", "label": "Pattern", "statement": "seen"}],
        )
        assert packet.gaps
        assert any("Pattern" in gap for gap in packet.gaps)

    def test_a_claim_with_no_evidence_says_so(self) -> None:
        packet = build_evidence_packet(claim="you are improving")
        assert packet.items == []
        assert any("no stored evidence" in gap for gap in packet.gaps)

    def test_a_prediction_without_a_model_is_refused(self) -> None:
        packet = build_evidence_packet(
            claim="you will win", answer_type="prediction", references=[]
        )
        assert packet.has_prediction is False
        assert any("no production model" in gap for gap in packet.gaps)

    def test_the_method_publishes_the_rules(self) -> None:
        method = evidence_method()
        assert "engine_fact" in method["kinds"]
        assert any("never merged" in rule for rule in method["rules"])


# ---------------------------------------------------------------------------
# collections
# ---------------------------------------------------------------------------


class TestStudyCollections:
    def test_a_collection_is_typed_pointers(self) -> None:
        collection = build_collection(
            player_id=1,
            name="Morphy games",
            kind=CollectionKind.GAME_SET,
            items=[StudyItem(kind=ItemKind.GAME, ref="g1")],
        )
        assert collection.size == 1
        assert collection.item_keys() == ["game:g1"]
        assert collection.kinds_present() == ["game"]

    def test_a_kind_the_collection_forbids_is_refused(self) -> None:
        collection = build_collection(player_id=1, name="Games", kind=CollectionKind.GAME_SET)
        try:
            add_item(collection, StudyItem(kind=ItemKind.OPENING, ref="C50"))
        except CollectionError as exc:
            assert "cannot hold" in str(exc)
        else:  # pragma: no cover - the refusal is the point
            raise AssertionError("an opening item in a game set should be refused")

    def test_adding_the_same_item_twice_is_refused(self) -> None:
        collection = build_collection(
            player_id=1, name="Sets", kind=CollectionKind.MIXED,
            items=[StudyItem(kind=ItemKind.TRAINING, ref="9")],
        )
        try:
            add_item(collection, StudyItem(kind=ItemKind.TRAINING, ref="9"))
        except CollectionError as exc:
            assert "already in the collection" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("a duplicate item should be refused")

    def test_remove_takes_the_item_out_or_refuses(self) -> None:
        collection = build_collection(
            player_id=1, name="Sets", kind=CollectionKind.MIXED,
            items=[StudyItem(kind=ItemKind.GAME, ref="g1")],
        )
        trimmed = remove_item(collection, kind=ItemKind.GAME, ref="g1")
        assert trimmed.size == 0
        try:
            remove_item(collection, kind=ItemKind.GAME, ref="nope")
        except CollectionError as exc:
            assert "not in the collection" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("removing an absent item should be refused")

    def test_a_nameless_collection_is_refused(self) -> None:
        try:
            build_collection(player_id=1, name="   ")
        except Exception as exc:  # pydantic validation
            assert "name" in str(exc).lower()
        else:  # pragma: no cover
            raise AssertionError("a nameless collection should be refused")


# ---------------------------------------------------------------------------
# unified search
# ---------------------------------------------------------------------------


CANDIDATES = [
    SearchCandidate(kind=SearchKind.GAME, id="g1", title="Morphy vs Duke", body="Opera Game Paris"),
    SearchCandidate(kind=SearchKind.PLAYER, id="p1", title="Paul Morphy", subtitle="12 games"),
    SearchCandidate(kind=SearchKind.OPENING, id="o1", title="Italian Game", tags=("C50",)),
]


class TestUnifiedSearch:
    def test_a_title_match_outranks_a_body_match(self) -> None:
        candidates = [
            SearchCandidate(kind=SearchKind.GAME, id="a", title="Nothing here", body="Morphy"),
            SearchCandidate(kind=SearchKind.PLAYER, id="b", title="Morphy"),
        ]
        result = search(candidates, "morphy")
        assert result.hits[0].id == "b"
        assert result.hits[0].matched_fields == ["title"]

    def test_every_hit_explains_itself(self) -> None:
        result = search(CANDIDATES, "opera")
        assert result.status == "ok"
        hit = result.hits[0]
        assert "opera" in hit.matched_terms
        assert "Matched" in hit.explanation

    def test_no_candidates_and_no_matches_are_different(self) -> None:
        empty = search([], "anything")
        assert empty.status == "no_candidates"
        unmatched = search(CANDIDATES, "zzzz")
        assert unmatched.status == "no_matches"
        assert unmatched.total_candidates == 3
        assert "none matched" in (unmatched.reason or "")

    def test_an_empty_query_is_refused_rather_than_dumping(self) -> None:
        result = search(CANDIDATES, "   ")
        assert result.status == "empty_query"
        assert result.hits == []

    def test_ordering_is_deterministic(self) -> None:
        first = [hit.id for hit in search(CANDIDATES, "game").hits]
        second = [hit.id for hit in search(CANDIDATES, "game").hits]
        assert first == second

    def test_kind_filter_narrows_the_search(self) -> None:
        result = search(CANDIDATES, "morphy", kinds={SearchKind.PLAYER})
        assert all(hit.kind is SearchKind.PLAYER for hit in result.hits)

    def test_the_method_is_published(self) -> None:
        assert search_method()["field_weights"]["title"] > search_method()["field_weights"]["body"]


# ---------------------------------------------------------------------------
# match preparation
# ---------------------------------------------------------------------------

RICH_OPPONENT = {
    "identity": {"display_name": "Rival"},
    "profile_version": "1.0",
    "coverage": {"band": "moderate"},
    "imported_games": 12,
    "analyzed_games": 9,
    "insights": [
        {"id": "t1", "title": "Early queen sortie", "statement": "They bring the queen out early.",
         "occurrences": 4, "evidence": [{"game_id": "g2", "ply": 6}]},
        {"id": "t2", "title": "Rare", "statement": "once", "occurrences": 1},
    ],
    "repertoire": {
        "by_colour": [
            {"colour": "black", "opening": "Sicilian Defence", "games": 6, "line_san": ["e4", "c5"]},
            {"colour": "black", "opening": "Rare Line", "games": 1},
        ]
    },
    "recurring_positions": [
        {"fen": "r1bqkbnr/pp1ppppp/2n5/2p5/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 0 3", "games": 5},
    ],
    "phase_performance": {"endgame": {"games": 6, "score": 0.25}},
}


class TestMatchPreparation:
    def test_each_section_shows_evidence_or_its_gate(self) -> None:
        prep = build_match_preparation(
            preparing_player_id=1, opponent_id=2, opponent_profile=RICH_OPPONENT, now=NOW
        )
        statuses = {section.key: section.status.value for section in prep.sections}
        assert statuses["opening_lines"] == "available"   # 6 games clears the gate of 4
        assert statuses["tendencies"] == "available"      # 4 occurrences clears the gate of 3
        assert statuses["danger_zones"] == "available"    # 5 clears the gate of 5
        assert statuses["phase_weakness"] == "available"  # 6 clears the gate of 4
        # The one-game opening line is not named.
        lines = next(s for s in prep.sections if s.key == "opening_lines")
        assert all(item.sample_size >= 4 for item in lines.items)

    def test_a_thin_profile_names_the_gate_it_missed(self) -> None:
        prep = build_match_preparation(
            preparing_player_id=1, opponent_id=2,
            opponent_profile={"identity": {"display_name": "New"}, "imported_games": 1},
            now=NOW,
        )
        for section in prep.sections:
            if section.status.value != "available":
                assert section.reason
                assert section.gate is not None
        assert prep.scenario_count == 0

    def test_the_brief_never_predicts(self) -> None:
        prep = build_match_preparation(
            preparing_player_id=1, opponent_id=2, opponent_profile=RICH_OPPONENT, now=NOW
        )
        brief = match_brief(prep)
        assert brief["title"] == "Caissa MATCH BRIEF"
        assert "does not predict" in brief["disclaimer"]
        assert brief["priorities"]
        assert all("sample_size" in item for item in brief["priorities"])

    def test_scenarios_link_to_practice(self) -> None:
        prep = build_match_preparation(
            preparing_player_id=1, opponent_id=2, opponent_profile=RICH_OPPONENT, now=NOW
        )
        with_fen = [s for s in prep.scenarios if s.fen]
        assert with_fen and all(s.practice_href for s in with_fen)

    def test_an_empty_profile_still_produces_a_honest_brief(self) -> None:
        prep = build_match_preparation(
            preparing_player_id=1, opponent_id=2, opponent_profile=None, now=NOW
        )
        brief = match_brief(prep)
        assert "no analysed games" in brief["headline"].lower() or prep.coverage == "insufficient"
        assert brief["unavailable_sections"]

    def test_the_method_publishes_the_gates(self) -> None:
        method = match_prep_method()
        assert method["sample_gates"]["repertoire"] == 4
        assert any("never predicts a move" in rule for rule in method["rules"])


# ---------------------------------------------------------------------------
# progress
# ---------------------------------------------------------------------------


def _games(side_accuracy: float, acpl: float, blunders: int, count: int) -> list[dict]:
    return [
        {
            "has_report": True,
            "metrics": {
                "accuracy": side_accuracy,
                "mean_centipawn_loss": acpl,
                "blunder_count": blunders,
                "mistake_count": 2,
            },
        }
        for _ in range(count)
    ]


class TestProgress:
    def test_a_thin_sample_is_refused_not_compared(self) -> None:
        comparison = compare_periods(
            before_games=_games(70, 120, 3, 2),
            after_games=_games(80, 90, 1, 2),
        )
        assert comparison.status == "insufficient_data"
        assert comparison.reason
        for measure in comparison.measures:
            assert measure.verdict == "insufficient"

    def test_a_measured_improvement_is_reported_with_its_sample(self) -> None:
        comparison = compare_periods(
            before_games=_games(70, 120, 3, 6),
            after_games=_games(82, 90, 1, 6),
        )
        by_key = {m.key: m for m in comparison.measures}
        assert by_key["accuracy"].verdict == "improved"
        assert by_key["mean_centipawn_loss"].verdict == "improved"
        assert by_key["blunders_per_game"].verdict == "improved"
        assert by_key["accuracy"].sample_before == 6
        assert by_key["accuracy"].sample_after == 6
        assert "improved" in comparison.summary

    def test_a_decline_is_reported_as_a_decline(self) -> None:
        comparison = compare_periods(
            before_games=_games(85, 70, 0, 6),
            after_games=_games(70, 120, 3, 6),
        )
        by_key = {m.key: m for m in comparison.measures}
        assert by_key["accuracy"].verdict == "declined"
        assert "declined" in comparison.summary

    def test_a_change_inside_tolerance_is_unchanged(self) -> None:
        comparison = compare_periods(
            before_games=_games(80.0, 100, 2, 6),
            after_games=_games(80.5, 102, 2, 6),
        )
        by_key = {m.key: m for m in comparison.measures}
        assert by_key["accuracy"].verdict == "unchanged"

    def test_the_causality_warning_always_travels(self) -> None:
        for comparison in (
            compare_periods(before_games=_games(70, 120, 3, 6), after_games=_games(82, 90, 1, 6)),
            compare_periods(before_games=[], after_games=[]),
        ):
            assert comparison.causality_note == CAUSALITY_NOTE
            assert "not proof that anything caused it" in comparison.causality_note

    def test_a_snapshot_excludes_games_without_a_report(self) -> None:
        snapshot = build_snapshot(
            label="Recent",
            games=[{"has_report": True, "metrics": {"accuracy": 80}}, {"has_report": False}],
        )
        accuracy = next(m for m in snapshot.measures if m.key == "accuracy")
        assert accuracy.sample_after == 1
        assert snapshot.analysed_games == 1
