"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, CheckCircle2, Circle, Info, XCircle } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { ConfidencePill, StatusBadge } from "@/components/status-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  api,
  type Extraction,
  formatDate,
  formatINR,
  IN_FLIGHT,
  type InvoiceDetail,
  type WorkflowEvent,
} from "@/lib/api";

type FieldKey = Exclude<keyof Extraction, "line_items" | "currency">;

const FIELDS: { key: FieldKey; label: string; kind?: "money" | "date" | "mono" }[] = [
  { key: "vendor_name", label: "Vendor" },
  { key: "vendor_gstin", label: "Vendor GSTIN", kind: "mono" },
  { key: "buyer_gstin", label: "Buyer GSTIN", kind: "mono" },
  { key: "invoice_number", label: "Invoice number", kind: "mono" },
  { key: "invoice_date", label: "Invoice date", kind: "date" },
  { key: "due_date", label: "Due date", kind: "date" },
  { key: "po_number", label: "PO number", kind: "mono" },
  { key: "subtotal", label: "Subtotal", kind: "money" },
  { key: "cgst", label: "CGST", kind: "money" },
  { key: "sgst", label: "SGST", kind: "money" },
  { key: "igst", label: "IGST", kind: "money" },
  { key: "total", label: "Total", kind: "money" },
  { key: "bank_account_last4", label: "Bank account (last 4)", kind: "mono" },
  { key: "bank_ifsc", label: "Bank IFSC", kind: "mono" },
];

function display(value: string | null, kind?: string) {
  if (value === null || value === "") return <span className="text-muted-foreground">—</span>;
  if (kind === "money") return <span className="tabular-nums">{formatINR(value)}</span>;
  if (kind === "date") return formatDate(value);
  if (kind === "mono") return <span className="font-mono text-xs">{value}</span>;
  return value;
}

export default function InvoiceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: inv, isLoading, isError } = useQuery({
    queryKey: ["invoice", id],
    queryFn: () => api.getInvoice(id),
    refetchInterval: (q) => (q.state.data && IN_FLIGHT.includes(q.state.data.status) ? 1_500 : false),
  });

  if (isLoading) return <Skeleton className="h-[70vh] w-full" />;
  if (isError || !inv) return <p className="text-destructive">Invoice not found.</p>;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Link
          href="/invoices"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-4" /> Invoices
        </Link>
        <h1 className="text-xl font-semibold tracking-tight">
          {inv.extraction?.vendor_name ?? inv.original_filename}
        </h1>
        <StatusBadge status={inv.status} />
      </div>

      {inv.error_message && (
        <div className="flex gap-2 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-900">
          <XCircle className="mt-0.5 size-4 shrink-0" />
          <span className="line-clamp-3">{inv.error_message}</span>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <DocumentViewer inv={inv} />
        <div className="space-y-4">
          <FieldsCard inv={inv} />
          <LineItemsCard inv={inv} />
          <TimelineCard inv={inv} />
        </div>
      </div>
    </div>
  );
}

function DocumentViewer({ inv }: { inv: InvoiceDetail }) {
  const [page, setPage] = useState(0);
  const current = inv.pages[page];
  return (
    <Card className="h-fit lg:sticky lg:top-4">
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-base">Document</CardTitle>
        <div className="flex items-center gap-2 text-sm">
          {inv.pages.length > 1 &&
            inv.pages.map((p, i) => (
              <button
                key={p.page_no}
                onClick={() => setPage(i)}
                className={`size-7 rounded border ${i === page ? "bg-primary text-primary-foreground" : "hover:bg-muted"}`}
              >
                {i + 1}
              </button>
            ))}
          {inv.original_url && (
            <a href={inv.original_url} target="_blank" rel="noreferrer" className="text-primary hover:underline">
              Original
            </a>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {current ? (
          // Pre-signed storage URL; next/image optimisation adds nothing here.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={current.url} alt={`Page ${page + 1}`} className="w-full rounded border bg-white" />
        ) : (
          <div className="flex h-96 items-center justify-center rounded border border-dashed text-sm text-muted-foreground">
            {IN_FLIGHT.includes(inv.status) ? "Rendering pages…" : "No page images"}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function FieldsCard({ inv }: { inv: InvoiceDetail }) {
  const ext = inv.extraction;
  const conf = inv.field_confidence ?? {};
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-base">Extracted fields</CardTitle>
        <span className="text-xs text-muted-foreground">confidence ≥95% green · ≥85% amber · below red</span>
      </CardHeader>
      <CardContent>
        {!ext ? (
          <div className="space-y-2">
            {IN_FLIGHT.includes(inv.status) ? (
              Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-5 w-full" />)
            ) : (
              <p className="text-sm text-muted-foreground">No extraction.</p>
            )}
          </div>
        ) : (
          <dl className="divide-y text-sm">
            {FIELDS.map(({ key, label, kind }) => {
              const c = conf[key];
              const low = c !== undefined && c < 0.85;
              return (
                <div key={key} className={`grid grid-cols-[10rem_1fr_auto] items-center gap-3 py-1.5 ${low ? "bg-rose-50/60" : ""}`}>
                  <dt className="text-muted-foreground">{label}</dt>
                  <dd className={key === "total" ? "font-semibold" : ""}>{display(ext[key], kind)}</dd>
                  <dd>
                    <ConfidencePill value={c} />
                  </dd>
                </div>
              );
            })}
          </dl>
        )}
        {(inv.model_used || inv.latency_ms) && (
          <p className="mt-4 border-t pt-3 text-xs text-muted-foreground">
            {inv.model_used && <>Model <span className="font-mono">{inv.model_used}</span> · </>}
            {inv.latency_ms !== null && <>{(inv.latency_ms / 1000).toFixed(1)}s end to end · </>}
            Cost {formatUSD(inv.cost_usd)} (list price {formatUSD(inv.list_price_usd)})
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function formatUSD(v: string) {
  return `$${Number(v).toFixed(4)}`;
}

function LineItemsCard({ inv }: { inv: InvoiceDetail }) {
  const items = inv.extraction?.line_items;
  if (!items) return null;
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-base">Line items</CardTitle>
        <ConfidencePill value={inv.field_confidence?.line_items} />
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Description</TableHead>
              <TableHead>HSN/SAC</TableHead>
              <TableHead className="text-right">Qty</TableHead>
              <TableHead className="text-right">Rate</TableHead>
              <TableHead className="text-right">GST</TableHead>
              <TableHead className="text-right">Amount</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((li, i) => (
              <TableRow key={i}>
                <TableCell className="max-w-56 whitespace-normal">{li.description}</TableCell>
                <TableCell className="font-mono text-xs">{li.hsn_sac ?? "—"}</TableCell>
                <TableCell className="text-right tabular-nums">{Number(li.quantity)}</TableCell>
                <TableCell className="text-right tabular-nums">{formatINR(li.unit_price)}</TableCell>
                <TableCell className="text-right tabular-nums">{Number(li.tax_rate)}%</TableCell>
                <TableCell className="text-right tabular-nums">{formatINR(li.amount)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

const EVENT_ICON: Record<WorkflowEvent["status"], React.ReactNode> = {
  started: <Circle className="size-4 text-blue-600" />,
  completed: <CheckCircle2 className="size-4 text-emerald-600" />,
  failed: <AlertTriangle className="size-4 text-rose-600" />,
  info: <Info className="size-4 text-muted-foreground" />,
};

function TimelineCard({ inv }: { inv: InvoiceDetail }) {
  if (!inv.events.length) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Workflow</CardTitle>
      </CardHeader>
      <CardContent>
        <ol className="space-y-3">
          {inv.events.map((e, i) => (
            <li key={i} className="flex gap-3 text-sm">
              <span className="mt-0.5">{EVENT_ICON[e.status]}</span>
              <div className="min-w-0">
                <p>
                  <span className="font-medium">{e.node}</span>{" "}
                  <span className="text-muted-foreground">{e.status}</span>
                  <span className="ml-2 text-xs text-muted-foreground">
                    {new Date(e.at).toLocaleTimeString("en-IN")}
                  </span>
                </p>
                {e.detail && <p className="line-clamp-2 text-xs text-muted-foreground">{e.detail}</p>}
              </div>
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}
