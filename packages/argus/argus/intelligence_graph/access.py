"""Graph authorization (§33): a relationship never grants access to its objects.

The graph is a *view* over the domain, so it must obey the same authorization the
domain does. The rule this module enforces is deliberately blunt:

* a node is readable only if every game/player it is scoped to is readable;
* an edge is readable only if both of its endpoints are readable.

The important consequence is the one §33 names: **a graph relationship does not
grant access to the underlying object**. Walking ``pattern → position`` never
reveals a position from a game the caller cannot open, because the position node
itself is scoped to its game and is filtered first.

Today Caissa is single-user, so the default policy is unrestricted. The policy is
still constructed explicitly, and the SQL store honors it in the query, so when
accounts arrive the scoping is already threaded through rather than bolted on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from argus.intelligence_graph.models import GraphEdge, GraphNode
from argus.intelligence_graph.taxonomy import NodeType

#: Node kinds that describe a stored game and therefore inherit that game's
#: visibility. A node of these kinds with no resolvable game is denied rather
#: than shown, because it cannot be proven to belong to an authorized game.
_GAME_SCOPED = frozenset({NodeType.GAME, NodeType.MOVE, NodeType.POSITION})

#: Node kinds owned by a player (their private intelligence).
_PLAYER_SCOPED = frozenset(
    {
        NodeType.PLAYER,
        NodeType.INSIGHT,
        NodeType.TRAINING_POSITION,
        NodeType.TRAINING_ATTEMPT,
        NodeType.TRAINING_SESSION,
        NodeType.SCENARIO,
        NodeType.PREDICTION,
        NodeType.OPPONENT_PROFILE,
        NodeType.PREPARATION_REPORT,
        NodeType.STUDY_ITEM,
    }
)

_GAME_KEYS = ("game_ids", "game_id")
_PLAYER_KEYS = ("player_ids", "player_id", "owner_player_id")


@dataclass
class GraphAccessPolicy:
    """What the caller may read.

    ``allowed_game_ids`` / ``allowed_player_ids`` set to ``None`` means
    "unrestricted" — the current single-user deployment. An empty set means
    "nothing", which is the honest reading for a restricted caller with no grants.
    """

    allowed_game_ids: set[str] | None = None
    allowed_player_ids: set[int] | None = None
    #: Counts denials without recording what was denied, so observability never
    #: leaks private content (§54).
    denials: int = field(default=0, init=False)

    def can_read_game(self, game_id: str) -> bool:
        if self.allowed_game_ids is None:
            return True
        return game_id in self.allowed_game_ids

    def can_read_player(self, player_id: int) -> bool:
        if self.allowed_player_ids is None:
            return True
        return player_id in self.allowed_player_ids

    def _game_ids(self, node: GraphNode) -> list[str] | None:
        for key in _GAME_KEYS:
            if key in node.attributes:
                value = node.attributes[key]
                if isinstance(value, (list, tuple, set)):
                    return [str(item) for item in value]
                if value is not None:
                    return [str(value)]
        if node.node_type is NodeType.GAME:
            return [node.node_key]
        return None

    def _player_id(self, node: GraphNode) -> int | None:
        for key in _PLAYER_KEYS:
            if key in node.attributes and node.attributes[key] is not None:
                try:
                    return int(node.attributes[key])
                except (TypeError, ValueError):
                    return None
        if node.node_type is NodeType.PLAYER:
            try:
                return int(node.node_key)
            except (TypeError, ValueError):
                return None
        return None

    def can_read_node(self, node: GraphNode) -> tuple[bool, str | None]:
        """``(allowed, reason)`` — the reason is safe to log (no content)."""
        if node.node_type in _GAME_SCOPED:
            game_ids = self._game_ids(node)
            if not game_ids:
                self.denials += 1
                return False, f"{node.node_type.value} node has no resolvable game"
            if not all(self.can_read_game(game_id) for game_id in game_ids):
                self.denials += 1
                return False, "caller may not read the game this node belongs to"
            return True, None
        if node.node_type in _PLAYER_SCOPED:
            player_id = self._player_id(node)
            if player_id is not None and not self.can_read_player(player_id):
                self.denials += 1
                return False, "caller may not read this player's data"
            return True, None
        # Openings, knowledge and patterns that are not player-scoped are public
        # chess knowledge; they carry no private content.
        return True, None

    def can_read_edge(
        self, edge: GraphEdge, from_node: GraphNode | None, to_node: GraphNode | None
    ) -> bool:
        """An edge is visible only when both endpoints are."""
        if from_node is not None:
            allowed, _ = self.can_read_node(from_node)
            if not allowed:
                return False
        if to_node is not None:
            allowed, _ = self.can_read_node(to_node)
            if not allowed:
                return False
        return True

    def describe(self) -> dict[str, Any]:
        return {
            "scope": (
                "unrestricted"
                if self.allowed_game_ids is None and self.allowed_player_ids is None
                else "restricted"
            ),
            "game_scope": None if self.allowed_game_ids is None else len(self.allowed_game_ids),
            "player_scope": (
                None if self.allowed_player_ids is None else len(self.allowed_player_ids)
            ),
            "denials": self.denials,
        }


def unrestricted() -> GraphAccessPolicy:
    """The single-user default: every stored object is readable."""
    return GraphAccessPolicy()


__all__ = ["GraphAccessPolicy", "unrestricted"]
