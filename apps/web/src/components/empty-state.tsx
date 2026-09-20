// Honest empty / error / loading states. The UI never displays fabricated
// statistics: when functionality is not implemented, it says so.

export function EmptyState({
  title,
  message,
  action,
}: {
  title: string;
  message: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-dashed border-neutral-300 bg-neutral-50 px-6 py-12 text-center">
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-neutral-200 text-neutral-500">
        ♟
      </div>
      <h3 className="text-sm font-semibold text-neutral-900">{title}</h3>
      <p className="mt-1 max-w-sm text-sm text-neutral-500">{message}</p>
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

export function ErrorState({ title, message }: { title: string; message: string }) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3">
      <h3 className="text-sm font-semibold text-red-800">{title}</h3>
      <p className="mt-1 text-sm text-red-700">{message}</p>
    </div>
  );
}

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center py-12 text-sm text-neutral-500">
      <span className="mr-2 inline-block h-4 w-4 animate-spin rounded-full border-2 border-neutral-300 border-t-neutral-700" />
      {label}
    </div>
  );
}
