"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { streamInvoiceEvents } from "@/lib/api";

/**
 * Keeps a live event stream open for one invoice and refreshes its cached detail on
 * every message, so the timeline, fields and status update as the agent works.
 * Reconnects with backoff; returns whether the stream is currently connected.
 */
export function useInvoiceStream(id: string, enabled = true) {
  const qc = useQueryClient();
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!enabled) return;
    const abort = new AbortController();
    let attempt = 0;

    async function loop() {
      while (!abort.signal.aborted) {
        try {
          await streamInvoiceEvents(
            id,
            () => {
              attempt = 0;
              setConnected(true);
              qc.invalidateQueries({ queryKey: ["invoice", id] });
              qc.invalidateQueries({ queryKey: ["invoices"] });
            },
            abort.signal,
          );
        } catch {
          /* network error or server restart: fall through to retry */
        }
        setConnected(false);
        if (abort.signal.aborted) return;
        attempt += 1;
        await new Promise((r) => setTimeout(r, Math.min(1000 * 2 ** attempt, 15_000)));
      }
    }
    loop();
    return () => abort.abort();
  }, [id, enabled, qc]);

  return connected;
}
