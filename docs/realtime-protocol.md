# Caissa real-time protocol (Phase 12)

This document is the contract between a live-game client and the Caissa server. It
describes the transport, the event envelope, the event types, the synchronization
rules, and the error codes. It is deliberately small: a live game has exactly one
stream of events, and every action is an ordinary REST call.

The guiding rule, stated once: **the server is authoritative**. A client sends an
*intent* (a move, a resign) and receives the resulting state. It never sends a
position, a clock, a result, a player identity or a version that is trusted.

---

## 1. Two channels, one source of truth

| Channel | Path | Purpose |
| --- | --- | --- |
| REST (actions) | `/api/live/games/{id}/…` | create, join, start, move, resign, draw, abort, pause, resume, coach, invite, visibility |
| REST (reads) | `/api/live/games/{id}`, `/state`, `/sync`, `/pgn`, `/analysis` | current state, delta, missed events, export |
| WebSocket (events) | `/api/live/games/{id}/ws` | the game's event stream, in sequence order |

Actions are REST on purpose. Each one is transactional, has an HTTP status, and
goes through the same authorization, error mapping and audit trail as the rest of
Caissa. The WebSocket carries **events only** — it never accepts an action, and a
client that sends one receives `unsupported_message`.

The database event log is the source of truth. The in-process hub
(`services/live_hub.py`) is a latency optimization: a reconnecting client replays
what it missed from the log, so correctness does not depend on the hub. With more
than one API worker, a Redis fan-out is the deployment step that makes the hub
shared; until then the log + `GET /sync` is the recovery path.

---

## 2. Authentication

* **REST**: `player_id` is supplied on every action and read; the server decides
  whether that player may read or act on the game.
* **WebSocket**: identity is resolved from the *connection* (`?player_id=`), before
  the socket is accepted. A connection the caller is not authorized for is closed
  with code `4401` and never upgraded. Events received on the socket can never
  promote a connection: the only messages a client may send are `ping` and `sync`.

Roles are `player` (a seated player), `owner` (the creator), and `spectator` (a
public game). A private game is invisible to everyone else — authorization, not
obscurity.

---

## 3. The event envelope

Every event has the same fields (§10):

```json
{
  "event_id": "3f9c…",
  "game_id": "0f21354f-…",
  "event_type": "MOVE_MADE",
  "sequence_number": 104,
  "game_version": 42,
  "timestamp": "2026-10-02T12:00:03.114Z",
  "payload": { "uci": "e2e4", "san": "e4", "side": "white", "ply": 1 }
}
```

* `sequence_number` is strictly increasing and gap-free. It is what a client uses
  to detect a missing event.
* `game_version` is the game state's optimistic-concurrency version. It increases
  with each accepted transition; several events can share one version (a move
  emits `MOVE_MADE` and `CLOCK_UPDATED` at the same version).
* `timestamp` is server time.

### Event types

| Event | Emitted when |
| --- | --- |
| `GAME_CREATED` | the game is created |
| `GAME_READY` | the second seat is filled |
| `GAME_STARTED` | the clock starts |
| `MOVE_MADE` | a legal move is applied (human or engine) |
| `CLOCK_UPDATED` | the clock is recalculated after a move |
| `DRAW_OFFERED` / `DRAW_ACCEPTED` / `DRAW_DECLINED` | draw negotiation |
| `PLAYER_RESIGNED` | a player resigns |
| `GAME_FINISHED` | checkmate, stalemate, insufficient material, fivefold/75-move, timeout |
| `GAME_ABORTED` / `GAME_PAUSED` / `GAME_RESUMED` | lifecycle control |
| `PLAYER_DISCONNECTED` / `PLAYER_RECONNECTED` | presence |
| `ANALYSIS_UPDATED` | real-time or post-game analysis is ready |
| `COACH_MESSAGE` | the in-game coach produced a message |
| `SYNC_REQUIRED` | a client is behind the oldest retained event and must resync |

---

## 4. Synchronization (§17, §47)

A client tracks the last `sequence_number` it applied and sends it when it
reconnects.

* **On socket connect**: the server's *first* message is a `sync` frame containing
  the difference the client missed, or the full state when the gap cannot be
  filled.
* **On demand**: send `{"type": "sync", "after_sequence": N}` and receive a fresh
  `sync` frame. `after_sequence` must be a non-negative integer; anything else
  returns `{"type": "error", "error": "after_sequence_must_be_a_non_negative_integer"}`.
* **REST equivalent**: `GET /api/live/games/{id}/sync?after_sequence=N`.

The `sync` frame:

```json
{
  "type": "sync",
  "game_id": "0f21354f-…",
  "after_sequence": 100,
  "server_sequence": 110,
  "server_version": 47,
  "count": 10,
  "events": [ … ],
  "resync": false,
  "clock": { … },
  "status": "active",
  "version": 47
}
```

If `resync` is `true`, the frame also carries `state` (the full game payload) and
the client must replace its local state wholesale rather than applying events. A
client must never apply a partial event list when `resync` is `true`.

### Gap handling on the socket

When an `event` frame arrives whose `sequence_number` is greater than
`last_seen + 1`, the client **must** request a resync (`{"type": "sync"}`) instead
of applying it. Applying a gap is how a live board drifts out of sync.

---

## 5. Server messages

| `type` | Meaning |
| --- | --- |
| `sync` | the initial or requested synchronization frame (see above) |
| `event` | one event: `{"type": "event", "event": <envelope>}` |
| `pong` | reply to `{"type": "ping"}` |
| `error` | a refused client message: `{"type": "error", "error": "<code>", "detail": "…"}` |

Client messages (the only ones accepted): `{"type": "ping"}` and
`{"type": "sync", "after_sequence": N}`.

---

## 6. The game payload

Reads return this shape (`GET /api/live/games/{id}`), and every action returns
`{"events": [...], "state": <payload>}`:

```json
{
  "game_id": "…",
  "status": "active",
  "mode": "private_match",
  "analysis_mode": "no_analysis",
  "coach_level": "hints",
  "training_mode": "",
  "visibility": "private",
  "rated": false,
  "variant": "standard",
  "initial_fen": "…",
  "current_fen": "…",
  "side_to_move": "white",
  "move_number": 1,
  "version": 42,
  "sequence": 104,
  "result": "*",
  "result_reason": null,
  "draw_offer": null,
  "clock_config": { "base_ms": 600000, "increment_ms": 5000 },
  "clock": {
    "white_ms": 597880, "black_ms": 600000,
    "running": true, "turn_side": "white",
    "turn_started_at": "2026-10-02T12:00:02.000Z",
    "server_time": "2026-10-02T12:00:03.114Z",
    "display": { "white": "9:57", "black": "10:00" }
  },
  "seats": { "white": "human", "black": "open" },
  "players": {
    "white": { "player_id": 13, "name": "…", "kind": "human", "connected": true, "rating": null },
    "black": { "player_id": null, "name": null, "kind": "open", "connected": false, "rating": null }
  },
  "permissions": {
    "competitive": true,
    "may_give_engine_moves": false,
    "may_show_evaluation": false,
    "may_show_engine_lines": false,
    "may_give_hints": true,
    "refusal": "This is a competitive game, so Caissa will not suggest a move. …"
  },
  "moves": [ { "ply": 1, "san": "e4", "uci": "e2e4", "side": "white", "clock_after": { … } } ],
  "legal_moves": ["e2e4", "d2d4", "…"],
  "session_counts": { "white": 1, "black": 2 },
  "turn_owner": "player",
  "library_game_id": null,
  "viewer": "player"
}
```

`permissions` is the fair-play contract and is computed by the server from the
game, never from the request. `session_counts` reports how many open sockets each
side has for this game, so a client can detect that it is one of several tabs
(§18); the state remains authoritative regardless.

---

## 7. Error codes

REST actions map a domain refusal to a stable HTTP status:

| Situation | Status | Code |
| --- | --- | --- |
| Illegal move (not legal in the position, or malformed) | 422 | `invalid_move` |
| Out of turn | 409 | `conflict` |
| Stale `expected_version` | 409 | `conflict` (details: `current_version`) |
| Terminal game / illegal transition | 409 | `conflict` |
| Not a member / unreadable game | 404 | `not_found` |
| Unknown time control / bad date / bad colour | 422 | `validation_error` |

A refused action changes nothing: the transition is computed in memory and only
persisted when it is legal, so a stale or illegal move cannot leave a half-applied
game.

---

## 8. Action-time concurrency (§8)

`POST /api/live/games/{id}/move` accepts an optional `expected_version`. If it is
present and does not match the stored version, the move is refused with `409` and
`details.current_version`. The client should then resync and, if it still wants to
move, retry against the new version. Omitting `expected_version` means "apply to
whatever the current state is", which the server still validates (turn, legality)
before persisting.

---

## 9. Post-game (§29, §30)

When a game reaches a terminal state the server, in the background:

1. turns the live game into a normal library game (real PGN, real moves);
2. runs the full Stockfish analysis and Game Intelligence;
3. emits `ANALYSIS_UPDATED` on the game's own event stream with
   `library_game_id` and `debrief_path`.

The client polls `GET /api/live/games/{id}` (or watches the socket) for
`library_game_id`, then hands off to `/game/{library_game_id}` and
`/api/coaching/games/{library_game_id}/debrief`.
