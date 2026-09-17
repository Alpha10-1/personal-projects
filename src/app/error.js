"use client"; // Error boundaries must be Client Components.

import { useEffect } from "react";
import { AlertTriangle } from "lucide-react";

import { Button, Card, EmptyState } from "@/components/ui";

/**
 * Catches render-time errors anywhere under the root layout.
 *
 * Failed API calls are already handled per panel by `useAsync` and
 * `ErrorNote`; this is the net for everything else -- a component throwing
 * while rendering, which would otherwise drop the user on Next's default
 * error screen with no way back into the app.
 *
 * The root layout sits above this boundary, so the nav is still there and the
 * user can navigate away rather than only reloading.
 */
export default function Error({ error, retry }) {
  useEffect(() => {
    // No error reporting service on a local single-user app: the console is
    // where this is actually read.
    console.error(error);
  }, [error]);

  return (
    <Card className="mx-auto mt-10 max-w-lg">
      <EmptyState
        icon={AlertTriangle}
        title="Something went wrong"
        description={
          error?.message ||
          "An unexpected error stopped this page from rendering."
        }
        action={
          <div className="mt-2 flex gap-2">
            {/* retry() re-fetches and re-renders this segment. Next 16 renamed
                the old `reset` prop, which only cleared the error state. */}
            <Button variant="primary" onClick={() => retry()}>
              Try again
            </Button>
            <Button onClick={() => window.location.reload()}>
              Reload the page
            </Button>
          </div>
        }
      />
      {error?.digest ? (
        <p className="pb-4 text-center text-xs text-[var(--text-muted)]">
          Reference: {error.digest}
        </p>
      ) : null}
    </Card>
  );
}
