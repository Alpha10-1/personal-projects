// A Client Component because it passes an icon component to EmptyState, and
// a function cannot cross the server/client boundary as a prop.
"use client";

import Link from "next/link";
import { Compass } from "lucide-react";

import { Card, EmptyState } from "@/components/ui";

export default function NotFound() {
  return (
    <Card className="mx-auto mt-10 max-w-lg">
      <EmptyState
        icon={Compass}
        title="Page not found"
        description="That address doesn't match anything in the app."
        action={
          <Link
            href="/"
            className="mt-2 inline-flex items-center rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors hover:bg-[var(--surface-2)]"
          >
            Back to today
          </Link>
        }
      />
    </Card>
  );
}
