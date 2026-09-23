"use client";

import Link from "next/link";

import Assistant from "@/components/Assistant";
import { usePathname } from "next/navigation";
import { useEffect, useState, useSyncExternalStore } from "react";
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
  Users,
  Sunrise,
  Sparkles,
  X,
} from "lucide-react";

const NAV = [
  { href: "/", label: "Today", icon: Sunrise },
  { href: "/projects", label: "Projects", icon: FolderKanban },
  { href: "/tasks", label: "Tasks", icon: CheckSquare },
  { href: "/time", label: "Time", icon: Timer },
  { href: "/library", label: "Library", icon: BookMarked },
  { href: "/insights", label: "Insights", icon: BarChart3 },
  { href: "/personal", label: "Personal", icon: Sparkles },
  { href: "/people", label: "People", icon: Users },
  { href: "/review", label: "Review", icon: Stethoscope },
];

function isActive(pathname, href) {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

/**
 * The saved theme, as an external store.
 *
 * `localStorage` is exactly what `useSyncExternalStore` is for: a value that
 * lives outside React, cannot be read on the server, and has to arrive after
 * hydration without the markup disagreeing. Reading it in an effect and
 * calling `setState` did the same job and needed the rule suppressed; this
 * needs no suppression because React is doing the two-pass read itself.
 *
 * The `storage` event only fires in *other* tabs, so writes here notify the
 * listeners directly -- which also keeps two open tabs in step.
 */
const themeListeners = new Set();

// Where the choice lives when `localStorage` will not have it: private
// browsing, or storage blocked by policy. Without this the toggle reads
// back null however many times it is pressed, and does nothing at all --
// which is worse than not remembering, and was a regression the test for
// "still works when storage is unavailable" caught.
let themeInMemory = null;

const themeStore = {
  subscribe(listener) {
    themeListeners.add(listener);
    const relay = () => themeListeners.forEach((fn) => fn());
    window.addEventListener("storage", relay);
    return () => {
      themeListeners.delete(listener);
      window.removeEventListener("storage", relay);
    };
  },
  read() {
    try {
      const stored = window.localStorage.getItem("pp-theme");
      return stored === "light" || stored === "dark" ? stored : null;
    } catch {
      // Only here. A stored value that is not a theme means no choice has
      // been made; storage *throwing* means the choice could not be kept,
      // and only the second case is what the in-memory copy is for.
      return themeInMemory;
    }
  },
  // The server has no idea, and saying so is what keeps hydration honest.
  readOnServer() {
    return null;
  },
  write(next) {
    themeInMemory = next;
    try {
      window.localStorage.setItem("pp-theme", next);
    } catch {
      // The toggle still works for this session -- that is what the line
      // above is for -- it just will not be remembered past it.
    }
    themeListeners.forEach((fn) => fn());
  },
};

function ThemeToggle() {
  const theme = useSyncExternalStore(
    themeStore.subscribe,
    themeStore.read,
    themeStore.readOnServer,
  );

  // A DOM side effect, which is what effects are for -- unlike the setState
  // that used to live here.
  useEffect(() => {
    if (theme) document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const toggle = () => {
    const current =
      theme ??
      (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    themeStore.write(current === "dark" ? "light" : "dark");
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

  // The menu remembers which page it was opened on, and is open only while
  // you are still there. Derived rather than closed in an effect: navigating
  // changes `pathname`, so it closes on its own, and that covers back and
  // forward too, which no click handler sees.
  const [openedAt, setOpenedAt] = useState(null);
  const menuOpen = openedAt === pathname;
  const setMenuOpen = (open) => setOpenedAt(open ? pathname : null);

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
          onClick={() => setMenuOpen(!menuOpen)}
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
