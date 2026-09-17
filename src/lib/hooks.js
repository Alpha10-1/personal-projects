"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Runs an async loader and tracks {data, error, loading}.
 *
 * `deps` is the dependency list for the loader. Results from a superseded call
 * are dropped, so a fast filter change can't be overwritten by the slower
 * response it replaced.
 */
export function useAsync(loader, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const runId = useRef(0);

  const run = useCallback(
    async ({ quiet = false } = {}) => {
      const id = ++runId.current;
      if (!quiet) setState((s) => ({ ...s, loading: true, error: null }));
      try {
        const data = await loader();
        if (id === runId.current) setState({ data, error: null, loading: false });
      } catch (error) {
        if (id === runId.current) setState((s) => ({ ...s, error, loading: false }));
      }
    },
    // The loader is rebuilt by the caller whenever its inputs change, and the
    // caller declares those inputs here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    deps,
  );

  useEffect(() => {
    run();
  }, [run]);

  return {
    ...state,
    reload: run,
    setData: (updater) =>
      setState((s) => ({
        ...s,
        data: typeof updater === "function" ? updater(s.data) : updater,
      })),
  };
}

/** Tracks whether the OS (or the theme toggle) is currently showing dark. Charts
 *  need the actual hex values, which differ per mode. */
export function useIsDark() {
  const [isDark, setIsDark] = useState(false);

  useEffect(() => {
    const root = document.documentElement;
    const media = window.matchMedia("(prefers-color-scheme: dark)");

    const resolve = () => {
      const stamped = root.getAttribute("data-theme");
      setIsDark(stamped ? stamped === "dark" : media.matches);
    };

    resolve();
    media.addEventListener("change", resolve);
    // The toggle writes data-theme on <html>, which no event covers.
    const observer = new MutationObserver(resolve);
    observer.observe(root, { attributes: true, attributeFilter: ["data-theme"] });

    return () => {
      media.removeEventListener("change", resolve);
      observer.disconnect();
    };
  }, []);

  return isDark;
}

/** Debounces a value -- used so a search box hits the API once the typing
 *  pauses rather than on every keystroke. */
export function useDebounced(value, delay = 300) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}
