"""Opening repertoire: what the opponent actually plays, from stored games.

The repertoire is a **tree keyed by position**. For every game the opponent
played as a given colour, each move they made inside the opening window is
folded into a node ``(position before the move → move chosen)``. A node's
``occurrences`` is how many games reached that position and picked that move;
its ``share`` is occurrences over the games that reached the position at all.

Nothing is inferred. If the opponent has played three games as White and castled
kingside in two of them, the report says two of three and marks it an
observation — not a "preference". Only when the sample-size gate and the share
gate are both met does the node earn ``pattern`` or ``tendency``.

The generator never runs an engine and never consults an opening book: every
line printed is a line the opponent has actually played, in a game Caissa stores.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import chess

from argus.opponent_intelligence.common import normalize_fen, share
from argus.opponent_intelligence.models import (
    OpponentEvidence,
    OpponentGameInput,
    OpponentOpeningNode,
    OpponentOpeningProfile,
)
from argus.opponent_intelligence.policy import ClaimLevel, Coverage, OpponentInsightPolicy
from argus.player_intelligence.models import GameOutcome

MAX_EVIDENCE_PER_NODE = 5


@dataclass
class _Child:
    uci: str
    san: str
    fen_before: str
    fen_after: str
    ply: int
    move_number: int
    count: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    evidence: list[OpponentEvidence] = field(default_factory=list)


def _result_for(child: _Child, outcome: GameOutcome) -> None:
    if outcome == GameOutcome.WIN:
        child.wins += 1
    elif outcome == GameOutcome.DRAW:
        child.draws += 1
    elif outcome == GameOutcome.LOSS:
        child.losses += 1


def _apply_uci(fen: str, uci: str) -> str:
    try:
        board = chess.Board(fen)
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            return ""
        board.push(move)
        return board.fen()
    except ValueError:
        return ""


def build_repertoire(
    games: list[OpponentGameInput],
    *,
    color: str,
    policy: OpponentInsightPolicy,
    coverage: Coverage,
) -> OpponentOpeningProfile:
    """Aggregate one colour's opening choices across the opponent's games."""
    color = color.lower()
    relevant = [game for game in games if game.color == color]
    analyzed = [game for game in relevant if game.analyzed]

    # tree[parent_fen] → {uci: child}; reached[parent_fen] → games that got there
    tree: dict[str, dict[str, _Child]] = {}
    reached: dict[str, int] = {}

    for game in analyzed:
        for move in sorted(game.moves, key=lambda m: m.ply):
            if move.color != color or move.ply > policy.repertoire_max_ply:
                continue
            parent = normalize_fen(move.fen_before)
            if not parent:
                continue
            child = tree.setdefault(parent, {}).get(move.uci)
            if child is None:
                child = _Child(
                    uci=move.uci,
                    san=move.san,
                    fen_before=move.fen_before,
                    fen_after=_apply_uci(move.fen_before, move.uci),
                    ply=move.ply,
                    move_number=move.move_number,
                )
                tree[parent][move.uci] = child
            child.count += 1
            _result_for(child, game.outcome)
            if len(child.evidence) < MAX_EVIDENCE_PER_NODE:
                child.evidence.append(
                    OpponentEvidence(
                        game_id=game.game_id,
                        ply=move.ply,
                        move_number=move.move_number,
                        san=move.san,
                        label=f"{game.outcome.value} as {color}",
                        detail=game.opening_name,
                    )
                )
        # Count reachability once per opponent move (a position reached is a
        # position the opponent had to answer).
        for move in sorted(game.moves, key=lambda m: m.ply):
            if move.color != color or move.ply > policy.repertoire_max_ply:
                continue
            parent = normalize_fen(move.fen_before)
            if parent:
                reached[parent] = reached.get(parent, 0) + 1

    nodes: list[OpponentOpeningNode] = []
    for parent, children in tree.items():
        total = reached.get(parent, 0)
        for child in children.values():
            node_share = share(child.count, total)
            node_claim = _node_claim(child.count, node_share, policy, len(analyzed))
            if child.count < policy.min_repetitions_for_node and node_share < policy.repertoire_share_for_pattern:
                continue
            nodes.append(
                OpponentOpeningNode(
                    san=child.san,
                    uci=child.uci,
                    fen=child.fen_before,
                    ply=child.ply,
                    move_number=child.move_number,
                    occurrences=child.count,
                    share=node_share,
                    wins=child.wins,
                    draws=child.draws,
                    losses=child.losses,
                    claim_level=node_claim,
                    evidence=list(child.evidence),
                )
            )

    nodes.sort(key=lambda node: (node.occurrences, node.share), reverse=True)
    nodes = nodes[:120]

    root_fens = _root_fens(analyzed, color)
    top_lines = _top_lines(tree, root_fens)
    _ = coverage  # coverage drives the note text below

    families: dict[str, int] = {}
    for game in relevant:
        key = game.opening_name or (f"ECO {game.eco_code}" if game.eco_code else "Unclassified")
        families[key] = families.get(key, 0) + 1

    policy_dict = policy.to_dict()
    if not analyzed:
        note = (
            "No analysed games as this colour, so there is no repertoire to report. "
            "Caissa will not invent opening choices."
        )
    elif coverage in (Coverage.INSUFFICIENT, Coverage.LIMITED):
        note = (
            f"Repertoire is built from {len(analyzed)} analysed game(s) as {color}: counts are "
            "printed, but no choice is called characteristic until the sample-size gate is met."
        )
    else:
        note = (
            f"Built from {len(analyzed)} analysed game(s) as {color}. Every node is a move the "
            "opponent actually played; shares are occurrences over games that reached the position."
        )

    return OpponentOpeningProfile(
        color=color,
        total_games=len(relevant),
        analyzed_games=len(analyzed),
        coverage=coverage,
        nodes=nodes,
        top_lines=top_lines,
        opening_families=families,
        policy=policy_dict,
        note=note,
    )


def _node_claim(
    occurrences: int,
    node_share: float,
    policy: OpponentInsightPolicy,
    analyzed_games: int,
) -> ClaimLevel:
    """A repertoire node's claim level, gated by *games* and by share.

    The gate is deliberately on games, not on occurrences: three repetitions of
    a move inside one game is still one game, and must never be promoted to a
    pattern.
    """
    # A node that is listed at all is at least an observation: its count is real
    # and printed with its sample size. Below the repetition floor it is never
    # promoted further; insufficient is reserved for claims we refuse to state.
    if occurrences < policy.min_repetitions_for_node:
        return ClaimLevel.OBSERVATION
    if analyzed_games < policy.min_games_for_repertoire_insight:
        return ClaimLevel.OBSERVATION
    if node_share >= policy.tendency_share_for_tendency:
        return ClaimLevel.TENDENCY
    if node_share >= policy.repertoire_share_for_pattern:
        return ClaimLevel.PATTERN
    return ClaimLevel.OBSERVATION


def _root_fens(games: list[OpponentGameInput], color: str) -> list[str]:
    """Positions where the opponent made their first move of the game."""
    roots: dict[str, int] = {}
    for game in games:
        for move in sorted(game.moves, key=lambda m: m.ply):
            if move.color != color:
                continue
            parent = normalize_fen(move.fen_before)
            if parent:
                roots[parent] = roots.get(parent, 0) + 1
            break
    return [fen for fen, _ in sorted(roots.items(), key=lambda item: item[1], reverse=True)]


def _top_lines(
    tree: dict[str, dict[str, _Child]], root_fens: list[str], *, length: int = 6, count: int = 3
) -> list[str]:
    """Greedy most-played continuations from the opponent's first-move positions."""
    lines: list[str] = []
    for root in root_fens[:count]:
        fen = root
        plies: list[tuple[int, str]] = []
        for _ in range(length):
            children = tree.get(fen)
            if not children:
                break
            best = max(children.values(), key=lambda child: (child.count, child.san))
            plies.append((best.ply, best.san))
            if not best.fen_after:
                break
            fen = normalize_fen(best.fen_after)
        if plies:
            lines.append(_numbered(plies))
    return lines


def _numbered(plies: list[tuple[int, str]]) -> str:
    """Format (ply, san) pairs with the real move number and ellipses for gaps."""
    out: list[str] = []
    expected_ply = plies[0][0]
    for ply, san in plies:
        move_number = (ply + 1) // 2
        white_to_move = ply % 2 == 1
        if ply != expected_ply:
            out[-1] = out[-1] + " …"
        if white_to_move:
            out.append(f"{move_number}.")
            out.append(san)
        else:
            if not out:
                out.append(f"{move_number}...")
            out.append(san)
        expected_ply = ply + 1
    return " ".join(out)


__all__ = ["build_repertoire"]
