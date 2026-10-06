"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, UploadCloud } from "lucide-react";
import { useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";

const ACCEPT = "application/pdf,image/png,image/jpeg";

export function UploadDropzone() {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);

  const upload = useMutation({
    mutationFn: async (files: File[]) => {
      const failed: string[] = [];
      for (const f of files) {
        try {
          await api.uploadInvoice(f);
        } catch (e) {
          failed.push(`${f.name}: ${e instanceof ApiError ? e.message : "upload failed"}`);
        }
      }
      return failed;
    },
    onSuccess: (failed) => setErrors(failed),
    onSettled: () => qc.invalidateQueries({ queryKey: ["invoices"] }),
  });

  function handle(files: FileList | null) {
    if (files?.length) upload.mutate(Array.from(files));
  }

  return (
    <div>
      <button
        type="button"
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          handle(e.dataTransfer.files);
        }}
        className={`flex w-full flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-6 py-8 text-sm transition-colors ${
          dragging ? "border-primary bg-primary/5" : "border-muted-foreground/25 bg-background hover:bg-muted/50"
        }`}
      >
        {upload.isPending ? (
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
        ) : (
          <UploadCloud className="size-6 text-muted-foreground" />
        )}
        <span className="font-medium">
          {upload.isPending ? "Uploading…" : "Drop invoices here or click to choose"}
        </span>
        <span className="text-muted-foreground">PDF, PNG or JPG, up to 15 MB each</span>
      </button>
      <input
        ref={input}
        type="file"
        accept={ACCEPT}
        multiple
        className="hidden"
        onChange={(e) => {
          handle(e.target.files);
          e.target.value = "";
        }}
      />
      {errors.length > 0 && (
        <ul className="mt-2 space-y-1 text-sm text-destructive">
          {errors.map((m) => (
            <li key={m}>{m}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
