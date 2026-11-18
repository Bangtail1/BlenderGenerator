import * as React from "react";
import {
  Check,
  CheckSquare,
  CloudUpload,
  Database,
  ExternalLink,
  FolderOpen,
  HardDrive,
  Loader2,
  RefreshCw,
  Search,
  ShieldAlert,
  Square,
} from "lucide-react";
import { ConfirmDialog, StatusBadge } from "@/components/StatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Input, Select } from "@/components/ui/input";
import { studioApi } from "@/lib/api";
import { formatBytes, formatNumber } from "@/lib/utils";
import type { ReferenceAsset } from "@/types/studio";

function isRefDriveSynced(status?: string | null): boolean {
  const s = (status || "").toUpperCase();
  return s === "SYNCED" || s === "VERIFIED" || s === "UPLOADED";
}

export function ReferenceLibraryPage() {
  const [items, setItems] = React.useState<ReferenceAsset[]>([]);
  const [total, setTotal] = React.useState(0);
  const [loading, setLoading] = React.useState(true);
  const [facets, setFacets] = React.useState<{
    categories: string[];
    licenses: string[];
    grades: string[];
    modeling_styles: string[];
    poly_complexities: string[];
    dates_collected: string[];
  }>({
    categories: [],
    licenses: [],
    grades: ["A", "B", "C", "D", "F"],
    modeling_styles: [],
    poly_complexities: ["low_poly", "mid_poly", "high_poly"],
    dates_collected: [],
  });

  const [search, setSearch] = React.useState("");
  const [category, setCategory] = React.useState("all");
  const [licenseKey, setLicenseKey] = React.useState("all");
  const [gradeLetter, setGradeLetter] = React.useState("all");
  const [polyComplexity, setPolyComplexity] = React.useState("all");
  const [modelingStyle, setModelingStyle] = React.useState("all");
  const [dateCollected, setDateCollected] = React.useState("all");
  const [driveFilter, setDriveFilter] = React.useState<"all" | "unsynced" | "synced">("all");

  const [selectedIds, setSelectedIds] = React.useState<string[]>([]);
  const [cardBusyId, setCardBusyId] = React.useState<string | null>(null);
  const [batchProgress, setBatchProgress] = React.useState<{
    current: number;
    total: number;
    label: string;
  } | null>(null);
  const [confirmReinspectAll, setConfirmReinspectAll] = React.useState(false);

  const [selected, setSelected] = React.useState<ReferenceAsset | null>(null);
  const [busyAction, setBusyAction] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);
  const [modalFeedback, setModalFeedback] = React.useState<{ type: "ok" | "err"; text: string } | null>(null);

  const loadLibrary = React.useCallback(async () => {
    try {
      const res = await studioApi.listReferenceLibrary({
        search: search || undefined,
        category,
        license_key: licenseKey,
        grade_letter: gradeLetter,
        poly_complexity: polyComplexity,
        modeling_style: modelingStyle,
        date_collected: dateCollected,
      });
      setItems(res.items);
      setTotal(res.total);
      setFacets(res.facets);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Failed to load Reference Library.");
    } finally {
      setLoading(false);
    }
  }, [search, category, licenseKey, gradeLetter, polyComplexity, modelingStyle, dateCollected]);

  React.useEffect(() => {
    void loadLibrary();
  }, [loadLibrary]);

  const handleOpenDetail = async (item: ReferenceAsset) => {
    setModalFeedback(null);
    setSelected(item);
    try {
      const full = await studioApi.getReferenceAsset(item.blendswap_id);
      setSelected({
        ...item,
        ...full,
        preview_urls: (full.preview_paths || []).map(
          (p) => `/api/reference/assets/${full.blendswap_id}/preview/${encodeURIComponent(p.split(/[/\\]/).pop() || "")}`,
        ),
      });
    } catch {
      // Keep list detail
    }
  };

  const handleOpenInBlender = async (blendswapId: string) => {
    setBusyAction("blender");
    setModalFeedback(null);
    try {
      const res = await studioApi.openReferenceInBlender(blendswapId);
      const blendName = String(res.blend_path || "").split(/[/\\]/).pop() || `#${blendswapId}`;
      const msg = `Launched Blender for ${blendName}.`;
      setNotice(msg);
      setModalFeedback({ type: "ok", text: msg });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Could not launch Blender for this asset.";
      setNotice(msg);
      setModalFeedback({ type: "err", text: msg });
    } finally {
      setBusyAction(null);
    }
  };

  const handleReinspect = async (blendswapId: string) => {
    setBusyAction("reinspect");
    setModalFeedback(null);
    try {
      const updated = await studioApi.reinspectReference(blendswapId);
      setSelected((prev) => (prev ? { ...prev, ...updated } : updated));
      const msg = `Re-inspected and re-graded #${blendswapId}.`;
      setNotice(msg);
      setModalFeedback({ type: "ok", text: msg });
      await loadLibrary();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Re-inspection failed.";
      setNotice(msg);
      setModalFeedback({ type: "err", text: msg });
    } finally {
      setBusyAction(null);
    }
  };

  const handleSyncDrive = async (blendswapId: string) => {
    setBusyAction("drive");
    setModalFeedback(null);
    try {
      await studioApi.syncReferenceToDrive(blendswapId);
      const msg = `Synced reference #${blendswapId} to Google Drive.`;
      setNotice(msg);
      setModalFeedback({ type: "ok", text: msg });
      await loadLibrary();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Drive sync failed.";
      setNotice(msg);
      setModalFeedback({ type: "err", text: msg });
    } finally {
      setBusyAction(null);
    }
  };

  const filteredItems = React.useMemo(() => {
    if (driveFilter === "all") return items;
    return items.filter((r) =>
      driveFilter === "synced" ? isRefDriveSynced(r.drive_sync_status) : !isRefDriveSynced(r.drive_sync_status),
    );
  }, [items, driveFilter]);

  const visibleSelectedItems = React.useMemo(
    () => filteredItems.filter((r) => selectedIds.includes(String(r.blendswap_id))),
    [filteredItems, selectedIds],
  );

  const allSelected =
    filteredItems.length > 0 && visibleSelectedItems.length === filteredItems.length;

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const toggleSelectAll = () => {
    if (allSelected) {
      setSelectedIds([]);
    } else {
      setSelectedIds(filteredItems.map((r) => String(r.blendswap_id)));
    }
  };

  const handleSyncAllPending = async () => {
    setBusyAction("sync-pending");
    try {
      const res = await studioApi.syncPendingReferenceDrive();
      const synced = Number((res as { synced?: number }).synced ?? 0);
      setNotice(`Synced ${synced} pending reference asset(s) to Google Drive.`);
      await loadLibrary();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Pending Drive sync failed.");
    } finally {
      setBusyAction(null);
    }
  };

  const runBatch = async (mode: "sync_drive" | "reinspect") => {
    if (visibleSelectedItems.length === 0) return;
    let okCount = 0;
    let skipped = 0;
    const failed: string[] = [];
    const keepSelected: string[] = [];

    for (let i = 0; i < visibleSelectedItems.length; i += 1) {
      const ref = visibleSelectedItems[i];
      const idStr = String(ref.blendswap_id);
      setBatchProgress({ current: i + 1, total: visibleSelectedItems.length, label: ref.title });
      try {
        if (mode === "sync_drive") {
          if (isRefDriveSynced(ref.drive_sync_status)) {
            skipped += 1;
            continue;
          }
          await studioApi.syncReferenceToDrive(idStr);
          okCount += 1;
        } else {
          await studioApi.reinspectReference(idStr);
          okCount += 1;
        }
      } catch (err) {
        failed.push(`${ref.title}: ${err instanceof Error ? err.message : "Error"}`);
        keepSelected.push(idStr);
      }
    }

    setBatchProgress(null);
    setSelectedIds(keepSelected);
    await loadLibrary();
    if (failed.length > 0) {
      setNotice(`Processed ${okCount}, skipped ${skipped}, failed ${failed.length} (${failed[0]})`);
    } else {
      setNotice(
        `Finished ${mode === "sync_drive" ? "Drive sync" : "re-inspection"}: ${okCount} succeeded${skipped > 0 ? `, ${skipped} already synced` : ""}.`,
      );
    }
  };

  const handleQuickOpenBlender = async (ref: ReferenceAsset) => {
    const idStr = String(ref.blendswap_id);
    setCardBusyId(idStr);
    try {
      const res = await studioApi.openReferenceInBlender(idStr);
      const blendName = String(res.blend_path || "").split(/[/\\]/).pop() || `#${idStr}`;
      setNotice(`Launched Blender for ${blendName}.`);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Could not launch Blender for this reference.");
    } finally {
      setCardBusyId(null);
    }
  };

  const handleQuickSyncDrive = async (ref: ReferenceAsset) => {
    const idStr = String(ref.blendswap_id);
    setCardBusyId(idStr);
    try {
      await studioApi.syncReferenceToDrive(idStr);
      setNotice(`Synced "${ref.title}" (#${idStr}) to Google Drive.`);
      await loadLibrary();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Drive sync failed.");
    } finally {
      setCardBusyId(null);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-semibold tracking-tight">Reference Library (Branch 2)</h1>
            <Badge variant="outline">Attribution Required</Badge>
          </div>
          <p className="text-xs text-muted-foreground">
            Downloaded BlendSwap benchmark assets kept strictly separate from original generated Branch 1 assets ({total} indexed).
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={busyAction !== null || batchProgress !== null}
            onClick={() => void handleSyncAllPending()}
          >
            {busyAction === "sync-pending" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <CloudUpload className="h-3.5 w-3.5" />
            )}
            Sync All Pending Drive
          </Button>
          <Button variant="outline" size="sm" onClick={() => void loadLibrary()}>
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh
          </Button>
        </div>
      </div>

      {notice ? (
        <div className="flex items-center justify-between rounded-md border border-border bg-card px-3 py-2 text-xs">
          <span>{notice}</span>
          <button type="button" onClick={() => setNotice(null)} className="text-muted-foreground hover:text-foreground">
            Close
          </button>
        </div>
      ) : null}

      {/* Facet Filter Bar + Selection Toolbar */}
      <Card>
        <CardContent className="space-y-3 p-3">
          <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-8">
            <div className="relative lg:col-span-2">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
              <Input
                className="pl-8"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search title, author, ID, tags..."
              />
            </div>
            <Select value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="all">All Categories</option>
              {facets.categories.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </Select>
            <Select value={licenseKey} onChange={(e) => setLicenseKey(e.target.value)}>
              <option value="all">All Licenses</option>
              {facets.licenses.map((l) => (
                <option key={l} value={l}>
                  {l.toUpperCase()}
                </option>
              ))}
            </Select>
            <Select value={gradeLetter} onChange={(e) => setGradeLetter(e.target.value)}>
              <option value="all">All Grades</option>
              {(facets.grades.length ? facets.grades : ["A", "B", "C", "D", "F"]).map((g) => (
                <option key={g} value={g}>
                  Grade {g}
                </option>
              ))}
            </Select>
            <Select value={polyComplexity} onChange={(e) => setPolyComplexity(e.target.value)}>
              <option value="all">All Poly Budgets</option>
              <option value="low_poly">Low Poly (&lt;=10k)</option>
              <option value="mid_poly">Mid Poly (10k-80k)</option>
              <option value="high_poly">High Poly (&gt;80k)</option>
            </Select>
            <Select
              value={driveFilter}
              onChange={(e) => setDriveFilter(e.target.value as "all" | "unsynced" | "synced")}
            >
              <option value="all">All Drive States</option>
              <option value="unsynced">Drive: Needs Sync</option>
              <option value="synced">Drive: Synced</option>
            </Select>
            <div className="grid grid-cols-2 gap-1.5 lg:col-span-1">
              <Select value={modelingStyle} onChange={(e) => setModelingStyle(e.target.value)} title="Modeling Style">
                <option value="all">Style</option>
                {facets.modeling_styles.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </Select>
              <Select value={dateCollected} onChange={(e) => setDateCollected(e.target.value)} title="Date Collected">
                <option value="all">Date</option>
                {facets.dates_collected.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </Select>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/60 pt-2.5 text-xs">
            <div className="flex flex-wrap items-center gap-1.5">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={filteredItems.length === 0 || batchProgress !== null}
                onClick={toggleSelectAll}
              >
                {allSelected ? (
                  <CheckSquare className="h-3.5 w-3.5 text-primary" />
                ) : (
                  <Square className="h-3.5 w-3.5" />
                )}
                {allSelected ? `Deselect All (${filteredItems.length})` : `Select All (${filteredItems.length})`}
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={filteredItems.length === 0 || batchProgress !== null}
                onClick={() =>
                  setSelectedIds(
                    filteredItems
                      .filter((r) => !isRefDriveSynced(r.drive_sync_status))
                      .map((r) => String(r.blendswap_id)),
                  )
                }
              >
                Select Unsynced
              </Button>
              {visibleSelectedItems.length > 0 ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={batchProgress !== null}
                  onClick={() => setSelectedIds([])}
                >
                  Clear ({visibleSelectedItems.length})
                </Button>
              ) : null}
            </div>

            {visibleSelectedItems.length > 0 ? (
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge variant="default" className="font-mono">
                  {visibleSelectedItems.length} selected
                </Badge>
                <Button
                  size="sm"
                  disabled={batchProgress !== null}
                  onClick={() => void runBatch("sync_drive")}
                >
                  <CloudUpload className="h-3.5 w-3.5" />
                  Sync Selected to Drive
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={batchProgress !== null}
                  onClick={() => setConfirmReinspectAll(true)}
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                  Re-Inspect Selected
                </Button>
              </div>
            ) : null}
          </div>

          {batchProgress ? (
            <div className="flex items-center gap-2 rounded border border-sky-500/40 bg-sky-500/10 px-3 py-1.5 text-xs text-sky-200">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              <span>
                Processing {batchProgress.current}/{batchProgress.total}: {batchProgress.label}...
              </span>
            </div>
          ) : null}
        </CardContent>
      </Card>

      {loading ? (
        <div className="py-12 text-center text-xs text-muted-foreground">Loading Reference Library...</div>
      ) : filteredItems.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-xs text-muted-foreground">
            <Database className="mx-auto mb-2 h-8 w-8 opacity-40" />
            No BlendSwap reference assets collected yet or none match the selected filters. Visit Collection Control to preview or run a collection cycle.
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {filteredItems.map((ref) => {
            const idStr = String(ref.blendswap_id);
            const isSelected = selectedIds.includes(idStr);
            const isCardBusy = cardBusyId === idStr;
            const synced = isRefDriveSynced(ref.drive_sync_status);

            return (
              <Card
                key={ref.blendswap_id}
                className={`flex cursor-pointer flex-col overflow-hidden transition-colors hover:border-primary/50 ${
                  isSelected ? "border-primary ring-1 ring-primary" : ""
                }`}
                onClick={() => void handleOpenDetail(ref)}
              >
                <div className="relative flex h-40 items-center justify-center border-b border-border bg-[#0c0f15]">
                  {ref.preview_urls?.[0] ? (
                    <img src={ref.preview_urls[0]} alt={ref.title} className="h-full w-full object-cover" />
                  ) : (
                    <Database className="h-8 w-8 text-muted-foreground/40" />
                  )}
                  <div className="absolute left-2 top-2 flex items-center gap-1.5">
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        toggleSelect(idStr);
                      }}
                      className={`flex h-5 w-5 items-center justify-center rounded border transition-colors ${
                        isSelected
                          ? "border-primary bg-primary text-primary-foreground"
                          : "border-border/80 bg-card/85 text-muted-foreground hover:border-primary hover:text-foreground"
                      }`}
                      aria-label={`Select ${ref.title}`}
                    >
                      {isSelected ? <Check className="h-3 w-3" /> : null}
                    </button>
                    <StatusBadge
                      status={ref.grade_letter || ref.status}
                      label={ref.grade_letter ? `Grade ${ref.grade_letter}` : ref.status}
                    />
                  </div>
                  <div className="absolute right-2 top-2">
                    <Badge variant="outline" className="bg-card/85 font-mono">
                      {ref.primary_format?.toUpperCase()}
                    </Badge>
                  </div>
                </div>
                <CardContent className="flex flex-1 flex-col justify-between gap-2 p-3">
                  <div>
                    <p className="truncate text-sm font-semibold">{ref.title}</p>
                    <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
                      by @{ref.author?.username || "unknown"} • {ref.primary_category}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
                    <Badge variant="secondary">{ref.license_assessment?.license_key?.toUpperCase() || "LICENSE"}</Badge>
                    <Badge variant="outline">{formatNumber(ref.counts?.downloads)} DLs</Badge>
                    {ref.blender_versions?.[0] ? (
                      <Badge variant="outline">Blender {ref.blender_versions[0]}</Badge>
                    ) : null}
                    <Badge variant={synced ? "success" : "outline"}>
                      <HardDrive className="h-2.5 w-2.5" />
                      {ref.drive_sync_status || "PENDING"}
                    </Badge>
                  </div>

                  {/* Direct 1-Click Card Actions */}
                  <div
                    className="flex items-center gap-1.5 border-t border-border/60 pt-2"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-7 flex-1 text-[11px]"
                      disabled={isCardBusy || batchProgress !== null}
                      onClick={() => void handleQuickOpenBlender(ref)}
                    >
                      {isCardBusy ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <FolderOpen className="h-3 w-3" />
                      )}
                      Blender
                    </Button>
                    {!synced ? (
                      <Button
                        size="sm"
                        className="h-7 flex-1 text-[11px]"
                        disabled={isCardBusy || batchProgress !== null}
                        onClick={() => void handleQuickSyncDrive(ref)}
                      >
                        <CloudUpload className="h-3 w-3" />
                        Sync Drive
                      </Button>
                    ) : null}
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-7 px-2 text-[11px]"
                      onClick={() => void handleOpenDetail(ref)}
                    >
                      Details
                    </Button>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      {/* Detail Modal */}
      <Dialog
        open={Boolean(selected)}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
        maxWidthClass="max-w-5xl"
        title={
          selected ? (
            <span className="inline-flex flex-wrap items-center gap-2">
              <span>{selected.title}</span>
              <Badge variant="outline" className="font-mono">
                #{selected.blendswap_id}
              </Badge>
              {selected.grade_letter ? <StatusBadge status={selected.grade_letter} label={`Grade ${selected.grade_letter}`} /> : null}
            </span>
          ) : (
            ""
          )
        }
        description={selected ? `${selected.primary_category} • by @${selected.author?.username} • Collected ${selected.date_collected || "—"}` : ""}
        footer={
          selected ? (
            <div className="flex w-full flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap gap-1.5">
                {selected.url ? (
                  <a
                    href={selected.url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex h-7 items-center gap-1 rounded-md border border-border px-2.5 text-[11px] hover:bg-accent"
                  >
                    Original BlendSwap <ExternalLink className="h-3 w-3" />
                  </a>
                ) : null}
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busyAction !== null}
                  onClick={() => void handleOpenInBlender(selected.blendswap_id)}
                >
                  <FolderOpen className="h-3.5 w-3.5" />
                  Open in Blender
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busyAction !== null}
                  onClick={() => void handleReinspect(selected.blendswap_id)}
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                  Re-Inspect
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busyAction !== null}
                  onClick={() => void handleSyncDrive(selected.blendswap_id)}
                >
                  <CloudUpload className="h-3.5 w-3.5" />
                  Sync Reference Drive
                </Button>
              </div>
              <Button variant="secondary" size="sm" onClick={() => setSelected(null)}>
                Close
              </Button>
            </div>
          ) : null
        }
      >
        {selected ? (
          <div className="space-y-4 text-xs">
            {modalFeedback ? (
              <div
                className={`rounded-md border px-3 py-2 text-xs ${
                  modalFeedback.type === "ok"
                    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
                    : "border-rose-500/40 bg-rose-500/10 text-rose-200"
                }`}
              >
                {modalFeedback.text}
              </div>
            ) : null}
            {/* License & Attribution Callout */}
            <div className="flex items-start gap-2.5 rounded-md border border-amber-500/35 bg-amber-500/10 p-3">
              <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
              <div className="space-y-1">
                <p className="font-semibold text-amber-200">
                  Declared License: {selected.license_assessment?.license_name || selected.license_assessment?.license_key?.toUpperCase()} —{" "}
                  Author: @{selected.author?.username}
                </p>
                <p className="text-[11px] text-muted-foreground">{selected.license_assessment?.verification_disclaimer}</p>
              </div>
            </div>

            {selected.preview_urls && selected.preview_urls.length > 0 ? (
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {selected.preview_urls.map((u, idx) => (
                  <img key={idx} src={u} alt={`${selected.title} preview ${idx + 1}`} className="h-32 w-full rounded border border-border object-cover" />
                ))}
              </div>
            ) : null}

            <div className="grid gap-4 md:grid-cols-2">
              <div className="rounded-md border border-border bg-background/60 p-3 space-y-2">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Geometry & UV Technical Inspection
                </p>
                {selected.inspection ? (
                  <div className="grid grid-cols-2 gap-y-1.5 text-[11px]">
                    <span className="text-muted-foreground">Blender Header</span>
                    <span className="font-mono">{selected.inspection.blend_header_version || selected.inspection.source_format}</span>
                    <span className="text-muted-foreground">Objects / Meshes</span>
                    <span className="font-mono">{selected.inspection.object_count} / {selected.inspection.mesh_object_count}</span>
                    <span className="text-muted-foreground">Triangles / Faces</span>
                    <span className="font-mono">{formatNumber(selected.inspection.triangle_count)} / {formatNumber(selected.inspection.face_count)}</span>
                    <span className="text-muted-foreground">Quad / N-Gon Ratio</span>
                    <span className="font-mono">
                      {(selected.inspection.quad_ratio * 100).toFixed(1)}% / {(selected.inspection.ngon_ratio * 100).toFixed(1)}%
                    </span>
                    <span className="text-muted-foreground">Manifold / Sub-D Ready</span>
                    <span className="font-mono">
                      {selected.inspection.is_manifold ? "Manifold" : "Open/Non-Manifold"} •{" "}
                      {selected.inspection.subdivision_ready ? "Yes" : "No"}
                    </span>
                    <span className="text-muted-foreground">UV Coverage / Overlap</span>
                    <span className="font-mono">
                      {selected.inspection.has_uv_map
                        ? `${(selected.inspection.uv_coverage_ratio * 100).toFixed(0)}% (${selected.inspection.uv_overlapping_islands_detected ? "Overlap!" : "Clean"})`
                        : "None"}
                    </span>
                    <span className="text-muted-foreground">Materials / Missing Tex</span>
                    <span className="font-mono">
                      {selected.inspection.material_count} ({selected.inspection.missing_image_textures} missing)
                    </span>
                    <span className="text-muted-foreground">Rigging / File Size</span>
                    <span className="font-mono">
                      {selected.inspection.has_rig ? `${selected.inspection.bone_count} bones` : "Static"} • {formatBytes(selected.file_size_bytes)}
                    </span>
                  </div>
                ) : (
                  <p className="text-muted-foreground">No technical inspection stored yet.</p>
                )}
              </div>

              <div className="rounded-md border border-border bg-background/60 p-3 space-y-2">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Benchmark Grade & Strengths
                </p>
                {selected.grade ? (
                  <>
                    <div className="grid grid-cols-3 gap-2">
                      <div className="rounded bg-card p-2">
                        <span className="text-[10px] text-muted-foreground">Total Score</span>
                        <p className="font-mono text-sm font-bold text-primary">{selected.grade.total_score.toFixed(1)}</p>
                      </div>
                      <div className="rounded bg-card p-2">
                        <span className="text-[10px] text-muted-foreground">Technical</span>
                        <p className="font-mono text-sm font-bold">{selected.grade.technical_quality_score.toFixed(1)}</p>
                      </div>
                      <div className="rounded bg-card p-2">
                        <span className="text-[10px] text-muted-foreground">Usefulness</span>
                        <p className="font-mono text-sm font-bold text-emerald-300">
                          {selected.grade.reference_usefulness_score.toFixed(1)}
                        </p>
                      </div>
                    </div>
                    {selected.grade.strengths.length > 0 ? (
                      <div>
                        <p className="font-medium text-emerald-300">Strengths</p>
                        <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[11px]">
                          {selected.grade.strengths.map((s, i) => (
                            <li key={i}>{s}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                    {selected.grade.weaknesses.length > 0 ? (
                      <div>
                        <p className="font-medium text-amber-300">Weaknesses</p>
                        <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[11px]">
                          {selected.grade.weaknesses.map((w, i) => (
                            <li key={i}>{w}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                  </>
                ) : (
                  <p className="text-muted-foreground">Not graded yet.</p>
                )}
              </div>
            </div>
          </div>
        ) : null}
      </Dialog>

      <ConfirmDialog
        open={confirmReinspectAll}
        onOpenChange={setConfirmReinspectAll}
        title={`Re-Inspect ${visibleSelectedItems.length} Reference Asset(s)?`}
        description="Runs headless Blender inspection and quality grading sequentially for each selected BlendSwap asset."
        confirmLabel="Re-Inspect Selected"
        busy={batchProgress !== null}
        onConfirm={async () => {
          setConfirmReinspectAll(false);
          await runBatch("reinspect");
        }}
      />
    </div>
  );
}
