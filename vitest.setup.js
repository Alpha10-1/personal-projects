/** Per-file setup for component tests: the DOM matchers, and a clean document
 *  between tests so one render can't be found by the next. */
import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(cleanup);
