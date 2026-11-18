import * as React from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  Activity,
  ArrowRight,
  CheckCircle2,
  Cpu,
  Database,
  HardDrive,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Wifi,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/controls";
import { StatusBadge } from "@/components/StatusBadge";
import { studioApi } from "@/lib/api";
import { formatDateTime, formatNumber, formatRelativeTime } from "@/lib/utils";
import type { DashboardOverview, NavSection } from "@/types/studio";

export function OverviewPage({
  onNavigate,
}: {
  onNavigate: (section: NavSection) => void;
}) {
  const [data, setData] = React.useState<DashboardOverview | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [probingLive, setProbingLive] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [logFilter, setLogFilter] = React.useState<string>("all");

  const load = React.useCallback(async (checkLive = false) => {
    try {
      if (checkLive) setProbingLive(true);
      const res = await studioApi.getOverview(checkLive);
      setData(res);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load dashboard overview.");
    } finally {
      setLoading(false);
      setProbingLive(false);
    }
  }, []);

  React.useEffect(() => {
    void load(false);
    const id = window.setInterval(() => void load(false), 8000);
    return () => window.clearInterval(id);
  }, [load]);

  if (loading && !data) {
    return <div className="p-6 text-xs text-muted-foreground">Loading unified studio telemetry...</div>;
  }

  const sys = data?.system_status;
  const gen = data?.generation;
  const ref = data?.reference_collection;
  const qual = data?.quality;

  const gradeChartData = ["A", "B", "C", "D", "F"].map((grade) => ({
    grade,
    Reference: qual?.reference_grade_distribution?.[grade] ?? 0,
  }));

  const categoryChartData = Object.entries(gen?.category_breakdown || {}).map(([name, count]) => ({
    name,
    Assets: count,
  }));

  const logs = (data?.recent_activity || []).filter(
    (item) => logFilter === "all" || item.source === logFilter,
  );

  const todayTarget = Math.max(1, ref?.daily_target || 7);
  const todayPct = Math.min(100, Math.round(((ref?.today_downloaded || 0) / todayTarget) * 100));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">System Overview</h1>
          <p className="text-xs text-muted-foreground">
            Unified telemetry across procedural 3D generation, Roblox QA, BlendSwap collection, and Google Drive sync.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => void load(false)} disabled={probingLive}>
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh
          </Button>
          <Button variant="outline" size="sm" onClick={() => void load(true)} disabled={probingLive}>
            <Wifi className="h-3.5 w-3.5" />
            {probingLive ? "Checking Live API..." : "Test Live Connections"}
          </Button>
          <Button size="sm" onClick={() => onNavigate("generation")}>
            <Sparkles className="h-3.5 w-3.5" />
            New Generation
          </Button>
        </div>
      </div>

      {error ? (
        <div className="rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
          {error}
        </div>
      ) : null}

      {/* System Status Strip */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <Card>
          <CardContent className="p-3.5">
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-muted-foreground">Backend API</span>
              <Activity className="h-3.5 w-3.5 text-emerald-400" />
            </div>
            <div className="mt-1.5 flex items-center justify-between">
              <span className="text-sm font-semibold">{sys?.backend.status || "ONLINE"}</span>
              <StatusBadge status={sys?.backend.ok ? "ONLINE" : "ERROR"} />
            </div>
            <p className="mt-1 truncate text-[10px] text-muted-foreground">FastAPI + SQLite WAL</p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-3.5">
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-muted-foreground">Blender Worker</span>
              <Cpu className="h-3.5 w-3.5 text-primary" />
            </div>
            <div className="mt-1.5 flex items-center justify-between gap-1">
              <span className="truncate text-sm font-semibold">{sys?.blender.version || "Not found"}</span>
              <StatusBadge
                status={sys?.blender.available ? "CONNECTED" : "ERROR"}
                label={sys?.blender.available ? "READY" : "MISSING"}
              />
            </div>
            <p className="mt-1 truncate text-[10px] text-muted-foreground">
              {sys?.blender.meets_minimum ? "Headless worker verified" : sys?.blender.error || "Check Blender path"}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-3.5">
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-muted-foreground">Google Drive</span>
              <HardDrive className="h-3.5 w-3.5 text-sky-400" />
            </div>
            <div className="mt-1.5 flex items-center justify-between gap-1">
              <span className="truncate text-sm font-semibold">
                {sys?.google_drive.root_folder_name || "Unconfigured"}
              </span>
              <StatusBadge
                status={sys?.google_drive.authenticated ? "CONNECTED" : "PENDING"}
                label={sys?.google_drive.authenticated ? "LINKED" : "DISCONNECTED"}
              />
            </div>
            <p className="mt-1 truncate text-[10px] text-muted-foreground">
              {sys?.google_drive.user_email || "Scope: drive.file"}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-3.5">
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-muted-foreground">BlendSwap API</span>
              <Wifi className="h-3.5 w-3.5 text-emerald-400" />
            </div>
            <div className="mt-1.5 flex items-center justify-between gap-1">
              <span className="truncate text-sm font-semibold">
                {sys?.blendswap.connectivity.username ||
                  (sys?.blendswap.key_configured ? "Key Configured" : "No API Key")}
              </span>
              <StatusBadge
                status={
                  sys?.blendswap.connectivity.connected
                    ? "CONNECTED"
                    : sys?.blendswap.key_configured
                      ? "SCHEDULED"
                      : "PENDING"
                }
                label={
                  sys?.blendswap.connectivity.connected
                    ? "LIVE"
                    : sys?.blendswap.key_configured
                      ? "CONFIGURED"
                      : "UNSET"
                }
              />
            </div>
            <p className="mt-1 truncate text-[10px] text-muted-foreground">
              Credits Spend: {sys?.blendswap.allow_credit_spend ? "Allowed" : "Strictly Disabled"}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-3.5">
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-muted-foreground">Databases</span>
              <Database className="h-3.5 w-3.5 text-amber-400" />
            </div>
            <div className="mt-1.5 flex items-center justify-between">
              <span className="text-sm font-semibold">
                {formatNumber((sys?.databases.vault.assets_count || 0) + (sys?.databases.reference.assets_count || 0))} rows
              </span>
              <StatusBadge status="ONLINE" label="SQLITE" />
            </div>
            <p className="mt-1 truncate text-[10px] text-muted-foreground">
              Vault {sys?.databases.vault.assets_count || 0} / Ref {sys?.databases.reference.assets_count || 0} / RBLX {sys?.databases.roblox.assets_count || 0}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-3.5">
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-muted-foreground">Scheduler</span>
              <ShieldCheck className="h-3.5 w-3.5 text-primary" />
            </div>
            <div className="mt-1.5 flex items-center justify-between">
              <span className="text-sm font-semibold">{sys?.scheduler.collection_time || "07:00"}</span>
              <StatusBadge
                status={
                  ref?.control?.is_running
                    ? "RUNNING"
                    : sys?.scheduler.enabled
                      ? "SCHEDULED"
                      : "PAUSED"
                }
                label={ref?.control?.is_running ? "RUNNING" : sys?.scheduler.enabled ? "ACTIVE" : "PAUSED"}
              />
            </div>
            <p className="mt-1 truncate text-[10px] text-muted-foreground">
              {sys?.scheduler.timezone || "Europe/London"} ({formatRelativeTime(sys?.scheduler.next_run_at)})
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Generation + Collection + Quality Main KPI Row */}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle>Generation Pipeline</CardTitle>
              <CardDescription>Branch 1 assets & active jobs</CardDescription>
            </div>
            <Button variant="ghost" size="sm" onClick={() => onNavigate("generation")}>
              Studio <ArrowRight className="h-3.5 w-3.5" />
            </Button>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid grid-cols-3 gap-2">
              <div className="rounded-md bg-background/70 p-2.5">
                <p className="text-[10px] text-muted-foreground">Total Assets</p>
                <p className="mt-0.5 text-lg font-bold font-mono">{formatNumber(gen?.total_generated)}</p>
              </div>
              <div className="rounded-md bg-background/70 p-2.5">
                <p className="text-[10px] text-muted-foreground">Awaiting Approval</p>
                <p className="mt-0.5 text-lg font-bold font-mono text-amber-300">
                  {formatNumber(gen?.awaiting_human_approval)}
                </p>
              </div>
              <div className="rounded-md bg-background/70 p-2.5">
                <p className="text-[10px] text-muted-foreground">Approved / Synced</p>
                <p className="mt-0.5 text-lg font-bold font-mono text-emerald-300">{formatNumber(gen?.approved)}</p>
              </div>
            </div>
            <div className="flex items-center justify-between rounded-md border border-border/70 px-3 py-2 text-xs">
              <span className="text-muted-foreground">Awaiting Automated QA</span>
              <span className="font-mono font-semibold">{formatNumber(gen?.awaiting_qa)}</span>
            </div>
            <div className="flex items-center justify-between rounded-md border border-border/70 px-3 py-2 text-xs">
              <span className="text-muted-foreground">Active Queue Jobs</span>
              <Badge variant={(gen?.active_jobs_count || 0) > 0 ? "info" : "secondary"}>
                {gen?.active_jobs_count || 0} RUNNING/QUEUED
              </Badge>
            </div>
            <div className="flex items-center justify-between rounded-md border border-border/70 px-3 py-2 text-xs">
              <span className="text-muted-foreground">Failed / Rejected</span>
              <span className="font-mono font-semibold text-rose-300">{formatNumber(gen?.failed)}</span>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle>Reference Collection</CardTitle>
              <CardDescription>Daily BlendSwap collection quota</CardDescription>
            </div>
            <Button variant="ghost" size="sm" onClick={() => onNavigate("collection")}>
              Control <ArrowRight className="h-3.5 w-3.5" />
            </Button>
          </CardHeader>
          <CardContent className="space-y-3">
            <div>
              <div className="mb-1.5 flex items-center justify-between text-xs">
                <span className="text-muted-foreground">Today ({ref?.today_date || "Today"})</span>
                <span className="font-mono font-semibold">
                  {ref?.today_downloaded ?? 0} / {ref?.daily_target ?? 7} assets
                </span>
              </div>
              <Progress value={todayPct} />
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div className="rounded-md bg-background/70 p-2.5">
                <p className="text-[10px] text-muted-foreground">Free Allowance</p>
                <p className="mt-0.5 text-lg font-bold font-mono text-sky-300">
                  {ref?.free_downloads_remaining ?? 0}/{ref?.free_downloads_limit ?? 7}
                </p>
              </div>
              <div className="rounded-md bg-background/70 p-2.5">
                <p className="text-[10px] text-muted-foreground">Total Collected</p>
                <p className="mt-0.5 text-lg font-bold font-mono text-emerald-300">
                  {formatNumber(ref?.total_successful_downloads)}
                </p>
              </div>
              <div className="rounded-md bg-background/70 p-2.5">
                <p className="text-[10px] text-muted-foreground">Total Failed</p>
                <p className="mt-0.5 text-lg font-bold font-mono text-rose-300">
                  {formatNumber(ref?.total_failed_downloads)}
                </p>
              </div>
            </div>
            <div className="flex items-center justify-between rounded-md border border-border/70 px-3 py-2 text-xs">
              <span className="text-muted-foreground">Last Cycle Result</span>
              {ref?.latest_cycle ? (
                <StatusBadge status={ref.latest_cycle.status} />
              ) : (
                <span className="text-muted-foreground">No cycles recorded</span>
              )}
            </div>
            <div className="flex items-center justify-between rounded-md border border-border/70 px-3 py-2 text-xs">
              <span className="text-muted-foreground">Next Schedule</span>
              <span className="font-mono text-[11px]">{formatDateTime(ref?.next_collection_cycle)}</span>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle>Quality Intelligence</CardTitle>
              <CardDescription>QA scoring & reference benchmarking</CardDescription>
            </div>
            <Button variant="ghost" size="sm" onClick={() => onNavigate("quality")}>
              Reports <ArrowRight className="h-3.5 w-3.5" />
            </Button>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-md bg-background/70 p-2.5">
                <p className="text-[10px] text-muted-foreground">Avg Generation QA</p>
                <p className="mt-0.5 text-lg font-bold font-mono text-primary">
                  {qual?.average_generation_qa_score !== null && qual?.average_generation_qa_score !== undefined
                    ? `${qual.average_generation_qa_score}/100`
                    : "—"}
                </p>
              </div>
              <div className="rounded-md bg-background/70 p-2.5">
                <p className="text-[10px] text-muted-foreground">Reference Avg Grade</p>
                <p className="mt-0.5 text-lg font-bold font-mono text-emerald-300">
                  {qual?.average_reference_score !== null && qual?.average_reference_score !== undefined
                    ? `${qual.average_reference_score}/100`
                    : "—"}
                </p>
              </div>
            </div>
            <div className="flex items-center justify-between rounded-md border border-border/70 px-3 py-2 text-xs">
              <span className="text-muted-foreground">Reference Library Size</span>
              <span className="font-mono font-semibold">{formatNumber(qual?.reference_library_size)} graded models</span>
            </div>
            <div className="flex items-center justify-between rounded-md border border-border/70 px-3 py-2 text-xs">
              <span className="text-muted-foreground">Benchmark Reports</span>
              <span className="font-mono font-semibold">{formatNumber(qual?.benchmark_count)} comparisons</span>
            </div>
            <div className="rounded-md border border-border/70 px-3 py-2 text-xs">
              <p className="text-[11px] font-medium text-muted-foreground">Latest Benchmark Recommendation</p>
              <p className="mt-1 line-clamp-2 text-[11px] text-foreground">
                {qual?.recent_benchmarks?.[0]?.recommended_improvements?.[0] ||
                  qual?.recent_benchmarks?.[0]?.efficiency_note ||
                  "Generate or benchmark an asset to see targeted topology/UV improvements."}
              </p>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Charts + Recent Activity */}
      <div className="grid gap-4 lg:grid-cols-12">
        <Card className="lg:col-span-4">
          <CardHeader>
            <CardTitle>Vault Category Mix</CardTitle>
            <CardDescription>Distribution of generated & imported models</CardDescription>
          </CardHeader>
          <CardContent className="h-56">
            {categoryChartData.length === 0 ? (
              <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
                No assets in Vault yet.
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={categoryChartData} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#242b38" />
                  <XAxis dataKey="name" tick={{ fill: "#8f9bb3", fontSize: 10 }} />
                  <YAxis allowDecimals={false} tick={{ fill: "#8f9bb3", fontSize: 10 }} />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "#141821",
                      borderColor: "#283040",
                      fontSize: 11,
                    }}
                  />
                  <Bar dataKey="Assets" fill="#f58220" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-4">
          <CardHeader>
            <CardTitle>Reference Grade Distribution</CardTitle>
            <CardDescription>Technical quality grades across BlendSwap references</CardDescription>
          </CardHeader>
          <CardContent className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={gradeChartData} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#242b38" />
                <XAxis dataKey="grade" tick={{ fill: "#8f9bb3", fontSize: 11 }} />
                <YAxis allowDecimals={false} tick={{ fill: "#8f9bb3", fontSize: 10 }} />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "#141821",
                    borderColor: "#283040",
                    fontSize: 11,
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar dataKey="Reference" fill="#34d399" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card className="lg:col-span-4">
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle>System Activity Log</CardTitle>
              <CardDescription>Real-time jobs, collection & QA events</CardDescription>
            </div>
            <div className="flex gap-1">
              {["all", "generation", "collection", "roblox_qa"].map((f) => (
                <button
                  key={f}
                  type="button"
                  onClick={() => setLogFilter(f)}
                  className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${
                    logFilter === f ? "bg-primary/20 text-primary" : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {f === "roblox_qa" ? "QA" : f}
                </button>
              ))}
            </div>
          </CardHeader>
          <CardContent className="max-h-56 space-y-2 overflow-y-auto">
            {logs.length === 0 ? (
              <p className="py-8 text-center text-xs text-muted-foreground">No activity recorded yet.</p>
            ) : (
              logs.map((ev, idx) => (
                <div
                  key={`${ev.timestamp}-${idx}`}
                  className="flex items-start justify-between gap-2 rounded-md border border-border/60 bg-background/50 p-2"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <CheckCircle2 className="h-3 w-3 shrink-0 text-muted-foreground" />
                      <span className="truncate text-[11px] font-medium">{ev.title}</span>
                    </div>
                    {ev.message ? (
                      <p className="mt-0.5 line-clamp-2 text-[10px] text-muted-foreground">{ev.message}</p>
                    ) : null}
                  </div>
                  <div className="shrink-0 text-right">
                    <StatusBadge status={ev.status} />
                    <p className="mt-0.5 font-mono text-[9px] text-muted-foreground">
                      {formatRelativeTime(ev.timestamp)}
                    </p>
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
