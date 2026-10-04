"use client";

// Study Collections (§29): named, user-curated sets of pointers.
//
// A collection never copies a game or an analysis; it holds typed pointers, and
// the page makes that visible. Creating, adding and removing are all real
// operations against the backend, and a refused item (a kind the collection does
// not permit, or a duplicate) surfaces the backend's own reason rather than a
// generic error.

import { useCallback, useEffect, useState } from "react";

import { EmptyState, ErrorState, LoadingState } from "@/components/empty-state";
import { Panel } from "@/components/ui";
import { api, type PlayerListItem, type StudyCollection } from "@/lib/api";

const COLLECTION_KINDS = [
  "mixed",
  "game_set",
  "opening_study",
  "endgame_study",
  "tactics",
  "position_set",
];

const ITEM_KINDS = ["game", "position", "training", "scenario", "insight", "opening", "endgame"];

export default function CollectionsPage() {
  const [players, setPlayers] = useState<PlayerListItem[]>([]);
  const [playerId, setPlayerId] = useState<number | null>(null);
  const [collections, setCollections] = useState<StudyCollection[]>([]);
  const [selected, setSelected] = useState<StudyCollection | null>(null);
  const [name, setName] = useState("");
  const [kind, setKind] = useState("mixed");
  const [itemKind, setItemKind] = useState("game");
  const [itemRef, setItemRef] = useState("");
  const [itemLabel, setItemLabel] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (id: number) => {
    setLoading(true);
    setError(null);
    try {
      const body = await api.listCollections(id);
      setCollections(body.collections);
      setSelected((current) => current);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Collections could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    api
      .listPlayers()
      .then((body) => {
        setPlayers(body.players);
        if (body.players.length) {
          const id = Number(body.players[0].id);
          setPlayerId(id);
          void load(id);
        }
      })
      .catch(() => undefined);
  }, [load]);

  const create = useCallback(async () => {
    if (!playerId || !name.trim()) return;
    setError(null);
    try {
      await api.createCollection({ player_id: String(playerId), name, kind });
      setName("");
      await load(playerId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The collection could not be created.");
    }
  }, [playerId, name, kind, load]);

  const addItem = useCallback(async () => {
    if (!selected || !playerId || !itemRef.trim()) return;
    setError(null);
    try {
      const updated = await api.addCollectionItem(selected.id, {
        player_id: String(playerId),
        kind: itemKind,
        ref: itemRef,
        label: itemLabel,
      });
      setSelected(updated);
      setItemRef("");
      setItemLabel("");
      await load(playerId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The item could not be added.");
    }
  }, [selected, playerId, itemKind, itemRef, itemLabel, load]);

  const removeItem = useCallback(
    async (collectionKind: string, ref: string) => {
      if (!selected || !playerId) return;
      setError(null);
      try {
        const updated = await api.removeCollectionItem(selected.id, {
          kind: collectionKind,
          ref,
          player_id: playerId,
        });
        setSelected(updated);
        await load(playerId);
      } catch (err) {
        setError(err instanceof Error ? err.message : "The item could not be removed.");
      }
    },
    [selected, playerId, load]
  );

  const removeCollection = useCallback(
    async (id: number) => {
      if (!playerId) return;
      setError(null);
      try {
        await api.deleteCollection(id, playerId);
        if (selected?.id === id) setSelected(null);
        await load(playerId);
      } catch (err) {
        setError(err instanceof Error ? err.message : "The collection could not be deleted.");
      }
    },
    [playerId, selected, load]
  );

  return (
    <div className="space-y-5">
      <div>
        <p className="eyebrow">Players</p>
        <h1 className="title mt-1">Study Collections</h1>
        <p className="subtitle mt-1 max-w-2xl">
          Curate the games, positions, exercises and insights worth returning to.
        </p>
      </div>

      {players.length ? (
        <Panel title="Player">
          <select
            className="input"
            aria-label="Player"
            value={playerId ?? ""}
            onChange={(event) => {
              const id = Number(event.target.value);
              setPlayerId(id);
              setSelected(null);
              void load(id);
            }}
          >
            {players.map((player) => (
              <option key={player.id} value={player.id}>
                {player.name}
              </option>
            ))}
          </select>
        </Panel>
      ) : null}

      {error ? <ErrorState title="Collections" message={error} /> : null}

      <Panel title="New collection">
        <div className="flex flex-wrap items-end gap-2">
          <label className="min-w-[12rem] flex-1">
            <span className="label">Name</span>
            <input
              className="input mt-1 w-full"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. Endgames to revisit"
            />
          </label>
          <label>
            <span className="label">Kind</span>
            <select className="input mt-1" value={kind} onChange={(event) => setKind(event.target.value)}>
              {COLLECTION_KINDS.map((option) => (
                <option key={option} value={option}>
                  {option.replace(/_/g, " ")}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="btn btn-primary" onClick={() => void create()}>
            Create
          </button>
        </div>
      </Panel>

      {loading ? <LoadingState label="Loading collections…" /> : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Your collections">
          {collections.length === 0 ? (
            <EmptyState
              title="No collections yet"
              message="Create one above, then add games, positions or exercises to it."
            />
          ) : (
            <ul className="space-y-2">
              {collections.map((collection) => (
                <li
                  key={collection.id}
                  className="flex items-center justify-between gap-3 rounded-xl border border-ink-700 bg-ink-850 p-3"
                >
                  <button
                    type="button"
                    className="min-w-0 text-left"
                    onClick={() => setSelected(collection)}
                  >
                    <p className="text-small font-semibold text-mist-100">{collection.name}</p>
                    <p className="mono text-meta text-mist-500">
                      {collection.kind.replace(/_/g, " ")} · {collection.size} item(s)
                    </p>
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost"
                    onClick={() => void removeCollection(collection.id)}
                  >
                    Delete
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title={selected ? `Items · ${selected.name}` : "Items"}>
          {!selected ? (
            <EmptyState
              title="Select a collection"
              message="Pick a collection on the left to see and edit the pointers it holds."
            />
          ) : (
            <div className="space-y-3">
              {selected.items.length === 0 ? (
                <p className="text-small text-mist-400">
                  This collection is empty. That is a valid state — nothing is suggested on your
                  behalf.
                </p>
              ) : (
                <ul className="space-y-1.5">
                  {selected.items.map((item) => (
                    <li
                      key={`${item.kind}-${item.ref}`}
                      className="flex items-center justify-between gap-2 border-b border-ink-800/70 pb-1.5 last:border-0"
                    >
                      <span className="min-w-0 text-small text-mist-200">
                        <span className="badge border-ink-600 text-mist-400">{item.kind}</span>{" "}
                        {item.label || item.ref}
                      </span>
                      <button
                        type="button"
                        className="btn btn-ghost"
                        onClick={() => void removeItem(item.kind, item.ref)}
                      >
                        Remove
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              <div className="border-t border-ink-700 pt-3">
                <p className="label">Add a pointer</p>
                <div className="mt-2 flex flex-wrap items-end gap-2">
                  <label>
                    <span className="label">Kind</span>
                    <select
                      className="input mt-1"
                      value={itemKind}
                      onChange={(event) => setItemKind(event.target.value)}
                    >
                      {ITEM_KINDS.map((option) => (
                        <option key={option} value={option}>
                          {option}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="min-w-[10rem] flex-1">
                    <span className="label">Stored identifier</span>
                    <input
                      className="input mt-1 w-full"
                      value={itemRef}
                      onChange={(event) => setItemRef(event.target.value)}
                      placeholder="game id, position id, opening…"
                    />
                  </label>
                  <label className="min-w-[10rem] flex-1">
                    <span className="label">Label</span>
                    <input
                      className="input mt-1 w-full"
                      value={itemLabel}
                      onChange={(event) => setItemLabel(event.target.value)}
                    />
                  </label>
                  <button type="button" className="btn btn-secondary" onClick={() => void addItem()}>
                    Add
                  </button>
                </div>
              </div>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}
