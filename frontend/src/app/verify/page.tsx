"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { verifyOtp, resendOtp, errorMessage } from "@/lib/auth";

function VerifyForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState(params.get("email") ?? "");
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [resending, setResending] = useState(false);

  async function onVerify(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    try {
      await verifyOtp(email, code.trim());
      toast.success("Email verified — welcome!");
      router.push("/dashboard");
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }

  async function onResend() {
    setResending(true);
    try {
      await resendOtp(email);
      toast.success("A new code has been sent.");
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setResending(false);
    }
  }

  return (
    <Card className="w-full max-w-sm">
      <CardHeader>
        <CardTitle className="text-2xl">Verify your email</CardTitle>
        <CardDescription>
          We sent a 6-digit code to{" "}
          <span className="font-medium">{email || "your email"}</span>. Enter it
          below to activate your account.
        </CardDescription>
      </CardHeader>
      <form onSubmit={onVerify}>
        <CardContent className="space-y-4">
          {!params.get("email") && (
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
              />
            </div>
          )}
          <div className="space-y-2">
            <Label htmlFor="code">Verification code</Label>
            <Input
              id="code"
              inputMode="numeric"
              maxLength={6}
              placeholder="123456"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              className="tracking-[0.5em] text-center text-lg"
            />
          </div>
        </CardContent>
        <CardFooter className="mt-6 flex-col gap-3">
          <Button
            type="submit"
            className="w-full"
            disabled={loading || code.length < 4 || !email}
          >
            {loading ? "Verifying…" : "Verify email"}
          </Button>
          <Button
            type="button"
            variant="ghost"
            className="w-full"
            onClick={onResend}
            disabled={resending || !email}
          >
            {resending ? "Sending…" : "Resend code"}
          </Button>
          <p className="text-sm text-muted-foreground">
            Wrong email?{" "}
            <Link href="/signup" className="font-medium underline">
              Sign up again
            </Link>
          </p>
        </CardFooter>
      </form>
    </Card>
  );
}

export default function VerifyPage() {
  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-12">
      <Suspense fallback={null}>
        <VerifyForm />
      </Suspense>
    </div>
  );
}
