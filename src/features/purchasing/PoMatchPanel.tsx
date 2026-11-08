import { useCallback, useEffect, useState } from "react";
import { Check, CircleAlert, CircleHelp } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { ApiError, getWorkflow, submitReview } from "@/lib/api";
import type { Workflow } from "@/lib/types";

const ICON = { match: Check, mismatch: CircleAlert, missing: CircleHelp };
const TONE = {
  match: "text-approve",
  mismatch: "text-flag",
  missing: "text-review-foreground",
};

export function PoMatchPanel({
  docId,
  refreshKey,
}: {
  docId: string;
  refreshKey: unknown;
}) {
  const [wf, setWf] = useState<Workflow | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setWf(await getWorkflow(docId));
      setError(null);
    } catch (e) {
      setWf(null);
      setError(e instanceof Error ? e.message : "Could not load");
    }
  }, [docId]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load, refreshKey]);

  const rec = wf?.po_reconciliation;
  if (!rec)
    return (
      <p className="rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
        {error ?? "PO reconciliation appears once the decision stage has run."}
      </p>
    );

  const bad = rec.comparisons.filter((c) => c.status !== "match").length;
  return (
    <div className="space-y-4">
      <div
        className={cn(
          "rounded-xl border p-3 text-sm",
          rec.status === "matched"
            ? "border-approve/40 bg-approve/[0.06]"
            : "border-review/40 bg-review-muted/40",
        )}
      >
        <div className="font-medium">
          {rec.status === "matched"
            ? `Matches ${rec.po_number} - ${rec.supplier_on_po}`
            : `Routed to review: ${bad} difference${bad === 1 ? "" : "s"} against ${rec.po_number ?? "no PO"}`}
        </div>
        {rec.reasons.length > 0 && (
          <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-foreground/80">
            {rec.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        )}
      </div>

      <div className="overflow-x-auto rounded-xl border">
        <table className="w-full text-left text-xs">
          <thead className="bg-muted/50 text-muted-foreground">
            <tr>
              <th className="px-3 py-2 font-medium">Check</th>
              <th className="px-3 py-2 font-medium">Expected (PO)</th>
              <th className="px-3 py-2 font-medium">Observed (invoice)</th>
              <th className="px-3 py-2 font-medium">Evidence</th>
            </tr>
          </thead>
          <tbody>
            {rec.comparisons.map((c) => {
              const Icon = ICON[c.status];
              return (
                <tr
                  key={c.key}
                  className={cn(
                    "border-t align-top",
                    c.status !== "match" && "bg-flag/[0.04]",
                  )}
                >
                  <td className="px-3 py-2">
                    <span className="flex items-start gap-1.5">
                      <Icon className={cn("mt-0.5 size-3.5 shrink-0", TONE[c.status])} />
                      {c.label}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-mono">{c.expected ?? "-"}</td>
                  <td className={cn("px-3 py-2 font-mono", c.status !== "match" && "font-semibold text-flag")}>
                    {c.observed ?? "not found"}
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">
                    {c.evidence?.snippet ? (
                      <span>
                        <span className="font-mono">{c.evidence.snippet}</span>
                        {c.evidence.page != null && <span> (p.{c.evidence.page})</span>}
                      </span>
                    ) : (
                      "-"
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <ReviewBox docId={docId} wf={wf} onSaved={load} />
    </div>
  );
}

function ReviewBox({
  docId,
  wf,
  onSaved,
}: {
  docId: string;
  wf: Workflow;
  onSaved: () => void;
}) {
  const [reviewer, setReviewer] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  if (wf.review) {
    const r = wf.review;
    return (
      <div className="rounded-xl border bg-card p-3 text-sm">
        <div className="font-medium">
          Reviewer decision: {r.outcome === "approve" ? "Approved" : "Rejected"}{" "}
          <span className="font-normal text-muted-foreground">
            by {r.reviewer} at {new Date(r.decided_at).toLocaleString()}
          </span>
        </div>
        <p className="mt-1 text-foreground/80">{r.reason}</p>
        <p className="mt-1 text-xs text-muted-foreground">
          System decision was <span className="font-mono">{r.system_decision}</span>
        </p>
      </div>
    );
  }

  async function save(outcome: "approve" | "reject") {
    setBusy(true);
    try {
      await submitReview(docId, { reviewer, outcome, reason });
      toast.success("Reviewer decision recorded");
      onSaved();
    } catch (e) {
      toast.error("Could not record decision", {
        description: e instanceof ApiError && e.status === 422 ? "Name needs 2+ characters and the reason 10+." : (e as Error).message,
      });
    } finally {
      setBusy(false);
    }
  }

  const valid = reviewer.trim().length >= 2 && reason.trim().length >= 10;
  return (
    <div className="space-y-2 rounded-xl border bg-card p-3">
      <div className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
        Reviewer decision
      </div>
      <input
        value={reviewer}
        onChange={(e) => setReviewer(e.target.value)}
        maxLength={60}
        placeholder="Your name"
        aria-label="Reviewer name"
        className="w-full rounded-md border bg-background px-3 py-2 text-sm"
      />
      <textarea
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        maxLength={500}
        rows={2}
        placeholder="Reason (at least 10 characters)"
        aria-label="Decision reason"
        className="w-full rounded-md border bg-background px-3 py-2 text-sm"
      />
      <div className="flex gap-2">
        <Button size="sm" disabled={!valid || busy} onClick={() => save("approve")}>
          Approve invoice
        </Button>
        <Button size="sm" variant="outline" disabled={!valid || busy} onClick={() => save("reject")}>
          Reject invoice
        </Button>
      </div>
    </div>
  );
}
