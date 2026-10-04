"use client";

const STORAGE_KEY = "argus-theme";

/**
 * Theme switch.
 *
 * The applied theme lives on `<html data-theme>` and is set before first paint
 * by the inline script in the root layout, so there is no flash. This button
 * therefore holds no state: which icon is visible is decided by CSS from that
 * attribute, and the click reads the same attribute it is about to change. That
 * keeps the control and the page from ever disagreeing, and keeps the markup
 * identical on the server and the client.
 */
export function ThemeToggle({ className = "" }: { className?: string }) {
  const toggle = () => {
    const current = document.documentElement.dataset.theme === "light" ? "light" : "dark";
    const next = current === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Storage can be unavailable (private mode); the theme still applies.
    }
  };

  return (
    <button
      type="button"
      onClick={toggle}
      className={`btn-icon ${className}`}
      aria-label="Switch between light and dark theme"
      title="Switch between light and dark theme"
    >
      <span className="icon-sun">
        <SunIcon />
      </span>
      <span className="icon-moon">
        <MoonIcon />
      </span>
    </button>
  );
}

function SunIcon() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" aria-hidden>
      <circle cx="12" cy="12" r="4.2" />
      <path d="M12 2.6v2.2M12 19.2v2.2M2.6 12h2.2M19.2 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M18.7 5.3l-1.6 1.6M6.9 17.1l-1.6 1.6" />
    </svg>
  );
}

function MoonIcon() {
  return (
    <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M20.5 14.3A8.5 8.5 0 0 1 9.7 3.5a8.5 8.5 0 1 0 10.8 10.8Z" />
    </svg>
  );
}
