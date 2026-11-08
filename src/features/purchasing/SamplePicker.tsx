import { useEffect, useState } from "react";
import { FileCheck2, FileWarning, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { ApiError, getConfig, listSamples, runSample } from "@/lib/api";
import { usePipelineContext } from "@/features/pipeline/PipelineContext";
import type { AppConfig, RunMode, SampleInfo } from "@/lib/types";

export function SamplePicker() {
  const { openDocument } = usePipelineContext();
  const [samples, setSamples] = useState<SampleInfo[]>([]);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [mode, setMode] = useState<RunMode>("recorded");
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    listSamples().then(setSamples).catch(() => undefined);
    getConfig().then(setConfig).catch(() => undefined);
  }, []);

  async function run(id: string) {
    setBusy(id);
    try {
      const { document_id } = await runSample(id, mode);
      await openDocument(document_id);
    } catch (e) {
      toast.error("Sample run failed", {
        description: e instanceof ApiError ? e.message : "Unexpected error",
      });
    } finally {
      setBusy(null);
    }
  }

  if (samples.length === 0) return null;
  return (
    <div className="space-y-3 rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold">Vendor invoice review: pick a sample</h2>
        <div role="group" aria-label="Run mode" className="inline-flex rounded-md border text-xs">
          {(["recorded", "live"] as const).map((m) => {
            const off = m === "live" && !config?.live_available;
            return (
              <button
                key={m}
                disabled={off}
                onClick={() => setMode(m)}
                title={off ? "Live inference is not enabled on this deployment" : undefined}
                className={`px-3 py-1.5 first:rounded-l-md last:rounded-r-md ${mode === m ? "bg-primary text-primary-foreground" : "text-muted-foreground"} ${off ? "cursor-not-allowed opacity-50" : ""}`}
              >
                {m === "recorded" ? "Recorded result" : "Live inference"}
              </button>
            );
          })}
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        {mode === "recorded"
          ? "Replays a stored extraction of a synthetic invoice. PO matching, rules and the decision run live in code; no model is called."
          : "Runs OCR, extraction and the decision on a model (capped per visitor). Synthetic documents only."}
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        {samples.map((s) => {
          const Icon = s.id.includes("mismatch") ? FileWarning : FileCheck2;
          return (
            <div key={s.id} className="flex flex-col justify-between gap-3 rounded-lg border p-3">
              <div>
                <div className="flex items-center gap-2 text-sm font-medium">
                  <Icon className="size-4 text-brand" />
                  {s.title}
                </div>
                <p className="mt-1 text-xs text-muted-foreground">{s.expect}</p>
              </div>
              <Button size="sm" disabled={busy !== null} onClick={() => run(s.id)}>
                {busy === s.id ? <Loader2 className="size-4 animate-spin" /> : null}
                {busy === s.id ? "Running pipeline..." : "Run this sample"}
              </Button>
            </div>
          );
        })}
      </div>
      {config?.public_mode && (
        <p className="text-xs text-muted-foreground">
          Arbitrary upload is disabled on this public demo. Documents are deleted after {config.ttl_minutes} minutes.
        </p>
      )}
    </div>
  );
}
