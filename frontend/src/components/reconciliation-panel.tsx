import { AlertTriangle, ArrowLeftRight, Banknote, CheckCircle2, FileText, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { formatDate, formatINR, type MatchResult, type MatchStatus } from "@/lib/api";

const MATCH_STYLES: Record<MatchStatus, { label: string; className: string }> = {
  matched: { label: "Matched", className: "bg-emerald-100 text-emerald-800" },
  partial: { label: "Part-paid", className: "bg-sky-100 text-sky-800" },
  unpaid: { label: "Unpaid", className: "bg-slate-100 text-slate-700" },
  mismatch: { label: "PO mismatch", className: "bg-rose-100 text-rose-800" },
  no_po: { label: "No PO", className: "bg-amber-100 text-amber-900" },
};

export function MatchBadge({ status }: { status: MatchStatus }) {
  const s = MATCH_STYLES[status];
  return (
    <Badge variant="outline" className={`border-transparent ${s.className}`}>
      {s.label}
    </Badge>
  );
}

/** Invoice ↔ purchase order ↔ bank, side by side, with the plain-language explanation. */
export function ReconciliationPanel({ match, total }: { match: MatchResult; total: string | null }) {
  const { po, payment } = match;
  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <section className="rounded-lg border bg-background p-4">
          <h3 className="mb-1 flex items-center gap-2 text-sm font-medium">
            <ArrowLeftRight className="size-4" /> Invoice ↔ purchase order
            <span className="ml-auto font-mono text-xs text-muted-foreground">
              {po.po_number ?? "—"}
            </span>
          </h3>
          <p className="mb-3 text-xs text-muted-foreground">{po.explanation}</p>
          {po.lines.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-left text-muted-foreground">
                  <tr>
                    <th className="py-1 pr-2 font-normal">Invoice line</th>
                    <th className="py-1 pr-2 font-normal">PO line</th>
                    <th className="py-1 pr-2 text-right font-normal">Qty / left</th>
                    <th className="py-1 text-right font-normal">Price / PO</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {po.lines.map((l) => (
                    <tr key={l.invoice_line} className={l.status === "ok" ? "" : "bg-rose-50"}>
                      <td className="py-1.5 pr-2 align-top">
                        <span className="flex items-start gap-1">
                          {l.status === "ok" ? (
                            <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-emerald-600" />
                          ) : (
                            <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-rose-600" />
                          )}
                          {l.invoice_description}
                        </span>
                      </td>
                      <td className="py-1.5 pr-2 align-top text-muted-foreground">
                        {l.po_description ?? "—"}
                        {l.matched_by === "llm" && (
                          <span
                            className="ml-1 inline-flex items-center gap-0.5 text-violet-700"
                            title="Different wording; an LLM judged these the same item"
                          >
                            <Sparkles className="size-3" /> AI-matched
                          </span>
                        )}
                      </td>
                      <td className="py-1.5 pr-2 text-right align-top tabular-nums">
                        <span className={l.status === "quantity" ? "font-medium text-rose-700" : ""}>
                          {Number(l.quantity)}
                        </span>
                        {l.quantity_available !== null && (
                          <span className="text-muted-foreground"> / {Number(l.quantity_available)}</span>
                        )}
                      </td>
                      <td className="py-1.5 text-right align-top tabular-nums">
                        <span className={l.status === "price" ? "font-medium text-rose-700" : ""}>
                          {formatINR(l.unit_price)}
                        </span>
                        {l.po_unit_price !== null && (
                          <span className="block text-muted-foreground">
                            {formatINR(l.po_unit_price)}
                            {l.price_diff_pct ? ` (${l.price_diff_pct > 0 ? "+" : ""}${l.price_diff_pct}%)` : ""}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="rounded-lg border bg-background p-4">
          <h3 className="mb-1 flex items-center gap-2 text-sm font-medium">
            <Banknote className="size-4" /> Invoice ↔ bank
          </h3>
          <p className="mb-3 text-xs text-muted-foreground">{payment.explanation}</p>
          <dl className="mb-3 grid grid-cols-3 gap-2 text-xs">
            <Stat label="Invoice total" value={formatINR(total)} />
            <Stat label="Paid" value={formatINR(payment.paid)} />
            <Stat
              label={Number(payment.tds_amount) ? `TDS ${payment.tds_rate}%` : "Outstanding"}
              value={formatINR(Number(payment.tds_amount) ? payment.tds_amount : payment.outstanding)}
            />
          </dl>
          {payment.allocations.length > 0 && (
            <ul className="divide-y text-xs">
              {payment.allocations.map((a) => (
                <li key={`${a.transaction_id}-${a.amount}`} className="flex gap-3 py-1.5">
                  <span className="w-20 shrink-0 text-muted-foreground">{formatDate(a.date)}</span>
                  <span className="min-w-0 flex-1 truncate font-mono" title={a.narration}>
                    {a.narration}
                  </span>
                  {a.kind === "combined" && (
                    <span className="shrink-0 rounded bg-sky-50 px-1 text-sky-800">combined</span>
                  )}
                  <span className="shrink-0 tabular-nums">{formatINR(a.amount)}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
      <p className="flex items-start gap-2 text-xs text-muted-foreground">
        <FileText className="mt-0.5 size-3.5 shrink-0" />
        Only PO problems (no PO, mismatch) stop auto-approval. Unpaid and part-paid describe payment, which
        normally follows approval.
      </p>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-muted/50 px-2 py-1.5">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular-nums">{value}</dd>
    </div>
  );
}
