"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, ShieldCheck } from "lucide-react";
import { Fragment, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Skeleton } from "@/components/ui/skeleton";
import { api, type EvalCase, type EvalRunDetail, type EvalRunSummary } from "@/lib/api";

// Categorical slots 1 and 2 of the validated reference palette (light surface).
const SERIES = { field: "#2a78d6", routing: "#eb6834" };
const pct = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined ? "—" : `${(Number(v) * 100).toFixed(digits)}%`;

export default function EvalsPage() {
  const { data: runs, isLoading } = useQuery({ queryKey: ["eval-runs"], queryFn: api.evalRuns });
  const [selected, setSelected] = useState<string | null>(null);
  const llmRuns = (runs ?? []).filter((r) => r.mode !== "oracle");
  const current = selected ?? llmRuns.find((r) => r.mode === "full")?.id ?? llmRuns[0]?.id ?? null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Evaluation</h1>
        <p className="text-sm text-muted-foreground">
          100 synthetic invoices with ground truth. Every prompt or model change is measured here, and CI
          blocks a merge that loses more than 2 points of field accuracy or auto-approves a bad invoice.
        </p>
      </div>

      {isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : !runs?.length ? (
        <p className="rounded-lg border bg-background py-12 text-center text-muted-foreground">
          No eval runs yet. Run <code className="font-mono">make eval</code>.
        </p>
      ) : (
        <>
          <TrendChart runs={llmRuns} />
          <RunsTable runs={runs} current={current} onSelect={setSelected} />
          {current && <RunDetail id={current} />}
        </>
      )}
    </div>
  );
}

function TrendChart({ runs }: { runs: EvalRunSummary[] }) {
  const data = [...runs]
    .reverse()
    .map((r) => ({
      when: new Date(r.created_at).toLocaleString("en-IN", { dateStyle: "short", timeStyle: "short" }),
      label: `${r.mode}${r.label ? ` · ${r.label}` : ""}`,
      field: Number(r.field_accuracy) * 100,
      routing: Number(r.routing_accuracy) * 100,
    }));
  if (data.length < 2) return null;
  return (
    <section className="rounded-lg border bg-background p-4">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-medium">Accuracy over time</h2>
        <div className="flex gap-4 text-xs text-muted-foreground">
          <LegendKey color={SERIES.field} label="Field accuracy" />
          <LegendKey color={SERIES.routing} label="Routing accuracy" />
        </div>
      </div>
      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: -12 }}>
            <CartesianGrid stroke="#e7e6e2" vertical={false} />
            <XAxis dataKey="when" tick={{ fontSize: 11, fill: "#52514e" }} tickLine={false} axisLine={false} />
            <YAxis
              domain={[50, 100]}
              tickFormatter={(v) => `${v}%`}
              tick={{ fontSize: 11, fill: "#52514e" }}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip
              formatter={(v, name) => [`${Number(v).toFixed(1)}%`, name === "field" ? "Field accuracy" : "Routing accuracy"]}
              labelFormatter={(_, p) => (p?.[0] ? `${p[0].payload.when} · ${p[0].payload.label}` : "")}
              contentStyle={{ fontSize: 12, borderRadius: 8 }}
            />
            <Line type="monotone" dataKey="field" stroke={SERIES.field} strokeWidth={2} dot={{ r: 4 }} />
            <Line type="monotone" dataKey="routing" stroke={SERIES.routing} strokeWidth={2} dot={{ r: 4 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}

function LegendKey({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="h-0.5 w-4 rounded" style={{ background: color }} />
      {label}
    </span>
  );
}

function RunsTable({
  runs,
  current,
  onSelect,
}: {
  runs: EvalRunSummary[];
  current: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <section className="overflow-x-auto rounded-lg border bg-background">
      <table className="w-full text-sm">
        <thead className="border-b text-left text-xs text-muted-foreground">
          <tr>
            {["Run", "Cases", "Field acc.", "Line F1", "Routing", "False auto-approvals", "Recon.", "p95", "Model"].map(
              (h) => (
                <th key={h} className="px-3 py-2 font-normal">
                  {h}
                </th>
              ),
            )}
          </tr>
        </thead>
        <tbody className="divide-y">
          {runs.map((r) => (
            <tr
              key={r.id}
              onClick={() => onSelect(r.id)}
              className={`cursor-pointer ${r.id === current ? "bg-primary/5" : "hover:bg-muted/40"}`}
            >
              <td className="px-3 py-2">
                <span className="font-medium">{r.mode}</span>
                {r.label && <span className="text-muted-foreground"> · {r.label}</span>}
                <span className="block text-xs text-muted-foreground">
                  {new Date(r.created_at).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })}
                  {r.git_sha && <span className="font-mono"> · {r.git_sha.slice(0, 7)}</span>}
                </span>
              </td>
              <td className="px-3 py-2 tabular-nums">{r.n_cases}</td>
              <td className="px-3 py-2 tabular-nums">{pct(r.field_accuracy)}</td>
              <td className="px-3 py-2 tabular-nums">{pct(r.line_item_f1)}</td>
              <td className="px-3 py-2 tabular-nums">{pct(r.routing_accuracy)}</td>
              <td className="px-3 py-2 tabular-nums">
                {r.false_auto_approvals === 0 ? (
                  <span className="inline-flex items-center gap-1 text-emerald-700">
                    <ShieldCheck className="size-3.5" /> 0
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-rose-700">
                    <AlertTriangle className="size-3.5" /> {r.false_auto_approvals}
                  </span>
                )}
              </td>
              <td className="px-3 py-2 tabular-nums">{pct(r.match_accuracy)}</td>
              <td className="px-3 py-2 tabular-nums">{(r.p95_latency_ms / 1000).toFixed(1)}s</td>
              <td className="px-3 py-2 font-mono text-xs">{r.model ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function RunDetail({ id }: { id: string }) {
  const { data } = useQuery({ queryKey: ["eval-run", id], queryFn: () => api.evalRun(id) });
  if (!data) return <Skeleton className="h-64 w-full" />;
  const m = data.report.metrics;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Tile label="Field accuracy" value={pct(data.field_accuracy)} />
        <Tile label="Routing accuracy" value={pct(data.routing_accuracy)} note={`${m.n_gated} gated cases`} />
        <Tile
          label="False auto-approvals"
          value={String(data.false_auto_approvals)}
          note={m.false_auto_approvals_deferred ? `+${m.false_auto_approvals_deferred} need M5 anomaly rules` : "target: zero"}
        />
        <Tile label="Auto-approval rate" value={pct(data.auto_approval_rate)} />
        <Tile label="Line-item F1" value={pct(data.line_item_f1)} />
        <Tile label="Reconciliation" value={pct(data.match_accuracy)} />
        <Tile
          label="Cost / invoice"
          value={`$${Number(m.avg_list_price_usd).toFixed(4)}`}
          note="list-price equivalent; actual $0 on free tiers"
        />
        <Tile label="Latency p50 / p95" value={`${(data.p50_latency_ms / 1000).toFixed(1)}s / ${(data.p95_latency_ms / 1000).toFixed(1)}s`} />
      </div>
      {data.report.gate_failures?.length > 0 && (
        <p className="flex items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-900">
          <AlertTriangle className="size-4" /> Gate failed: {data.report.gate_failures.join("; ")}
        </p>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        <FieldBars byField={m.by_field} />
        <div className="space-y-4">
          <Breakdown title="By document type" rows={m.by_category} />
          <Breakdown title="By model" rows={m.by_model} mono />
        </div>
      </div>
      <FailedCases run={data} />
    </div>
  );
}

function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="rounded-lg border bg-background p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-xl font-semibold tabular-nums">{value}</p>
      {note && <p className="text-xs text-muted-foreground">{note}</p>}
    </div>
  );
}

function FieldBars({ byField }: { byField: Record<string, number> }) {
  const rows = Object.entries(byField).sort((a, b) => a[1] - b[1]);
  return (
    <section className="rounded-lg border bg-background p-4">
      <h2 className="mb-3 text-sm font-medium">Accuracy by field (worst first)</h2>
      <ul className="space-y-1.5 text-xs">
        {rows.map(([field, v]) => (
          <li key={field} className="grid grid-cols-[9rem_1fr_3.5rem] items-center gap-2" title={`${field}: ${pct(v)}`}>
            <span className="truncate font-mono text-muted-foreground">{field}</span>
            <span className="h-2 rounded-full bg-muted">
              <span className="block h-2 rounded-full" style={{ width: `${v * 100}%`, background: SERIES.field }} />
            </span>
            <span className="text-right tabular-nums">{pct(v)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Breakdown({ title, rows, mono }: { title: string; rows: Record<string, number>; mono?: boolean }) {
  return (
    <section className="rounded-lg border bg-background p-4">
      <h2 className="mb-2 text-sm font-medium">{title}</h2>
      <table className="w-full text-xs">
        <tbody className="divide-y">
          {Object.entries(rows).map(([k, v]) => (
            <tr key={k}>
              <td className={`py-1.5 ${mono ? "font-mono" : ""}`}>{k}</td>
              <td className="py-1.5 text-right tabular-nums">{pct(v)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function FailedCases({ run }: { run: EvalRunDetail }) {
  const [open, setOpen] = useState<string | null>(null);
  const failed = run.report.cases.filter(
    (c) => c.error || c.predicted_route !== c.expected_route || c.wrong_fields.length > 0 || c.line_items.f1 < 1,
  );
  return (
    <section className="rounded-lg border bg-background">
      <h2 className="flex items-center gap-2 border-b px-4 py-3 text-sm font-medium">
        {failed.length === 0 ? (
          <>
            <CheckCircle2 className="size-4 text-emerald-600" /> Every case passed
          </>
        ) : (
          `${failed.length} case${failed.length === 1 ? "" : "s"} with a difference`
        )}
      </h2>
      {failed.length > 0 && (
        <table className="w-full text-sm">
          <tbody className="divide-y">
            {failed.map((c) => (
              <Fragment key={c.case_id}>
                <tr className="cursor-pointer hover:bg-muted/40" onClick={() => setOpen(open === c.case_id ? null : c.case_id)}>
                  <td className="w-8 pl-3 text-muted-foreground">
                    {open === c.case_id ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs">{c.case_id}</td>
                  <td className="px-3 py-2 text-xs text-muted-foreground">{c.category}</td>
                  <td className="px-3 py-2 text-xs">{summary(c)}</td>
                </tr>
                {open === c.case_id && (
                  <tr className="bg-muted/20">
                    <td colSpan={4} className="px-4 py-3 text-xs">
                      <CaseDetail c={c} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function summary(c: EvalCase): string {
  if (c.error) return `error: ${c.error.slice(0, 90)}`;
  const parts = [];
  if (c.predicted_route !== c.expected_route) {
    parts.push(`routed ${c.predicted_route ?? "nowhere"}, expected ${c.expected_route}${c.requires.length ? " (needs M5)" : ""}`);
  }
  if (c.wrong_fields.length) parts.push(`${c.wrong_fields.length} wrong field(s)`);
  if (c.line_items.f1 < 1) parts.push(`line items F1 ${pct(c.line_items.f1, 0)}`);
  return parts.join(" · ");
}

function CaseDetail({ c }: { c: EvalCase }) {
  return (
    <div className="space-y-2">
      {c.wrong_fields.length > 0 && (
        <table className="w-full max-w-2xl">
          <thead className="text-left text-muted-foreground">
            <tr>
              <th className="py-1 font-normal">Field</th>
              <th className="py-1 font-normal">Expected</th>
              <th className="py-1 font-normal">Extracted</th>
            </tr>
          </thead>
          <tbody>
            {c.wrong_fields.map((f) => (
              <tr key={f.field}>
                <td className="py-0.5 font-mono">{f.field}</td>
                <td className="py-0.5 font-mono">{f.expected ?? "—"}</td>
                <td className="py-0.5 font-mono text-rose-700">{f.actual ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="text-muted-foreground">
        Line items: {c.line_items.tp} of {c.line_items.expected} matched, {c.line_items.predicted} extracted · model{" "}
        <span className="font-mono">{c.model ?? "—"}</span> · {(c.latency_ms / 1000).toFixed(1)}s
      </p>
      {c.route_reasons.length > 0 && <p className="text-muted-foreground">Route reasons: {c.route_reasons.join("; ")}</p>}
    </div>
  );
}
