import Link from "next/link";

export default function NotFound() {
  return (
    <div className="animate-fade-up flex flex-col items-center justify-center py-24 text-center">
      <span className="mb-4 text-4xl text-mist-600">♞</span>
      <h1 className="text-lg font-semibold text-mist-200">Not found</h1>
      <p className="mt-1.5 max-w-sm text-sm leading-relaxed text-mist-500">
        This page does not exist on this backend — the record may have been deleted,
        never imported, or the link is stale.
      </p>
      <Link href="/dashboard" className="btn btn-ghost mt-6">
        ← Back to workspace
      </Link>
    </div>
  );
}
