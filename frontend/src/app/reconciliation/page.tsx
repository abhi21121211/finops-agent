"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Loader2, Sheet } from "lucide-react";
import Link from "next/link";
import { Fragment, useRef, useState } from "react";

import { MatchBadge, ReconciliationPanel } from "@/components/reconciliation-panel";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError, formatDate, formatINR, type MatchStatus, type ReconciliationRow } from "@/lib/api";

const FILTERS: { value: MatchStatus | ""; label: string }[] = [
  { value: "", label: "All" },
  { value: "matched", label: "Matched" },
  { value: "partial", label: "Part-paid" },
  { value: "unpaid", label: "Unpaid" },
  { value: "mismatch", label: "PO mismatch" },
  { value: "no_po", label: "No PO" },
];

export default function ReconciliationPage() {
  const [filter, setFilter] = useState<MatchStatus | "">("");
  const [open, setOpen] = useState<string | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ["reconciliation"],
    queryFn: api.reconciliation,
    refetchInterval: 5_000,
  });

  const rows = (data ?? []).filter((r) => !filter || r.status === filter);
  const counts = (data ?? []).reduce<Record<string, number>>((acc, r) => {
    acc[r.status] = (acc[r.status] ?? 0) + 1;
    return acc;
  }, {});
  const outstanding = (data ?? [])
    .filter((r) => r.invoice_status === "approved")
    .reduce((sum, r) => sum + Number(r.outstanding), 0);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Reconciliation</h1>
          <p className="text-sm text-muted-foreground">
            Every invoice matched against its purchase order and your bank statements.
          </p>
        </div>
        <div className="text-right">
          <p className="text-xs text-muted-foreground">Approved, still to pay</p>
          <p className="text-xl font-semibold tabular-nums">{formatINR(outstanding)}</p>
        </div>
      </div>

      <StatementUpload />

      <div className="flex flex-wrap gap-2">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            onClick={() => setFilter(f.value)}
            className={`rounded-full border px-3 py-1 text-sm ${
              filter === f.value ? "border-primary bg-primary text-primary-foreground" : "bg-background hover:bg-muted"
            }`}
          >
            {f.label}
            {f.value && counts[f.value] ? <span className="ml-1.5 opacity-70">{counts[f.value]}</span> : null}
          </button>
        ))}
      </div>

      {isLoading ? (
        <Skeleton className="h-48 w-full" />
      ) : rows.length === 0 ? (
        <p className="rounded-lg border bg-background py-12 text-center text-muted-foreground">
          Nothing here yet. Invoices appear once they have been processed.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border bg-background">
          <table className="w-full text-sm">
            <thead className="border-b text-left text-muted-foreground">
              <tr>
                <th className="w-8" />
                <th className="px-3 py-2 font-normal">Vendor</th>
                <th className="px-3 py-2 font-normal">Invoice</th>
                <th className="px-3 py-2 font-normal">PO</th>
                <th className="px-3 py-2 text-right font-normal">Total</th>
                <th className="px-3 py-2 text-right font-normal">Paid</th>
                <th className="px-3 py-2 text-right font-normal">Outstanding</th>
                <th className="px-3 py-2 font-normal">Match</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {rows.map((r) => (
                <Fragment key={r.invoice_id}>
                  <tr
                    className="cursor-pointer hover:bg-muted/40"
                    onClick={() => setOpen(open === r.invoice_id ? null : r.invoice_id)}
                  >
                    <td className="pl-3 text-muted-foreground">
                      {open === r.invoice_id ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                    </td>
                    <td className="px-3 py-2 font-medium">{r.vendor_name ?? "—"}</td>
                    <td className="px-3 py-2">
                      <Link
                        href={`/invoices/${r.invoice_id}`}
                        onClick={(e) => e.stopPropagation()}
                        className="font-mono text-xs hover:underline"
                      >
                        {r.invoice_number}
                      </Link>
                      <span className="block text-xs text-muted-foreground">{formatDate(r.invoice_date)}</span>
                    </td>
                    <td className="px-3 py-2 font-mono text-xs">{r.po_number ?? "—"}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{formatINR(r.total)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{formatINR(r.paid)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {Number(r.outstanding) ? formatINR(r.outstanding) : "—"}
                      {Number(r.tds_amount) > 0 && (
                        <span className="block text-xs text-muted-foreground">TDS {formatINR(r.tds_amount)}</span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <MatchBadge status={r.status} />
                    </td>
                  </tr>
                  {open === r.invoice_id && (
                    <tr className="bg-muted/20">
                      <td colSpan={8} className="p-4">
                        <Expanded row={r} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Expanded({ row }: { row: ReconciliationRow }) {
  const { data } = useQuery({
    queryKey: ["invoice", row.invoice_id],
    queryFn: () => api.getInvoice(row.invoice_id),
  });
  if (!data?.match) return <Skeleton className="h-40 w-full" />;
  return <ReconciliationPanel match={data.match} total={data.total} />;
}

function StatementUpload() {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [message, setMessage] = useState<string | null>(null);
  const upload = useMutation({
    mutationFn: api.uploadStatement,
    onSuccess: (r) => {
      setMessage(
        `${r.rows} rows read: ${r.inserted} new, ${r.duplicates} already imported. ` +
          (r.inserted ? "Re-matching payments…" : ""),
      );
      // The refresh runs in the worker; poll briefly so results show up.
      for (const ms of [1500, 4000, 8000]) {
        setTimeout(() => {
          qc.invalidateQueries({ queryKey: ["reconciliation"] });
          qc.invalidateQueries({ queryKey: ["invoice"] });
        }, ms);
      }
    },
    onError: (e) => setMessage(e instanceof ApiError ? e.message : "Upload failed"),
  });
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-dashed bg-background px-4 py-3 text-sm">
      <Sheet className="size-5 text-muted-foreground" />
      <span>
        Upload a bank statement (CSV export). Re-uploading the same file is safe: rows are imported once.
      </span>
      <button
        onClick={() => input.current?.click()}
        disabled={upload.isPending}
        className="ml-auto inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 font-medium hover:bg-muted"
      >
        {upload.isPending && <Loader2 className="size-4 animate-spin" />}
        Choose CSV
      </button>
      <input
        ref={input}
        type="file"
        accept=".csv,text/csv"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) upload.mutate(f);
          e.target.value = "";
        }}
      />
      {message && <p className="w-full text-xs text-muted-foreground">{message}</p>}
    </div>
  );
}
