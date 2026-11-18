import * as React from "react";
import {
  Ban,
  CheckCircle2,
  Eye,
  History,
  Loader2,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Save,
  SlidersHorizontal,
  Wifi,
} from "lucide-react";
import { ConfirmDialog, StageIcon, StatusBadge } from "@/components/StatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress, Switch, Tabs } from "@/components/ui/controls";
import { Dialog } from "@/components/ui/dialog";
import { Input, Select } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { studioApi } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/utils";
import type {
  CollectionCycle,
  CollectionQueueItem,
  CollectionRules,
  CollectionStatusResponse,
} from "@/types/studio";

export function CollectionControlPage() {
  const [tab, setTab] = React.useState<"today" | "rules" | "history">("today");
  const [statusData, setStatusData] = React.useState<CollectionStatusResponse | null>(null);
  const [historyCycles, setHistoryCycles] = React.useState<CollectionCycle[]>([]);
  const [sseConnected, setSseConnected] = React.useState(false);

  // Rules editor state
  const [draftRules, setDraftRules] = React.useState<CollectionRules | null>(null);
  const [ruleOptions, setRuleOptions] = React.useState<{
    categories: string[];
    licenses: string[];
    modeling_styles: string[];
    poly_complexities: string[];
    file_formats: string[];
    timezones: string[];
  }>({
    categories: [
      "Architecture",
      "Characters",
      "Clothing & Accessories",
      "Furniture",
      "Landscapes",
      "Machinery",
      "Mechanical components",
      "Plants",
      "Props and objects",
      "Vehicles",
      "Weapons & Armor",
      "Other",
    ],
    licenses: ["cc0", "cc_by", "cc_by_sa", "cc_by_nc", "cc_by_nc_sa"],
    modeling_styles: ["low-poly", "sub-d", "pbr", "stylized", "procedural", "rigged"],
    poly_complexities: ["any", "low_poly", "mid_poly", "high_poly"],
    file_formats: ["blend", "fbx", "obj", "glb", "any"],
    timezones: ["Europe/London", "UTC", "America/New_York", "America/Los_Angeles", "Asia/Kuala_Lumpur", "Asia/Tokyo"],
  });

  // Preview & cycle detail dialogs
  const [previewResult, setPreviewResult] = React.useState<Awaited<
    ReturnType<typeof studioApi.previewCandidates>
  > | null>(null);
  const [previewBusy, setPreviewBusy] = React.useState(false);
  const [selectedHistoryCycle, setSelectedHistoryCycle] = React.useState<{
    cycle: CollectionCycle;
    queue: CollectionQueueItem[];
  } | null>(null);

  const [notice, setNotice] = React.useState<{ type: "ok" | "err"; text: string } | null>(null);
  const [confirmState, setConfirmState] = React.useState<{
    open: boolean;
    title: string;
    description: string;
    confirmLabel: string;
    variant: "default" | "destructive";
    onConfirm: () => Promise<void>;
  } | null>(null);
  const [actionBusy, setActionBusy] = React.useState(false);

  const loadStatus = React.useCallback(async (checkLive = false) => {
    try {
      const res = await studioApi.getCollectionStatus(checkLive);
      setStatusData(res);
      setDraftRules((prev) => prev || res.rules.active_rules);
    } catch (err) {
      setNotice({ type: "err", text: err instanceof Error ? err.message : "Failed to load collection status." });
    }
  }, []);

  const loadRules = React.useCallback(async () => {
    try {
      const res = await studioApi.getCollectionRules();
      if (res.options) setRuleOptions(res.options);
      setDraftRules(res.active_rules);
    } catch {
      // Ignore
    }
  }, []);

  const loadHistory = React.useCallback(async () => {
    try {
      const res = await studioApi.getCollectionHistory(40);
      setHistoryCycles(res.cycles);
    } catch {
      // Ignore
    }
  }, []);

  React.useEffect(() => {
    void loadStatus(false);
    void loadRules();
    void loadHistory();
  }, [loadStatus, loadRules, loadHistory]);

  // Real-time SSE listener with automatic polling fallback
  React.useEffect(() => {
    let closed = false;
    let es: EventSource | null = null;
    try {
      es = new EventSource("/api/collection/events");
      es.onopen = () => {
        if (!closed) setSseConnected(true);
      };
      const refreshOnEvent = () => {
        if (!closed) void loadStatus(false);
      };
      ["snapshot", "cycle_started", "stage_updated", "item_updated", "cycle_paused", "cycle_resumed", "cycle_cancel_requested", "cycle_finished", "cycle_failed"].forEach(
        (evtName) => es?.addEventListener(evtName, refreshOnEvent),
      );
      es.onerror = () => {
        if (!closed) setSseConnected(false);
      };
    } catch {
      setSseConnected(false);
    }
    return () => {
      closed = true;
      es?.close();
    };
  }, [loadStatus]);

  const isRunning = Boolean(statusData?.control.is_running);
  React.useEffect(() => {
    const id = window.setInterval(() => {
      void loadStatus(false);
      if (tab === "history") void loadHistory();
    }, isRunning || !sseConnected ? 2500 : 10000);
    return () => window.clearInterval(id);
  }, [isRunning, sseConnected, tab, loadStatus, loadHistory]);

  const openConfirm = (opts: {
    title: string;
    description: string;
    confirmLabel: string;
    variant?: "default" | "destructive";
    action: () => Promise<void>;
  }) => {
    setConfirmState({
      open: true,
      title: opts.title,
      description: opts.description,
      confirmLabel: opts.confirmLabel,
      variant: opts.variant || "default",
      onConfirm: opts.action,
    });
  };

  const executeConfirm = async () => {
    if (!confirmState) return;
    setActionBusy(true);
    try {
      await confirmState.onConfirm();
      setConfirmState(null);
      await loadStatus(false);
      await loadHistory();
    } catch (err) {
      setNotice({ type: "err", text: err instanceof Error ? err.message : "Action failed." });
    } finally {
      setActionBusy(false);
    }
  };

  const toggleArrayValue = (key: keyof CollectionRules, value: string) => {
    setDraftRules((prev) => {
      if (!prev) return prev;
      const cur = Array.isArray(prev[key]) ? [...(prev[key] as string[])] : [];
      const exists = cur.includes(value);
      const next = exists ? cur.filter((x) => x !== value) : [...cur, value];
      return { ...prev, [key]: next };
    });
  };

  const handleSaveRules = async (scope: "permanent" | "next_run") => {
    if (!draftRules) return;
    setActionBusy(true);
    try {
      const res = await studioApi.saveCollectionRules({ ...draftRules, scope });
      setDraftRules(res.active_rules);
      setNotice({
        type: "ok",
        text:
          scope === "next_run"
            ? "Saved one-time rules override for the next collection run."
            : "Saved permanent default collection rules and synced scheduler.",
      });
      await loadStatus(false);
    } catch (err) {
      setNotice({ type: "err", text: err instanceof Error ? err.message : "Failed to save rules." });
    } finally {
      setActionBusy(false);
    }
  };

  const handlePreview = async (overrideRules?: CollectionRules) => {
    setPreviewBusy(true);
    try {
      const res = await studioApi.previewCandidates(overrideRules || draftRules || undefined);
      setPreviewResult(res);
    } catch (err) {
      setNotice({ type: "err", text: err instanceof Error ? err.message : "Preview failed." });
    } finally {
      setPreviewBusy(false);
    }
  };

  const toggleExcludedCandidate = async (blendswapId: string) => {
    if (!draftRules) return;
    const cur = draftRules.excluded_candidate_ids || [];
    const nextIds = cur.includes(blendswapId)
      ? cur.filter((x) => x !== blendswapId)
      : [...cur, blendswapId];
    const updated = { ...draftRules, excluded_candidate_ids: nextIds };
    setDraftRules(updated);
    await handlePreview(updated);
  };

  const today = statusData?.today_cycle;
  const ov = statusData?.overview;
  const ctrl = statusData?.control;
  const sched = statusData?.scheduler;
  const stages = (today?.stages && today.stages.length > 0 ? today.stages : ctrl?.stages) || [];
  const dailyDownloaded = ov?.today_downloaded ?? today?.downloaded_count ?? 0;
  const dailyFailed = ov?.today_failed ?? today?.failed_count ?? 0;
  const dailyTarget = Math.max(1, ov?.daily_target ?? today?.target_count ?? 7);
  const pct = Math.min(100, Math.round((dailyDownloaded / dailyTarget) * 100));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-semibold tracking-tight">Collection Control</h1>
            <Badge variant={sseConnected ? "success" : "warning"}>
              <Wifi className="h-2.5 w-2.5" />
              {sseConnected ? "SSE LIVE" : "POLLING FALLBACK"}
            </Badge>
            <Badge variant="outline">Credits Spend: Disabled (Free Only)</Badge>
          </div>
          <p className="text-xs text-muted-foreground">
            Operate the daily BlendSwap reference collector, watch live pipeline stages, configure selection rules, and audit cycle history.
          </p>
        </div>

        <Tabs
          value={tab}
          onChange={setTab}
          items={[
            { id: "today", label: "Today's Collection" },
            { id: "rules", label: "Collection Rule Editor" },
            { id: "history", label: "History", badge: historyCycles.length },
          ]}
        />
      </div>

      {notice ? (
        <div
          className={`flex items-center justify-between rounded-md border px-3 py-2 text-xs ${
            notice.type === "ok"
              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
              : "border-rose-500/40 bg-rose-500/10 text-rose-200"
          }`}
        >
          <span>{notice.text}</span>
          <button type="button" onClick={() => setNotice(null)} className="opacity-70 hover:opacity-100">
            Dismiss
          </button>
        </div>
      ) : null}

      {/* Manual Control Toolbar */}
      <Card>
        <CardContent className="flex flex-wrap items-center justify-between gap-3 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              disabled={isRunning}
              onClick={() =>
                openConfirm({
                  title: "Run Collection Now?",
                  description:
                    "This launches an immediate BlendSwap collection cycle using your active free daily allowance. Credit spending remains strictly disabled (0 credits spent).",
                  confirmLabel: "Start Collection",
                  action: async () => {
                    await studioApi.runCollection({ sync_to_drive: true });
                    setNotice({ type: "ok", text: "Manual collection cycle dispatched." });
                  },
                })
              }
            >
              {isRunning ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
              Run Collection Now
            </Button>

            {ctrl?.paused ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  openConfirm({
                    title: "Resume Collection & Scheduler?",
                    description: "Resumes paused collection downloads and re-enables automatic daily runs.",
                    confirmLabel: "Resume",
                    action: async () => {
                      await studioApi.resumeCollection();
                      setNotice({ type: "ok", text: "Collection & scheduler resumed." });
                    },
                  })
                }
              >
                <Play className="h-3.5 w-3.5" />
                Resume Collection
              </Button>
            ) : (
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  openConfirm({
                    title: "Pause Collection?",
                    description:
                      "Pauses scheduled daily runs and signals any active collection loop to pause before the next download.",
                    confirmLabel: "Pause",
                    action: async () => {
                      await studioApi.pauseCollection();
                      setNotice({ type: "ok", text: "Collection paused." });
                    },
                  })
                }
              >
                <Pause className="h-3.5 w-3.5" />
                Pause Collection
              </Button>
            )}

            <Button
              variant="destructive"
              size="sm"
              disabled={!isRunning}
              onClick={() =>
                openConfirm({
                  title: "Cancel Active Collection?",
                  description:
                    "Requests cooperative cancellation of the currently running collection cycle after the current step finishes.",
                  confirmLabel: "Cancel Cycle",
                  variant: "destructive",
                  action: async () => {
                    await studioApi.cancelCollection();
                    setNotice({ type: "ok", text: "Cancellation requested for active collection cycle." });
                  },
                })
              }
            >
              <Ban className="h-3.5 w-3.5" />
              Cancel Pending Collection
            </Button>

            <Button
              variant="outline"
              size="sm"
              disabled={isRunning || !today}
              onClick={() =>
                openConfirm({
                  title: "Retry Failed Downloads?",
                  description:
                    "Re-processes only FAILED, RETRY_WAIT, or CANCELLED queue items from the latest cycle without re-downloading completed assets.",
                  confirmLabel: "Retry Failed Items",
                  action: async () => {
                    await studioApi.retryFailedCollection(today?.cycle_id);
                    setNotice({ type: "ok", text: "Retry job started for failed queue items." });
                  },
                })
              }
            >
              <RotateCcw className="h-3.5 w-3.5" />
              Retry Failed Downloads
            </Button>
          </div>

          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={() => setTab("history")}>
              <History className="h-3.5 w-3.5" />
              View History
            </Button>
            <Button variant="ghost" size="sm" onClick={() => void loadStatus(true)}>
              <RefreshCw className="h-3.5 w-3.5" />
              Sync Allowance
            </Button>
          </div>
        </CardContent>
      </Card>

      {tab === "today" ? (
        <div className="space-y-4">
          {/* Today KPIs */}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <Card>
              <CardContent className="p-3.5">
                <p className="text-[11px] text-muted-foreground">Cycle Date / Schedule</p>
                <p className="mt-1 text-sm font-semibold">{today?.cycle_date || ov?.today_date || "—"}</p>
                <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                  {sched?.collection_time || "03:00"} {sched?.timezone || "Europe/London"}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="p-3.5">
                <p className="text-[11px] text-muted-foreground">Current Status</p>
                <div className="mt-1 flex items-center justify-between">
                  <StatusBadge status={isRunning ? "RUNNING" : today?.status || sched?.state || "IDLE"} />
                </div>
                <p className="mt-1 truncate text-[10px] text-muted-foreground">
                  Start: {formatDateTime(today?.started_at)}
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="p-3.5">
                <p className="text-[11px] text-muted-foreground">Today&apos;s Downloads (OK / Fail / Target)</p>
                <p className="mt-1 font-mono text-sm font-bold">
                  <span className="text-emerald-300">{dailyDownloaded}</span>
                  {" / "}
                  <span className="text-rose-300">{dailyFailed}</span>
                  {" / "}
                  <span>{ov?.daily_target ?? today?.target_count ?? 7}</span>
                </p>
                <Progress className="mt-2 h-1.5" value={pct} />
                {today ? (
                  <p className="mt-1 text-[10px] text-muted-foreground">
                    Latest run: {today.downloaded_count}/{today.target_count} (fail {today.failed_count})
                  </p>
                ) : null}
              </CardContent>
            </Card>
            <Card>
              <CardContent className="p-3.5">
                <p className="text-[11px] text-muted-foreground">Remaining Free Allowance</p>
                <p className="mt-1 font-mono text-sm font-bold text-sky-300">
                  {ov?.free_downloads_remaining ?? 0} / {ov?.free_downloads_limit ?? 7}
                </p>
                <p className="mt-0.5 text-[10px] text-muted-foreground">Credits Spent: 0</p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="p-3.5">
                <p className="text-[11px] text-muted-foreground">Processing Stage</p>
                <p className="mt-1 truncate font-mono text-xs font-semibold text-primary">
                  {today?.current_stage || ctrl?.current_stage || "idle"}
                </p>
                <p className="mt-0.5 text-[10px] text-muted-foreground">
                  {statusData?.rules.has_next_run_override ? "Next-Run Rules Active" : "Default Rules Active"}
                </p>
              </CardContent>
            </Card>
          </div>

          {/* Live 10-Stage Collection Timeline */}
          <Card>
            <CardHeader>
              <CardTitle>Live 10-Stage Collection Pipeline</CardTitle>
              <CardDescription>
                Real stage statuses emitted directly from the BlendSwap collector worker
              </CardDescription>
            </CardHeader>
            <CardContent>
              {stages.length === 0 ? (
                <p className="py-4 text-center text-xs text-muted-foreground">
                  No collection cycle has run yet. Click "Run Collection Now" or wait for the scheduled window.
                </p>
              ) : (
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
                  {stages.map((st, idx) => (
                    <div
                      key={st.id || st.key || String(idx)}
                      className="flex flex-col justify-between rounded-md border border-border/75 bg-background/60 p-2.5"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <span className="font-mono text-[10px] text-muted-foreground">STEP {idx + 1}</span>
                        <StageIcon status={st.status} />
                      </div>
                      <p className="mt-1.5 text-xs font-semibold leading-snug">{st.label}</p>
                      <div className="mt-1.5 flex items-center justify-between gap-1">
                        <StatusBadge status={st.status} />
                      </div>
                      {st.detail ? (
                        <p className="mt-1.5 line-clamp-2 text-[10px] text-muted-foreground">{st.detail}</p>
                      ) : null}
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Current Cycle Queue */}
          <Card>
            <CardHeader>
              <CardTitle>Current Cycle Item Queue ({statusData?.queue.length || 0})</CardTitle>
              <CardDescription>Candidates selected and processed in the latest collection cycle</CardDescription>
            </CardHeader>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>BlendSwap ID</TableHead>
                    <TableHead>Title</TableHead>
                    <TableHead>Category</TableHead>
                    <TableHead>License</TableHead>
                    <TableHead>Score</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Retries / Notes</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {!statusData?.queue.length ? (
                    <TableRow>
                      <TableCell colSpan={7} className="py-8 text-center text-muted-foreground">
                        No items queued for today's cycle yet.
                      </TableCell>
                    </TableRow>
                  ) : (
                    statusData.queue.map((item) => (
                      <TableRow key={item.item_id}>
                        <TableCell className="font-mono text-[11px]">#{item.blendswap_id}</TableCell>
                        <TableCell className="font-medium">{item.title}</TableCell>
                        <TableCell>{item.primary_category}</TableCell>
                        <TableCell className="uppercase">{item.license_key}</TableCell>
                        <TableCell className="font-mono">{item.selection_score.toFixed(1)}</TableCell>
                        <TableCell>
                          <StatusBadge status={item.status} />
                        </TableCell>
                        <TableCell className="max-w-[260px] truncate text-[11px] text-muted-foreground">
                          {item.retry_count > 0 ? `${item.retry_count} retry` : ""}{" "}
                          {item.error_message || ""}
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </div>
      ) : null}

      {tab === "rules" && draftRules ? (
        <div className="grid gap-4 xl:grid-cols-12">
          <Card className="xl:col-span-7">
            <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
              <div>
                <CardTitle className="flex items-center gap-2">
                  <SlidersHorizontal className="h-4 w-4 text-primary" />
                  Collection Selection Rules
                </CardTitle>
                <CardDescription>
                  Configure target quotas, categories, licenses, styles, poly budgets, and scheduling.
                </CardDescription>
              </div>
              {statusData?.rules.has_next_run_override ? (
                <Badge variant="warning">One-Time Next-Run Override Active</Badge>
              ) : null}
            </CardHeader>
            <CardContent className="space-y-4 text-xs">
              <div className="grid gap-3 sm:grid-cols-3">
                <div>
                  <label className="mb-1 block text-[11px] text-muted-foreground">Daily Download Target</label>
                  <Input
                    type="number"
                    min={1}
                    max={50}
                    value={draftRules.daily_target}
                    onChange={(e) =>
                      setDraftRules({ ...draftRules, daily_target: Number(e.target.value) || 7 })
                    }
                  />
                </div>
                <div>
                  <label className="mb-1 block text-[11px] text-muted-foreground">Collection Time (24h)</label>
                  <Input
                    value={draftRules.collection_time}
                    onChange={(e) => setDraftRules({ ...draftRules, collection_time: e.target.value })}
                    placeholder="03:00"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-[11px] text-muted-foreground">Timezone</label>
                  <Select
                    value={draftRules.timezone}
                    onChange={(e) => setDraftRules({ ...draftRules, timezone: e.target.value })}
                  >
                    {ruleOptions.timezones.map((tz) => (
                      <option key={tz} value={tz}>
                        {tz}
                      </option>
                    ))}
                  </Select>
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-3">
                <div>
                  <label className="mb-1 block text-[11px] text-muted-foreground">Min Downloads Popularity</label>
                  <Input
                    type="number"
                    min={0}
                    value={draftRules.min_downloads}
                    onChange={(e) =>
                      setDraftRules({ ...draftRules, min_downloads: Number(e.target.value) || 0 })
                    }
                  />
                </div>
                <div>
                  <label className="mb-1 block text-[11px] text-muted-foreground">Polygon Complexity</label>
                  <Select
                    value={draftRules.preferred_poly_complexity}
                    onChange={(e) =>
                      setDraftRules({ ...draftRules, preferred_poly_complexity: e.target.value })
                    }
                  >
                    {ruleOptions.poly_complexities.map((pc) => (
                      <option key={pc} value={pc}>
                        {pc}
                      </option>
                    ))}
                  </Select>
                </div>
                <div>
                  <label className="mb-1 block text-[11px] text-muted-foreground">Preferred File Format</label>
                  <Select
                    value={draftRules.preferred_file_format}
                    onChange={(e) =>
                      setDraftRules({ ...draftRules, preferred_file_format: e.target.value })
                    }
                  >
                    {ruleOptions.file_formats.map((fmt) => (
                      <option key={fmt} value={fmt}>
                        {fmt.toUpperCase()}
                      </option>
                    ))}
                  </Select>
                </div>
              </div>

              <div>
                <p className="mb-1.5 text-[11px] font-medium text-muted-foreground">Preferred Categories</p>
                <div className="flex flex-wrap gap-1.5">
                  {ruleOptions.categories.map((cat) => {
                    const active = draftRules.preferred_categories.includes(cat);
                    return (
                      <button
                        key={cat}
                        type="button"
                        onClick={() => toggleArrayValue("preferred_categories", cat)}
                        className={`rounded border px-2 py-1 text-[11px] ${
                          active
                            ? "border-emerald-500/60 bg-emerald-500/15 text-emerald-200"
                            : "border-border text-muted-foreground hover:text-foreground"
                        }`}
                      >
                        {cat}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div>
                <p className="mb-1.5 text-[11px] font-medium text-muted-foreground">Excluded Categories</p>
                <div className="flex flex-wrap gap-1.5">
                  {ruleOptions.categories.map((cat) => {
                    const active = draftRules.excluded_categories.includes(cat);
                    return (
                      <button
                        key={cat}
                        type="button"
                        onClick={() => toggleArrayValue("excluded_categories", cat)}
                        className={`rounded border px-2 py-1 text-[11px] ${
                          active
                            ? "border-rose-500/60 bg-rose-500/15 text-rose-200"
                            : "border-border text-muted-foreground hover:text-foreground"
                        }`}
                      >
                        {cat}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <p className="mb-1.5 text-[11px] font-medium text-muted-foreground">Preferred Licenses</p>
                  <div className="flex flex-wrap gap-1.5">
                    {ruleOptions.licenses.map((lic) => {
                      const active = draftRules.preferred_licenses.includes(lic);
                      return (
                        <button
                          key={lic}
                          type="button"
                          onClick={() => toggleArrayValue("preferred_licenses", lic)}
                          className={`rounded border px-2 py-1 text-[11px] uppercase ${
                            active
                              ? "border-primary bg-primary/15 text-primary"
                              : "border-border text-muted-foreground hover:text-foreground"
                          }`}
                        >
                          {lic}
                        </button>
                      );
                    })}
                  </div>
                </div>
                <div>
                  <p className="mb-1.5 text-[11px] font-medium text-muted-foreground">Preferred Modeling Styles</p>
                  <div className="flex flex-wrap gap-1.5">
                    {ruleOptions.modeling_styles.map((st) => {
                      const active = draftRules.preferred_modeling_styles.includes(st);
                      return (
                        <button
                          key={st}
                          type="button"
                          onClick={() => toggleArrayValue("preferred_modeling_styles", st)}
                          className={`rounded border px-2 py-1 text-[11px] ${
                            active
                              ? "border-sky-500/60 bg-sky-500/15 text-sky-200"
                              : "border-border text-muted-foreground hover:text-foreground"
                          }`}
                        >
                          {st}
                        </button>
                      );
                    })}
                  </div>
                </div>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-background/60 p-3">
                <div className="flex items-center gap-3">
                  <Switch
                    id="diversity-switch"
                    checked={draftRules.diversity_enabled}
                    onCheckedChange={(checked) =>
                      setDraftRules({ ...draftRules, diversity_enabled: checked })
                    }
                  />
                  <div>
                    <label htmlFor="diversity-switch" className="font-medium">
                      Asset Diversity Requirement
                    </label>
                    <p className="text-[11px] text-muted-foreground">
                      Prevent selecting too many items from the same category in a single day.
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-[11px] text-muted-foreground">Max / Category:</span>
                  <Input
                    className="w-16"
                    type="number"
                    min={1}
                    max={10}
                    value={draftRules.max_per_category_per_cycle}
                    onChange={(e) =>
                      setDraftRules({
                        ...draftRules,
                        max_per_category_per_cycle: Number(e.target.value) || 2,
                      })
                    }
                  />
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2 pt-1">
                <Button size="sm" variant="outline" disabled={actionBusy} onClick={() => void handleSaveRules("next_run")}>
                  <Save className="h-3.5 w-3.5" />
                  Save for Next Collection Only
                </Button>
                <Button size="sm" disabled={actionBusy} onClick={() => void handleSaveRules("permanent")}>
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  Save as Permanent Defaults
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={actionBusy}
                  onClick={() =>
                    openConfirm({
                      title: "Restore Default Rules?",
                      description: "Resets permanent rules, clears any next-run override, and restores factory defaults.",
                      confirmLabel: "Restore Defaults",
                      action: async () => {
                        const res = await studioApi.restoreDefaultRules();
                        setDraftRules(res.active_rules);
                        setNotice({ type: "ok", text: "Restored default collection rules." });
                      },
                    })
                  }
                >
                  <RotateCcw className="h-3.5 w-3.5" />
                  Restore Default Rules
                </Button>
              </div>
            </CardContent>
          </Card>

          {/* Candidate Selection Preview */}
          <Card className="xl:col-span-5">
            <CardHeader className="flex-row items-center justify-between space-y-0">
              <div>
                <CardTitle>Preview Selected Candidates</CardTitle>
                <CardDescription>
                  Dry-run BlendSwap discovery using draft rules (consumes 0 free quota / 0 credits)
                </CardDescription>
              </div>
              <Button size="sm" onClick={() => void handlePreview()} disabled={previewBusy}>
                {previewBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Eye className="h-3.5 w-3.5" />}
                Preview
              </Button>
            </CardHeader>
            <CardContent className="space-y-3">
              {!previewResult ? (
                <p className="py-10 text-center text-xs text-muted-foreground">
                  Click "Preview" to inspect which BlendSwap models will be chosen next and toggle off any specific model IDs you want excluded.
                </p>
              ) : (
                <>
                  <div className="grid grid-cols-3 gap-2 text-center text-xs">
                    <div className="rounded bg-background/70 p-2">
                      <p className="text-[10px] text-muted-foreground">Evaluated</p>
                      <p className="font-mono font-bold">{previewResult.total_candidates}</p>
                    </div>
                    <div className="rounded bg-background/70 p-2">
                      <p className="text-[10px] text-muted-foreground">Selected</p>
                      <p className="font-mono font-bold text-emerald-300">{previewResult.selected_count}</p>
                    </div>
                    <div className="rounded bg-background/70 p-2">
                      <p className="text-[10px] text-muted-foreground">Excluded IDs</p>
                      <p className="font-mono font-bold text-amber-300">
                        {draftRules.excluded_candidate_ids?.length || 0}
                      </p>
                    </div>
                  </div>
                  {(draftRules.excluded_candidate_ids || []).length > 0 ? (
                    <div className="flex flex-wrap items-center gap-1 rounded border border-amber-500/30 bg-amber-500/10 p-2 text-[10px]">
                      <span className="text-amber-200 mr-1">Excluded:</span>
                      {(draftRules.excluded_candidate_ids || []).map((id) => (
                        <button
                          key={id}
                          type="button"
                          onClick={() => void toggleExcludedCandidate(id)}
                          className="rounded bg-background/80 px-1.5 py-0.5 font-mono text-amber-200 hover:bg-background"
                          title="Click to restore candidate"
                        >
                          #{id} ×
                        </button>
                      ))}
                    </div>
                  ) : null}
                  <div className="max-h-96 space-y-2 overflow-y-auto">
                    {previewResult.selected.map((cand) => {
                      const excluded = draftRules.excluded_candidate_ids?.includes(cand.blendswap_id);
                      return (
                        <div
                          key={cand.blendswap_id}
                          className="flex items-start justify-between gap-2 rounded-md border border-border/70 bg-background/60 p-2.5 text-xs"
                        >
                          <div className="min-w-0 flex-1">
                            <p className="truncate font-semibold">{cand.title}</p>
                            <p className="mt-0.5 text-[10px] text-muted-foreground">
                              #{cand.blendswap_id} • {cand.primary_category} • {cand.license_key.toUpperCase()} •{" "}
                              {formatNumber(cand.downloads)} DLs
                            </p>
                            <p className="mt-0.5 text-[10px] text-sky-300">{cand.reason}</p>
                          </div>
                          <Button
                            size="sm"
                            variant={excluded ? "secondary" : "outline"}
                            onClick={() => void toggleExcludedCandidate(cand.blendswap_id)}
                          >
                            {excluded ? "Restore" : "Exclude"}
                          </Button>
                        </div>
                      );
                    })}
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </div>
      ) : null}

      {tab === "history" ? (
        <Card>
          <CardHeader>
            <CardTitle>Collection Cycle History</CardTitle>
            <CardDescription>Click any past cycle to inspect detailed stage logs and item queue results</CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Cycle ID</TableHead>
                  <TableHead>Date</TableHead>
                  <TableHead>Trigger</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Downloaded / Fail</TableHead>
                  <TableHead>Credits Spent</TableHead>
                  <TableHead>Started</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {historyCycles.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={7} className="py-8 text-center text-muted-foreground">
                      No historical collection cycles recorded yet.
                    </TableCell>
                  </TableRow>
                ) : (
                  historyCycles.map((cyc) => (
                    <TableRow
                      key={cyc.cycle_id}
                      className="cursor-pointer"
                      onClick={() => void studioApi.getCollectionCycle(cyc.cycle_id).then(setSelectedHistoryCycle)}
                    >
                      <TableCell className="font-mono text-[11px]">{cyc.cycle_id}</TableCell>
                      <TableCell>{cyc.cycle_date}</TableCell>
                      <TableCell className="uppercase">{cyc.trigger_type}</TableCell>
                      <TableCell>
                        <StatusBadge status={cyc.status} />
                      </TableCell>
                      <TableCell className="font-mono">
                        {cyc.downloaded_count} / {cyc.target_count} (fail {cyc.failed_count})
                      </TableCell>
                      <TableCell className="font-mono text-emerald-300">{cyc.credits_spent}</TableCell>
                      <TableCell className="font-mono text-[11px] text-muted-foreground">
                        {formatDateTime(cyc.started_at)}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      ) : null}

      {/* Cycle Detail Modal */}
      <Dialog
        open={Boolean(selectedHistoryCycle)}
        onOpenChange={(open) => {
          if (!open) setSelectedHistoryCycle(null);
        }}
        title={`Collection Report: ${selectedHistoryCycle?.cycle.cycle_id || ""}`}
        description={
          selectedHistoryCycle
            ? `${selectedHistoryCycle.cycle.cycle_date} • Status ${selectedHistoryCycle.cycle.status}`
            : ""
        }
      >
        {selectedHistoryCycle ? (
          <div className="space-y-4 text-xs">
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
              {(selectedHistoryCycle.cycle.stages || []).map((st, idx) => (
                <div key={st.id || st.key || String(idx)} className="rounded border border-border p-2">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-[10px]">#{idx + 1}</span>
                    <StageIcon status={st.status} />
                  </div>
                  <p className="mt-1 font-medium">{st.label}</p>
                  {st.detail ? <p className="mt-0.5 text-[10px] text-muted-foreground">{st.detail}</p> : null}
                </div>
              ))}
            </div>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>BlendSwap ID</TableHead>
                  <TableHead>Title</TableHead>
                  <TableHead>Category</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Notes</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {selectedHistoryCycle.queue.map((q) => (
                  <TableRow key={q.item_id}>
                    <TableCell className="font-mono">#{q.blendswap_id}</TableCell>
                    <TableCell>{q.title}</TableCell>
                    <TableCell>{q.primary_category}</TableCell>
                    <TableCell>
                      <StatusBadge status={q.status} />
                    </TableCell>
                    <TableCell className="text-muted-foreground">{q.error_message || "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : null}
      </Dialog>

      {confirmState ? (
        <ConfirmDialog
          open={confirmState.open}
          onOpenChange={(open) => {
            if (!open) setConfirmState(null);
          }}
          title={confirmState.title}
          description={confirmState.description}
          confirmLabel={confirmState.confirmLabel}
          confirmVariant={confirmState.variant}
          busy={actionBusy}
          onConfirm={executeConfirm}
        />
      ) : null}
    </div>
  );
}
