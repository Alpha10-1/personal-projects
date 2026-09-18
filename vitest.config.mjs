import { fileURLToPath } from "node:url";

import { transformWithEsbuild } from "vite";
import { defineConfig } from "vitest/config";

/**
 * Components here are `.js` files containing JSX, which Next allows and Vite
 * does not: it parses `.js` as plain JavaScript, so the first tag in a file is
 * a syntax error. Renaming sixteen components to `.jsx` to suit the test
 * runner would be the tail wagging the dog, so they are put through esbuild's
 * JSX loader first instead.
 */
const jsxInJsFiles = {
  name: "jsx-in-js-files",
  enforce: "pre",
  async transform(code, id) {
    const path = id.replace(/\\/g, "/").split("?")[0];
    if (!path.includes("/src/") || !path.endsWith(".js")) return null;
    return transformWithEsbuild(code, path, { loader: "jsx", jsx: "automatic" });
  },
};

/**
 * Tests run under Vitest rather than through Next, so nothing here starts a
 * dev server or talks to the backend. The alias mirrors jsconfig.json, which
 * is what `@/lib/...` resolves through at build time.
 */
export default defineConfig({
  plugins: [jsxInJsFiles],
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{js,jsx}"],
    setupFiles: ["./vitest.setup.js"],
    restoreMocks: true,
    // Explicit imports from "vitest" instead of globals, so the linter sees
    // where describe/it/expect come from.
    globals: false,
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
});
