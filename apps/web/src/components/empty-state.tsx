// Honest empty / error / loading states. The UI never displays fabricated
// statistics: when functionality is not implemented, it says so.

export function EmptyState({
  title,
  message,
  action,
  icon = "♟",
}: {
  title: string;
  message: string;
  action?: React.ReactNode;
  icon?: string;
}) {
  return (
    <div className="animate-fade-up flex flex-col items-center justify-center rounded-xl border border-dashed border-ink-600 bg-ink-850/50 px-6 py-12 text-center">
      <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-xl border border-ink-600 bg-ink-800 text-lg text-mist-500">
        {icon}
      </div>
      <h3 className="text-sm font-semibold text-mist-200">{title}</h3>
      {/* Body copy clears AA contrast, including on this translucent panel. */}
      <p className="mt-1 max-w-sm text-sm leading-relaxed text-mist-300">{message}</p>
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}

export function ErrorState({ title, message }: { title: string; message: string }) {
  return (
    <div
      role="alert"
      className="animate-fade-in rounded-xl border border-rose-500/25 bg-rose-500/10 px-4 py-3"
    >
      <h3 className="text-sm font-semibold text-rose-300">{title}</h3>
      <p className="mt-1 text-sm leading-relaxed text-rose-200">{message}</p>
    </div>
  );
}

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center py-12 text-sm text-mist-500">
      <span className="mr-2.5 inline-block h-4 w-4 animate-spin rounded-full border-2 border-ink-600 border-t-emerald-400" />
      {label}
    </div>
  );
}

export function SkeletonRows({ rows = 4 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-hidden>
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="skeleton h-14"
          style={{ animationDelay: `${i * 120}ms`, opacity: 1 - i * 0.15 }}
        />
      ))}
    </div>
  );
}

export function StatusDot({ ok, pulse = false }: { ok: boolean; pulse?: boolean }) {
  return (
    <span className="relative inline-flex h-2 w-2">
      {ok && pulse ? (
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
      ) : null}
      <span
        className={`relative inline-flex h-2 w-2 rounded-full ${
          ok ? "bg-emerald-400" : "bg-mist-600"
        }`}
      />
    </span>
  );
}
