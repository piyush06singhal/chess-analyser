import type { Metadata, Viewport } from "next";
import Link from "next/link";
import { Fraunces, Geist, Geist_Mono } from "next/font/google";
import { Navigation } from "@/components/navigation";
import { WebVitals } from "@/components/vitals";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

/**
 * Display serif for hero headlines — the market chess sites this product
 * competes with lead with big editorial type, so Caissa carries a real display
 * face for exactly that job (and only that job).
 */
const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
  axes: ["SOFT", "WONK", "opsz"],
});

export const metadata: Metadata = {
  // A self-hosted product has no canonical public host baked in; the base URL is
  // configuration (`NEXT_PUBLIC_SITE_URL`), so metadata never advertises a domain
  // the operator did not choose. Without it, relative metadata is still correct.
  metadataBase: process.env.NEXT_PUBLIC_SITE_URL
    ? new URL(process.env.NEXT_PUBLIC_SITE_URL)
    : undefined,
  title: {
    default: "Caissa — Chess intelligence you can verify",
    template: "%s · Caissa",
  },
  description:
    "Explainable chess analysis, a versioned player profile and an evidence-gated AI coach, built on Stockfish. Every number measured, nothing fabricated.",
  applicationName: "Caissa",
  // The workspace is private; only the landing surface should be indexed. The
  // page-level routes (games, training, coach) are disallowed in robots.ts too.
  openGraph: {
    type: "website",
    siteName: "Caissa",
    title: "Caissa — Chess intelligence you can verify",
    description:
      "Explainable chess analysis, a versioned player profile and an evidence-gated AI coach, built on Stockfish. Every number measured, nothing fabricated.",
  },
  twitter: {
    card: "summary",
    title: "Caissa — Chess intelligence you can verify",
    description:
      "Explainable chess analysis and an evidence-gated AI coach built on Stockfish. Every number measured, nothing fabricated.",
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

/**
 * Applies the chosen theme before the first paint so there is never a flash of
 * the wrong theme. Runs inline (blocking) on purpose; it only touches
 * `document.documentElement.dataset.theme`.
 */
const THEME_INIT = `(function(){try{var s=localStorage.getItem("argus-theme");var m=window.matchMedia("(prefers-color-scheme: light)").matches?"light":"dark";document.documentElement.dataset.theme=s==="light"||s==="dark"?s:m;}catch(e){document.documentElement.dataset.theme="dark";}})();`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      data-theme="dark"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} ${fraunces.variable} h-full antialiased`}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT }} />
      </head>
      <body className="flex min-h-full flex-col">
        {/* Decorative chess backdrop: pure CSS, aria-hidden, behind all content. */}
        <div className="page-backdrop" aria-hidden>
          <div className="page-backdrop-grain" />
        </div>
        <Navigation />
        <WebVitals />
        <main className="mx-auto w-full max-w-[1520px] flex-1 px-4 py-7 sm:px-6 sm:py-9">
          {children}
        </main>
        <footer className="mt-12 border-t border-ink-700">
          <div className="mx-auto flex max-w-[1520px] flex-wrap items-center justify-between gap-2 px-4 py-5 text-meta sm:px-6">
            <span>Caissa · deterministic analysis powered by Stockfish</span>
            <span className="flex items-center gap-3">
              <Link href="/dashboard" className="text-mist-400 underline-offset-4 hover:text-mist-100 hover:underline">
                Workspace
              </Link>
              <Link href="/system" className="text-mist-400 underline-offset-4 hover:text-mist-100 hover:underline">
                System health
              </Link>
            </span>
          </div>
        </footer>
      </body>
    </html>
  );
}
