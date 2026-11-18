import { AlertTriangle, CheckCircle2, Clock, Loader2, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { statusBadgeVariant } from "@/lib/utils";

export function StatusBadge({ status, label }: { status?: string | null; label?: string }) {
  const variant = statusBadgeVariant(status);
  const text = label || status || "UNKNOWN";
  const upper = text.toUpperCase();
  const isRunning = ["RUNNING", "PROCESSING", "TECHNICAL_TESTING", "VISUAL_TESTING", "QA_RUNNING"].includes(upper);

  return (
    <Badge variant={variant} className="whitespace-nowrap">
      {isRunning ? <Loader2 className="h-2.5 w-2.5 animate-spin" /> : null}
      <span>{text}</span>
    </Badge>
  );
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "Confirm",
  confirmVariant = "default",
  busy = false,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  confirmLabel?: string;
  confirmVariant?: "default" | "destructive";
  busy?: boolean;
  onConfirm: () => void | Promise<void>;
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      maxWidthClass="max-w-md"
      title={
        <span className="inline-flex items-center gap-2">
          <AlertTriangle className="h-4 w-4 text-amber-400" />
          {title}
        </span>
      }
      footer={
        <>
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant={confirmVariant}
            size="sm"
            onClick={() => void onConfirm()}
            disabled={busy}
          >
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            {confirmLabel}
          </Button>
        </>
      }
    >
      <p className="text-xs leading-relaxed text-muted-foreground">{description}</p>
    </Dialog>
  );
}

export function StageIcon({ status }: { status: string }) {
  const s = (status || "").toUpperCase();
  if (s === "COMPLETED") return <CheckCircle2 className="h-4 w-4 text-emerald-400" />;
  if (s === "RUNNING") return <Loader2 className="h-4 w-4 animate-spin text-sky-400" />;
  if (s === "FAILED" || s === "CANCELLED") return <XCircle className="h-4 w-4 text-rose-400" />;
  return <Clock className="h-4 w-4 text-muted-foreground" />;
}
