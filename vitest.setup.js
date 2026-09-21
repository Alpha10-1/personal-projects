/** Per-file setup for component tests: the DOM matchers, and a clean document
 *  between tests so one render can't be found by the next. */
import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(cleanup);

/**
 * jsdom has no `matchMedia`, and anything reading the colour scheme needs
 * it -- `useIsDark`, and therefore the charts and the code editor. Without
 * this the component throws before it renders, which shows up as every
 * assertion in the file failing for a reason that has nothing to do with
 * what is being tested.
 *
 * Reports light, because that is the app's default. A test that cares
 * overrides it.
 */
//
// A plain function rather than `vi.fn()`: a test calling
// `vi.clearAllMocks()` in its own `beforeEach` -- which several do -- would
// strip the implementation off a mock and leave `matchMedia` returning
// undefined, which fails further in and reads like a different bug.
if (!window.matchMedia) {
  const noop = () => {};
  window.matchMedia = (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: noop,
    removeEventListener: noop,
    addListener: noop,
    removeListener: noop,
    dispatchEvent: () => false,
  });
}
