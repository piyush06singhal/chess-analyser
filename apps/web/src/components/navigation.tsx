"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";

import { ThemeToggle } from "@/components/theme-toggle";

/**
 * Navigation is one entry per *job*, not per system (§35).
 *
 * Several systems combine behind "Coach", "Prepare" and "Analytics", so the nav
 * is grouped by what the user is trying to do rather than by which package
 * happens to own the endpoint. Each group is either a single link (Play,
 * Players, Training, Search) or a small dropdown (Prepare, Analytics) so the top
 * row stays readable while the deeper surfaces remain one click away.
 */
interface NavItem {
  href: string;
  label: string;
  description: string;
}

interface NavGroup {
  label: string;
  href?: string;
  items: NavItem[];
}

const GROUPS: NavGroup[] = [
  { label: "Dashboard", href: "/dashboard", items: [] },
  {
    label: "Play",
    items: [
      { href: "/live", label: "Live", description: "Play a game with a real clock and the coach" },
      { href: "/games", label: "Library", description: "Every imported game and its analysis" },
      { href: "/import", label: "Import", description: "Paste a PGN or read a platform account" },
      { href: "/lab", label: "Position Lab", description: "Send any FEN to the engine" },
    ],
  },
  {
    label: "Coach",
    items: [
      { href: "/coach", label: "AI Coach", description: "Conversation grounded in evidence" },
      { href: "/coach?tab=today", label: "Today", description: "Focus, feed and the game debrief" },
      { href: "/training", label: "Training", description: "Exercises from your own mistakes" },
      { href: "/scenarios", label: "What-If Lab", description: "Compare moves and lines" },
    ],
  },
  {
    label: "Players",
    items: [
      { href: "/players", label: "Players", description: "Chess DNA and player intelligence" },
      { href: "/opponents", label: "Opponents", description: "Repertoire, tendencies, preparation" },
      { href: "/collections", label: "Collections", description: "Curated sets worth returning to" },
    ],
  },
  {
    label: "Analytics",
    items: [
      { href: "/intelligence", label: "Explorer", description: "Follow the evidence graph: position, game, player" },
      { href: "/progress", label: "Progress", description: "Measured change between periods" },
      { href: "/search", label: "Search", description: "One query across everything stored" },
    ],
  },
];

export function Navigation() {
  const pathname = usePathname();
  const navRef = useRef<HTMLElement>(null);

  // Native <details> menus do not close when another opens or when the pointer
  // leaves; closing the others on toggle is the least surprising behaviour for a
  // top-level nav.
  useEffect(() => {
    const nav = navRef.current;
    if (!nav) return;
    const menus = Array.from(nav.querySelectorAll<HTMLDetailsElement>("details[data-menu]"));
    const closeOthers = (open: HTMLDetailsElement) => {
      for (const menu of menus) if (menu !== open) menu.open = false;
    };
    const handlers = menus.map((menu) => {
      const handler = () => {
        if (menu.open) closeOthers(menu);
      };
      menu.addEventListener("toggle", handler);
      return () => menu.removeEventListener("toggle", handler);
    });
    return () => handlers.forEach((off) => off());
  }, []);

  return (
    <header className="sticky top-0 z-40 border-b border-ink-700 bg-ink-900/85 backdrop-blur-xl">
      <div className="mx-auto flex max-w-[1520px] flex-wrap items-center gap-x-3 gap-y-0.5 px-4 py-2 sm:px-6 lg:h-16 lg:flex-nowrap lg:py-0">
        <Link href="/" className="group order-1 flex shrink-0 items-center gap-2.5 lg:order-none">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-emerald-primary/40 bg-gradient-to-br from-emerald-primary/25 via-emerald-primary/10 to-violet-primary/20 text-lg text-emerald-primary transition-transform duration-200 group-hover:scale-105 group-hover:rotate-3">
            ♞
          </span>
          {/* Shown at every width: the CTA beside it is hidden on phones, so the
              wordmark is what identifies the product there. */}
          <span className="text-[15px] font-semibold tracking-tight text-mist-50">Caissa</span>
        </Link>

        <nav
          ref={navRef}
          aria-label="Main"
          className="order-3 flex w-full min-w-0 flex-wrap items-center gap-0.5 lg:order-none lg:w-auto lg:flex-1 lg:justify-center"
        >
          {GROUPS.map((group) => {
            if (group.href) {
              const active = pathname === group.href || pathname.startsWith(`${group.href}/`);
              return (
                <Link
                  key={group.label}
                  href={group.href}
                  aria-current={active ? "page" : undefined}
                  className={`relative shrink-0 rounded-xl px-2 py-2 text-small font-medium transition-colors duration-150 sm:px-2.5 sm:text-sm lg:px-3 ${
                    active
                      ? "bg-ink-800 font-semibold text-mist-50"
                      : "text-mist-400 hover:bg-ink-800/70 hover:text-mist-100"
                  }`}
                >
                  {group.label}
                  {active ? (
                    <span className="absolute inset-x-2 -bottom-px h-[2px] rounded-full bg-gradient-to-r from-emerald-primary to-violet-primary sm:inset-x-2.5 lg:inset-x-3" />
                  ) : null}
                </Link>
              );
            }
            const active = group.items.some((item) =>
              pathname.startsWith(item.href.split("?")[0])
            );
            return (
              <details key={group.label} data-menu className="nav-menu relative shrink-0">
                <summary
                  className={`relative flex cursor-pointer list-none items-center gap-1 rounded-xl px-2 py-2 text-small font-medium transition-colors duration-150 sm:px-2.5 sm:text-sm lg:px-3 ${
                    active
                      ? "bg-ink-800 font-semibold text-mist-50"
                      : "text-mist-400 hover:bg-ink-800/70 hover:text-mist-100"
                  }`}
                >
                  {group.label}
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" aria-hidden>
                    <path d="m6 9 6 6 6-6" />
                  </svg>
                </summary>
                <div className="nav-menu-panel">
                  {group.items.map((item) => (
                    <Link key={item.href} href={item.href} className="nav-menu-item">
                      <span className="text-small font-medium text-mist-100">{item.label}</span>
                      <span className="text-meta text-mist-500">{item.description}</span>
                    </Link>
                  ))}
                </div>
              </details>
            );
          })}
        </nav>

        <div className="order-2 ml-auto flex shrink-0 items-center gap-2 lg:order-none lg:ml-0">
          <ThemeToggle />
          <Link href="/import?source=platform" className="btn btn-primary hidden sm:inline-flex">
            Connect an account
          </Link>
        </div>
      </div>
    </header>
  );
}
