// Flat config for ESLint 9. Next 16 removed `next lint`, so linting runs
// through the ESLint CLI directly (`npm run lint`).
import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";

const eslintConfig = defineConfig([
  ...nextVitals,
  // Spelling out the ignores replaces the defaults from eslint-config-next,
  // so the build output and the Python side both have to be listed here.
  globalIgnores([".next/**", "out/**", "build/**", "next-env.d.ts", "backend/**"]),
]);

export default eslintConfig;
