import { LiveAnalysis } from "@/components/live-analysis";

export const metadata = {
  title: "Analyse a video",
};

/**
 * Upload a video and watch the analysis happen.
 *
 * The upload returns immediately and the worker streams its progress back, so
 * extracted frames are on screen long before the detection models finish.
 */
export default function AnalyzePage() {
  return (
    <div className="mx-auto w-full max-w-4xl px-6 py-8">
      <h1 className="text-2xl font-semibold">Analyse a video</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Upload once — frames appear as they are extracted, incidents as they are
        found.
      </p>

      <div className="mt-8">
        <LiveAnalysis />
      </div>
    </div>
  );
}
