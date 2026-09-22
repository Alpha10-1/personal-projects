import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ErrorBoundary from "@/components/ErrorBoundary";

function Throws() {
  throw new Error("map is not a function");
}

// React logs the caught error itself. That is noise in a test that is
// asserting the error was handled, so it is silenced here and only here.
beforeEach(() => vi.spyOn(console, "error").mockImplementation(() => {}));
afterEach(() => vi.restoreAllMocks());

describe("a panel that fails to render", () => {
  it("is replaced by a message rather than taking the page", () => {
    render(
      <div>
        <ErrorBoundary label="The explanation">
          <Throws />
        </ErrorBoundary>
        <p>The editor</p>
      </div>,
    );
    expect(screen.getByText(/The explanation could not be shown/)).toBeInTheDocument();
    expect(screen.getByText("The editor")).toBeInTheDocument();
  });

  it("says what went wrong, so it can be reported", () => {
    render(
      <ErrorBoundary>
        <Throws />
      </ErrorBoundary>,
    );
    expect(screen.getByText(/map is not a function/)).toBeInTheDocument();
  });

  it("promises nothing was changed, because nothing was", () => {
    render(
      <ErrorBoundary>
        <Throws />
      </ErrorBoundary>,
    );
    expect(screen.getByText(/nothing was changed/)).toBeInTheDocument();
  });
});

describe("a panel that renders", () => {
  it("is left entirely alone", () => {
    render(
      <ErrorBoundary label="The explanation">
        <p>All fine</p>
      </ErrorBoundary>,
    );
    expect(screen.getByText("All fine")).toBeInTheDocument();
    expect(screen.queryByText(/could not be shown/)).not.toBeInTheDocument();
  });
});
