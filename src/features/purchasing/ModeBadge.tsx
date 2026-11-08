import { Badge } from "@/components/ui/badge";
import type { RunMode } from "@/lib/types";

const COPY: Record<RunMode, { label: string; hint: string }> = {
  recorded: {
    label: "Recorded result",
    hint: "Stored extraction replayed; PO matching and rules run live in code. No model call.",
  },
  live: {
    label: "Live inference",
    hint: "OCR, extraction and decision ran on a model just now.",
  },
  manual: { label: "Manual upload", hint: "Run with your own settings." },
};

export function ModeBadge({ mode }: { mode: RunMode }) {
  const c = COPY[mode];
  return (
    <Badge
      variant="outline"
      title={c.hint}
      className={
        mode === "live"
          ? "border-approve/50 text-approve"
          : "border-review/50 text-review-foreground"
      }
    >
      {c.label}
    </Badge>
  );
}
