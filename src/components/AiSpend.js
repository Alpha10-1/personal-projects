"use client";

import { useCallback, useState } from "react";
import { AlertTriangle, Coins, Globe } from "lucide-react";

import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { formatDate } from "@/lib/format";
import {
  Badge,
  Card,
  CardHeader,
  EmptyState,
  ErrorNote,
  Select,
  Spinner,
} from "@/components/ui";

const WINDOWS = [
  { value: 7, label: "Last 7 days" },
  { value: 30, label: "Last 30 days" },
  { value: 90, label: "Last 90 days" },
  { value: 365, label: "Last year" },
];

/** Dollars, at the precision the number actually deserves.
 *
 *  Sub-cent amounts are the normal case here and rounding them to $0.00 would
 *  make the whole panel look broken. */
export function money(value) {
  const amount = Number(value || 0);
  if (amount === 0) return "$0";
  if (amount < 0.01) return `$${amount.toFixed(4)}`;
  if (amount < 1) return `$${amount.toFixed(3)}`;
  return `$${amount.toFixed(2)}`;
}

export function tokens(value) {
  const count = Number(value || 0);
  if (count < 1000) return String(count);
  if (count < 1_000_000) return `${(count / 1000).toFixed(1)}k`;
  return `${(count / 1_000_000).toFixed(2)}M`;
}

function Stat({ label, value, hint }) {
  return (
    <div>
      <p className="text-xs text-[var(--text-muted)]">{label}</p>
      <p className="text-lg font-medium">{value}</p>
      {hint ? <p className="text-[11px] text-[var(--text-muted)]">{hint}</p> : null}
    </div>
  );
}

function Bar({ value, max }) {
  const width = max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0;
  return (
    <span className="inline-block h-1.5 w-full overflow-hidden rounded bg-[var(--surface-2)]">
      <span className="block h-full bg-[var(--accent)]" style={{ width: `${width}%` }} />
    </span>
  );
}

/**
 * What the assistant has cost.
 *
 * Every model call is recorded when it is made, including the ones that
 * failed, so this is what was spent rather than what succeeded. The figures
 * are computed from the token counts the API reported at the published rates
 * — close enough to decide whether a feature is worth keeping, not a
 * substitute for the invoice.
 */
export default function AiSpend() {
  const [days, setDays] = useState(30);

  const spend = useAsync(
    useCallback(() => api.get("/ai/spend", { days, recent: 12 }), [days]),
    [days],
  );
  const data = spend.data;

  if (spend.loading && !data) return <Spinner label="Reading the ledger" />;
  if (spend.error) {
    return <ErrorNote error={spend.error} onDismiss={() => spend.reload()} />;
  }
  if (!data) return null;

  const { total, month_to_date: month } = data;
  const maxFeature = Math.max(1, ...data.by_feature.map((f) => f.cost_usd));

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="What the assistant costs"
          subtitle="Recorded per call, successes and failures alike."
          action={
            <Select
              value={days}
              onChange={(event) => setDays(Number(event.target.value))}
              options={WINDOWS}
            />
          }
        />

        {total.calls === 0 ? (
          <EmptyState
            icon={Coins}
            title="Nothing spent in this window"
            description="Every model call is recorded from now on — drafting, chat, repo reviews, histories, plans and web research."
          />
        ) : (
          <>
            <div className="grid grid-cols-2 gap-4 px-4 py-3 sm:grid-cols-4">
              <Stat
                label="This window"
                value={money(total.cost_usd)}
                hint={`${total.calls} call${total.calls === 1 ? "" : "s"}`}
              />
              <Stat label="Month to date" value={money(month.cost_usd)} />
              <Stat
                label="Tokens"
                value={`${tokens(total.input_tokens)} in`}
                hint={`${tokens(total.output_tokens)} out`}
              />
              <Stat
                label="Web searches"
                value={String(total.web_searches)}
                hint={money(total.web_searches * data.web_search_usd)}
              />
            </div>

            {total.failed > 0 ? (
              <div className="flex items-center gap-2 border-t border-[var(--border)] px-4 py-2 text-xs">
                <AlertTriangle size={13} className="text-[var(--warning)]" />
                <span>
                  {total.failed} call{total.failed === 1 ? "" : "s"} failed. Failed
                  calls can still cost input tokens, which is why they are counted
                  here.
                </span>
              </div>
            ) : null}

            {data.unpriced_models?.length ? (
              <div className="flex items-center gap-2 border-t border-[var(--border)] px-4 py-2 text-xs">
                <AlertTriangle size={13} className="text-[var(--warning)]" />
                <span>
                  No published rate for {data.unpriced_models.join(", ")}, so those
                  calls are counted but not priced. The totals above are therefore a
                  floor, not the whole bill.
                </span>
              </div>
            ) : null}
          </>
        )}
      </Card>

      {data.by_feature.length ? (
        <Card>
          <CardHeader title="Where it went" subtitle="Most expensive first." />
          <div className="space-y-2 px-4 py-3">
            {data.by_feature.map((row) => (
              <div key={row.feature}>
                <div className="flex items-baseline justify-between text-xs">
                  <span className="font-medium">{row.feature}</span>
                  <span className="text-[var(--text-muted)]">
                    {money(row.cost_usd)} · {row.calls} call
                    {row.calls === 1 ? "" : "s"}
                    {row.failed ? ` · ${row.failed} failed` : ""}
                    {row.web_searches ? (
                      <>
                        {" · "}
                        <Globe size={10} className="inline" /> {row.web_searches}
                      </>
                    ) : null}
                  </span>
                </div>
                <Bar value={row.cost_usd} max={maxFeature} />
              </div>
            ))}
          </div>
        </Card>
      ) : null}

      {data.most_expensive_call ? (
        <Card>
          <CardHeader
            title="The single most expensive call"
            subtitle="Worth knowing what the ceiling looks like."
          />
          <div className="px-4 py-3 text-xs">
            <span className="font-medium">{data.most_expensive_call.feature}</span> on{" "}
            {formatDate(data.most_expensive_call.at)} —{" "}
            {money(data.most_expensive_call.cost_usd)} for{" "}
            {tokens(data.most_expensive_call.input_tokens)} in /{" "}
            {tokens(data.most_expensive_call.output_tokens)} out
            {data.most_expensive_call.web_searches
              ? ` and ${data.most_expensive_call.web_searches} search${
                  data.most_expensive_call.web_searches === 1 ? "" : "es"
                }`
              : ""}
            .
          </div>
        </Card>
      ) : null}

      {data.recent?.length ? (
        <Card>
          <CardHeader title="Recent calls" />
          <div className="overflow-x-auto px-4 py-3">
            <table className="w-full text-xs">
              <tbody>
                {data.recent.map((row, index) => (
                  <tr
                    key={`${row.at}-${index}`}
                    className="border-t border-[var(--border)] first:border-t-0"
                  >
                    <td className="py-1 pr-2">{formatDate(row.at)}</td>
                    <td className="py-1 pr-2 font-medium">{row.feature}</td>
                    <td className="py-1 pr-2 text-[var(--text-muted)]">
                      {tokens(row.input_tokens)}/{tokens(row.output_tokens)}
                    </td>
                    <td className="py-1 pr-2 text-right">{money(row.cost_usd)}</td>
                    <td className="py-1 text-right">
                      {row.ok ? (
                        row.seconds ? (
                          <span className="text-[var(--text-muted)]">
                            {row.seconds.toFixed(1)}s
                          </span>
                        ) : null
                      ) : (
                        <Badge tone="critical">failed</Badge>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}
    </div>
  );
}
