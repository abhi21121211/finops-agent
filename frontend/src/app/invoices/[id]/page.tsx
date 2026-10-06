"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  CheckCircle2,
  Circle,
  Info,
  Loader2,
  Pencil,
  RotateCw,
  ShieldAlert,
  X,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";

import { MatchBadge, ReconciliationPanel } from "@/components/reconciliation-panel";
import { ConfidencePill, StatusBadge } from "@/components/status-badge";
import { Button } from "@/components/ui/button";
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
  ApiError,
  type Extraction,
  formatDate,
  formatINR,
  IN_FLIGHT,
  type InvoiceDetail,
  type ReviewAction,
  type WorkflowEvent,
} from "@/lib/api";
import { useInvoiceStream } from "@/lib/use-invoice-stream";

type FieldKey = Exclude<keyof Extraction, "line_items" | "currency">;
type Kind = "money" | "date" | "mono" | "text";

const FIELDS: { key: FieldKey; label: string; kind: Kind }[] = [
  { key: "vendor_name", label: "Vendor", kind: "text" },
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

function display(value: string | null, kind: Kind) {
  if (value === null || value === "") return <span className="text-muted-foreground">—</span>;
  if (kind === "money") return <span className="tabular-nums">{formatINR(value)}</span>;
  if (kind === "date") return formatDate(value);
  if (kind === "mono") return <span className="font-mono text-xs">{value}</span>;
  return value;
}

export default function InvoiceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const { data: inv, isLoading, isError } = useQuery({
    queryKey: ["invoice", id],
    queryFn: () => api.getInvoice(id),
  });
  const live = useInvoiceStream(id);
  // Field edits made in edit mode; null when not editing.
  const [edits, setEdits] = useState<Record<string, string> | null>(null);

  const reprocess = useMutation({
    mutationFn: () => api.reprocess(id),
    onSuccess: (d) => qc.setQueryData(["invoice", id], d),
  });

  if (isLoading) return <Skeleton className="h-[70vh] w-full" />;
  if (isError || !inv) return <p className="text-destructive">Invoice not found.</p>;

  const reviewable = inv.status === "needs_review";

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
        <span
          className={`inline-flex items-center gap-1.5 text-xs ${live ? "text-emerald-700" : "text-muted-foreground"}`}
          title={live ? "Receiving live updates" : "Reconnecting…"}
        >
          <span className={`size-1.5 rounded-full ${live ? "bg-emerald-500" : "bg-muted-foreground/40"}`} />
          {live ? "Live" : "Offline"}
        </span>
        {!IN_FLIGHT.includes(inv.status) && (
          <Button
            variant="outline"
            size="sm"
            className="ml-auto"
            onClick={() => reprocess.mutate()}
            disabled={reprocess.isPending}
          >
            <RotateCw className={`size-4 ${reprocess.isPending ? "animate-spin" : ""}`} />
            Reprocess
          </Button>
        )}
      </div>

      {inv.error_message && inv.status === "failed" && (
        <div className="flex gap-2 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-900">
          <XCircle className="mt-0.5 size-4 shrink-0" />
          <span className="line-clamp-3">{inv.error_message}</span>
        </div>
      )}

      <OutcomeBanner inv={inv} />

      <div className="grid gap-4 lg:grid-cols-2">
        <DocumentViewer inv={inv} />
        <div className="space-y-4">
          {reviewable && (
            <ReviewBar
              inv={inv}
              edits={edits}
              onStartEdit={() => setEdits({})}
              onCancelEdit={() => setEdits(null)}
              onDone={() => setEdits(null)}
            />
          )}
          <ValidationCard inv={inv} />
          {inv.match && (
            <Card>
              <CardHeader className="flex flex-row items-center justify-between">
                <CardTitle className="text-base">Reconciliation</CardTitle>
                <MatchBadge status={inv.match.status} />
              </CardHeader>
              <CardContent>
                <ReconciliationPanel match={inv.match} total={inv.total} />
              </CardContent>
            </Card>
          )}
          <FieldsCard inv={inv} edits={edits} setEdits={setEdits} />
          <LineItemsCard inv={inv} />
          <TimelineCard inv={inv} />
          <ReviewHistory inv={inv} />
        </div>
      </div>
    </div>
  );
}

function OutcomeBanner({ inv }: { inv: InvoiceDetail }) {
  if (inv.status === "needs_review" && inv.route_reasons.length) {
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
        <p className="flex items-center gap-2 font-medium">
          <ShieldAlert className="size-4" /> Waiting for a reviewer because:
        </p>
        <ul className="mt-1 list-disc pl-10">
          {inv.route_reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      </div>
    );
  }
  if (inv.status === "approved") {
    const by = inv.reviews.at(-1);
    return (
      <div className="flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900">
        <CheckCircle2 className="size-4" />
        {by
          ? `Approved by ${by.user_email ?? "a reviewer"}${by.action === "edit" ? " after edits" : ""}.`
          : "Auto-approved: every check and routing rule passed."}
      </div>
    );
  }
  if (inv.status === "rejected") {
    const by = inv.reviews.at(-1);
    return (
      <div className="flex items-center gap-2 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-900">
        <XCircle className="size-4" />
        Rejected by {by?.user_email ?? "a reviewer"}
        {by?.comment ? `: ${by.comment}` : "."}
      </div>
    );
  }
  return null;
}

function ReviewBar({
  inv,
  edits,
  onStartEdit,
  onCancelEdit,
  onDone,
}: {
  inv: InvoiceDetail;
  edits: Record<string, string> | null;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const [comment, setComment] = useState("");
  const [error, setError] = useState<string | null>(null);
  const editing = edits !== null;
  const changed = editing ? Object.keys(edits).length : 0;

  const submit = useMutation({
    mutationFn: (action: ReviewAction) =>
      api.review(inv.id, {
        action,
        comment: comment || undefined,
        field_edits: action === "edit" ? toFieldEdits(edits ?? {}) : undefined,
      }),
    onSuccess: (d) => {
      qc.setQueryData(["invoice", inv.id], d);
      qc.invalidateQueries({ queryKey: ["invoices"] });
      setError(null);
      onDone();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not submit"),
  });

  return (
    <Card className="border-amber-300 ring-1 ring-amber-200">
      <CardContent className="space-y-3 pt-6">
        <textarea
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder="Comment (optional)"
          rows={2}
          className="w-full resize-none rounded-md border bg-background px-3 py-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <div className="flex flex-wrap items-center gap-2">
          {!editing ? (
            <>
              <Button onClick={() => submit.mutate("approve")} disabled={submit.isPending}>
                {submit.isPending ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}
                Approve
              </Button>
              <Button variant="outline" onClick={onStartEdit} disabled={submit.isPending}>
                <Pencil className="size-4" /> Edit fields
              </Button>
              <Button
                variant="outline"
                className="ml-auto text-rose-700 hover:bg-rose-50 hover:text-rose-800"
                onClick={() => submit.mutate("reject")}
                disabled={submit.isPending}
              >
                <X className="size-4" /> Reject
              </Button>
            </>
          ) : (
            <>
              <Button onClick={() => submit.mutate("edit")} disabled={!changed || submit.isPending}>
                {submit.isPending ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}
                Save {changed || ""} edit{changed === 1 ? "" : "s"} & approve
              </Button>
              <Button variant="ghost" onClick={onCancelEdit} disabled={submit.isPending}>
                Cancel
              </Button>
              <span className="text-xs text-muted-foreground">Edit values in the fields below.</span>
            </>
          )}
        </div>
        {error && <p className="text-sm text-destructive">{error}</p>}
        {inv.reviews.length === 0 && !editing && (
          <p className="text-xs text-muted-foreground">
            Approving resumes the paused workflow; it continues even if the server restarted.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function toFieldEdits(edits: Record<string, string>): Record<string, string | null> {
  return Object.fromEntries(Object.entries(edits).map(([k, v]) => [k, v.trim() === "" ? null : v.trim()]));
}

function ValidationCard({ inv }: { inv: InvoiceDetail }) {
  if (!inv.extraction) return null;
  const issues = inv.validation_issues;
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-base">Checks</CardTitle>
        <span className="text-xs text-muted-foreground">
          {inv.extraction_attempts} extraction attempt{inv.extraction_attempts === 1 ? "" : "s"}
        </span>
      </CardHeader>
      <CardContent>
        {issues.length === 0 ? (
          <p className="flex items-center gap-2 text-sm text-emerald-700">
            <CheckCircle2 className="size-4" /> GSTIN, tax maths, tax split, dates, duplicates and
            vendor master all passed.
          </p>
        ) : (
          <ul className="space-y-2 text-sm">
            {issues.map((i, n) => (
              <li key={n} className="flex gap-2">
                {i.severity === "error" ? (
                  <AlertTriangle className="mt-0.5 size-4 shrink-0 text-rose-600" />
                ) : (
                  <Info className="mt-0.5 size-4 shrink-0 text-amber-600" />
                )}
                <span>
                  <span className="font-medium">{i.check.replace("_", " ")}</span>
                  <span className="text-muted-foreground">: </span>
                  {i.message}
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
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

function FieldsCard({
  inv,
  edits,
  setEdits,
}: {
  inv: InvoiceDetail;
  edits: Record<string, string> | null;
  setEdits: (e: Record<string, string> | null) => void;
}) {
  const ext = inv.extraction;
  const conf = inv.field_confidence ?? {};
  const flagged = new Set(inv.validation_issues.map((i) => i.field));
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
              const attention = (c !== undefined && c < 0.85) || flagged.has(key);
              const original = ext[key] ?? "";
              const value = edits?.[key] ?? original;
              return (
                <div
                  key={key}
                  className={`grid grid-cols-[10rem_1fr_auto] items-center gap-3 py-1.5 ${attention ? "bg-rose-50/60" : ""}`}
                >
                  <dt className="text-muted-foreground">{label}</dt>
                  <dd className={key === "total" ? "font-semibold" : ""}>
                    {edits ? (
                      <input
                        value={value}
                        type={kind === "date" ? "date" : "text"}
                        inputMode={kind === "money" ? "decimal" : undefined}
                        onChange={(e) => {
                          const next = { ...edits };
                          if (e.target.value === original) delete next[key];
                          else next[key] = e.target.value;
                          setEdits(next);
                        }}
                        className={`h-7 w-full rounded border px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                          key in edits ? "border-primary bg-primary/5" : "bg-background"
                        } ${kind === "mono" ? "font-mono text-xs" : ""}`}
                      />
                    ) : (
                      display(ext[key], kind)
                    )}
                  </dd>
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
            {inv.model_used && (
              <>
                Model <span className="font-mono">{inv.model_used}</span> ·{" "}
              </>
            )}
            {inv.latency_ms !== null && <>{(inv.latency_ms / 1000).toFixed(1)}s processing · </>}
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
                <TableCell className="text-right tabular-nums">
                  {li.tax_rate === null ? "—" : `${Number(li.tax_rate)}%`}
                </TableCell>
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
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-base">Workflow</CardTitle>
        {IN_FLIGHT.includes(inv.status) && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
      </CardHeader>
      <CardContent>
        <ol className="space-y-3">
          {inv.events.map((e, i) => (
            <li key={i} className="flex gap-3 text-sm">
              <span className="mt-0.5">{EVENT_ICON[e.status]}</span>
              <div className="min-w-0">
                <p>
                  <span className="font-medium">{e.node.replace("_", " ")}</span>{" "}
                  <span className="text-muted-foreground">{e.status}</span>
                  <span className="ml-2 text-xs text-muted-foreground">
                    {new Date(e.at).toLocaleTimeString("en-IN")}
                  </span>
                </p>
                {e.detail && <p className="line-clamp-3 text-xs text-muted-foreground">{e.detail}</p>}
              </div>
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}

function ReviewHistory({ inv }: { inv: InvoiceDetail }) {
  if (!inv.reviews.length) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Review history</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {inv.reviews.map((r, i) => (
          <div key={i}>
            <p>
              <span className="font-medium capitalize">{r.action}</span> by {r.user_email ?? "reviewer"}
              <span className="ml-2 text-xs text-muted-foreground">
                {new Date(r.created_at).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })}
              </span>
            </p>
            {r.field_edits && (
              <ul className="mt-1 font-mono text-xs text-muted-foreground">
                {Object.entries(r.field_edits).map(([k, v]) => (
                  <li key={k}>
                    {k} → {String(v)}
                  </li>
                ))}
              </ul>
            )}
            {r.comment && <p className="text-muted-foreground">“{r.comment}”</p>}
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
