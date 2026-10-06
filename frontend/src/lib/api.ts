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
}

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
  uploadInvoice: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<InvoiceDetail>("/invoices", { method: "POST", body: form });
  },
};

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
