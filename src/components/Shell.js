"use client";

import Link from "next/link";

import Assistant from "@/components/Assistant";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import {
  BarChart3,
  BookMarked,
  CheckSquare,
  FolderKanban,
  Menu,
  Moon,
  Sun,
  Timer,
  Stethoscope,
  Sunrise,
  X,
} from "lucide-react";

const NAV = [
  { href: "/", label: "Today", icon: Sunrise },
  { href: "/projects", label: "Projects", icon: FolderKanban },
  { href: "/tasks", label: "Tasks", icon: CheckSquare },
  { href: "/time", label: "Time", icon: Timer },
  { href: "/library", label: "Library", icon: BookMarked },
  { href: "/insights", label: "Insights", icon: BarChart3 },
  { href: "/review", label: "Review", icon: Stethoscope },
];

function isActive(pathname, href) {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

function ThemeToggle() {
  const [theme, setTheme] = useState(null);

  // The stored choice is applied after mount rather than during render: the
  // server has no way to know it, and reading it during render would produce
  // markup that doesn't match what gets hydrated.
  useEffect(() => {
    const stored = window.localStorage.getItem("pp-theme");
    if (stored === "light" || stored === "dark") {
      // set-state-in-effect is suppressed rather than fixed: localStorage is
      // unreadable during SSR, so this genuinely cannot move into render or
      // into a lazy initialiser without a hydration mismatch. It runs once on
      // mount, so the cascading render the rule guards against is bounded.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setTheme(stored);
      document.documentElement.setAttribute("data-theme", stored);
    }
  }, []);

  const toggle = () => {
    const current =
      theme ??
      (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = current === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.setAttribute("data-theme", next);
    try {
      window.localStorage.setItem("pp-theme", next);
    } catch {
      // Private browsing or blocked storage: the toggle still works for this
      // session, it just won't be remembered.
    }
  };

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label="Toggle colour theme"
      className="rounded-lg border p-1.5 hover:bg-[var(--surface-2)]"
    >
      {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
    </button>
  );
}

export default function Shell({ children }) {
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);

  // Closing the mobile menu on navigation. Suppressed rather than moved into
  // the link's onClick because this also covers back/forward navigation,
  // which no click handler sees.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMenuOpen(false);
  }, [pathname]);

  const nav = (
    <nav className="flex flex-col gap-0.5">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = isActive(pathname, href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors ${
              active
                ? "bg-[var(--accent-soft)] font-medium text-[var(--accent)]"
                : "text-[var(--text-secondary)] hover:bg-[var(--surface-2)]"
            }`}
          >
            <Icon size={16} />
            {label}
          </Link>
        );
      })}
    </nav>
  );

  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-40 flex items-center gap-3 border-b bg-[var(--surface-1)] px-4 py-2.5 lg:hidden">
        <button
          type="button"
          onClick={() => setMenuOpen((open) => !open)}
          aria-label={menuOpen ? "Close navigation" : "Open navigation"}
          aria-expanded={menuOpen}
          className="rounded-lg border p-1.5"
        >
          {menuOpen ? <X size={16} /> : <Menu size={16} />}
        </button>
        <span className="text-sm font-semibold">Projects</span>
        <div className="ml-auto">
          <ThemeToggle />
        </div>
      </header>

      {menuOpen ? (
        <div className="border-b bg-[var(--surface-1)] px-3 py-2 lg:hidden">{nav}</div>
      ) : null}

      <div className="lg:flex">
        <aside className="sticky top-0 hidden h-dvh w-56 shrink-0 flex-col border-r bg-[var(--surface-1)] px-3 py-4 lg:flex">
          <div className="mb-5 flex items-center justify-between px-2">
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold">Projects</p>
              <p className="truncate text-[11px] text-[var(--text-muted)]">
                Junior AI Analyst
              </p>
            </div>
            <ThemeToggle />
          </div>
          {nav}
        </aside>

        <main className="min-w-0 flex-1 px-4 py-5 sm:px-6 lg:px-8">
          <div className="mx-auto w-full max-w-6xl">{children}</div>
        </main>
      </div>

      {/* Mounted in the shell rather than per page so the conversation
          survives navigating between them. */}
      <Assistant />
    </div>
  );
}
