import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDateTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatRelativeTime(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const diffSec = Math.round((d.getTime() - Date.now()) / 1000);
  const abs = Math.abs(diffSec);
  if (abs < 60) return diffSec >= 0 ? `in ${abs}s` : `${abs}s ago`;
  const mins = Math.round(abs / 60);
  if (mins < 60) return diffSec >= 0 ? `in ${mins}m` : `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 48) return diffSec >= 0 ? `in ${hrs}h` : `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return diffSec >= 0 ? `in ${days}d` : `${days}d ago`;
}

export function formatNumber(n?: number | null): string {
  if (n === undefined || n === null || Number.isNaN(n)) return "—";
  return n.toLocaleString();
}

export function formatBytes(bytes?: number | null): string {
  if (!bytes || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let idx = 0;
  let val = bytes;
  while (val >= 1024 && idx < units.length - 1) {
    val /= 1024;
    idx += 1;
  }
  return `${val.toFixed(idx === 0 ? 0 : 1)} ${units[idx]}`;
}

export type BadgeVariant =
  | "default"
  | "secondary"
  | "success"
  | "warning"
  | "destructive"
  | "outline"
  | "info";

export function statusBadgeVariant(status?: string | null): BadgeVariant {
  const s = (status || "").toUpperCase();
  if (
    [
      "COMPLETED",
      "APPROVED",
      "UPLOADED",
      "PASSED",
      "QA_PASSED",
      "GRADED",
      "SYNCED",
      "CONNECTED",
      "ONLINE",
      "A",
    ].includes(s)
  ) {
    return "success";
  }
  if (
    [
      "RUNNING",
      "PROCESSING",
      "TECHNICAL_TESTING",
      "VISUAL_TESTING",
      "QA_RUNNING",
      "SCHEDULED",
      "B",
    ].includes(s)
  ) {
    return "info";
  }
  if (
    [
      "QUEUED",
      "PENDING",
      "AWAITING_HUMAN_APPROVAL",
      "PAUSED",
      "PARTIAL",
      "RETRY_WAIT",
      "POSTPONED_WAITING_RESET",
      "C",
    ].includes(s)
  ) {
    return "warning";
  }
  if (
    [
      "FAILED",
      "REJECTED",
      "QA_FAILED",
      "TECH_QA_FAILED",
      "VISUAL_QA_FAILED",
      "CANCELLED",
      "ERROR",
      "D",
      "F",
    ].includes(s)
  ) {
    return "destructive";
  }
  return "secondary";
}
