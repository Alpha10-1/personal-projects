"use client";

import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { chartTheme, slotColor } from "@/lib/palette";
import { useIsDark } from "@/lib/hooks";
import { formatWeek } from "@/lib/format";

/** Tooltip styled from the theme tokens. Values stay in text colours; the
 *  colour lives in the swatch beside each row, never in the text itself. */
function ChartTooltip({ active, payload, label, theme, unit = "", labelFormatter }) {
  if (!active || !payload?.length) return null;
  const rows = payload.filter((row) => Number(row.value) > 0);
  if (!rows.length) return null;
  return (
    <div
      className="rounded-lg border px-2.5 py-2 text-xs shadow-lg"
      style={{ background: theme.surface, borderColor: theme.border, color: theme.text }}
    >
      <p className="mb-1 font-medium">{labelFormatter ? labelFormatter(label) : label}</p>
      {rows.map((row) => (
        <p key={row.dataKey} className="flex items-center gap-1.5">
          <span
            aria-hidden
            className="inline-block h-2 w-2 shrink-0 rounded-full"
            style={{ background: row.color }}
          />
          <span style={{ color: theme.textSecondary }}>{row.name}</span>
          <span className="ml-auto pl-3 font-medium tabular-nums">
            {row.value}
            {unit}
          </span>
        </p>
      ))}
    </div>
  );
}

function LegendRow({ items }) {
  return (
    <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
      {items.map((item) => (
        <li
          key={item.label}
          className="flex items-center gap-1.5 text-[11px] text-[var(--text-secondary)]"
        >
          <span
            aria-hidden
            className="inline-block h-2 w-2 rounded-full"
            style={{ background: item.color }}
          />
          {item.label}
        </li>
      ))}
    </ul>
  );
}

/** Every chart ships a table view: identity and value are then available
 *  without relying on colour, and the numbers are readable at any contrast. */
function TableToggle({ open, onToggle }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      className="text-[11px] font-medium text-[var(--accent)]"
    >
      {open ? "Hide table" : "Show table"}
    </button>
  );
}

function DataTable({ columns, rows }) {
  return (
    <div className="mt-2 max-h-64 overflow-auto rounded-lg border">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-[var(--surface-2)]">
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                className={`px-2 py-1.5 font-medium ${column.numeric ? "text-right" : "text-left"}`}
              >
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} className="border-t">
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={`px-2 py-1.5 ${column.numeric ? "text-right tabular-nums" : ""}`}
                >
                  {row[column.key] ?? "—"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Hours per week, split by what the time went into.
 *
 * Stacked columns: the total per week is the headline and the split is the
 * detail, which is exactly what a stack reads as.
 */
export function HoursByWeekChart({ data, categories, categoryLabels, categorySlots }) {
  const isDark = useIsDark();
  const theme = chartTheme(isDark);
  const [showTable, setShowTable] = useState(false);

  if (!data?.length || !categories?.length) {
    return <p className="py-8 text-center text-sm text-[var(--text-muted)]">No time logged in this window.</p>;
  }

  // The slot comes from the caller's fixed vocabulary, so a category's colour
  // does not shift when another category drops out of the window.
  const colorFor = (category, index) =>
    slotColor(theme, categorySlots?.[category] ?? index);

  const legend = categories.map((category, index) => ({
    label: categoryLabels[category] || category,
    color: colorFor(category, index),
  }));

  return (
    <div>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -18 }}>
            <CartesianGrid stroke={theme.grid} strokeWidth={1} vertical={false} />
            <XAxis
              dataKey="week"
              tickFormatter={formatWeek}
              tick={{ fill: theme.axis, fontSize: 11 }}
              tickLine={false}
              axisLine={{ stroke: theme.grid }}
            />
            <YAxis
              tick={{ fill: theme.axis, fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              width={44}
            />
            <Tooltip
              cursor={{ fill: theme.grid, opacity: 0.35 }}
              content={
                <ChartTooltip theme={theme} unit="h" labelFormatter={(w) => `Week of ${formatWeek(w)}`} />
              }
            />
            {categories.map((category, index) => (
              <Bar
                key={category}
                dataKey={category}
                name={categoryLabels[category] || category}
                stackId="hours"
                fill={colorFor(category, index)}
                maxBarSize={24}
                // A 2px stroke in the surface colour is the gap between
                // segments -- white doing the separating, not an outline.
                stroke={theme.surface}
                strokeWidth={2}
                radius={index === categories.length - 1 ? [4, 4, 0, 0] : 0}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="flex items-start justify-between gap-3">
        <LegendRow items={legend} />
        <TableToggle open={showTable} onToggle={() => setShowTable((o) => !o)} />
      </div>

      {showTable ? (
        <DataTable
          columns={[
            { key: "week", label: "Week of" },
            ...categories.map((c) => ({
              key: c,
              label: categoryLabels[c] || c,
              numeric: true,
            })),
            { key: "total", label: "Total", numeric: true },
          ]}
          rows={data.map((row) => ({ ...row, week: formatWeek(row.week) }))}
        />
      ) : null}
    </div>
  );
}

/** Tasks opened vs closed each week -- whether work is actually clearing. */
export function ThroughputChart({ data }) {
  const isDark = useIsDark();
  const theme = chartTheme(isDark);
  const [showTable, setShowTable] = useState(false);

  if (!data?.length) return null;

  const series = [
    { key: "opened", label: "Opened", color: slotColor(theme, 0) },
    { key: "closed", label: "Closed", color: slotColor(theme, 1) },
  ];

  return (
    <div>
      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: -18 }}>
            <CartesianGrid stroke={theme.grid} strokeWidth={1} vertical={false} />
            <XAxis
              dataKey="week"
              tickFormatter={formatWeek}
              tick={{ fill: theme.axis, fontSize: 11 }}
              tickLine={false}
              axisLine={{ stroke: theme.grid }}
            />
            <YAxis
              allowDecimals={false}
              tick={{ fill: theme.axis, fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              width={44}
            />
            <Tooltip
              cursor={{ stroke: theme.axis, strokeWidth: 1 }}
              content={
                <ChartTooltip theme={theme} labelFormatter={(w) => `Week of ${formatWeek(w)}`} />
              }
            />
            {series.map((item) => (
              <Line
                key={item.key}
                // Straight segments, not a spline: these are weekly counts, and
                // a smoothed curve would draw values between the weeks that
                // were never measured (and overshoot past the real maximum).
                type="linear"
                dataKey={item.key}
                name={item.label}
                stroke={item.color}
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
                dot={false}
                // The 2px surface ring keeps the marker legible where the two
                // lines cross, and enlarges the hover target.
                activeDot={{ r: 4.5, strokeWidth: 2, stroke: theme.surface }}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="flex items-start justify-between gap-3">
        <LegendRow items={series.map((s) => ({ label: s.label, color: s.color }))} />
        <TableToggle open={showTable} onToggle={() => setShowTable((o) => !o)} />
      </div>

      {showTable ? (
        <DataTable
          columns={[
            { key: "week", label: "Week of" },
            { key: "opened", label: "Opened", numeric: true },
            { key: "closed", label: "Closed", numeric: true },
          ]}
          rows={data.map((row) => ({ ...row, week: formatWeek(row.week) }))}
        />
      ) : null}
    </div>
  );
}

/**
 * Hours by project -- one series, so one colour for every bar. Shading bars by
 * their own length would double-encode what the bar already shows.
 */
export function HoursByProjectChart({ data }) {
  const isDark = useIsDark();
  const theme = chartTheme(isDark);
  const [showTable, setShowTable] = useState(false);

  if (!data?.length) {
    return (
      <p className="py-8 text-center text-sm text-[var(--text-muted)]">
        No time logged in this window.
      </p>
    );
  }

  const rows = data.slice(0, 8);
  const color = slotColor(theme, 0);

  return (
    <div>
      <div style={{ height: Math.max(140, rows.length * 34 + 28) }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={rows}
            layout="vertical"
            margin={{ top: 4, right: 44, bottom: 4, left: 4 }}
          >
            <CartesianGrid stroke={theme.grid} strokeWidth={1} horizontal={false} />
            <XAxis
              type="number"
              tick={{ fill: theme.axis, fontSize: 11 }}
              tickLine={false}
              axisLine={false}
            />
            <YAxis
              type="category"
              dataKey="name"
              width={140}
              tick={{ fill: theme.textSecondary, fontSize: 11 }}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip
              cursor={{ fill: theme.grid, opacity: 0.35 }}
              content={<ChartTooltip theme={theme} unit="h" />}
            />
            <Bar
              dataKey="hours"
              name="Hours"
              fill={color}
              maxBarSize={24}
              radius={[0, 4, 4, 0]}
              label={{
                position: "right",
                fill: theme.textSecondary,
                fontSize: 11,
                formatter: (value) => `${value}h`,
              }}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="flex justify-end">
        <TableToggle open={showTable} onToggle={() => setShowTable((o) => !o)} />
      </div>

      {showTable ? (
        <DataTable
          columns={[
            { key: "name", label: "Project" },
            { key: "hours", label: "Hours", numeric: true },
          ]}
          rows={data}
        />
      ) : null}
    </div>
  );
}
