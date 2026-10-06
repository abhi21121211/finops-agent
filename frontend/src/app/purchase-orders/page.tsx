"use client";

import { useQuery } from "@tanstack/react-query";

import { Skeleton } from "@/components/ui/skeleton";
import { api, formatDate, formatINR } from "@/lib/api";

export default function PurchaseOrdersPage() {
  const { data, isLoading } = useQuery({ queryKey: ["purchase-orders"], queryFn: api.purchaseOrders });

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Purchase orders</h1>
        <p className="text-sm text-muted-foreground">
          Billed quantities come from live invoices matched to each line; over-billing is flagged on the invoice.
        </p>
      </div>
      {isLoading ? (
        <Skeleton className="h-48 w-full" />
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {data?.map((po) => (
            <div key={po.id} className="rounded-lg border bg-background p-4">
              <div className="mb-3 flex items-baseline justify-between gap-3">
                <div>
                  <p className="font-mono text-sm font-medium">{po.po_number}</p>
                  <p className="text-xs text-muted-foreground">
                    {po.vendor_name} · {formatDate(po.date)} · {po.status}
                  </p>
                </div>
                <p className="tabular-nums">{formatINR(po.total)}</p>
              </div>
              <ul className="space-y-2 text-sm">
                {po.lines.map((l, i) => {
                  const pct = Math.min(100, (Number(l.billed) / Number(l.quantity)) * 100);
                  return (
                    <li key={i}>
                      <div className="flex justify-between gap-3">
                        <span>{l.description}</span>
                        <span className="shrink-0 tabular-nums text-muted-foreground">
                          {Number(l.billed)} / {Number(l.quantity)} × {formatINR(l.unit_price)}
                        </span>
                      </div>
                      <div className="mt-1 h-1.5 rounded-full bg-muted">
                        <div className="h-1.5 rounded-full bg-primary/70" style={{ width: `${pct}%` }} />
                      </div>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
