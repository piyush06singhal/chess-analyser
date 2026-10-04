"use client";

// System health — the operator view (§52). Read-only, and deliberately small: it
// renders the two JSON endpoints that already exist rather than inventing a new
// telemetry surface.
//
//   GET /ready              → per-dependency probe + overall readiness
//   GET /api/graph/health   → the derived graph's consistency report
//
// It shows nothing about game content: a counter is a count, a status is a
// status. Health is honest — a missing engine is shown as missing, not smoothed
// into "OK". This page is not in the player navigation; it is linked from the
// footer, because operating the service is not a chess task.

import { useCallback, useEffect, useState } from "react";

import { ErrorState, LoadingState, StatusDot } from "@/components/empty-state";
import { Panel } from "@/components/ui";
import { api, graphHealth, type ReadyCheck, type ReadyResponse } from "@/lib/api";

//: The checks `/ready` reports, in the order an operator reasons about them:
//: storage → engine → schema → models → provider.
const CHECK_ORDER: Array<[key: string, label: string]> = [
  ["database", "Database"],
  ["stockfish", "Stockfish engine"],
  ["migrations", "Schema tables"],
  ["redis", "Redis"],
  ["ml_registry", "ML model registry"],
  ["ai_provider", "AI provider"],
];

/** The single most useful line a check carries, without dumping its whole payload. */
function checkDetail(check: ReadyCheck): string {
  const detail = check.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const inner = detail as Record<string, unknown>;
    if (typeof inner.detail === "string") return inner.detail;
    if (typeof inner.reason === "string") return inner.reason;
    if (typeof inner.connected === "boolean") {
      if (!inner.connected) return "Not connected.";
      const target =
        inner.dialect ?? (inner.host ? `${inner.host}:${inner.port ?? ""}` : null);
      return target ? `Connected (${target}).` : "Connected.";
    }
  }
  return check.ok ? "Reported healthy." : "Reported unavailable.";
}

/** A measured value worth showing next to a check, when the check has one. */
function checkMeta(check: ReadyCheck): string | null {
  // The engine version is only known once Stockfish has started; until then there
  // is nothing worth adding beside the label.
  if (check.name) return check.version ? `${check.name} ${check.version}` : null;
  if (check.provider) return check.provider;
  if (typeof check.present === "number" && typeof check.expected === "number") {
    return `${check.present}/${check.expected} tables`;
  }
  if (typeof check.registered === "number") {
    const production = check.production_models?.length ?? 0;
    return `${check.registered} registered · ${production} in production`;
  }
  if (typeof check.configured === "boolean") {
    return check.configured ? "configured" : "not configured";
  }
  return null;
}

interface GraphHealth {
  healthy: boolean;
  schema_version: string;
  report: {
    node_count: number;
    edge_count: number;
    orphan_nodes: { count: number };
    dangling_edges: { count: number };
    invalid_edges: { count: number };
    missing_evidence: { count: number };
    stale_edges: { count: number };
    duplicate_edges: { count: number };
  };
}

export default function SystemHealthPage() {
  const [ready, setReady] = useState<ReadyResponse | null>(null);
  const [graph, setGraph] = useState<GraphHealth | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Both reads are independent; a failed graph check must not hide the API's own
  // readiness, so they are settled separately. The state updates live in this
  // callback so they run after the promises settle — never synchronously inside an
  // effect body (which React flags as a cascading render).
  const apply = useCallback(
    (
      readyResult: PromiseSettledResult<ReadyResponse>,
      graphResult: PromiseSettledResult<unknown>,
    ) => {
      if (readyResult.status === "fulfilled") {
        setReady(readyResult.value);
        setError(null);
      } else {
        setError(
          readyResult.reason instanceof Error
            ? readyResult.reason.message
            : "System health could not be loaded.",
        );
      }
      setGraph(graphResult.status === "fulfilled" ? (graphResult.value as GraphHealth) : null);
      setLoading(false);
    },
    [],
  );

  useEffect(() => {
    Promise.allSettled([api.ready(), graphHealth()]).then(([ready, graph]) =>
      apply(ready, graph),
    );
  }, [apply]);

  // Refresh re-arms the loading state from an event handler, not the effect.
  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.allSettled([api.ready(), graphHealth()]).then(([ready, graph]) =>
      apply(ready, graph),
    );
  }, [apply]);

  const isReady = ready?.status === "ready";

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="eyebrow">Operations</p>
          <h1 className="title mt-1">System health</h1>
          <p className="subtitle mt-1 max-w-2xl">
            A read-only view of the service&apos;s dependencies, read from the same JSON the
            deployment exposes.
          </p>
        </div>
        <button type="button" className="btn btn-ghost" onClick={refresh} disabled={loading}>
          Refresh
        </button>
      </div>

      {error ? <ErrorState title="Could not load system health" message={error} /> : null}
      {loading && !ready ? <LoadingState label="Probing dependencies…" /> : null}

      {ready ? (
        <>
          <div
            className={`flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border p-4 ${
              isReady
                ? "border-emerald-primary/40 bg-emerald-primary/10"
                : "border-amber-primary/40 bg-amber-primary/10"
            }`}
          >
            <span className="flex items-center gap-2.5">
              <StatusDot ok={isReady} pulse={isReady} />
              <span className="text-base font-semibold text-mist-50">
                {isReady ? "Ready" : "Degraded"}
              </span>
            </span>
            <span className="mono text-small text-mist-300">
              {ready.service} {ready.version} · {ready.environment}
            </span>
            {ready.blocking.length ? (
              <span className="text-small text-amber-300">
                Blocking: {ready.blocking.join(", ")}
              </span>
            ) : (
              <span className="text-small text-mist-400">
                Every blocking dependency is up; Redis, migrations, models and the AI provider
                are reported below and do not gate serving.
              </span>
            )}
          </div>

          <Panel title="Dependencies" subtitle="Each probe is a real check, not a stub.">
            <ul className="divide-y divide-ink-800/70">
              {CHECK_ORDER.filter(([key]) => key in ready.checks).map(([key, label]) => {
                const check = ready.checks[key];
                const meta = checkMeta(check);
                return (
                  <li key={key} className="flex items-start gap-3 py-2.5">
                    <span className="mt-1.5">
                      <StatusDot ok={check.ok} />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                        <span className="text-small font-medium text-mist-100">{label}</span>
                        {meta ? (
                          <span className="mono text-meta text-mist-500">{meta}</span>
                        ) : null}
                      </div>
                      <p className="mt-0.5 text-small leading-relaxed text-mist-400">
                        {checkDetail(check)}
                      </p>
                    </div>
                  </li>
                );
              })}
            </ul>
          </Panel>
        </>
      ) : null}

      {graph ? (
        <Panel
          title="Intelligence graph"
          subtitle={`The derived graph's consistency report · schema ${graph.schema_version}`}
        >
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <span className="flex items-center gap-2.5">
              <StatusDot ok={graph.healthy} />
              <span className="text-base font-semibold text-mist-50">
                {graph.healthy ? "Consistent" : "Inconsistent"}
              </span>
            </span>
            <span className="mono text-small text-mist-300">
              {graph.report.node_count} nodes · {graph.report.edge_count} edges
            </span>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
            {[
              ["Orphan nodes", graph.report.orphan_nodes.count],
              ["Dangling edges", graph.report.dangling_edges.count],
              ["Invalid edges", graph.report.invalid_edges.count],
              ["Missing evidence", graph.report.missing_evidence.count],
              ["Stale edges", graph.report.stale_edges.count],
              ["Duplicate edges", graph.report.duplicate_edges.count],
            ].map(([label, value]) => (
              <div
                key={label as string}
                className="rounded-lg border border-ink-700 bg-ink-900/50 px-3 py-2"
              >
                <p className="text-meta text-mist-500">{label}</p>
                <p
                  className={`mono mt-0.5 text-base font-semibold ${
                    value === 0 ? "text-mist-200" : "text-amber-300"
                  }`}
                >
                  {value}
                </p>
              </div>
            ))}
          </div>
          <p className="mt-3 text-meta leading-relaxed text-mist-500">
            Only dangling edges, invalid edges, missing evidence and duplicates make the graph
            inconsistent; orphan nodes and stale edges are reported so an interrupted rebuild is
            visible.
          </p>
        </Panel>
      ) : null}

      <p className="text-meta leading-relaxed text-mist-500">
        Source: <span className="mono">GET /ready</span> and{" "}
        <span className="mono">GET /api/graph/health</span>. Per-process counters (requests,
        refusals, engine time, cache) are served at <span className="mono">/metrics</span> and{" "}
        <span className="mono">/api/scenarios/metrics</span>; they reset on restart and hold
        counts only.
      </p>
    </div>
  );
}
