import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AiSpend, { money, tokens } from "@/components/AiSpend";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: { get: vi.fn() } }));

const BODY = {
  days: 30,
  web_search_usd: 0.01,
  total: {
    calls: 12,
    failed: 0,
    input_tokens: 240_000,
    output_tokens: 18_000,
    web_searches: 4,
    cost_usd: 0.7234,
  },
  month_to_date: { calls: 9, cost_usd: 0.5102, failed: 0 },
  by_feature: [
    { feature: "plan", calls: 3, failed: 0, cost_usd: 0.52, web_searches: 4 },
    { feature: "suggest_project", calls: 8, failed: 0, cost_usd: 0.0031, web_searches: 0 },
  ],
  by_model: [],
  by_day: [],
  most_expensive_call: {
    feature: "plan",
    model: "claude-sonnet-5",
    at: "2026-09-18",
    cost_usd: 0.31,
    input_tokens: 120_000,
    output_tokens: 6_000,
    web_searches: 4,
  },
  unpriced_models: [],
  recent: [],
  rates: {},
};

beforeEach(() => {
  vi.mocked(api.get).mockReset();
  vi.mocked(api.get).mockResolvedValue(BODY);
});

describe("formatting money", () => {
  it("keeps sub-cent amounts visible instead of rounding them to nothing", () => {
    // Most calls here cost a fraction of a cent; $0.00 everywhere would make
    // the panel look broken rather than cheap.
    expect(money(0.0031)).toBe("$0.0031");
    expect(money(0.42)).toBe("$0.420");
    expect(money(12.5)).toBe("$12.50");
    expect(money(0)).toBe("$0");
  });

  it("scales token counts to something readable", () => {
    expect(tokens(940)).toBe("940");
    expect(tokens(12_700)).toBe("12.7k");
    expect(tokens(2_400_000)).toBe("2.40M");
  });
});

describe("the panel", () => {
  it("shows the window total and the month to date as different figures", async () => {
    render(<AiSpend />);

    expect(await screen.findByText("$0.723")).toBeInTheDocument();
    expect(screen.getByText("$0.510")).toBeInTheDocument();
    expect(screen.getByText(/12 calls/)).toBeInTheDocument();
  });

  it("ranks features by what they cost", async () => {
    render(<AiSpend />);

    expect(await screen.findByText(/\$0.520 · 3 calls/)).toBeInTheDocument();
    expect(screen.getByText(/\$0.0031 · 8 calls/)).toBeInTheDocument();
  });

  it("asks for a different window without reloading the page", async () => {
    const user = userEvent.setup();
    render(<AiSpend />);
    await screen.findByText("$0.723");

    await user.selectOptions(screen.getByRole("combobox"), "7");

    expect(api.get).toHaveBeenLastCalledWith("/ai/spend", { days: 7, recent: 12 });
  });

  it("says when calls failed, because a failure can still cost tokens", async () => {
    vi.mocked(api.get).mockResolvedValue({
      ...BODY,
      total: { ...BODY.total, failed: 3 },
    });
    render(<AiSpend />);

    expect(await screen.findByText(/3 calls failed/)).toBeInTheDocument();
  });

  it("admits when a model has no published rate, so the total is a floor", async () => {
    vi.mocked(api.get).mockResolvedValue({
      ...BODY,
      unpriced_models: ["claude-something-new"],
    });
    render(<AiSpend />);

    expect(await screen.findByText(/No published rate for claude-something-new/)).toBeInTheDocument();
    expect(screen.getByText(/a floor, not the whole bill/)).toBeInTheDocument();
  });

  it("reads as empty rather than broken when nothing has been spent", async () => {
    vi.mocked(api.get).mockResolvedValue({
      ...BODY,
      total: { ...BODY.total, calls: 0, cost_usd: 0, failed: 0 },
      by_feature: [],
      most_expensive_call: null,
    });
    render(<AiSpend />);

    expect(await screen.findByText("Nothing spent in this window")).toBeInTheDocument();
  });
});
