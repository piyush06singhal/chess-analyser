"use client";

export default function GlobalError({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <div className="animate-fade-up flex flex-col items-center justify-center py-24 text-center">
      <span className="mb-4 text-3xl">⚠</span>
      <h1 className="text-lg font-semibold text-mist-200">Something went wrong</h1>
      <p className="mt-1.5 max-w-md text-sm leading-relaxed text-mist-500">
        {error.message || "An unexpected error occurred while loading this page."}
      </p>
      <button type="button" onClick={reset} className="btn btn-ghost mt-6">
        Try again
      </button>
    </div>
  );
}
