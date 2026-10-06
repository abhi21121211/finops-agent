export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

const TOKEN_KEY = "finops.token";

export type InvoiceStatus =
  | "received"
  | "processing"
  | "needs_review"
  | "awaiting_vendor"
  | "approved"
  | "rejected"
  | "failed";

export interface InvoiceSummary {
  id: string;
  status: InvoiceStatus;
  source: string;
  original_filename: string | null;
  vendor_name: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  total: string | null;
  min_confidence: number | null;
  route_reasons: string[];
  created_at: string;
}

export interface LineItem {
  description: string;
  hsn_sac: string | null;
  quantity: string;
  unit_price: string;
  tax_rate: string;
  amount: string;
}

export interface Extraction {
  vendor_name: string;
  vendor_gstin: string | null;
  buyer_gstin: string | null;
  invoice_number: string;
  invoice_date: string;
  due_date: string | null;
  po_number: string | null;
  currency: string;
  line_items: LineItem[];
  subtotal: string;
  cgst: string;
  sgst: string;
  igst: string;
  total: string;
  bank_account_last4: string | null;
  bank_ifsc: string | null;
}

export interface WorkflowEvent {
  at: string;
  node: string;
  status: "started" | "completed" | "failed" | "info";
  detail: string;
}

export interface ValidationIssue {
  check: string;
  field: string | null;
  severity: "error" | "warning";
  message: string;
  fixable: boolean;
}

export type ReviewAction = "approve" | "edit" | "reject";

export interface Review {
  action: ReviewAction;
  field_edits: Record<string, unknown> | null;
  comment: string | null;
  user_email: string | null;
  created_at: string;
}

export type MatchStatus = "matched" | "partial" | "mismatch" | "no_po" | "unpaid";

export interface LineMatch {
  invoice_line: number;
  po_line: number | null;
  invoice_description: string;
  po_description: string | null;
  similarity: number;
  matched_by: "text" | "llm" | "none";
  quantity: string;
  quantity_available: string | null;
  unit_price: string;
  po_unit_price: string | null;
  price_diff_pct: number | null;
  status: "ok" | "price" | "quantity" | "unmatched";
  note: string;
}

export interface Allocation {
  transaction_id: string;
  amount: string;
  date: string;
  narration: string;
  kind: "reference" | "exact" | "combined";
}

export interface MatchResult {
  status: MatchStatus;
  explanation: string;
  po: {
    status: "matched" | "mismatch" | "no_po";
    po_id: string | null;
    po_number: string | null;
    found_by: "po_number" | "vendor_amount" | "none";
    lines: LineMatch[];
    explanation: string;
  };
  payment: {
    status: "paid" | "partial" | "unpaid";
    paid: string;
    tds_rate: string | null;
    tds_amount: string;
    outstanding: string;
    allocations: Allocation[];
    explanation: string;
  };
}

export interface ReconciliationRow {
  invoice_id: string;
  invoice_status: InvoiceStatus;
  vendor_name: string | null;
  invoice_number: string | null;
  invoice_date: string | null;
  total: string | null;
  status: MatchStatus;
  po_number: string | null;
  paid: string;
  outstanding: string;
  tds_amount: string;
  explanation: string;
}

export interface PurchaseOrder {
  id: string;
  po_number: string;
  date: string;
  status: string;
  total: string;
  vendor_id: string;
  vendor_name: string;
  lines: { description: string; quantity: string; unit_price: string; billed: string }[];
}

export interface StatementUpload {
  rows: number;
  inserted: number;
  duplicates: number;
  money_out: number;
}

export interface EvalRunSummary {
  id: string;
  created_at: string;
  mode: string;
  label: string | null;
  git_sha: string | null;
  model: string | null;
  n_cases: number;
  field_accuracy: number;
  line_item_f1: number;
  routing_accuracy: number;
  false_auto_approvals: number;
  auto_approval_rate: number;
  match_accuracy: number | null;
  avg_cost_usd: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
}

export interface EvalCase {
  case_id: string;
  category: string;
  requires: string[];
  expected_route: string;
  predicted_route: string | null;
  expected_match: string | null;
  predicted_match: string | null;
  field_accuracy: number;
  wrong_fields: { field: string; expected: string | null; actual: string | null }[];
  line_items: { tp: number; predicted: number; expected: number; f1: number };
  model: string | null;
  latency_ms: number;
  error: string | null;
  route_reasons: string[];
}

export interface EvalRunDetail extends EvalRunSummary {
  report: {
    metrics: {
      by_field: Record<string, number>;
      by_model: Record<string, number>;
      by_category: Record<string, number>;
      false_auto_approvals_deferred: number;
      n_gated: number;
      errors: number;
      avg_list_price_usd: number;
    };
    gate_failures: string[];
    cases: EvalCase[];
  };
}

export interface InvoiceDetail extends InvoiceSummary {
  extraction: Extraction | null;
  field_confidence: Record<string, number> | null;
  original_url: string | null;
  original_content_type: string | null;
  pages: { page_no: number; url: string }[];
  events: WorkflowEvent[];
  model_used: string | null;
  cost_usd: string;
  list_price_usd: string;
  latency_ms: number | null;
  error_message: string | null;
  extraction_attempts: number;
  validation_issues: ValidationIssue[];
  route: "auto_approve" | "human_review" | "vendor_query" | "reject" | null;
  match: MatchResult | null;
  reviews: Review[];
}

export type StreamMessage =
  | { type: "snapshot"; status: InvoiceStatus; events: WorkflowEvent[] }
  | ({ type: "event" } & WorkflowEvent)
  | { type: "status"; status: InvoiceStatus };

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

function readToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: session-only */
  }
}

export function hasToken(): boolean {
  return readToken() !== null;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = readToken();
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init.headers },
  });
  if (!res.ok) {
    if (res.status === 401) setToken(null);
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, body.detail ?? res.statusText);
  }
  return res.json() as Promise<T>;
}

export const api = {
  demoLogin: async () => {
    const { access_token } = await request<{ access_token: string }>("/auth/demo", {
      method: "POST",
    });
    setToken(access_token);
  },
  listInvoices: (params: { status?: string; vendor?: string } = {}) => {
    const q = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v) as [string, string][],
    );
    return request<{ items: InvoiceSummary[]; total: number }>(`/invoices?${q}`);
  },
  getInvoice: (id: string) => request<InvoiceDetail>(`/invoices/${id}`),
  review: (
    id: string,
    body: { action: ReviewAction; field_edits?: Record<string, unknown>; comment?: string },
  ) =>
    request<InvoiceDetail>(`/invoices/${id}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  reprocess: (id: string) =>
    request<InvoiceDetail>(`/invoices/${id}/reprocess`, { method: "POST" }),
  evalRuns: () => request<EvalRunSummary[]>("/evals/runs"),
  evalRun: (id: string) => request<EvalRunDetail>(`/evals/runs/${id}`),
  reconciliation: () => request<ReconciliationRow[]>("/reconciliation"),
  purchaseOrders: () => request<PurchaseOrder[]>("/purchase-orders"),
  uploadStatement: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<StatementUpload>("/bank-statements", { method: "POST", body: form });
  },
  uploadInvoice: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<InvoiceDetail>("/invoices", { method: "POST", body: form });
  },
};

/**
 * Server-Sent Events over fetch, so the bearer token goes in a header (EventSource
 * cannot set headers, and a token in the URL would end up in logs).
 */
export async function streamInvoiceEvents(
  id: string,
  onMessage: (m: StreamMessage) => void,
  signal: AbortSignal,
): Promise<void> {
  const token = readToken();
  const res = await fetch(`${API_URL}/invoices/${id}/events`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    signal,
  });
  if (!res.ok || !res.body) throw new ApiError(res.status, "event stream unavailable");
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += value;
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const data = frame
        .split("\n")
        .filter((l) => l.startsWith("data: "))
        .map((l) => l.slice(6))
        .join("\n");
      if (data) onMessage(JSON.parse(data) as StreamMessage);
    }
  }
}

export const IN_FLIGHT: InvoiceStatus[] = ["received", "processing"];

export function formatINR(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR" }).format(
    Number(value),
  );
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}
