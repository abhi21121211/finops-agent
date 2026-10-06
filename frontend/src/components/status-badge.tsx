import { Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { InvoiceStatus } from "@/lib/api";

const STYLES: Record<InvoiceStatus, { label: string; className: string }> = {
  received: { label: "Received", className: "bg-slate-100 text-slate-700" },
  processing: { label: "Processing", className: "bg-blue-100 text-blue-800" },
  needs_review: { label: "Needs review", className: "bg-amber-100 text-amber-900" },
  awaiting_vendor: { label: "Awaiting vendor", className: "bg-purple-100 text-purple-800" },
  approved: { label: "Approved", className: "bg-emerald-100 text-emerald-800" },
  rejected: { label: "Rejected", className: "bg-rose-100 text-rose-800" },
  failed: { label: "Failed", className: "bg-rose-100 text-rose-800" },
};

export function StatusBadge({ status }: { status: InvoiceStatus }) {
  const s = STYLES[status];
  const busy = status === "received" || status === "processing";
  return (
    <Badge variant="outline" className={`gap-1 border-transparent ${s.className}`}>
      {busy && <Loader2 className="size-3 animate-spin" />}
      {s.label}
    </Badge>
  );
}

/** ≥0.95 green, ≥0.85 (the default routing threshold) amber, below that red. */
export function confidenceTone(c: number | null | undefined) {
  if (c === null || c === undefined) return "text-muted-foreground";
  if (c >= 0.95) return "text-emerald-700";
  if (c >= 0.85) return "text-amber-700";
  return "text-rose-700";
}

export function ConfidencePill({ value }: { value: number | null | undefined }) {
  if (value === null || value === undefined) return <span className="text-muted-foreground">—</span>;
  const bg = value >= 0.95 ? "bg-emerald-50" : value >= 0.85 ? "bg-amber-50" : "bg-rose-50";
  return (
    <span
      className={`inline-block rounded px-1.5 py-0.5 font-mono text-xs tabular-nums ${bg} ${confidenceTone(value)}`}
    >
      {Math.round(value * 100)}%
    </span>
  );
}
