"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ConfidencePill, StatusBadge } from "@/components/status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { UploadDropzone } from "@/components/upload-dropzone";
import { api, formatDate, formatINR, IN_FLIGHT } from "@/lib/api";

const FILTERS = [
  { value: "", label: "All" },
  { value: "processing", label: "Processing" },
  { value: "needs_review", label: "Needs review" },
  { value: "approved", label: "Approved" },
  { value: "failed", label: "Failed" },
];

export default function InvoicesPage() {
  const router = useRouter();
  const [status, setStatus] = useState("");
  const [vendor, setVendor] = useState("");

  const { data, isLoading, isError } = useQuery({
    queryKey: ["invoices", status, vendor],
    queryFn: () => api.listInvoices({ status, vendor }),
    // Poll while anything is still being processed.
    refetchInterval: (q) =>
      q.state.data?.items.some((i) => IN_FLIGHT.includes(i.status)) ? 2_000 : false,
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Invoices</h1>
        <p className="text-sm text-muted-foreground">
          Upload a vendor invoice; the agent extracts every field and scores its confidence.
        </p>
      </div>

      <UploadDropzone />

      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => (
          <button
            key={f.value}
            onClick={() => setStatus(f.value)}
            className={`rounded-full border px-3 py-1 text-sm ${
              status === f.value ? "border-primary bg-primary text-primary-foreground" : "bg-background hover:bg-muted"
            }`}
          >
            {f.label}
          </button>
        ))}
        <input
          value={vendor}
          onChange={(e) => setVendor(e.target.value)}
          placeholder="Filter by vendor…"
          className="ml-auto h-8 w-56 rounded-md border bg-background px-3 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </div>

      <div className="rounded-lg border bg-background">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Vendor</TableHead>
              <TableHead>Invoice no.</TableHead>
              <TableHead>Date</TableHead>
              <TableHead className="text-right">Total</TableHead>
              <TableHead>Lowest confidence</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Uploaded</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading &&
              Array.from({ length: 3 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell colSpan={7}>
                    <Skeleton className="h-5 w-full" />
                  </TableCell>
                </TableRow>
              ))}
            {isError && (
              <TableRow>
                <TableCell colSpan={7} className="py-8 text-center text-destructive">
                  Could not load invoices.
                </TableCell>
              </TableRow>
            )}
            {data?.items.length === 0 && (
              <TableRow>
                <TableCell colSpan={7} className="py-10 text-center text-muted-foreground">
                  No invoices yet. Upload one above.
                </TableCell>
              </TableRow>
            )}
            {data?.items.map((inv) => (
              <TableRow
                key={inv.id}
                className="cursor-pointer"
                onClick={() => router.push(`/invoices/${inv.id}`)}
              >
                <TableCell className="font-medium">
                  {inv.vendor_name ?? (
                    <span className="text-muted-foreground">{inv.original_filename}</span>
                  )}
                </TableCell>
                <TableCell className="font-mono text-xs">{inv.invoice_number ?? "—"}</TableCell>
                <TableCell>{formatDate(inv.invoice_date)}</TableCell>
                <TableCell className="text-right tabular-nums">{formatINR(inv.total)}</TableCell>
                <TableCell>
                  <ConfidencePill value={inv.min_confidence} />
                </TableCell>
                <TableCell>
                  <StatusBadge status={inv.status} />
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {new Date(inv.created_at).toLocaleString("en-IN", {
                    dateStyle: "medium",
                    timeStyle: "short",
                  })}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
