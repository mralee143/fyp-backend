import Link from "next/link";
import { Button } from "@/components/ui/button";

const CAPABILITIES = [
  { icon: "🥊", title: "Fighting", desc: "Physical violence & assault" },
  { icon: "🔫", title: "Weapons", desc: "Guns, knives, rifles" },
  { icon: "💣", title: "Explosives", desc: "Bombs & grenades" },
  { icon: "🚨", title: "Robbery", desc: "Snatching & theft" },
];

export default function Home() {
  return (
    <div className="flex flex-col min-h-screen">
      {/* Header */}
      <header className="border-b">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-2 font-semibold">
            <span className="grid size-8 place-items-center rounded-lg bg-primary text-primary-foreground">
              S
            </span>
            SentinelAI
          </div>
          <nav className="flex items-center gap-2">
            <Button
              render={<Link href="/login" />}
              nativeButton={false}
              variant="ghost"
            >
              Log in
            </Button>
            <Button render={<Link href="/signup" />} nativeButton={false}>
              Get started
            </Button>
          </nav>
        </div>
      </header>

      {/* Hero */}
      <main className="flex-1">
        <section className="mx-auto max-w-6xl px-6 py-20 text-center">
          <span className="inline-flex items-center rounded-full border px-3 py-1 text-xs text-muted-foreground">
            Final Year Project · Computer Vision
          </span>
          <h1 className="mx-auto mt-6 max-w-3xl text-4xl font-bold tracking-tight sm:text-5xl">
            Detect violence, crime &amp; weapons in video — automatically.
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-lg text-muted-foreground">
            Upload footage and let AI flag threats: fights, robberies,
            shootings, explosions and weapons — with timestamps and confidence
            scores.
          </p>
          <div className="mt-8 flex justify-center gap-3">
            <Button
              render={<Link href="/signup" />}
              nativeButton={false}
              size="lg"
            >
              Create an account
            </Button>
            <Button
              render={<Link href="/login" />}
              nativeButton={false}
              size="lg"
              variant="outline"
            >
              Log in
            </Button>
          </div>
        </section>

        {/* Capabilities */}
        <section className="mx-auto max-w-6xl px-6 pb-24">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {CAPABILITIES.map((c) => (
              <div
                key={c.title}
                className="rounded-xl border bg-card p-6 text-card-foreground"
              >
                <div className="text-3xl">{c.icon}</div>
                <h3 className="mt-3 font-semibold">{c.title}</h3>
                <p className="mt-1 text-sm text-muted-foreground">{c.desc}</p>
              </div>
            ))}
          </div>
        </section>
      </main>

      <footer className="border-t">
        <div className="mx-auto max-w-6xl px-6 py-6 text-sm text-muted-foreground">
          SentinelAI — Final Year Project
        </div>
      </footer>
    </div>
  );
}
