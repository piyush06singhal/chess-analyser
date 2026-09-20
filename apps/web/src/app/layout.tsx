import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Navigation } from "@/components/navigation";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "ARGUS Chess — AI-Powered Game Analysis",
  description:
    "Engine-grade chess analysis, explainable game reports, and personalized coaching.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col bg-neutral-50 text-neutral-900">
        <Navigation />
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">{children}</main>
        <footer className="border-t border-neutral-200 bg-white py-4">
          <p className="mx-auto max-w-6xl px-4 text-xs text-neutral-400">
            ARGUS Chess · Analysis powered by Stockfish · Under active development
          </p>
        </footer>
      </body>
    </html>
  );
}

