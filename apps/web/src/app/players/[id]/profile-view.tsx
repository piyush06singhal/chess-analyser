"use client";

// The Player Intelligence dashboard.
//
// Three rules shape this page, and they are the reason it is arranged the way it
// is rather than as one long column of numbers:
//
// 1. **Every number carries its sample.** A value with "2 games" under it is read
//    differently from one with "50 games", so the sample is part of the row, not
//    a footnote. Sections below the minimum-data line are not shown at all.
// 2. **Claims are labelled.** Each insight shows how strong a statement its
//    evidence supports (observation → pattern → tendency) and where it came
//    from, with clickable evidence into the exact game position.
// 3. **Nothing is invented.** The UI renders what the backend computed; where
//    there is not enough data it says so instead of showing a lonely number.

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type {
  ChessDnaDimension,
  ClaimLevel,
  CoverageLevel,
  EvidenceRef,
  PlayerFeatureSet,
  PlayerInsight,
  PlayerProfilePayload,
  SampleInfo,
} from "@/lib/api";
import { Disclosure, Note, Panel, Stat, TabPanel, Tabs } from "@/components/ui";
import { plural } from "@/lib/text";

// --- shared vocabulary --------------------------------------------------------

const CLAIM_LABEL: Record<ClaimLevel, string> = {
  insufficient: "Insufficient evidence",
  observation: "Observation",
  pattern: "Recurring pattern",
  tendency: "Established tendency",
};

const CLAIM_STYLE: Record<ClaimLevel, string> = {
  insufficient: "border-ink-600 text-mist-500",
  observation: "border-sky-primary/40 text-sky-300",
  pattern: "border-violet-primary/40 text-violet-300",
  tendency: "border-emerald-primary/40 text-emerald-300",
};

const COVERAGE_LABEL: Record<CoverageLevel, string> = {
  insufficient: "Insufficient data",
  limited: "Limited coverage",
  moderate: "Moderate coverage",
  robust: "Robust coverage",
};

function percent(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(digits)}%`;
}

function number(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? "—" : value.toFixed(digits);
}

function claimBadge(level: ClaimLevel) {
  return (
    <span className={`badge ${CLAIM_STYLE[level]}`} title={CLAIM_LABEL[level]}>
      {CLAIM_LABEL[level]}
    </span>
  );
}

/** One line stating the sample behind a value. */
function SampleLine({ sample, suffix }: { sample: SampleInfo; suffix?: string }) {
  return (
    <p className="text-meta text-mist-500">
      {sample.games} game{sample.games === 1 ? "" : "s"}
      {sample.events ? ` · ${sample.events} ${suffix ?? "events"}` : ""} ·{" "}
      {COVERAGE_LABEL[sample.coverage].toLowerCase()}
      {sample.note ? ` — ${sample.note}` : ""}
    </p>
  );
}

function EvidenceLinks({ refs, limit = 6 }: { refs: EvidenceRef[]; limit?: number }) {
  if (refs.length === 0) return null;
  const shown = refs.slice(0, limit);
  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5">
      {shown.map((ref, index) => (
        <Link
          key={`${ref.game_id}-${ref.ply}-${ref.label ?? index}`}
          href={`/game/${ref.game_id}?ply=${ref.ply}`}
          className="chip"
          title={`${ref.label ?? "evidence"} · game ${ref.game_id.slice(0, 8)}`}
        >
          {ref.move_number ? `move ${ref.move_number}` : "game start"}
          {ref.san ? ` · ${ref.san}` : ""}
        </Link>
      ))}
      {refs.length > shown.length ? (
        <span className="text-meta text-mist-500">+{refs.length - shown.length} more</span>
      ) : null}
    </div>
  );
}

/** A panel whose content is a frequency table (event type → count). */
function CountTable({
  title,
  counts,
  emptyLabel,
}: {
  title: string;
  counts: Record<string, number>;
  emptyLabel: string;
}) {
  const entries = Object.entries(counts);
  return (
    <div>
      <p className="label">{title}</p>
      {entries.length === 0 ? (
        <p className="mt-1 text-small text-mist-500">{emptyLabel}</p>
      ) : (
        <ul className="mt-1.5 space-y-1">
          {entries.map(([name, count]) => (
            <li key={name} className="flex items-baseline justify-between gap-3">
              <span className="text-small text-mist-300">{name.replaceAll("_", " ")}</span>
              <span className="mono text-small text-mist-100">{count}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Insight card: statement, strength of claim, sample, evidence. */
function InsightCard({ insight }: { insight: PlayerInsight }) {
  return (
    <li className="rounded-lg border border-ink-700 bg-ink-850/60 p-3">
      <div className="flex flex-wrap items-center gap-2">
        {claimBadge(insight.claim_level)}
        <span className="badge border-ink-600 text-mist-500">
          {insight.category.replaceAll("_", " ")}
        </span>
      </div>
      <p className="mt-2 text-sm font-medium text-mist-100">{insight.title}</p>
      <p className="mt-1 text-small leading-relaxed text-mist-300">{insight.statement}</p>
      <p className="mt-1.5 text-meta text-mist-500">
        {insight.games} game{insight.games === 1 ? "" : "s"} · {insight.occurrences} relevant
        position{insight.occurrences === 1 ? "" : "s"} · {COVERAGE_LABEL[insight.coverage].toLowerCase()}
      </p>
      {insight.evidence.length > 0 ? (
        <Disclosure summary={`Evidence (${insight.evidence.length})`}>
          <EvidenceLinks refs={insight.evidence} limit={12} />
          <Note>
            Each link opens that game at the exact position the measurement came from.
          </Note>
        </Disclosure>
      ) : null}
    </li>
  );
}

function DnaRow({ dimension }: { dimension: ChessDnaDimension }) {
  return (
    <div className="border-b border-ink-800/70 py-3 last:border-0">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm font-medium text-mist-100">{dimension.label}</p>
        <p className="mono text-sm text-mist-50">
          {dimension.value === null ? "—" : number(dimension.value, 2)}
          <span className="ml-1.5 text-meta font-normal text-mist-500">{dimension.unit}</span>
        </p>
      </div>
      <p className="mt-1 text-meta text-mist-500">
        {dimension.games} game{dimension.games === 1 ? "" : "s"} · {dimension.events} measurement
        {dimension.events === 1 ? "" : "s"} · {COVERAGE_LABEL[dimension.coverage].toLowerCase()}
      </p>
      {dimension.note ? <p className="mt-1 text-small text-mist-400">{dimension.note}</p> : null}
      <Disclosure summary="Definition">
        <Note>{dimension.definition}</Note>
      </Disclosure>
    </div>
  );
}

// --- the page -----------------------------------------------------------------

export function PlayerProfileView({ profile }: { profile: PlayerProfilePayload }) {
  const [tab, setTab] = useState("overview");
  const [rebuilt, setRebuilt] = useState<PlayerProfilePayload | null>(null);
  const [rebuilding, setRebuilding] = useState(false);
  const [rebuildError, setRebuildError] = useState<string | null>(null);
  const [features, setFeatures] = useState<PlayerFeatureSet | null>(null);
  const [featuresError, setFeaturesError] = useState<string | null>(null);

  // A rebuild returns the same contract, so the whole page simply renders the
  // newer snapshot instead of patching individual numbers.
  const data = rebuilt ?? profile;
  const games = data.games;
  const analysed = data.analyzed_games;

  const insightGroups = groupInsights(data.insights);
  const observedCount = data.insights.filter((i) => i.claim_level !== "insufficient").length;

  async function rebuild() {
    setRebuilding(true);
    setRebuildError(null);
    try {
      setRebuilt(await api.rebuildPlayerProfile(data.player_id));
    } catch (err) {
      setRebuildError(err instanceof Error ? err.message : "Rebuild failed");
    } finally {
      setRebuilding(false);
    }
  }

  async function loadFeatures() {
    try {
      setFeatures(await api.getPlayerFeatures(data.player_id));
    } catch (err) {
      setFeaturesError(err instanceof Error ? err.message : "Could not load features");
    }
  }

  return (
    <div className="space-y-5">
      {/* --- header ------------------------------------------------------- */}
      <section className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <p className="eyebrow">Players</p>
          <h1 className="title mt-1">{data.display_name}</h1>
          <p className="subtitle mt-1">
            {analysed} analyzed game{analysed === 1 ? "" : "s"} of {data.imported_games} imported ·{" "}
            {COVERAGE_LABEL[data.coverage].toLowerCase()}
            {games.time_span[0] && games.time_span[1]
              ? ` · ${games.time_span[0]} → ${games.time_span[1]}`
              : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link href="/players" className="btn btn-ghost">
            All players
          </Link>
          <button type="button" className="btn" onClick={rebuild} disabled={rebuilding}>
            {rebuilding ? "Rebuilding…" : "Rebuild profile"}
          </button>
        </div>
      </section>

      {rebuildError ? (
        <p role="alert" className="text-small text-rose-300">
          {rebuildError}
        </p>
      ) : null}

      {/* --- minimum-data state ------------------------------------------- */}
      {!data.sufficient_data ? (
        <Panel title="Not enough games for a player-level profile">
          <p className="text-sm leading-relaxed text-mist-300">
            Caissa builds a profile from analyzed games, and needs at least{" "}
            {String(data.policy.min_games_for_profile)} of them: one game is a single observation,
            not a pattern. Statistics below describe exactly what has been analyzed — they are not
            a profile of your play.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <span className="badge border-ink-600 text-mist-400">
              {analysed} analyzed · {data.policy.min_games_for_profile as number} needed
            </span>
            <span className="badge border-ink-600 text-mist-400">
              repeated patterns need {data.policy.min_games_for_tendency as number} analyzed games
            </span>
            {data.excluded_games > 0 ? (
              <span className="badge border-amber-primary/40 text-amber-300">
                {data.excluded_games} game{data.excluded_games === 1 ? "" : "s"} without analysis
              </span>
            ) : null}
          </div>

          {data.excluded_game_ids.length > 0 ? (
            <Disclosure summary={`Games not yet counted (${data.excluded_game_ids.length})`}>
              <Note>
                These games are imported but have no stored analysis, so they contribute nothing
                to a profile yet. They are listed rather than silently dropped.
              </Note>
              <ul className="mt-1 space-y-1">
                {data.excluded_game_ids.map((id) => (
                  <li key={id}>
                    <Link href={`/game/${id}`} className="mono text-small text-sky-300 underline">
                      {id}
                    </Link>
                  </li>
                ))}
              </ul>
            </Disclosure>
          ) : null}

          <div className="mt-4 flex flex-wrap gap-2">
            <Link href="/games" className="btn btn-primary">
              Analyze more games
            </Link>
          </div>
        </Panel>
      ) : null}

      {/* With fewer than the minimum analyzed games the backend computes no
          sections at all, so rendering them would be a page of dashes. The
          honest thing is to show the one thing that is true and stop. */}
      {!data.sufficient_data ? (
        <Panel title="What Caissa can still tell you today">
          <p className="text-small leading-relaxed text-mist-300">
            Each of these games already has its own full report — the openings played, the moments
            the game turned, and where the moves cost the most. Those are single-game measurements
            and they are available now; only the cross-game profile waits for more analyzed games.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Link href="/games" className="btn">
              Open the library
            </Link>
            <Link href="/import?source=platform" className="btn btn-ghost">
              Connect an account
            </Link>
          </div>
        </Panel>
      ) : null}

      {/* --- record at a glance ------------------------------------------- */}
      {data.sufficient_data ? (
      <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat
          label="Analyzed games"
          value={analysed}
          hint={data.imported_games > analysed ? `${data.imported_games - analysed} not analyzed` : "all imported games"}
          icon="♟"
        />
        <Stat label="Won" value={games.wins} tone="accent" hint={percent(games.win_rate)} />
        <Stat label="Drawn" value={games.draws} hint={percent(games.draw_rate)} />
        <Stat label="Lost" value={games.losses} tone="danger" hint={percent(games.loss_rate)} />
        <Stat
          label="Avg accuracy"
          value={games.average_accuracy === null ? "—" : `${number(games.average_accuracy)}%`}
          hint={`median ${games.median_accuracy === null ? "—" : `${number(games.median_accuracy)}%`}`}
        />
        <Stat
          label="Avg CPL"
          value={number(games.average_centipawn_loss)}
          tone="warn"
          hint={`median ${number(games.median_centipawn_loss)}`}
        />
      </section>
      ) : null}

      {data.sufficient_data ? (
      <Panel
        title="How this profile is built"
        subtitle={`Profile ${data.profile_version} · methodology ${data.methodology_version} · computed ${formatStamp(data.generated_at)}${data.cache?.hit ? " (served from the stored snapshot)" : " (recomputed just now)"}`}
      >
        {/* The notes are methodology, not a measurement: one click away so the
            panel leads with the numbers it is actually about. */}
        <Disclosure summary="Notes and caveats">
          <ul className="space-y-1.5">
            {data.notes.map((note) => (
              <li key={note} className="text-small leading-relaxed text-mist-300">
                {note}
              </li>
            ))}
          </ul>
        </Disclosure>
        <Disclosure summary="Sample policy used for this profile">
          <dl className="mt-1 space-y-1">
            {Object.entries(data.policy)
              .filter(([, value]) => !Array.isArray(value))
              .map(([key, value]) => (
                <div key={key} className="flex items-baseline justify-between gap-4">
                  <dt className="text-small text-mist-500">{key.replaceAll("_", " ")}</dt>
                  <dd className="mono text-small text-mist-200">{String(value)}</dd>
                </div>
              ))}
          </dl>
          <Note>
            These thresholds decide what Caissa is willing to state. They are documented defaults,
            and the values actually used travel with every profile (spec: sample-size policy).
          </Note>
        </Disclosure>
        {data.excluded_game_ids.length > 0 ? (
          <Disclosure summary={`Games excluded from this profile (${data.excluded_game_ids.length})`}>
            <Note>
              These games are imported but have no stored analysis, so they contribute nothing to
              the numbers above. They are listed rather than silently dropped.
            </Note>
            <ul className="mt-1 space-y-1">
              {data.excluded_game_ids.map((id) => (
                <li key={id} className="mono text-small text-mist-400">
                  {id}
                </li>
              ))}
            </ul>
          </Disclosure>
        ) : null}
      </Panel>
      ) : null}

      {/* --- sections ------------------------------------------------------ */}
      {data.sufficient_data ? (
        <>
      <Tabs
        items={[
          { id: "overview", label: "Overview" },
          { id: "openings", label: "Openings", count: data.openings.distinct_openings },
          { id: "play", label: "Play" },
          { id: "structure", label: "Structure" },
          { id: "trends", label: "Trends & DNA" },
          { id: "method", label: "Method" },
        ]}
        active={tab}
        onChange={setTab}
        className="panel sticky top-16 z-20"
      />

      {tab === "overview" ? (
        <TabPanel>
          <div className="space-y-4">
            <div className="grid gap-4 lg:grid-cols-2">
              <Panel title="Colour split">
                <div className="space-y-3">
                  {data.by_color.map((row) => (
                    <div key={row.color} className="rounded-lg border border-ink-700 bg-ink-850/60 p-3">
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-sm font-medium text-mist-100">
                          {row.color === "white" ? "As White" : "As Black"}
                        </p>
                        {claimBadge(row.sample.claim_level)}
                      </div>
                      <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-4">
                        <MiniStat label="Games" value={row.games} />
                        <MiniStat label="Record" value={`${row.wins}W·${row.draws}D·${row.losses}L`} />
                        <MiniStat
                          label="Accuracy"
                          value={row.average_accuracy === null ? "—" : `${number(row.average_accuracy)}%`}
                        />
                        <MiniStat label="CPL" value={number(row.average_centipawn_loss)} />
                        <MiniStat label="Blunders" value={row.blunders} />
                        <MiniStat label="Mistakes" value={row.mistakes} />
                        <MiniStat label="Inaccuracies" value={row.inaccuracies} />
                        <MiniStat label="Win rate" value={percent(row.win_rate)} />
                      </div>
                      <p className="mt-2 text-meta text-mist-500">{row.sample.note ?? "—"}</p>
                    </div>
                  ))}
                </div>
                <Note>
                  A split is shown side by side so the games behind each colour stay visible: a
                  difference between them is only meaningful when both colours have enough games.
                </Note>
              </Panel>

              <Panel title="Game shape">
                <div className="grid grid-cols-2 gap-3">
                  <MiniStat
                    label="Average length"
                    value={
                      games.average_game_length == null
                        ? "—"
                        : plural(Math.round(games.average_game_length), "move")
                    }
                  />
                  <MiniStat label="Blunders / game" value={number(games.blunders_per_game, 2)} />
                  <MiniStat label="Mistakes / game" value={number(games.mistakes_per_game, 2)} />
                  <MiniStat label="Inaccuracies / game" value={number(games.inaccuracies_per_game, 2)} />
                  <MiniStat label="Avg captures / game" value={number(data.material.average_captures)} />
                  <MiniStat
                    label="Left equality"
                    value={percent(data.material.imbalance_share, 0)}
                    hint="share of games"
                  />
                </div>
                <SampleLine sample={games.sample} suffix="scored positions" />

                <div className="mt-4 border-t border-ink-700 pt-3">
                  <p className="label">Time controls</p>
                  {data.time_controls.entries.length === 0 ? (
                    <p className="mt-1 text-small text-mist-500">
                      No time-control metadata in the analyzed games.
                    </p>
                  ) : (
                    <ul className="mt-1.5 space-y-2">
                      {data.time_controls.entries.map((entry) => (
                        <li key={entry.time_class}>
                          <div className="flex items-baseline justify-between gap-3">
                            <span className="text-small capitalize text-mist-200">
                              {entry.time_class}
                            </span>
                            <span className="mono text-small text-mist-100">
                              {entry.games} game{entry.games === 1 ? "" : "s"} ·{" "}
                              {entry.average_accuracy === null
                                ? "—"
                                : `${number(entry.average_accuracy)}%`}{" "}
                              · CPL {number(entry.average_centipawn_loss)}
                            </span>
                          </div>
                          {entry.sample.note ? (
                            <p className="text-meta text-mist-500">{entry.sample.note}</p>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                <div className="mt-4 border-t border-ink-700 pt-3">
                  <p className="label">Opponent strength</p>
                  <p className="mt-1.5 text-small text-mist-300">
                    Opponent rating {number(data.opponents.average_opponent_rating, 0)} · your rating{" "}
                    {number(data.opponents.average_player_rating, 0)} · difference{" "}
                    {number(data.opponents.average_rating_difference, 0)}
                  </p>
                  <SampleLine sample={data.opponents.sample} suffix="rated games" />
                </div>
              </Panel>
            </div>

            <Panel
              title="What the sample supports"
              subtitle={`${observedCount} evidenced statement${observedCount === 1 ? "" : "s"} from ${analysed} analyzed game${analysed === 1 ? "" : "s"}. Each one states its sample and links to the positions behind it.`}
            >
              {data.insights.length === 0 ? (
                <p className="text-small text-mist-500">
                  No statement in this profile met the evidence threshold.
                </p>
              ) : (
                <div className="space-y-4">
                  {insightGroups.map((group) => (
                    <div key={group.category}>
                      <p className="label">{group.label}</p>
                      <ul className="mt-2 space-y-2">
                        {group.items.map((insight) => (
                          <InsightCard key={insight.id} insight={insight} />
                        ))}
                      </ul>
                    </div>
                  ))}
                </div>
              )}
            </Panel>
          </div>
        </TabPanel>
      ) : null}

      {tab === "openings" ? (
        <TabPanel>
          <div className="space-y-4">
            <Panel
              title="Opening repertoire"
              subtitle="Frequency is a fact from the games; performance is only claimed where the opening has enough games behind it."
            >
              <div className="grid gap-5 lg:grid-cols-2">
                <RepertoireTable title="As White" rows={data.openings.white_repertoire} />
                <RepertoireTable title="As Black" rows={data.openings.black_repertoire} />
              </div>
              <div className="mt-4 grid grid-cols-2 gap-3 border-t border-ink-700 pt-3 sm:grid-cols-4">
                <MiniStat label="Distinct openings" value={data.openings.distinct_openings} />
                <MiniStat
                  label="Opening families"
                  value={Object.keys(data.openings.families).length}
                />
                <MiniStat label="Left the book" value={percent(data.openings.deviation_rate, 0)} />
                <MiniStat
                  label="Most played"
                  value={data.openings.most_played[0]?.name ?? "—"}
                />
              </div>
              <SampleLine sample={data.openings.sample} suffix="openings" />
            </Panel>
          </div>
        </TabPanel>
      ) : null}

      {tab === "play" ? (
        <TabPanel>
          <div className="space-y-4">
            <Panel
              title="By game phase"
              subtitle="Where the moves that cost the most were played. Each phase carries its own evaluated-move count."
            >
              <div className="overflow-x-auto">
                <table className="data-table min-w-[640px]">
                  <thead>
                    <tr>
                      <th scope="col">Phase</th>
                      <th scope="col">Evaluated moves</th>
                      <th scope="col">Avg CPL</th>
                      <th scope="col">Accuracy</th>
                      <th scope="col">Problem moves</th>
                      <th scope="col">Share of loss</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.phases.phases.map((row) => (
                      <tr key={row.phase}>
                        <td className="capitalize text-mist-100">{row.phase}</td>
                        <td className="mono">{row.evaluated_moves}</td>
                        <td className="mono">{number(row.average_centipawn_loss)}</td>
                        <td className="mono">
                          {row.accuracy === null ? "—" : `${number(row.accuracy)}%`}
                        </td>
                        <td className="mono">{row.problem_moves}</td>
                        <td className="mono">{percent(row.share_of_loss, 0)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {data.phases.weakest_phase ? (
                  <span className="badge border-amber-primary/40 text-amber-300">
                    Highest CPL: {data.phases.weakest_phase}
                  </span>
                ) : null}
                {data.phases.strongest_phase ? (
                  <span className="badge border-emerald-primary/40 text-emerald-300">
                    Lowest CPL: {data.phases.strongest_phase}
                  </span>
                ) : null}
              </div>
              <SampleLine sample={data.phases.sample} suffix="evaluated moves" />
              {data.phases.phases.some((row) => row.sample.note) ? (
                <Disclosure summary="Phase caveats">
                  <ul className="space-y-1">
                    {data.phases.phases
                      .filter((row) => row.sample.note)
                      .map((row) => (
                        <li key={row.phase} className="text-small text-mist-400">
                          <span className="capitalize text-mist-300">{row.phase}:</span>{" "}
                          {row.sample.note}
                        </li>
                      ))}
                  </ul>
                </Disclosure>
              ) : null}
            </Panel>

            <div className="grid gap-4 lg:grid-cols-2">
              <Panel
                title="Tactical play"
                subtitle="Tactics you created are kept separate from tactics you allowed — a player can do both."
              >
                <div className="grid grid-cols-2 gap-3">
                  <MiniStat label="Created / game" value={number(data.tactical.created_per_game)} />
                  <MiniStat label="Allowed / game" value={number(data.tactical.allowed_per_game)} />
                </div>
                <div className="mt-4 grid gap-4 sm:grid-cols-2">
                  <CountTable
                    title="Created"
                    counts={data.tactical.created}
                    emptyLabel="No tactical events created were detected."
                  />
                  <CountTable
                    title="Allowed"
                    counts={data.tactical.allowed}
                    emptyLabel="No tactical events allowed were detected."
                  />
                </div>
                {data.tactical.missed_opportunities > 0 ? (
                  <p className="mt-3 text-small text-mist-300">
                    {data.tactical.missed_opportunities} tactical opportunit
                    {data.tactical.missed_opportunities === 1 ? "y was" : "ies were"} available and
                    not taken.
                  </p>
                ) : null}
                <SampleLine sample={data.tactical.sample} suffix="tactical events" />
                <Note>
                  Counts are measured board events from the games in this profile. They describe
                  what happened, not a single &ldquo;tactics score&rdquo;.
                </Note>
              </Panel>

              <Panel title="Conversion & recovery">
                <div className="grid grid-cols-2 gap-3">
                  <MiniStat
                    label="Winning positions converted"
                    value={`${data.conversion.conversions}/${data.conversion.opportunities}`}
                  />
                  <MiniStat label="Conversion rate" value={percent(data.conversion.conversion_rate, 0)} />
                  <MiniStat
                    label="Advantage maintained"
                    value={percent(data.conversion.maintenance_rate, 0)}
                  />
                  <MiniStat label="Advantage lost" value={data.conversion.advantage_lost} />
                  <MiniStat
                    label="Losing positions improved"
                    value={`${data.recovery.improvements}/${data.recovery.situations}`}
                  />
                  <MiniStat label="Games saved" value={`${data.recovery.saved_games}`} />
                </div>
                <SampleLine sample={data.conversion.sample} suffix="opportunities" />
                <SampleLine sample={data.recovery.sample} suffix="losing positions" />
                <Disclosure summary="What counts as convertible">
                  <Note>
                    A &ldquo;winning position&rdquo; is a documented evaluation band, not a claim
                    that the game was trivially won: reaching a large engine advantage is an
                    opportunity to convert, and the outcome is measured separately.
                  </Note>
                </Disclosure>
                <EvidenceLinks refs={[...data.conversion.evidence, ...data.recovery.evidence]} limit={8} />
              </Panel>
            </div>
          </div>
        </TabPanel>
      ) : null}

      {tab === "structure" ? (
        <TabPanel>
          <div className="space-y-4">
            <div className="grid gap-4 lg:grid-cols-2">
              <Panel
                title="Positional features"
                subtitle="Structural facts observed while you were at the board, separated from the ones the engine's evaluation flags as errors."
              >
                <div className="grid grid-cols-2 gap-3">
                  <MiniStat label="Features / game" value={number(data.positional.features_per_game)} />
                  <MiniStat
                    label="Error candidates / game"
                    value={number(data.positional.error_candidates_per_game)}
                  />
                </div>
                <div className="mt-4 grid gap-4 sm:grid-cols-2">
                  <CountTable
                    title="Features"
                    counts={data.positional.features}
                    emptyLabel="No structural features were recorded."
                  />
                  <CountTable
                    title="Error candidates"
                    counts={data.positional.error_candidates}
                    emptyLabel="No positional error candidates were recorded."
                  />
                </div>
                <SampleLine sample={data.positional.sample} suffix="events" />
                <Disclosure summary={`Evidence (${data.positional.evidence.length})`}>
                  <EvidenceLinks refs={data.positional.evidence} limit={12} />
                </Disclosure>
              </Panel>

              <Panel title="King safety">
                <div className="grid grid-cols-2 gap-3">
                  <MiniStat label="Castled" value={`${data.king_safety.castled_games} games`} />
                  <MiniStat label="Castling rate" value={percent(data.king_safety.castling_rate, 0)} />
                  <MiniStat label="Late castling" value={`${data.king_safety.late_castling_games} games`} />
                  <MiniStat label="Events created" value={data.king_safety.events_created} />
                  <MiniStat label="Events allowed" value={data.king_safety.events_allowed} />
                </div>
                <div className="mt-4 grid gap-4 sm:grid-cols-2">
                  <CountTable
                    title="Event types"
                    counts={data.king_safety.top_event_types}
                    emptyLabel="No king-safety events were recorded."
                  />
                  <div>
                    <p className="label">By colour</p>
                    <ul className="mt-1.5 space-y-2">
                      {Object.entries(data.king_safety.by_color).map(([color, stats]) => (
                        <li key={color} className="flex items-baseline justify-between gap-3">
                          <span className="text-small capitalize text-mist-300">{color}</span>
                          <span className="mono text-small text-mist-100">
                            {stats.games} game{stats.games === 1 ? "" : "s"} · castled{" "}
                            {percent(stats.castling_rate, 0)} · {stats.king_safety_events} events
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
                <SampleLine sample={data.king_safety.sample} suffix="king-safety events" />
                <Disclosure summary={`Evidence (${data.king_safety.evidence.length})`}>
                  <EvidenceLinks refs={data.king_safety.evidence} limit={12} />
                </Disclosure>
              </Panel>
            </div>

            <Panel
              title="Material"
              subtitle="How often positions left equality, and on which side. This is a playing characteristic — not a good or bad one."
            >
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
                <MiniStat label="Captures / game" value={number(data.material.average_captures)} />
                <MiniStat label="Exchanges / game" value={number(data.material.average_exchanges)} />
                <MiniStat label="Promotions" value={data.material.promotions} />
                <MiniStat label="Left equality" value={percent(data.material.imbalance_share, 0)} />
                <MiniStat
                  label="Games imbalanced"
                  value={`${data.material.imbalance_games}/${analysed}`}
                />
                <MiniStat label="Final balance" value={number(data.material.average_final_balance)} />
              </div>
              <SampleLine sample={data.material.sample} suffix="material events" />
            </Panel>
          </div>
        </TabPanel>
      ) : null}

      {tab === "trends" ? (
        <TabPanel>
          <div className="space-y-4">
            <Panel
              title="Recent vs. historical"
              subtitle={data.trends.note}
            >
              <div className="overflow-x-auto">
                <table className="data-table min-w-[720px]">
                  <thead>
                    <tr>
                      <th scope="col">Window</th>
                      <th scope="col">Recent games</th>
                      <th scope="col">Recent CPL</th>
                      <th scope="col">Baseline games</th>
                      <th scope="col">Baseline CPL</th>
                      <th scope="col">Change</th>
                      <th scope="col">Reads as</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.trends.entries.map((entry) => (
                      <tr key={entry.window}>
                        <td className="text-mist-100">Last {entry.window}</td>
                        <td className="mono">
                          {entry.supported ? entry.recent_games : "—"}
                        </td>
                        <td className="mono">{number(entry.recent_average_cpl)}</td>
                        <td className="mono">
                          {entry.supported ? entry.baseline_games : "—"}
                        </td>
                        <td className="mono">{number(entry.baseline_average_cpl)}</td>
                        <td className="mono">
                          {entry.relative_change === null
                            ? "—"
                            : `${(entry.relative_change * 100).toFixed(1)}%`}
                        </td>
                        <td>
                          {entry.supported ? (
                            <span
                              className={`badge ${
                                entry.direction === "lower_cpl"
                                  ? "border-emerald-primary/40 text-emerald-300"
                                  : entry.direction === "higher_cpl"
                                    ? "border-amber-primary/40 text-amber-300"
                                    : "border-ink-600 text-mist-400"
                              }`}
                            >
                              {entry.direction === "lower_cpl"
                                ? "Lower CPL than baseline"
                                : entry.direction === "higher_cpl"
                                  ? "Higher CPL than baseline"
                                  : "In line with baseline"}
                            </span>
                          ) : entry.note ? (
                            <span className="text-small text-mist-500">{entry.note}</span>
                          ) : (
                            <span className="text-small text-mist-500">Not supported yet</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Note>
                A difference is reported as a measurement, not as a conclusion about improvement.
                Statistical significance is explicitly out of scope for this phase.
              </Note>
            </Panel>

            <Panel
              title="Chess DNA"
              subtitle={`Behavioral dimensions derived from ${data.chess_dna.derived_from_games} analyzed game${data.chess_dna.derived_from_games === 1 ? "" : "s"}. Each dimension states its definition, its sample and how much the data supports it.`}
            >
              <div className="divide-y divide-ink-800/70">
                {data.chess_dna.dimensions.map((dimension) => (
                  <DnaRow key={dimension.key} dimension={dimension} />
                ))}
              </div>
              {data.chess_dna.notes.length > 0 ? (
                <Disclosure summary="How these dimensions are defined">
                  <div className="space-y-1">
                    {data.chess_dna.notes.map((note) => (
                      <Note key={note}>{note}</Note>
                    ))}
                  </div>
                </Disclosure>
              ) : null}
            </Panel>
          </div>
        </TabPanel>
      ) : null}

      {tab === "method" ? (
        <TabPanel>
          <div className="space-y-4">
            <Panel
              title="Provenance"
              subtitle="What produced this profile, so a later methodology change never silently rewrites an earlier reading."
            >
              <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
                <MethodRow term="Profile version" value={data.profile_version} />
                <MethodRow term="Methodology version" value={data.methodology_version} />
                <MethodRow term="Feature version" value={data.feature_version} />
                <MethodRow term="Computed" value={formatStamp(data.generated_at)} />
                <MethodRow
                  term="Snapshot state"
                  value={data.cache?.hit ? "served from stored snapshot" : "computed for this request"}
                />
                <MethodRow
                  term="Input signature"
                  value={data.cache?.signature ? data.cache.signature.slice(0, 28) : "—"}
                  mono
                />
                <MethodRow
                  term="Coverage"
                  value={COVERAGE_LABEL[data.coverage]}
                />
                <MethodRow term="Excluded games" value={String(data.excluded_games)} />
              </dl>
              {data.cache?.updated_at ? (
                <Note>Last stored update: {formatStamp(data.cache.updated_at)}</Note>
              ) : null}
            </Panel>

            <Panel
              title="Feature set (ML-ready)"
              subtitle="The numeric interface a later phase can build on. Phase 5 trains no model, and none of these values is a prediction."
            >
              {features === null ? (
                <div>
                  <p className="mb-3 text-small leading-relaxed text-mist-400">
                    Showing the values is opt-in, because they are an interface rather than a
                    dashboard: each one ships with its definition, version and sample size.
                  </p>
                  <button type="button" className="btn" onClick={loadFeatures}>
                    Load feature set
                  </button>
                  {featuresError ? (
                    <p role="alert" className="mt-2 text-small text-rose-300">
                      {featuresError}
                    </p>
                  ) : null}
                </div>
              ) : (
                <div>
                  <div className="mb-3 flex flex-wrap gap-2">
                    <span className="badge border-sky-primary/40 text-sky-300">
                      version {features.feature_version}
                    </span>
                    <span className="badge border-ink-600 text-mist-400">
                      {features.games_analyzed} games
                    </span>
                    <span className="badge border-violet-primary/40 text-violet-300">
                      user-specific · not training-eligible
                    </span>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="data-table min-w-[720px]">
                      <thead>
                        <tr>
                          <th scope="col">Feature</th>
                          <th scope="col">Value</th>
                          <th scope="col">Unit</th>
                          <th scope="col">Sample</th>
                        </tr>
                      </thead>
                      <tbody>
                        {features.features.map((feature) => (
                          <tr key={feature.name}>
                            <td>
                              <span className="text-mist-100">
                                {feature.name.replaceAll("_", " ")}
                              </span>
                              <p className="text-meta text-mist-500">{feature.definition}</p>
                            </td>
                            <td className="mono">
                              {feature.value === null ? "—" : number(feature.value, 3)}
                            </td>
                            <td className="text-mist-400">{feature.unit}</td>
                            <td className="mono">{feature.sample_size}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="mt-3 space-y-1">
                    {features.notes.map((note) => (
                      <Note key={note}>{note}</Note>
                    ))}
                  </div>
                </div>
              )}
            </Panel>
          </div>
        </TabPanel>
      ) : null}
        </>
      ) : null}
    </div>
  );
}

// --- small local helpers ------------------------------------------------------

function MiniStat({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | number;
  hint?: string;
}) {
  return (
    <div>
      <p className="text-meta text-mist-500">{label}</p>
      <p className="mono mt-0.5 text-sm text-mist-100">{value}</p>
      {hint ? <p className="text-meta text-mist-500">{hint}</p> : null}
    </div>
  );
}

function MethodRow({ term, value, mono }: { term: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-ink-800/70 py-1.5">
      <dt className="text-small text-mist-500">{term}</dt>
      <dd className={`text-small text-mist-100 ${mono ? "mono" : ""}`}>{value}</dd>
    </div>
  );
}

function RepertoireTable({
  title,
  rows,
}: {
  title: string;
  rows: PlayerProfilePayload["openings"]["white_repertoire"];
}) {
  return (
    <div>
      <p className="label">{title}</p>
      {rows.length === 0 ? (
        <p className="mt-1.5 text-small text-mist-500">No analyzed games in this colour yet.</p>
      ) : (
        <div className="mt-1.5 overflow-x-auto">
          <table className="data-table min-w-[420px]">
            <thead>
              <tr>
                <th scope="col">Opening</th>
                <th scope="col">Games</th>
                <th scope="col">Record</th>
                <th scope="col">Accuracy</th>
                <th scope="col">CPL</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key}>
                  <td>
                    <div className="flex items-center gap-2">
                      {row.eco_code ? (
                        <span className="mono badge border-ink-600 text-mist-400">{row.eco_code}</span>
                      ) : null}
                      <span className="text-mist-100">{row.name ?? row.key}</span>
                    </div>
                    {row.sample.note ? (
                      <p className="text-meta text-mist-500">{row.sample.note}</p>
                    ) : null}
                  </td>
                  <td className="mono">{row.games}</td>
                  <td className="mono text-mist-300">
                    {row.wins}W·{row.draws}D·{row.losses}L
                  </td>
                  <td className="mono">
                    {row.average_accuracy === null ? "—" : `${number(row.average_accuracy)}%`}
                  </td>
                  <td className="mono">{number(row.average_centipawn_loss)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const CATEGORY_LABEL: Record<string, string> = {
  strength: "Strengths observed",
  weakness_candidate: "Possible weak spots",
  recurring_pattern: "Recurring patterns",
  improvement_trend: "Trend signals",
  opening_pattern: "Opening patterns",
  tactical_pattern: "Tactical patterns",
  positional_pattern: "Positional patterns",
  phase_pattern: "Phase patterns",
  conversion_pattern: "Conversion patterns",
  recovery_pattern: "Recovery patterns",
};

function groupInsights(insights: PlayerInsight[]) {
  const groups = new Map<string, PlayerInsight[]>();
  for (const insight of insights) {
    const list = groups.get(insight.category) ?? [];
    list.push(insight);
    groups.set(insight.category, list);
  }
  return [...groups.entries()].map(([category, items]) => ({
    category,
    label: CATEGORY_LABEL[category] ?? category.replaceAll("_", " "),
    items,
  }));
}

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/**
 * Provenance timestamps, formatted from the ISO string itself.
 *
 * `toLocaleString` renders differently on the server (container locale/timezone)
 * and in the browser, which React reports as a hydration mismatch. These stamps
 * are stored in UTC, so formatting them explicitly in UTC is both stable and
 * accurate.
 */
function formatStamp(value: string | null | undefined): string {
  if (!value) return "—";
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value);
  if (!match) return value;
  const [, year, month, day, hour, minute] = match;
  return `${day} ${MONTHS[Number(month) - 1]} ${year}, ${hour}:${minute} UTC`;
}
