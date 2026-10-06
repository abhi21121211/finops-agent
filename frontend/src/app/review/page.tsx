"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Inbox } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { ConfidencePill } from "@/components/status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError, formatDate, formatINR, type ReviewAction } from "@/lib/api";

const SHORTCUTS: [string, string][] = [
  ["j / ↓", "next"],
  ["k / ↑", "previous"],
  ["Enter", "open"],
  ["a", "approve"],
  ["r", "reject"],
];

export default function ReviewQueuePage() {
  const router = useRouter();
  const qc = useQueryClient();
  const [cursor, setCursor] = useState(0);
  const [flash, setFlash] = useState<string | null>(null);
  const rows = useRef<(HTMLLIElement | null)[]>([]);

  const { data, isLoading } = useQuery({
    queryKey: ["invoices", "needs_review"],
    queryFn: () => api.listInvoices({ status: "needs_review" }),
    refetchInterval: 5_000,
  });
  const items = data?.items ?? [];
  const selected = items[Math.min(cursor, items.length - 1)];

  const act = useMutation({
    mutationFn: ({ id, action }: { id: string; action: ReviewAction }) => api.review(id, { action }),
    onSuccess: (_, { action }) => {
      setFlash(action === "approve" ? "Approved" : "Rejected");
      qc.invalidateQueries({ queryKey: ["invoices"] });
    },
    onError: (e) => setFlash(e instanceof ApiError ? e.message : "Failed"),
  });

  useEffect(() => {
    rows.current[cursor]?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement;
      if (target.closest("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
      if (!items.length) return;
      if (e.key === "j" || e.key === "ArrowDown") setCursor((c) => Math.min(c + 1, items.length - 1));
      else if (e.key === "k" || e.key === "ArrowUp") setCursor((c) => Math.max(c - 1, 0));
      else if (e.key === "Enter" && selected) router.push(`/invoices/${selected.id}`);
      else if (e.key === "a" && selected && !act.isPending) act.mutate({ id: selected.id, action: "approve" });
      else if (e.key === "r" && selected && !act.isPending) act.mutate({ id: selected.id, action: "reject" });
      else return;
      e.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items.length, selected, act, router]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Review queue</h1>
          <p className="text-sm text-muted-foreground">
            Invoices the agent was not sure about. Everything else was handled automatically.
          </p>
        </div>
        <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
          {SHORTCUTS.map(([k, label]) => (
            <span key={k}>
              <kbd className="rounded border bg-background px-1.5 py-0.5 font-mono">{k}</kbd> {label}
            </span>
          ))}
        </div>
      </div>

      {flash && (
        <p className="text-sm text-muted-foreground" role="status">
          {flash}
        </p>
      )}

      {isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : items.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-lg border bg-background py-16 text-muted-foreground">
          <Inbox className="size-8" />
          <p>Nothing to review.</p>
        </div>
      ) : (
        <ul className="divide-y rounded-lg border bg-background">
          {items.map((inv, i) => (
            <li
              key={inv.id}
              ref={(el) => {
                rows.current[i] = el;
              }}
              onClick={() => setCursor(i)}
              onDoubleClick={() => router.push(`/invoices/${inv.id}`)}
              className={`grid cursor-pointer gap-1 px-4 py-3 sm:grid-cols-[1fr_auto] ${
                i === cursor ? "bg-primary/5 ring-2 ring-inset ring-primary/40" : "hover:bg-muted/50"
              }`}
            >
              <div className="min-w-0">
                <p className="font-medium">
                  {inv.vendor_name ?? inv.original_filename}
                  <span className="ml-2 font-mono text-xs text-muted-foreground">{inv.invoice_number}</span>
                </p>
                <ul className="mt-1 space-y-0.5 text-xs text-amber-900">
                  {inv.route_reasons.map((r) => (
                    <li key={r}>• {r}</li>
                  ))}
                </ul>
              </div>
              <div className="flex items-center gap-4 text-sm sm:justify-end">
                <span className="text-muted-foreground">{formatDate(inv.invoice_date)}</span>
                <ConfidencePill value={inv.min_confidence} />
                <span className="w-28 text-right font-medium tabular-nums">{formatINR(inv.total)}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
      {items.length > 0 && (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <CheckCircle2 className="size-3.5" /> Approving or rejecting resumes that invoice&apos;s paused
          workflow.
        </p>
      )}
    </div>
  );
}
