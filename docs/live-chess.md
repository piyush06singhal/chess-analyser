# Caissa live chess (Phase 12)

Phase 12 extends Caissa from post-game intelligence into **live play**: a
server-authoritative game, a real clock, optional real-time analysis where the
game permits it, and a post-game hand-off that reuses Phases 3–11 unchanged.

The non-negotiable constraint is **fair play**: Caissa must never hand a
competitive player an engine move during a game. That rule is enforced in the
backend, in one module, for every surface — the coach endpoint, the AI agent, and
the UI cannot disagree about it.

---

## 1. Where the code lives

| Layer | Path | Responsibility |
| --- | --- | --- |
| Domain (pure) | `packages/argus/argus/live/` | states, transitions, clock, move pipeline, fair play — no DB, no engine, no socket |
| Service | `apps/api/argus_api/services/live_service.py` | load → operate → persist → describe; the only writer of live state |
| Real-time analysis | `apps/api/argus_api/services/live_analysis.py` | score one move, classify it, shape coach events |
| Broadcast | `apps/api/argus_api/services/live_hub.py` | in-process fan-out of sequenced events |
| Clock sweeper | `apps/api/argus_api/services/live_sweeper.py` | flags games whose time ran out while nobody was watching |
| Transport | `apps/api/argus_api/routes/live.py` | REST actions + the WebSocket |
| Storage | `apps/api/argus_api/db/models.py` (`LiveGameRecord`, `LiveGameMoveRecord`, `LiveGameEventRecord`) | durable game, moves and event log |
| Agent tools | `packages/argus/argus/ai_agent/tools/live.py` | read-only live tools, gated by fair play |
| Frontend | `apps/web/src/app/live/…`, `components/live-board.tsx` | lobby, game room, board |

The domain package is deliberately pure. It decides *whether* an operation is
legal and *what* the resulting state and events are; the service persists and
broadcasts them. Keeping the rules there is what makes them testable without a
database, a socket or an engine — and what keeps the client from ever being the
source of truth.

---

## 2. The state machine (§4)

```
waiting ──► ready ──► active ──► paused ──► active
   │          │         │
   │          │         ├──► finished   (checkmate, stalemate, insufficient material,
   │          │         │                 fivefold repetition, 75-move)
   │          │         ├──► resigned
   │          │         ├──► timeout
   │          │         ├──► draw_agreed
   │          │         └──► aborted
   │          └─────────────► aborted
   └────────────────────────► aborted
```

Every transition is validated server-side against an explicit table
(`argus.live.models.ALLOWED_TRANSITIONS`). An illegal transition is refused with
`illegal_transition`; a terminal game accepts no moves and cannot be restarted.

A game with both seats decided at creation starts immediately — there is nobody
left to wait for.

---

## 3. The move pipeline (§7, §46)

```
client move (uci/san + optional expected_version)
   ↓ authenticate + authorize (member of this game, and not a spectator)
   ↓ version check (optimistic concurrency)
   ↓ side-to-move check
   ↓ legal-move validation against the stored FEN
   ↓ apply the move; rebuild the position server-side
   ↓ charge the clock from server time; credit the increment
   ↓ detect terminal (checkmate / draw rules)
   ↓ persist in one transaction (state, moves, events, version)
   ↓ emit events (MOVE_MADE, CLOCK_UPDATED, … GAME_FINISHED)
   ↓ broadcast to subscribers
```

The client's FEN is never read — there is no parameter for it. The board is
rebuilt from the stored FEN, so a forged position cannot be injected.

Persistence is transactional: the operation is applied to an in-memory copy, and
nothing is written unless it succeeds. A refused move leaves the stored game
byte-identical.

---

## 4. The clock (§11–§13)

The clock is **server-authoritative** and derived from timestamps, not counters.

* `turn_started_at` and the remaining banks are stored with the game.
* Remaining time = bank − (now − `turn_started_at`) for the side to move only.
* On a move: charge the mover the elapsed time, credit the increment, switch the
  side to move, and reset `turn_started_at`.
* A flag may fall with a small latency grace (default 250 ms) so a slow round trip
  does not lose a game that was in time.

The frontend interpolates a countdown for display, anchored to the server's
`clock.server_time`, but the server recalculates the truth on every move. A
timeout is applied by the game itself, and the **sweeper** applies it even when
nobody is connected, through the same validated transition a move uses.

Supported presets: `1+0`, `3+2`, `5+0`, `10+5`, `15+10`, `30+0`.

---

## 5. Concurrency and reconnection (§8, §17, §18)

* **Optimistic concurrency**: a move may carry `expected_version`. A mismatch is
  refused with the current version in the error details, and the client resyncs.
* **Multi-tab**: several tabs of the same player each hold their own socket and
  their own last sequence; the server state is the single authority, and a stale
  action from any tab is refused and answered with the current state. The state
  payload reports open sockets per side (`session_counts`), so the UI can warn
  when one side has more than one tab open rather than letting them fight.
* **Presence**: a seated player's connection is recorded. A disconnect stops the
  clock so time is not lost to a network outage, and a genuine return resumes it
  and emits `PLAYER_RECONNECTED` (a first connection is not counted as a
  reconnection).
* **Reconnection**: a client reports its last sequence and receives exactly the
  events it missed, or a full state when the gap cannot be filled. The event log in
  the database is the source of truth; the in-process hub is only a latency
  optimization. See `docs/realtime-protocol.md` for the wire contract.

---

## 6. Fair play (§19–§21, §54)

The permission belongs to the **game**, is fixed at creation, and is clamped by
`argus.live.models.resolve_analysis_mode` — never derived from a request.

| Game mode | Analysis mode | Coach |
| --- | --- | --- |
| `local`, `private_match` (competitive) | forced `no_analysis` | `hints` only: non-engine heuristics, plus the refusal |
| `training` | `training_analysis` | up to the training mode's assistance level |
| `sandbox` | `sandbox_analysis` | `full_analysis` |

Coach levels: `off`, `hints`, `conceptual`, `full_analysis`. In a competitive game
the coach may say *"look for checks, captures and threats first"* but never
*"play this move"* — and it never shows an evaluation or an engine line.

The rule is enforced in three places, all server-side:

1. `argus.live.fairplay` decides what the coach may say; the `/coach` endpoint
   returns the refusal instead of an engine answer.
2. The AI agent's tool layer marks **every engine-backed tool unusable** when the
   active context is a competitive live game (`AgentContext.live_analysis_forbidden`
   is read from the game's own permissions), so an engine tool the model reaches
   for is refused before it runs. The agent has `get_training_coach_state` to learn
   *why* and relay the refusal.
3. The UI renders `permissions` and hides the engine affordances it forbids.

Training-game levels (§55) define what assistance each mode allows, and the
assistance is **binding** rather than a label: it lowers the coach ceiling (and
with it the engine permission) for the game. A `practice_game`, for instance, is
really hint-only even though its analysis mode is enabled.

| Training mode | Assistance | Effective coach ceiling |
| --- | --- | --- |
| `coach_game` | conceptual | `conceptual` (no engine moves) |
| `puzzle_game` | conceptual | `conceptual` |
| `practice_game` | hints | `hints` |
| `opening_practice` | hints | `hints` |
| `endgame_practice` | hints | `hints` |
| `free_analysis` | full | `full_analysis` |
| (no training mode) | none | the analysis-mode ceiling |

---

## 7. Real-time analysis and engine resources (§24–§27, §45)

Real-time analysis runs **only** where the game permits it (training/sandbox). For
each human move the service:

1. scores the position the player actually faced (stored FEN, not the post-move
   board);
2. compares the played move against the engine's best (the Phase 3 machinery —
   no second move-classification implementation);
3. classifies it (Phase 3) and emits coach events only when a threshold is met,
   so a player is not interrupted after every small evaluation change.

Engine protection:

* per-player limit on open games (`max_open_games_per_player`, default 8);
* analysis is queued as a background task, never inside the move request, except
  the engine's own *reply* (the client's board is wrong until it arrives);
* obsolete analysis is cancelled (bumping `analysis_cancellations`) rather than
  run against a position that has moved on;
* the shared caching engine (Phase 3) caches by position and configuration.

---

## 8. Security (§43, §44)

* Every WebSocket connection authenticates from the connection request and is
  closed (`4401`) if unauthorized; it never promotes itself with a message.
* No client-supplied `player_id`, `side`, `clock`, `result` or `game_version` is
  trusted; the server derives all of them.
* Live game ids are unguessable UUIDs, and private games are authorized, not
  hidden behind an opaque id.
* Invitations are unpredictable (`secrets.token_urlsafe`), expire (7 days), and
  are single-use per join; rotating one invalidates the old one.
* Event authorization is re-checked on every read; a spectator of a public game
  may read and nothing more.
* Analytics counters (§48) hold **counts only** — never private game content.

---

## 9. Post-game hand-off (§29–§31)

A terminal game becomes a normal library game (real PGN, real moves), so the
entire Phase 3–11 pipeline runs on live games unchanged:

```
live game → library game → Stockfish analysis → Game Intelligence
          → GameDebrief → training positions → player profile / progress
```

The background pipeline is automatic: it saves the library game, runs the
analysis, **generates training positions for the owner**, and **rebuilds the
owner's player profile** (§29/§31). An `ANALYSIS_UPDATED` event announces
completion on the game's own stream, carrying the number of training positions
created and the debrief path; a training position extracted from a live game
links back to it so the review can find its origin.

---

## 10. Deployment requirements (§50, §64)

* One API process today: the event hub is per-process and the database log covers
  recovery. To run several workers, add a Redis fan-out for `HUB.publish` (the log
  and `/sync` already make correctness independent of the hub).
* The clock sweeper runs inside the API process, started with the application; it
  holds no state between passes, so a restart loses nothing.
* `GET /health` and `GET /ready` report database, Redis, engine, migrations, the
  ML registry and the AI provider. `/ready` is the probe for orchestration.

---

## 11. Verification

```bash
# full regression (backend domain, API, agent tools, performance/recovery)
.venv/bin/python -m pytest -p no:warnings -o addopts=

# live lifecycle against a running stack (real moves, real clock, real post-game)
.venv/bin/python scripts/verify_live.py --base-url http://127.0.0.1:8002

# real load probe: concurrent games, measured move/event/sync latency, spectators
.venv/bin/python scripts/load_live.py --base-url http://127.0.0.1:8002 --games 6 --moves 12

# whole-system checks (now includes the live surface and the fair-play rule)
.venv/bin/python scripts/system_check.py

# stored-analysis consistency (and, if ever needed, the repair)
.venv/bin/python scripts/check_data_consistency.py --database-url "$ARGUS_DATABASE_URL"
.venv/bin/python scripts/repair_stale_analysis.py --database-url "$ARGUS_DATABASE_URL"
```

The live tests are:

* `tests/test_live_core.py` — state machine, clock, fair play, events, PGN (pure).
* `tests/test_live_api.py` — REST actions, privacy, invites, WebSocket, coach.
* `tests/test_live_agent_tools.py` — live agent tools and engine-tool isolation.
* `tests/test_live_performance.py` — concurrency, throughput, recovery.

`scripts/verify_live.py` walks the §67 chain against a running server and prints
each arrow as a check; it reports SKIP (never PASS) for a step it cannot run.
