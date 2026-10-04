# Caissa — Player Intelligence (Phase 5)

Phase 4 answers *"what happened in this game?"*. Phase 5 answers a different
question:

> What can Caissa reliably learn about this player's chess behaviour from their
> historical games?

The whole design follows from one rule: **a single-game observation, a repeated
pattern and an established tendency are three different things, and the system
must never confuse them.**

```
ANALYZED GAMES → aggregation → feature extraction → pattern detection
      → evidence → PlayerProfile → Chess DNA → dashboard / AI tools
```

This pipeline is separate from the game-analysis pipeline. It adds **no engine
calls**: every number comes from the structured `GameReport` Phase 4 already
stored, so a profile is deterministic, cheap and auditable.

## 1. Where the code lives

| Module | Responsibility |
| ------ | -------------- |
| `argus/player_intelligence/policy.py` | `ClaimLevel`, `Coverage`, coverage bands, `PlayerInsightPolicy` (every threshold) |
| `models.py` | Inputs (`PlayerGameInput` and its event types) and the profile contract |
| `aggregate.py` | Statistics: games, colour split, openings, phases, tactics, positional, king safety, material, conversion, recovery, time control, opponents, trends |
| `patterns.py` | Recurring-pattern detection with evidence |
| `dna.py` | Chess DNA dimensions (interpretable metrics, no scores) |
| `features.py` | The ML-ready feature contract (`FEATURE_VERSION`) |
| `sanity.py` | `validate_profile` / `ProfileSanityError` invariants |
| `insights.py`, `profile.py`, `service.py` | Insight assembly, `build_profile`, the AI-ready accessor surface |
| `apps/api/argus_api/services/player_profile_service.py` | Mapping stored reports → inputs, staleness, persistence, rebuild |
| `apps/api/argus_api/routes/players.py` | `/api/players` endpoints |

## 2. Claim levels and coverage

```python
class ClaimLevel(Enum):    # how strong a statement the evidence supports
    INSUFFICIENT = "insufficient"
    OBSERVATION  = "observation"    # measured, over the available sample
    PATTERN      = "pattern"        # repetition thresholds met
    TENDENCY     = "tendency"       # a large-enough evidence base

class Coverage(Enum):      # how much data there is (never a judgement)
    INSUFFICIENT / LIMITED / MODERATE / ROBUST
```

Coverage bands (by analyzed games): `0 → insufficient`, `2 → limited`,
`5 → moderate`, `20 → robust`. The band describes the *sample*; it says nothing
about the player's strength.

Every aggregate carries a `sample` object:

```json
{ "games": 2, "events": 46, "claim_level": "observation",
  "coverage": "limited", "note": null }
```

## 3. Sample-size policy

All thresholds live in `PlayerInsightPolicy` (documented defaults, configurable
via settings, and **stored with every profile** so a reading is reproducible):

| Threshold | Default | Meaning |
| --------- | ------- | ------- |
| `min_games_for_profile` | 2 | Below this, no profile sections are computed at all |
| `min_games_for_tendency` | 20 | Below this, statistics are shown but no tendency |
| `min_games_for_strong_claim` | 50 | "Substantially larger evidence base" |
| `min_games_per_color_for_claim` | 5 | Per-colour claims (White vs Black) |
| `min_games_per_opening_for_claim` | 4 | Per-opening performance claims |
| `min_games_per_time_control_for_claim` | 5 | Per-time-control claims |
| `min_phase_moves_for_claim` | 20 | Evaluated moves needed per phase |
| `min_games_for_trend` | 10 | Trend baseline requirement |
| `min_recent_games_for_trend` | 5 | Trend recent-window requirement |
| `min_games_with_opponent_rating` | 5 | Opponent-strength context |
| `min_pattern_occurrences` | 4 | Occurrences before "recurring" |
| `min_pattern_games` | 4 | Distinct games before "recurring" |
| `min_pattern_coverage` | 0.25 | Share of games |
| `min_pattern_consistency` | 0.2 | Share of the player's flagged moves |
| `trend_windows` | `[5, 10, 20]` | Compared against all earlier games |
| `trend_min_relative_change` | 0.05 | Below this, the reading is "steady" |
| `conversion_min_band` / `recovery_max_band` | `3` / `-3` | Advantage bands for conversion/recovery |

They are conservative starting values, not statistical truths. Changing them
changes what the product is willing to claim.

## 4. Player identity

A player is **not** just a display name:

```
players(id, name, identity_key, platform, platform_username, title,
        created_at, updated_at)
player_games(player_id, game_id, color, rating)      # explicit relationship
games.white_player_id / black_player_id              # effective colours
```

- `identity_key` is the normalized matching key (case/whitespace-insensitive
  name today; the seat a platform link refines later). Name-only matching would
  split `Piyush1206` and `piyush1206` into two players and corrupt every trend.
- **Merging is explicit, never a startup side effect of guessing**: on startup
  `backfill_player_identities` fills missing keys, `merge_duplicate_players`
  merges rows that normalize to the same key (re-pointing games, links and
  profiles so nothing is lost), and `prune_orphan_players` removes players left
  behind by deleted games (with their stale derived snapshots).
- A game is stored **once**; colour lives on the relationship, so no game
  record is duplicated for White and Black.

## 5. The profile contract

```
PlayerProfile
├── coverage / sufficient_data / policy     ← what may be said, and why
├── games            analyzed, W/D/L, rates, accuracy (avg+median), CPL (avg+median),
│                    blunders/mistakes/inaccuracies per game, length, time span
├── by_color[]       the same per colour, each with its own sample
├── openings         white_repertoire, black_repertoire, most_played, families,
│                    distinct_openings, deviation_rate
├── phases[]         evaluated moves, avg CPL, accuracy, problem moves, share of loss
├── tactical         created vs allowed per type, per game, missed opportunities
├── positional       features vs error candidates, with evidence
├── king_safety      castling, late castling, created/allowed, by colour
├── material         captures, exchanges, promotions, imbalance share
├── conversion       opportunities, conversions, advantage lost, maintenance
├── recovery         losing positions, improvements, games saved
├── time_controls    per Bullet/Blitz/Rapid/Classical, with samples
├── opponents        rating context per bucket
├── trends           recent window vs historical baseline, per window
├── chess_dna        dimensions with definitions
├── insights[]       evidenced, claim-labelled statements
└── notes[]          plain-language coverage caveats
```

Derived statistics are stored as a **versioned snapshot** (`player_profiles`),
never as one enormous JSON blob of raw games:

```
player_profiles(player_id, profile_version, methodology_version, feature_version,
                source_signature, imported_games, analyzed_games, coverage,
                payload, generated_at, updated_at)
```

`PROFILE_VERSION = "5.0"`, `METHODOLOGY_VERSION = "5.1"`,
`FEATURE_VERSION = "5.0"`. One row per (player, profile_version), so the stored
reading stays addressable.

The two versions do different jobs, and both are checked for staleness:

- `profile_version` describes the **shape** of the stored document (sections,
  field names).
- `methodology_version` describes **how the numbers are derived**. A snapshot
  computed by an older methodology is recomputed rather than served next to new
  code — changing an insight's wording or its evidence rules does not require
  the games to change for the reading to be rebuilt. `5.1` records exactly that:
  evidence rows are de-duplicated by (game, ply, label), and a title no longer
  says "recurring" before the repetition thresholds are met.

## 6. Snapshot vs. live analysis

- `source_signature` fingerprints the inputs: analyzed games, latest analysis
  timestamps, report/analysis versions.
- `GET /api/players/{id}` serves the stored snapshot while the signature
  matches; a new analyzed game (or a version change) makes it stale and the
  profile is rebuilt automatically.
- `POST /api/players/{id}/rebuild` forces a full recomputation — for
  debugging and for methodology changes.
- Report materialisation: the profile is built from stored reports, so any
  **analyzed** game without one is repaired first (`ensure_report`, deterministic
  and engine-free). Games with no analysis are never guessed at — they are
  counted in `excluded_games` and named in `excluded_game_ids`.

## 7. Chess DNA

Dimensions are **measured** metrics, not personality scores. No composite
0–100 score is applied.

| Dimension | Metric |
| --------- | ------ |
| `tactical_creation` | Tactical events executed per analyzed game |
| `tactical_oversight` | Events allowed against the player + own tactical error candidates, per game |
| `opening_diversity` | Distinct openings ÷ analyzed games |
| `exchange_tendency` | Exchanges per game (measured from the material timeline) |
| `king_safety_tendency` | Share of games castled |
| `conversion_tendency` | Share of reached winning positions converted |
| `recovery_tendency` | Share of losing positions where the evaluation improved |
| `material_imbalance_tendency` | Share of games where material left equality |
| `positional_complexity` | Positional features per game |
| `aggression_indicator` | Captures per game (measured board events) |

Each dimension carries `value`, `unit`, `definition`, `games`, `events`,
`coverage`, `claim_level` and an optional note — so "what does 6.5 mean?" is
answered in the payload itself.

## 8. Evidence model

```
PlayerInsight
├── id, player_id, category, claim_level
├── title, statement          ← the measured sentence, with its sample inline
├── metric, value, unit, severity (where applicable)
├── games, occurrences, coverage
├── evidence[]                ← game_id, ply, move_number, san, label
├── methodology_version, generated_at
```

Rules enforced by the sanity checks:

- every insight has at least one evidence reference (§26);
- evidence rows are **unique per (game, ply, label)** — a move that changes two
  structures legitimately produces two rows, but the same row is never repeated;
- the evidence label names what was measured (`positional error candidate ·
  doubled_pawn`, `king safety · pawn_shield_reduced`), so a link is meaningful;
- a title never claims more than the claim level: with too few occurrences the
  title is `"Tactical errors observed"`, and only a met threshold produces
  `"Recurring tactical errors"`;
- counts and percentages are range-checked, W/D/L sums to analyzed games,
  `white_games + black_games = analyzed_games`, recent windows never exceed the
  available games.

The dashboard links each evidence row to `/game/{id}?ply=N`, which opens the
board at exactly that position.

## 9. Error taxonomy

Player-level errors are tagged `tactical`, `positional`, `opening`, `endgame`,
`king_safety`, `material`, `conversion`, `calculation` — and an error may carry
**several** tags. Caissa never forces a mistake into exactly one category, and a
category is only claimed when the thresholds are met.

## 10. Trends (recent vs. historical)

For each configured window, the recent `n` games are compared with all earlier
analyzed games on average CPL. The output is the measurement, a relative change
and a direction (`lower_cpl` / `higher_cpl` / `steady`), plus `supported`. The
system **never** concludes "you improved": with insufficient games the entry
carries the number of games required instead. Statistical significance is
explicitly out of scope for Phase 5.

## 11. Time control and opponent context

- Time class is derived with the **importer's own** time-control parser, so a
  profile cannot disagree with the game record.
- Per-time-control statistics are only claimed at `min_games_per_time_control_for_claim`.
- Player and opponent ratings plus the rating difference are stored, and
  outcomes are bucketed by opponent-strength context — performance against
  ~500-rated and ~2000-rated opponents is never silently pooled.

## 12. Features and privacy separation

`argus.player_intelligence.features`

```
PlayerFeatureSet
├── player_id, feature_version, generated_at, games_analyzed, data_range
└── features[]  {name, value, unit, definition, sample_size, source,
                 feature_version, user_specific, training_eligible, note}
```

Every feature is derived from **one player's private games**:
`user_specific = True` and `training_eligible = False`. A future global dataset
builder must make an explicit, documented decision before user data could enter
a shared training set (`docs/ml-and-data.md`). A feature without a sample size
is `None` by design. Phase 5 trains **no** model and presents **no** prediction.

## 13. API surface (AI-ready tools)

| Endpoint | Returns |
| -------- | ------- |
| `GET /api/players` | Every player with imported vs. analyzed counts |
| `GET /api/players/{id}` | The stored (or rebuilt) profile snapshot |
| `POST /api/players/{id}/rebuild` | Forced recomputation |
| `GET /api/players/{id}/insights` | Insights only, each with claim level, sample, evidence |
| `GET /api/players/{id}/evidence[?insight_id=]` | Evidence for one insight (or all) |
| `GET /api/players/{id}/features` | The ML-ready feature vector |

The same accessors are exposed as service methods
(`get_player_profile`, `get_player_statistics`, `get_player_opening_profile`,
`get_player_phase_statistics`, `get_player_tactical_profile`,
`get_player_positional_profile`, `get_player_trends`, `get_player_insights`,
`get_player_evidence`, `get_player_features`) — the surface the future AI agent
will call. No conversational agent is implemented in this phase; the coach's
player tools remain unavailable until they can answer from real data.

## 14. Limitations

- **Real corpora are small.** A profile from a handful of games is an honest
  observation set, not a statistical profile; the UI says so everywhere.
- **Depth is recorded, not normalised.** Classification thresholds are starting
  heuristics, so trends are self-consistent only when the corpus is like-depth.
  Provenance stores the depth, and a mixed-depth corpus should be read with that
  in mind.
- **Opponent strength is contextual, not adjusted.** No rating-normalised
  performance model exists yet.
- **No significance testing.** Trend differences are measurements; no p-values,
  no confidence intervals.
- **`create_all` + additive upgrades**, not Alembic. New Phase 5 tables arrive
  via `create_all`; newly added *columns* are handled by
  `ensure_schema_upgrades` (additive only). Migrations remain a follow-up.
- **No UI for identity management.** Merging happens automatically at startup
  for rows that normalize identically; a manual merge/split screen does not
  exist yet.
