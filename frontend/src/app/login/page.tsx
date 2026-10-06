"use client";

import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function tryDemo() {
    setLoading(true);
    setError(null);
    try {
      await api.demoLogin();
      router.replace("/invoices");
    } catch {
      setError("Could not reach the backend. Is it running on port 8000?");
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-1 items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="text-xl">FinOps Agent</CardTitle>
          <CardDescription>
            An AI accounts team you can trust with money, with measured accuracy.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Button className="w-full" onClick={tryDemo} disabled={loading}>
            {loading && <Loader2 className="size-4 animate-spin" />}
            Try the demo
          </Button>
          <p className="text-xs text-muted-foreground">
            Signs in to a demo company with synthetic data. Email sign-in arrives with Supabase
            Auth.
          </p>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </CardContent>
      </Card>
    </div>
  );
}
