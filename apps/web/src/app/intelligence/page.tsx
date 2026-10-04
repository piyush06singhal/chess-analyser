"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import {
  api,
  ApiError,
  graphExploreGame,
  graphExplorePlayer,
  graphExplorePosition,
  graphListNodes,
  graphMethod,
  graphNeighborhood,
  graphWhy,
} from "@/lib/api";
import type {
  GameListItem,
  GraphConcept,
  GraphGameExplorer,
  GraphMethod,
  GraphNeighborhood,
  GraphNeighborhoodNode,
  GraphNodeSummary,
  GraphPlayerExplorer,
  GraphPositionExplorer,
  GraphWhyTrace,
  PlayerListItem,
} from "@/lib/api";
import { AnalysisChessboard } from "@/components/chessboard";
import { ErrorState, LoadingState } from "@/components/empty-state";
import { IntelligenceMap, NeighborhoodList } from "@/components/intelligence-map";

const START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

type Tab = "position" | "game" | "player" | "map";

const TAB_LABELS: [Tab, string][] = [
  ["position", "Position"],
  ["game", "Game"],
  ["player", "Player"],
  ["map", "Map"],
];

function parseTab(value: string | null): Tab {
  return value === "game" || value === "player" || value === "map" ? value : "position";
}

function parseDepth(value: string | null): number {
  const parsed = Number(value);
  return parsed === 1 || parsed === 2 || parsed === 3 ? parsed : 2;
}

/**
 * The Intelligence Explorer (§40–§42) and the Intelligence Map (§39).
 *
 * One surface over the Phase 13 graph. Everything on screen is stored evidence:
 * an exact match is labelled exact, a weaker similarity keeps its own name, and a
 * section with nothing behind it says so rather than filling in.
 *
 * **The URL is the state.** The active tab and, for the map, the node and depth
 * live in the query string, so a neighbourhood — and the tab you were reading —
 * can be linked, bookmarked and reloaded. The controls below write to the URL
 * with ``replace`` rather than keeping a second copy in component state, which is
 * what stops the two from drifting.
 *
 * ``useSearchParams`` suspends during static prerender, so the surface sits under
 * one boundary. The fallback keeps the heading so the page does not flash empty.
 */
export default function IntelligenceExplorerPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-6">
          <p className="eyebrow">Analytics</p>
          <h1 className="title mt-1">Intelligence Explorer</h1>
          <div className="card p-4">
            <LoadingState label="Reading the graph…" />
          </div>
        </div>
      }
    >
      <IntelligenceExplorer />
    </Suspense>
  );
}

function IntelligenceExplorer() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [method, setMethod] = useState<GraphMethod | null>(null);
  const tab = parseTab(searchParams.get("tab"));
  const nodeType = searchParams.get("type") ?? "player";
  const nodeKey = searchParams.get("key") ?? "";
  const depth = parseDepth(searchParams.get("depth"));

  useEffect(() => {
    graphMethod()
      .then(setMethod)
      .catch(() => setMethod(null));
  }, []);

  // The live query string, read by every write below rather than the value
  // captured when a callback was created. A write issued from an older render —
  // the map's auto-select is exactly that, resolving after the user has already
  // clicked something — would otherwise rebuild the URL from stale state and
  // silently revert the control the user just used. The ref is advanced by the
  // write itself as well as on render, so two writes in one tick compose instead
  // of the second discarding the first.
  const queryRef = useRef(searchParams.toString());
  useEffect(() => {
    queryRef.current = searchParams.toString();
  }, [searchParams]);

  // The single writer for every URL-backed control. A `replace` (not a push)
  // keeps the back button for navigation rather than for each control change.
  const setParams = useCallback(
    (updates: Record<string, string | null>) => {
      const params = new URLSearchParams(queryRef.current);
      for (const [key, value] of Object.entries(updates)) {
        if (value === null || value === "") params.delete(key);
        else params.set(key, value);
      }
      const query = params.toString();
      // Rewriting the URL the browser already shows would re-render the tree for
      // nothing, and it is the redundant write that reverts a newer one.
      if (query === queryRef.current) return;
      queryRef.current = query;
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [pathname, router],
  );

  const viewOnMap = useCallback(
    (type: string, key: string) => {
      setParams({ tab: "map", type, key });
    },
    [setParams],
  );

  // Stable identities: the map's node-list effect depends on `onSelect`, so an
  // inline arrow would re-run it on every render and refetch the picker's nodes
  // each time a URL-backed control changed.
  const selectNode = useCallback(
    (type: string, key: string) => setParams({ type, key }),
    [setParams],
  );

  const changeDepth = useCallback(
    (value: number) => setParams({ depth: String(value) }),
    [setParams],
  );

  return (
    <div className="space-y-6">
      <section className="animate-fade-up">
        <p className="eyebrow">Analytics</p>
        <h1 className="title mt-1">Intelligence Explorer</h1>
        <p className="subtitle mt-2 max-w-3xl">
          Every connection below is a stored relationship with evidence behind it.
          A similarity level is never upgraded — <span className="text-mist-300">exact</span>{" "}
          means the same position, and anything weaker keeps its own name.
        </p>
      </section>

      <div className="flex flex-wrap gap-2">
        {TAB_LABELS.map(([value, label]) => (
          <button
            key={value}
            type="button"
            aria-current={tab === value ? "true" : undefined}
            onClick={() => setParams({ tab: value })}
            className={`btn ${tab === value ? "btn-primary" : "btn-ghost"}`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "position" ? <PositionExplorer onViewOnMap={viewOnMap} /> : null}
      {tab === "game" ? <GameExplorer onViewOnMap={viewOnMap} /> : null}
      {tab === "player" ? <PlayerExplorer onViewOnMap={viewOnMap} /> : null}
      {tab === "map" ? (
        <MapExplorer
          nodeType={nodeType}
          nodeKey={nodeKey}
          depth={depth}
          nodeTypes={method?.node_types ?? []}
          onSelect={selectNode}
          onDepthChange={changeDepth}
        />
      ) : null}

      {/* The build versions are real provenance, but they are not the first
          thing a reader needs. They live behind a disclosure instead of a
          headline card, so the surface leads with the graph, not the metadata. */}
      {method ? (
        <details className="card p-4 text-small text-mist-500">
          <summary className="cursor-pointer font-medium text-mist-400">About this view</summary>
          <dl className="mt-3 grid gap-x-8 gap-y-1 sm:grid-cols-2">
            {[
              ["Schema", method.schema_version],
              ["Methodology", method.methodology_version],
              ["Knowledge", method.knowledge_version],
              ["Storage", method.storage],
            ].map(([label, value]) => (
              <div key={label} className="flex items-baseline justify-between gap-3">
                <dt>{label}</dt>
                <dd className="mono text-mist-300">{value}</dd>
              </div>
            ))}
          </dl>
        </details>
      ) : null}
    </div>
  );
}

// --- Position -----------------------------------------------------------------

type ExplorerProps = {
  onViewOnMap: (nodeType: string, nodeKey: string, label?: string) => void;
};

function PositionExplorer({ onViewOnMap }: ExplorerProps) {
  const [fen, setFen] = useState(START_FEN);
  const [result, setResult] = useState<GraphPositionExplorer | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function explore(event: React.FormEvent) {
    event.preventDefault();
    if (!fen.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await graphExplorePosition(fen.trim()));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The explorer could not be reached.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <form onSubmit={explore} className="card animate-fade-up space-y-4 p-5">
        <div>
          <label htmlFor="graph-fen" className="label">
            Position (FEN)
          </label>
          <input
            id="graph-fen"
            value={fen}
            onChange={(event) => setFen(event.target.value)}
            spellCheck={false}
            className="input mt-1.5 mono text-xs"
            placeholder="Paste a FEN position"
          />
        </div>
        <div className="flex justify-end">
          <button type="submit" className="btn btn-primary" disabled={loading || !fen.trim()}>
            {loading ? "Exploring…" : "Explore"}
          </button>
        </div>
      </form>

      {error ? <ErrorState title="Exploration failed" message={error} /> : null}
      {loading ? <LoadingState label="Reading the graph…" /> : null}

      {result && !result.valid ? (
        <div className="card p-5">
          <p className="text-sm text-mist-400">{result.note}</p>
        </div>
      ) : null}

      {result && result.valid ? (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_460px]">
          <div className="card flex flex-col items-center gap-4 p-4 sm:p-6">
            <AnalysisChessboard fen={result.fen} />
            <p className="mono text-small text-mist-500">
              {result.position?.side_to_move} to move · {result.position?.position_hash.slice(0, 12)}…
            </p>
            <p className="text-small text-mist-500">
              {result.materialized
                ? "This position is materialised in the graph."
                : result.note ?? "This position is not in the graph yet."}
            </p>
            {result.position ? (
              <button
                type="button"
                className="btn btn-ghost text-xs"
                onClick={() =>
                  onViewOnMap(
                    "position",
                    result.position!.position_hash,
                    `${result.position!.side_to_move} to move`,
                  )
                }
              >
                View on map
              </button>
            ) : null}
          </div>

          <div className="space-y-4">
            <Section title="Exact matches" empty="No game contains this exact position." count={result.exact_matches?.length ?? 0}>
              <ul className="space-y-2">
                {(result.exact_matches ?? []).map((match) => (
                  <li key={match.game_id} className="rounded-lg border border-ink-700 bg-ink-900/60 px-3 py-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-small text-mist-200">
                        {match.white} vs {match.black}
                      </span>
                      <span className="badge shrink-0">exact</span>
                    </div>
                    <p className="mono mt-0.5 text-meta text-mist-500">
                      {match.result}
                      {match.date ? ` · ${match.date}` : ""}
                    </p>
                  </li>
                ))}
              </ul>
            </Section>

            <Section
              title="Similar positions"
              empty="Nothing weaker than an exact match was found in the scanned positions."
              count={Object.values(result.similar ?? {}).reduce((total, group) => total + group.length, 0)}
            >
              <div className="space-y-3">
                {Object.entries(result.similar ?? {}).map(([level, group]) => (
                  <div key={level}>
                    <p className="label mb-1">
                      {level.replace(/_/g, " ")} · {group.length}
                    </p>
                    <ul className="space-y-1">
                      {group.slice(0, 5).map((entry) => (
                        <li key={`${entry.game_id}:${entry.ply}`} className="mono text-meta text-mist-500">
                          {entry.game_id.slice(0, 8)}… ply {entry.ply}
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
              {result.similar_note ? (
                <p className="mt-3 text-meta text-mist-600">{result.similar_note}</p>
              ) : null}
            </Section>

            <Section
              title="Knowledge concepts"
              empty="Caissa has no deterministic rule that this position exhibits."
              count={result.knowledge_concepts?.length ?? 0}
            >
              <ul className="space-y-2">
                {(result.knowledge_concepts ?? []).map((concept) => (
                  <ConceptRow key={concept.slug} concept={concept} />
                ))}
              </ul>
            </Section>

            <Section title="Patterns" empty="No stored pattern points at this position." count={result.patterns?.length ?? 0}>
              <NodeList nodes={result.patterns ?? []} />
            </Section>

            <Section title="Training" empty="No exercise is derived from this position." count={result.training_positions?.length ?? 0}>
              <NodeList nodes={result.training_positions ?? []} />
            </Section>

            {result.openings && result.openings.length > 0 ? (
              <Section title="Openings" empty="" count={result.openings.length}>
                <div className="flex flex-wrap gap-2">
                  {result.openings.map((opening) => (
                    <span key={opening} className="badge">
                      {opening}
                    </span>
                  ))}
                </div>
              </Section>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

// --- Game ---------------------------------------------------------------------

function GameExplorer({ onViewOnMap }: ExplorerProps) {
  const [games, setGames] = useState<GameListItem[]>([]);
  const [gameId, setGameId] = useState("");
  const [result, setResult] = useState<GraphGameExplorer | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listGames()
      .then((listing) => {
        setGames(listing.games.slice(0, 50));
        if (listing.games[0]) setGameId(listing.games[0].id);
      })
      .catch(() => setGames([]));
  }, []);

  const explore = useCallback(async (id: string) => {
    if (!id) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await graphExploreGame(id));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The explorer could not be reached.");
    } finally {
      setLoading(false);
    }
  }, []);

  return (
    <div className="space-y-6">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void explore(gameId);
        }}
        className="card flex flex-wrap items-end gap-3 p-5"
      >
        <div className="min-w-[260px] flex-1">
          <label htmlFor="graph-game" className="label">
            Stored game
          </label>
          <select
            id="graph-game"
            value={gameId}
            onChange={(event) => setGameId(event.target.value)}
            className="input mt-1.5"
          >
            {games.length === 0 ? <option value="">No games stored</option> : null}
            {games.map((game) => (
              <option key={game.id} value={game.id}>
                {game.white_player} vs {game.black_player} — {game.result}
              </option>
            ))}
          </select>
        </div>
        <button type="submit" className="btn btn-primary" disabled={loading || !gameId}>
          {loading ? "Exploring…" : "Explore"}
        </button>
      </form>

      {error ? <ErrorState title="Exploration failed" message={error} /> : null}
      {loading ? <LoadingState label="Reading the graph…" /> : null}
      {result && !result.found ? (
        <div className="card p-5">
          <p className="text-sm text-mist-400">{result.note ?? "No such game is stored."}</p>
        </div>
      ) : null}

      {result && result.found && result.game ? (
        <div className="space-y-4">
          <div className="card p-5">
            <h2 className="text-lg font-semibold text-mist-50">
              {result.game.white} vs {result.game.black}
            </h2>
            <p className="mono mt-1 text-small text-mist-500">
              {result.game.result}
              {result.game.date ? ` · ${result.game.date}` : ""} · {result.game.analysis_status}
            </p>
            {result.openings && result.openings.length > 0 ? (
              <div className="mt-3 flex flex-wrap gap-2">
                {result.openings.map((opening) => (
                  <span key={opening} className="badge">
                    {opening}
                  </span>
                ))}
              </div>
            ) : (
              <p className="mt-2 text-small text-mist-500">
                No opening classification is stored for this game.
              </p>
            )}
            <button
              type="button"
              className="btn btn-ghost mt-3 text-xs"
              onClick={() =>
                onViewOnMap(
                  "game",
                  result.game_id,
                  `${result.game?.white ?? "?"} vs ${result.game?.black ?? "?"}`,
                )
              }
            >
              View on map
            </button>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <Section title="Positions" empty="No position is materialised for this game." count={result.positions?.length ?? 0}>
              {/* The count is already in the section header; list the positions
                  themselves rather than repeat the number in the body. */}
              <ul className="space-y-1">
                {(result.positions ?? []).map((entry) => (
                  <li key={entry.position_hash} className="mono text-meta text-mist-500">
                    {entry.position_hash.slice(0, 12)}… · {entry.edge}
                  </li>
                ))}
              </ul>
            </Section>
            <Section title="Patterns" empty="No stored pattern points at this game." count={result.patterns?.length ?? 0}>
              <NodeList nodes={result.patterns ?? []} />
            </Section>
            <Section title="Training" empty="No exercise derives from this game." count={result.training?.length ?? 0}>
              <NodeList nodes={result.training ?? []} />
            </Section>
            <Section title="Related games" empty="No other game shares this opening." count={result.related_games?.length ?? 0}>
              <ul className="space-y-1">
                {(result.related_games ?? []).map((related) => (
                  <li key={related.game_id} className="mono text-meta text-mist-500">
                    {related.game_id.slice(0, 8)}… · {related.shared_opening}
                  </li>
                ))}
              </ul>
            </Section>
          </div>
        </div>
      ) : null}
    </div>
  );
}

// --- Player -------------------------------------------------------------------

function PlayerExplorer({ onViewOnMap }: ExplorerProps) {
  const [players, setPlayers] = useState<PlayerListItem[]>([]);
  const [playerId, setPlayerId] = useState("");
  const [result, setResult] = useState<GraphPlayerExplorer | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [why, setWhy] = useState<GraphWhyTrace | null>(null);
  const [whyLoading, setWhyLoading] = useState(false);

  useEffect(() => {
    api
      .listPlayers()
      .then((listing) => {
        setPlayers(listing.players);
        if (listing.players[0]) setPlayerId(String(listing.players[0].id));
      })
      .catch(() => setPlayers([]));
  }, []);

  const explore = useCallback(async (id: string) => {
    if (!id) return;
    setLoading(true);
    setError(null);
    setResult(null);
    setWhy(null);
    try {
      setResult(await graphExplorePlayer(Number(id)));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The explorer could not be reached.");
    } finally {
      setLoading(false);
    }
  }, []);

  async function trace(node: GraphNodeSummary) {
    if (!node.node_type) return;
    setWhyLoading(true);
    setWhy(null);
    try {
      setWhy(await graphWhy(node.node_type, node.node_key));
    } catch {
      setWhy(null);
    } finally {
      setWhyLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void explore(playerId);
        }}
        className="card flex flex-wrap items-end gap-3 p-5"
      >
        <div className="min-w-[220px] flex-1">
          <label htmlFor="graph-player" className="label">
            Stored player
          </label>
          <select
            id="graph-player"
            value={playerId}
            onChange={(event) => setPlayerId(event.target.value)}
            className="input mt-1.5"
          >
            {players.length === 0 ? <option value="">No players stored</option> : null}
            {players.map((player) => (
              <option key={player.id} value={player.id}>
                {player.name}
              </option>
            ))}
          </select>
        </div>
        <button type="submit" className="btn btn-primary" disabled={loading || !playerId}>
          {loading ? "Exploring…" : "Explore"}
        </button>
      </form>

      {error ? <ErrorState title="Exploration failed" message={error} /> : null}
      {loading ? <LoadingState label="Reading the graph…" /> : null}
      {result && !result.found ? (
        <div className="card p-5">
          <p className="text-sm text-mist-400">{result.note ?? "No such player is stored."}</p>
        </div>
      ) : null}

      {result && result.found && result.player ? (
        <div className="space-y-4">
          <div className="card p-5">
            <h2 className="text-lg font-semibold text-mist-50">{result.player.name}</h2>
            <p className="mono mt-1 text-small text-mist-500">
              {result.game_count ?? 0} games · {result.patterns?.length ?? 0} patterns ·{" "}
              {result.training_attempts ?? 0} attempts ({result.training_correct ?? 0} correct)
            </p>
            <button
              type="button"
              className="btn btn-ghost mt-3 text-xs"
              onClick={() => onViewOnMap("player", String(result.player_id), result.player?.name)}
            >
              View on map
            </button>
          </div>

          <Section
            title="Patterns"
            empty="No evidenced pattern is stored for this player yet."
            count={result.patterns?.length ?? 0}
          >
            <ul className="space-y-2">
              {(result.patterns ?? []).map((pattern) => (
                <li
                  key={`${pattern.node_type}:${pattern.node_key}`}
                  className="flex items-start justify-between gap-3 rounded-lg border border-ink-700 bg-ink-900/60 px-3 py-2"
                >
                  <div className="min-w-0">
                    <p className="text-small text-mist-200">{pattern.label ?? pattern.node_key}</p>
                    <p className="mono mt-0.5 text-meta text-mist-600">
                      {pattern.node_type?.replace(/_/g, " ")}
                      {pattern.attributes?.claim_level ? ` · ${String(pattern.attributes.claim_level)}` : ""}
                    </p>
                  </div>
                  <button type="button" className="btn btn-ghost shrink-0 text-xs" onClick={() => trace(pattern)}>
                    Why?
                  </button>
                </li>
              ))}
            </ul>
          </Section>

          <Section title="Openings" empty="No opening is linked to this player yet." count={result.openings?.length ?? 0}>
            <div className="flex flex-wrap gap-2">
              {(result.openings ?? []).map((opening) => (
                <span key={opening} className="badge">
                  {opening}
                </span>
              ))}
            </div>
          </Section>

          {whyLoading ? <LoadingState label="Tracing evidence…" /> : null}
          {why ? <WhyPanel trace={why} /> : null}
        </div>
      ) : null}
    </div>
  );
}

// --- Map ----------------------------------------------------------------------

function MapExplorer({
  nodeType,
  nodeKey,
  depth,
  nodeTypes,
  onSelect,
  onDepthChange,
}: {
  nodeType: string;
  nodeKey: string;
  depth: number;
  nodeTypes: string[];
  onSelect: (nodeType: string, nodeKey: string) => void;
  onDepthChange: (depth: number) => void;
}) {
  const available = nodeTypes.length > 0 ? nodeTypes : ["player", "game", "position"];
  const [candidateState, setCandidateState] = useState<{
    type: string;
    nodes: GraphNodeSummary[];
    note: string | null;
  }>({ type: "", nodes: [], note: null });
  const [graph, setGraph] = useState<GraphNeighborhood | null>(null);
  const [selected, setSelected] = useState<GraphNeighborhoodNode | null>(null);
  const [why, setWhy] = useState<GraphWhyTrace | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The selection lives in the URL, so the candidate effect must read the latest
  // key without depending on it — a dependency on `nodeKey` would re-fetch the
  // node list on every selection.
  const nodeKeyRef = useRef(nodeKey);
  useEffect(() => {
    nodeKeyRef.current = nodeKey;
  }, [nodeKey]);

  // Pickable nodes for the current kind. State is written only from the async
  // callbacks and is stamped with the kind it belongs to, so a slow response for
  // a previous kind can never be shown as the current one.
  useEffect(() => {
    let cancelled = false;
    graphListNodes(nodeType, 100)
      .then((listing) => {
        if (cancelled) return;
        const nodes = listing.nodes ?? [];
        setCandidateState({
          type: nodeType,
          nodes,
          note: nodes.length === 0 ? "Caissa stores no node of that kind yet." : null,
        });
        // Nothing chosen yet → default to the first stored node. A key already in
        // the URL (a deep link) is left alone even when it is outside this page.
        if (!nodeKeyRef.current && nodes[0]) onSelect(nodeType, nodes[0].node_key);
      })
      .catch(() => {
        if (cancelled) return;
        setCandidateState({
          type: nodeType,
          nodes: [],
          note: "The node list could not be read.",
        });
      });
    return () => {
      cancelled = true;
    };
  }, [nodeType, onSelect]);

  const candidates = useMemo(
    () => (candidateState.type === nodeType ? candidateState.nodes : []),
    [candidateState, nodeType],
  );
  const listNote = candidateState.type === nodeType ? candidateState.note : null;

  const explore = useCallback(async (type: string, key: string, hops: number) => {
    if (!key) return;
    setLoading(true);
    setError(null);
    setGraph(null);
    setSelected(null);
    setWhy(null);
    try {
      setGraph(await graphNeighborhood(type, key, { depth: hops, limit: 120 }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "The map could not be reached.");
    } finally {
      setLoading(false);
    }
  }, []);

  // Re-map whenever the selection or depth changes. Deferred to a microtask so the
  // loader's own state writes do not cascade out of the effect synchronously (the
  // project's established fetch pattern).
  useEffect(() => {
    if (!nodeKey) return;
    void Promise.resolve().then(() => explore(nodeType, nodeKey, depth));
  }, [nodeType, nodeKey, depth, explore]);

  async function inspect(node: GraphNeighborhoodNode) {
    setSelected(node);
    setWhy(null);
    try {
      setWhy(await graphWhy(node.node_type, node.node_key));
    } catch {
      setWhy(null);
    }
  }

  // A deep-linked key may not be in the first page of candidates; keep it
  // selectable rather than silently replacing it with an unrelated node.
  const options = useMemo(() => {
    const opts = candidates.map((candidate) => ({
      key: candidate.node_key,
      label: candidate.label || candidate.node_key,
    }));
    if (nodeKey && !opts.some((option) => option.key === nodeKey)) {
      opts.unshift({ key: nodeKey, label: `${nodeKey.slice(0, 16)}…` });
    }
    return opts;
  }, [candidates, nodeKey]);

  return (
    <div className="space-y-6">
      <div className="card animate-fade-up space-y-4 p-5">
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)_auto] sm:items-end">
          <div>
            <label htmlFor="map-type" className="label">
              Node kind
            </label>
            <select
              id="map-type"
              className="input mt-1.5"
              value={nodeType}
              onChange={(event) => onSelect(event.target.value, "")}
            >
              {available.map((type) => (
                <option key={type} value={type}>
                  {type.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="map-key" className="label">
              Stored node
            </label>
            <select
              id="map-key"
              className="input mt-1.5"
              value={nodeKey}
              onChange={(event) => onSelect(nodeType, event.target.value)}
              disabled={options.length === 0}
            >
              {options.length === 0 ? <option value="">Nothing stored of this kind</option> : null}
              {options.map((option) => (
                <option key={option.key} value={option.key}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div>
            <span className="label">Depth</span>
            <div className="segmented mt-1.5">
              {[1, 2, 3].map((value) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={depth === value}
                  onClick={() => onDepthChange(value)}
                >
                  {value}
                </button>
              ))}
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-meta text-mist-500">
            {listNote ??
              "Pick any stored node to see what it connects to. This view lives in the URL, so a neighbourhood can be linked and reloaded."}
          </p>
          {loading ? <span className="mono text-meta text-mist-500">walking…</span> : null}
        </div>
      </div>

      {error ? <ErrorState title="The map could not be built" message={error} /> : null}
      {loading ? <LoadingState label="Walking the neighbourhood…" /> : null}

      {graph ? (
        <div className="space-y-4">
          <div className="card p-4">
            <IntelligenceMap
              graph={graph}
              selectedKey={
                selected ? `${selected.node_type}:${selected.node_key}` : graph.node
              }
              onSelect={inspect}
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            {selected ? (
              <div className="card p-4">
                <h3 className="label mb-2 flex items-center justify-between gap-2">
                  <span className="truncate">{selected.label || selected.node_key}</span>
                  <span className="badge shrink-0">{selected.node_type.replace(/_/g, " ")}</span>
                </h3>
                <p className="mono text-meta text-mist-600">
                  {selected.node_type}:{selected.node_key}
                </p>
                {selected.attributes && Object.keys(selected.attributes).length > 0 ? (
                  <dl className="mt-3 space-y-1">
                    {Object.entries(selected.attributes)
                      .slice(0, 10)
                      .map(([key, value]) => (
                        <div key={key} className="flex justify-between gap-3 text-small">
                          <dt className="text-mist-500">{key.replace(/_/g, " ")}</dt>
                          <dd className="mono max-w-[60%] truncate text-right text-mist-300">
                            {formatAttribute(value)}
                          </dd>
                        </div>
                      ))}
                  </dl>
                ) : (
                  <p className="mt-3 text-small text-mist-500">
                    This node carries no display attributes.
                  </p>
                )}
                <div className="mt-3 flex flex-wrap gap-2">
                  <button
                    type="button"
                    className="btn btn-ghost text-xs"
                    onClick={() => onSelect(selected.node_type, selected.node_key)}
                  >
                    Re-centre here
                  </button>
                </div>
              </div>
            ) : (
              <div className="card flex items-center p-4">
                <p className="text-small text-mist-500">
                  Select a node to see its attributes and to walk outward from it.
                </p>
              </div>
            )}
            {why ? <WhyPanel trace={why} /> : null}
          </div>

          <NeighborhoodList
            graph={graph}
            selectedKey={selected ? `${selected.node_type}:${selected.node_key}` : graph.node}
            onSelect={inspect}
          />
        </div>
      ) : null}
    </div>
  );
}

// --- shared pieces ------------------------------------------------------------

/** Render a stored attribute for display without inventing a value. */
function formatAttribute(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return value.length > 0 ? value.join(", ") : "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function Section({
  title,
  empty,
  count,
  children,
}: {
  title: string;
  empty: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <div className="card p-4">
      <h3 className="label mb-3 flex items-center justify-between">
        <span>{title}</span>
        <span className="mono text-mist-600">{count}</span>
      </h3>
      {count > 0 ? children : <p className="text-small text-mist-500">{empty}</p>}
    </div>
  );
}

function NodeList({ nodes }: { nodes: GraphNodeSummary[] }) {
  return (
    <ul className="space-y-1">
      {nodes.map((node) => (
        <li key={`${node.node_type}:${node.node_key}`} className="text-small text-mist-300">
          {node.label ?? node.node_key}
          {node.node_type ? (
            <span className="mono ml-2 text-meta text-mist-600">{node.node_type.replace(/_/g, " ")}</span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function ConceptRow({ concept }: { concept: GraphConcept }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="rounded-lg border border-ink-700 bg-ink-900/60 px-3 py-2">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-2 text-left"
        onClick={() => setOpen((value) => !value)}
      >
        <span className="text-small font-medium text-mist-100">{concept.name ?? concept.slug}</span>
        <span className="badge shrink-0">{concept.category}</span>
      </button>
      {open ? (
        <div className="mt-2 space-y-1 text-small text-mist-400">
          <p>{concept.definition}</p>
          {concept.how_to_spot ? (
            <p className="text-mist-500">
              <span className="text-mist-600">How to spot: </span>
              {concept.how_to_spot}
            </p>
          ) : null}
          {concept.source_id ? (
            <p className="mono text-meta text-mist-600">source: {concept.source_id}</p>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function WhyPanel({ trace }: { trace: GraphWhyTrace }) {
  if (!trace.found) {
    return (
      <div className="card p-4">
        <p className="text-sm text-mist-400">{trace.note ?? "Caissa has no such node available."}</p>
      </div>
    );
  }
  const evidenceKinds = Object.entries(trace.evidence ?? {});
  return (
    <div className="card space-y-3 p-4">
      <div className="flex items-center justify-between">
        <h3 className="label">Why? — {trace.label ?? trace.node}</h3>
        <span className="mono text-meta text-mist-600">methodology {trace.methodology_version}</span>
      </div>
      <p className="text-small text-mist-400">
        {trace.evidence_count ?? 0} evidence reference{(trace.evidence_count ?? 0) === 1 ? "" : "s"}
        {trace.sample_size ? ` · sample ${trace.sample_size}` : ""}
      </p>
      {evidenceKinds.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {evidenceKinds.map(([kind, refs]) => (
            <span key={kind} className="badge">
              {kind.replace(/_/g, " ")} · {refs.length}
            </span>
          ))}
        </div>
      ) : null}
      {trace.relationships && trace.relationships.length > 0 ? (
        <ul className="space-y-1">
          {trace.relationships.slice(0, 8).map((hop, index) => (
            <li key={index} className="mono text-meta text-mist-500">
              {hop.from_node} —{hop.edge_type}→ {hop.to_node}
              {hop.evidence_count ? ` (${hop.evidence_count} refs)` : ""}
            </li>
          ))}
        </ul>
      ) : null}
      {trace.gaps && trace.gaps.length > 0 ? (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2">
          <p className="text-meta text-amber-300">
            {trace.gaps.length} gap{trace.gaps.length === 1 ? "" : "s"} — a derived edge whose evidence could
            not be resolved:
          </p>
          <ul className="mt-1 space-y-0.5">
            {trace.gaps.slice(0, 4).map((gap, index) => (
              <li key={index} className="text-meta text-mist-500">
                {gap}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-meta text-mist-600">Every derived relationship here resolves to stored evidence.</p>
      )}
    </div>
  );
}
