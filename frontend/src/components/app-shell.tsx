"use client";

import { FileText, LogOut } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useSyncExternalStore } from "react";

import { Button } from "@/components/ui/button";
import { hasToken, setToken } from "@/lib/api";

function subscribeStorage(onChange: () => void) {
  window.addEventListener("storage", onChange);
  return () => window.removeEventListener("storage", onChange);
}

/** Top nav + auth gate. Pages under the shell require a token; /login does not. */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const isLogin = pathname === "/login";
  // null on the server (token lives in localStorage), boolean in the browser.
  const authed = useSyncExternalStore(subscribeStorage, hasToken, () => null);

  useEffect(() => {
    if (!isLogin && authed === false) router.replace("/login");
  }, [isLogin, authed, router]);

  if (isLogin) return <>{children}</>;
  if (!authed) return null;

  return (
    <div className="flex min-h-full flex-col">
      <header className="border-b bg-background">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-6 px-4">
          <Link href="/invoices" className="font-semibold tracking-tight">
            FinOps Agent
          </Link>
          <nav className="flex items-center gap-1 text-sm">
            <Link
              href="/invoices"
              className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 hover:bg-muted ${
                pathname.startsWith("/invoices") ? "bg-muted font-medium" : "text-muted-foreground"
              }`}
            >
              <FileText className="size-4" /> Invoices
            </Link>
          </nav>
          <div className="ml-auto">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setToken(null);
                router.replace("/login");
              }}
            >
              <LogOut className="size-4" /> Sign out
            </Button>
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6">{children}</main>
    </div>
  );
}
