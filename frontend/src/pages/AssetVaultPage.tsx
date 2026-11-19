import * as React from "react";
import {
  Award,
  Box,
  Check,
  CheckCircle2,
  CheckSquare,
  ChevronRight,
  CloudUpload,
  ExternalLink,
  FolderOpen,
  HardDrive,
  Loader2,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  Square,
  Trash2,
  XCircle,
} from "lucide-react";
import { GlbViewer } from "@/components/GlbViewer";
import { ConfirmDialog, StatusBadge } from "@/components/StatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs } from "@/components/ui/controls";
import { Dialog } from "@/components/ui/dialog";
import { Input, Select, Textarea } from "@/components/ui/input";
import { studioApi } from "@/lib/api";
import { formatBytes, formatDateTime, formatNumber } from "@/lib/utils";
import type { BenchmarkReport, VaultAsset } from "@/types/studio";

function isDriveSynced(status?: string | null): boolean {
  const s = (status || "").toUpperCase();
  return s === "VERIFIED" || s === "SYNCED" || s === "UPLOADED";
}

function extractPreviewImages(asset: VaultAsset): Array<{ name: string; url: string }> {
  const latest = asset.latest;
  if (!latest || !latest.visual_qa) return [];
  const v = latest.version;
  const out: Array<{ name: string; url: string }> = [];
  const seen = new Set<string>();
  const addPath = (rawPath: string | null | undefined, label?: string) => {
    if (!rawPath) return;
    const filename = rawPath.split(/[/\\]/).pop();
    if (!filename || seen.has(filename)) return;
    seen.add(filename);
    out.push({
      name: label || filename,
      url: `/api/assets/${asset.asset_id}/versions/${v}/preview/${encodeURIComponent(filename)}`,
    });
  };
  addPath(latest.visual_qa.contact_sheet_path, "Contact Sheet");
  const rp = latest.visual_qa.render_paths;
  if (Array.isArray(rp)) {
    rp.forEach((p) => addPath(p));
  } else if (rp && typeof rp === "object") {
    Object.entries(rp).forEach(([k, p]) => addPath(p, k));
  }
  addPath(latest.visual_qa.turntable_path, "Turntable");
  return out;
}

export function AssetVaultPage({
  initialAssetId,
  onClearInitialAsset,
}: {
  initialAssetId?: string | null;
  onClearInitialAsset?: () => void;
}) {
  const [assets, setAssets] = React.useState<VaultAsset[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [search, setSearch] = React.useState("");
  const [categoryFilter, setCategoryFilter] = React.useState("ALL");
  const [statusFilter, setStatusFilter] = React.useState("ALL");
  const [driveFilter, setDriveFilter] = React.useState<"ALL" | "UNSYNCED" | "SYNCED" | "FAILED">("ALL");

  const [selectedIds, setSelectedIds] = React.useState<string[]>([]);
  const [cardBusyId, setCardBusyId] = React.useState<string | null>(null);
  const [batchProgress, setBatchProgress] = React.useState<{
    current: number;
    total: number;
    label: string;
  } | null>(null);
  const [confirmBatch, setConfirmBatch] = React.useState<{
    mode: "reject" | "retry_qa" | "delete";
    count: number;
  } | null>(null);
  const [confirmDeleteAsset, setConfirmDeleteAsset] = React.useState<VaultAsset | null>(null);

  const [selectedAsset, setSelectedAsset] = React.useState<VaultAsset | null>(null);
  const [detailTab, setDetailTab] = React.useState<"viewer" | "qa" | "approval" | "benchmark">("viewer");
  const [reviewer, setReviewer] = React.useState("Local Reviewer");
  const [reviewNotes, setReviewNotes] = React.useState("");
  const [busyAction, setBusyAction] = React.useState<string | null>(null);
  const [banner, setBanner] = React.useState<{ type: "ok" | "err"; text: string } | null>(null);
  const [benchmarks, setBenchmarks] = React.useState<BenchmarkReport[]>([]);

  // Import modal
  const [importOpen, setImportOpen] = React.useState(false);
  const [importFile, setImportFile] = React.useState<File | null>(null);
  const [importName, setImportName] = React.useState("");
  const [importCategory, setImportCategory] = React.useState("Furniture");
  const [importDesc, setImportDesc] = React.useState("");
  const [importAuthor, setImportAuthor] = React.useState("Local Studio");
  const [importLicense, setImportLicense] = React.useState("Original / Custom");

  const loadAssets = React.useCallback(async () => {
    try {
      const res = await studioApi.listVaultAssets({
        search: search || undefined,
        category: categoryFilter,
        status: statusFilter,
      });
      setAssets(res.assets);
    } catch (err) {
      setBanner({ type: "err", text: err instanceof Error ? err.message : "Failed to load assets." });
    } finally {
      setLoading(false);
    }
  }, [search, categoryFilter, statusFilter]);

  React.useEffect(() => {
    void loadAssets();
  }, [loadAssets]);

  const openAssetDetail = React.useCallback(async (assetId: string) => {
    try {
      const full = await studioApi.getVaultAsset(assetId);
      setSelectedAsset(full);
      setDetailTab("viewer");
      setReviewNotes(full.latest?.human_review?.notes || "");
    } catch (err) {
      setBanner({ type: "err", text: err instanceof Error ? err.message : "Failed to load asset detail." });
    }
  }, []);

  React.useEffect(() => {
    if (initialAssetId) {
      void openAssetDetail(initialAssetId);
      onClearInitialAsset?.();
    }
  }, [initialAssetId, openAssetDetail, onClearInitialAsset]);

  const handleReview = async (decision: "APPROVED" | "REJECTED") => {
    if (!selectedAsset?.latest) return;
    setBusyAction(`review-${decision}`);
    try {
      const updated = await studioApi.reviewAssetVersion(selectedAsset.asset_id, selectedAsset.latest.version, {
        decision,
        reviewer: reviewer.trim() || "Local Reviewer",
        notes: reviewNotes.trim(),
      });
      setSelectedAsset(updated);
      setBanner({ type: "ok", text: `Asset ${updated.name} marked ${decision}.` });
      await loadAssets();
    } catch (err) {
      setBanner({ type: "err", text: err instanceof Error ? err.message : "Review failed." });
    } finally {
      setBusyAction(null);
    }
  };

  const handleDriveUpload = async () => {
    if (!selectedAsset?.latest) return;
    setBusyAction("drive-upload");
    try {
      const res = await studioApi.uploadAssetToDrive(selectedAsset.asset_id, selectedAsset.latest.version);
      setSelectedAsset(res.asset);
      setBanner({ type: "ok", text: `Uploaded "${res.asset.name}" files cleanly to Google Drive vault.` });
      await loadAssets();
    } catch (err) {
      setBanner({ type: "err", text: err instanceof Error ? err.message : "Drive upload failed." });
    } finally {
      setBusyAction(null);
    }
  };

  const handleApproveAndSync = async (advanceNext = false) => {
    if (!selectedAsset?.latest) return;
    const aid = selectedAsset.asset_id;
    const ver = selectedAsset.latest.version;
    setBusyAction("approve-sync");
    try {
      const needsApproval = selectedAsset.latest.status !== "APPROVED" && selectedAsset.latest.status !== "UPLOADED";
      if (needsApproval) {
        await studioApi.reviewAssetVersion(aid, ver, {
          decision: "APPROVED",
          reviewer: reviewer.trim() || "Local Reviewer",
          notes: reviewNotes.trim(),
        });
      }
      const res = await studioApi.uploadAssetToDrive(aid, ver);
      setSelectedAsset(res.asset);
      setBanner({ type: "ok", text: `Approved and synced "${res.asset.name}" to Google Drive.` });
      await loadAssets();
      if (advanceNext) {
        openAdjacentAsset(aid);
      }
    } catch (err) {
      setBanner({ type: "err", text: err instanceof Error ? err.message : "Approve & Sync failed." });
      await loadAssets();
    } finally {
      setBusyAction(null);
    }
  };

  const handleQuickApprove = async (asset: VaultAsset, decision: "APPROVED" | "REJECTED") => {
    if (!asset.latest) return;
    setCardBusyId(asset.asset_id);
    try {
      const updated = await studioApi.reviewAssetVersion(asset.asset_id, asset.latest.version, {
        decision,
        reviewer: reviewer.trim() || "Local Reviewer",
        notes: `${decision} via quick vault action`,
      });
      setBanner({ type: "ok", text: `"${updated.name}" marked ${decision}.` });
      await loadAssets();
    } catch (err) {
      setBanner({ type: "err", text: err instanceof Error ? err.message : `Failed to ${decision.toLowerCase()}.` });
    } finally {
      setCardBusyId(null);
    }
  };

  const handleQuickDriveUpload = async (asset: VaultAsset) => {
    if (!asset.latest) return;
    setCardBusyId(asset.asset_id);
    try {
      const res = await studioApi.uploadAssetToDrive(asset.asset_id, asset.latest.version);
      setBanner({ type: "ok", text: `Synced "${res.asset.name}" to Google Drive.` });
      await loadAssets();
    } catch (err) {
      setBanner({ type: "err", text: err instanceof Error ? err.message : "Drive sync failed." });
    } finally {
      setCardBusyId(null);
    }
  };

  const handleQuickRetryQa = async (asset: VaultAsset) => {
    if (!asset.latest) return;
    setCardBusyId(asset.asset_id);
    try {
      const updated = await studioApi.retryAssetVersion(asset.asset_id, asset.latest.version);
      setBanner({ type: "ok", text: `Completed QA re-evaluation for "${updated.name}".` });
      await loadAssets();
    } catch (err) {
      setBanner({ type: "err", text: err instanceof Error ? err.message : "Re-QA failed." });
    } finally {
      setCardBusyId(null);
    }
  };

  const handleVerifyDrive = async () => {
    if (!selectedAsset?.latest) return;
    setBusyAction("drive-verify");
    try {
      const res = await studioApi.verifyAssetDrive(selectedAsset.asset_id, selectedAsset.latest.version);
      setBanner({ type: "ok", text: `Drive verification result: ${JSON.stringify(res.status || res.verified || "OK")}` });
      await openAssetDetail(selectedAsset.asset_id);
    } catch (err) {
      setBanner({ type: "err", text: err instanceof Error ? err.message : "Drive verification failed." });
    } finally {
      setBusyAction(null);
    }
  };

  const handleRetryQa = async () => {
    if (!selectedAsset?.latest) return;
    setBusyAction("retry-qa");
    try {
      const updated = await studioApi.retryAssetVersion(selectedAsset.asset_id, selectedAsset.latest.version);
      setSelectedAsset(updated);
      setBanner({ type: "ok", text: "Completed QA re-evaluation." });
      await loadAssets();
    } catch (err) {
      setBanner({ type: "err", text: err instanceof Error ? err.message : "Retry QA failed." });
    } finally {
      setBusyAction(null);
    }
  };

  const handleOpenBlender = async () => {
    if (!selectedAsset?.latest) return;
    setBusyAction("open-blender");
    try {
      await studioApi.openAssetInBlender(selectedAsset.asset_id, selectedAsset.latest.version);
      setBanner({ type: "ok", text: `Launched "${selectedAsset.name}" in Blender desktop.` });
    } catch (err) {
      setBanner({ type: "err", text: err instanceof Error ? err.message : "Could not launch Blender." });
    } finally {
      setBusyAction(null);
    }
  };

  const handleRunBenchmark = async () => {
    if (!selectedAsset) return;
    setBusyAction("benchmark");
    try {
      const rep = await studioApi.runBenchmark(selectedAsset.asset_id);
      setBenchmarks([rep]);
      setDetailTab("benchmark");
      setBanner({
        type: "ok",
        text: `Benchmark score: ${rep.local_grade?.total_score?.toFixed(1) ?? "—"}/100 (Grade ${rep.local_grade?.grade_letter ?? "?"}).`,
      });
    } catch (err) {
      setBanner({ type: "err", text: err instanceof Error ? err.message : "Benchmark run failed." });
    } finally {
      setBusyAction(null);
    }
  };

  const handleImportSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!importFile) return;
    setBusyAction("import");
    try {
      const fd = new FormData();
      fd.append("file", importFile);
      fd.append("name", importName.trim() || importFile.name.replace(/\.[^.]+$/, ""));
      fd.append("category", importCategory);
      fd.append("description", importDesc.trim() || "Imported 3D model");
      fd.append("author", importAuthor.trim() || "Local Studio");
      fd.append("license_name", importLicense.trim() || "Original / Custom");
      const created = await studioApi.importAssetFile(fd);
      setImportOpen(false);
      setImportFile(null);
      setBanner({ type: "ok", text: `Imported "${created.name}" (${created.asset_id}).` });
      await loadAssets();
      await openAssetDetail(created.asset_id);
    } catch (err) {
      setBanner({ type: "err", text: err instanceof Error ? err.message : "Import failed." });
    } finally {
      setBusyAction(null);
    }
  };

  const filteredAssets = React.useMemo(() => {
    if (driveFilter === "ALL") return assets;
    return assets.filter((a) => {
      const ds = (a.latest?.drive_sync_status || "NOT_SYNCED").toUpperCase();
      if (driveFilter === "SYNCED") return isDriveSynced(ds);
      if (driveFilter === "FAILED") return ds === "FAILED";
      return !isDriveSynced(ds);
    });
  }, [assets, driveFilter]);

  const openAdjacentAsset = React.useCallback(
    (currentAssetId: string) => {
      const idx = filteredAssets.findIndex((a) => a.asset_id === currentAssetId);
      if (idx >= 0 && idx + 1 < filteredAssets.length) {
        void openAssetDetail(filteredAssets[idx + 1].asset_id);
      } else {
        setSelectedAsset(null);
      }
    },
    [filteredAssets, openAssetDetail],
  );

  const visibleSelectedAssets = React.useMemo(
    () => filteredAssets.filter((a) => selectedIds.includes(a.asset_id)),
    [filteredAssets, selectedIds],
  );

  const duplicateIds = React.useMemo(() => {
    const rankStatus = (a: VaultAsset): number => {
      const s = (a.latest?.status || "").toUpperCase();
      if (s === "UPLOADED") return 6;
      if (s === "APPROVED") return 5;
      if (s === "AWAITING_HUMAN_APPROVAL") return 4;
      if (s === "PROCESSING" || s === "TECHNICAL_TESTING" || s === "VISUAL_TESTING") return 3;
      if (s === "REJECTED") return 2;
      if (s === "FAILED") return 1;
      return 0;
    };
    const groups = new Map<string, VaultAsset[]>();
    for (const a of filteredAssets) {
      const key = `${a.name.trim().toLowerCase()}::${a.category.trim().toLowerCase()}`;
      const list = groups.get(key) || [];
      list.push(a);
      groups.set(key, list);
    }
    const dups: string[] = [];
    for (const group of groups.values()) {
      if (group.length <= 1) continue;
      const sorted = [...group].sort((a, b) => {
        const diff = rankStatus(b) - rankStatus(a);
        if (diff !== 0) return diff;
        const syncDiff =
          Number(isDriveSynced(b.latest?.drive_sync_status)) -
          Number(isDriveSynced(a.latest?.drive_sync_status));
        if (syncDiff !== 0) return syncDiff;
        return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
      });
      // Keep sorted[0] (highest-priority/newest); mark the rest as duplicates
      for (let i = 1; i < sorted.length; i += 1) {
        dups.push(sorted[i].asset_id);
      }
    }
    return dups;
  }, [filteredAssets]);

  const allFilteredSelected =
    filteredAssets.length > 0 && visibleSelectedAssets.length === filteredAssets.length;

  const toggleSelect = (assetId: string) => {
    setSelectedIds((prev) =>
      prev.includes(assetId) ? prev.filter((id) => id !== assetId) : [...prev, assetId],
    );
  };

  const toggleSelectAllFiltered = () => {
    if (allFilteredSelected) {
      setSelectedIds([]);
    } else {
      setSelectedIds(filteredAssets.map((a) => a.asset_id));
    }
  };

  const selectByPredicate = (pred: (a: VaultAsset) => boolean) => {
    setSelectedIds(filteredAssets.filter(pred).map((a) => a.asset_id));
  };

    const handleDeleteAsset = async (asset: VaultAsset) => {
      setBusyAction(`delete-${asset.asset_id}`);
      setCardBusyId(asset.asset_id);
      try {
        await studioApi.deleteVaultAsset(asset.asset_id);
        setSelectedIds((prev) => prev.filter((id) => id !== asset.asset_id));
        if (selectedAsset?.asset_id === asset.asset_id) {
          setSelectedAsset(null);
        }
        await loadAssets();
        setBanner({ type: "ok", text: `Deleted "${asset.name}" (${asset.asset_id}) from local vault.` });
      } catch (err) {
        setBanner({ type: "err", text: err instanceof Error ? err.message : "Failed to delete asset." });
      } finally {
        setBusyAction(null);
        setCardBusyId(null);
      }
    };

    const runBatch = async (mode: "approve" | "approve_sync" | "sync_drive" | "reject" | "retry_qa" | "delete") => {
      const queue = mode === "delete" ? visibleSelectedAssets : visibleSelectedAssets.filter((a) => a.latest);
      if (queue.length === 0) return;

      let okCount = 0;
      let skippedCount = 0;
      const failedNames: string[] = [];
      const keepSelectedIds: string[] = [];

      for (let i = 0; i < queue.length; i += 1) {
        const asset = queue[i];
        const ver = asset.latest!;
        const ver = asset.latest;
        setBatchProgress({ current: i + 1, total: queue.length, label: asset.name });

        try {
          if (mode === "delete") {
            await studioApi.deleteVaultAsset(asset.asset_id);
            if (selectedAsset?.asset_id === asset.asset_id) {
              setSelectedAsset(null);
            }
            okCount += 1;
            continue;
          }
          if (!ver) {
            skippedCount += 1;
            continue;
          }
          if (mode === "approve") {
            if (ver.status === "FAILED") {
              skippedCount += 1;
              continue;
            }
            await studioApi.reviewAssetVersion(asset.asset_id, ver.version, {
              decision: "APPROVED",
              reviewer: reviewer.trim() || "Local Reviewer",
              notes: "Batch approved from Asset Vault",
            });
            okCount += 1;
          } else if (mode === "approve_sync") {
            if (ver.status === "FAILED" || ver.status === "REJECTED") {
              skippedCount += 1;
              continue;
            }
            if (ver.status !== "APPROVED" && ver.status !== "UPLOADED") {
              await studioApi.reviewAssetVersion(asset.asset_id, ver.version, {
                decision: "APPROVED",
                reviewer: reviewer.trim() || "Local Reviewer",
                notes: "Batch approved & synced from Asset Vault",
              });
            }
            if (!isDriveSynced(ver.drive_sync_status)) {
              await studioApi.uploadAssetToDrive(asset.asset_id, ver.version);
            }
            okCount += 1;
          } else if (mode === "sync_drive") {
            if (ver.status === "FAILED" || ver.status === "REJECTED" || isDriveSynced(ver.drive_sync_status)) {
              skippedCount += 1;
              continue;
            }
            await studioApi.uploadAssetToDrive(asset.asset_id, ver.version);
            okCount += 1;
          } else if (mode === "reject") {
            await studioApi.reviewAssetVersion(asset.asset_id, ver.version, {
              decision: "REJECTED",
              reviewer: reviewer.trim() || "Local Reviewer",
              notes: "Batch rejected from Asset Vault",
            });
            okCount += 1;
          } else if (mode === "retry_qa") {
            await studioApi.retryAssetVersion(asset.asset_id, ver.version);
            okCount += 1;
          }
        } catch (err) {
          failedNames.push(`${asset.name}: ${err instanceof Error ? err.message : "Error"}`);
          keepSelectedIds.push(asset.asset_id);
        }
      }

      setBatchProgress(null);
      setSelectedIds(keepSelectedIds);
      await loadAssets();

      if (failedNames.length > 0) {
        setBanner({
          type: "err",
          text: `Processed ${okCount} asset(s), skipped ${skippedCount}, failed ${failedNames.length} (${failedNames[0]})`,
        });
      } else {
        setBanner({
          type: "ok",
          text: `Batch ${mode.replace("_", " ")} finished: ${okCount} succeeded${skippedCount > 0 ? `, ${skippedCount} skipped` : ""}.`,
        });
      }
    };

    const latest = selectedAsset?.latest || null;
    const previews = selectedAsset ? extractPreviewImages(selectedAsset) : [];
    const nextAssetIndex = selectedAsset
      ? filteredAssets.findIndex((a) => a.asset_id === selectedAsset.asset_id)
      : -1;
    const hasNextAsset = nextAssetIndex >= 0 && nextAssetIndex + 1 < filteredAssets.length;

    return (
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold tracking-tight">Asset Vault (Branch 1)</h1>
            <p className="text-xs text-muted-foreground">
              Generated & imported production assets awaiting QA verification, human approval, and Google Drive release.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => void loadAssets()}>
              <RefreshCw className="h-3.5 w-3.5" />
              Refresh
            </Button>
            <Button size="sm" onClick={() => setImportOpen(true)}>
              <CloudUpload className="h-3.5 w-3.5" />
              Import 3D Model
            </Button>
          </div>
        </div>

        {banner ? (
          <div
            className={`flex items-center justify-between rounded-md border px-3 py-2 text-xs ${banner.type === "ok"
                ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
                : "border-rose-500/40 bg-rose-500/10 text-rose-200"
              }`}
          >
            <span>{banner.text}</span>
            <button type="button" onClick={() => setBanner(null)} className="text-xs opacity-70 hover:opacity-100">
              Dismiss
            </button>
          </div>
        ) : null}

        {/* Filter bar + Selection & Batch Controls */}
        <Card>
          <CardContent className="space-y-3 p-3">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              <div className="relative lg:col-span-2">
                <Search className="pointer-events-none absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
                <Input
                  className="pl-8"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search name, ID, tags, description..."
                />
              </div>
              <Select value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}>
                <option value="ALL">All Categories</option>
                {[
                  "Architecture",
                  "Furniture",
                  "Characters",
                  "Mechanical Objects",
                  "Vehicles",
                  "Household Objects",
                  "Nature",
                  "Miscellaneous",
                ].map((cat) => (
                  <option key={cat} value={cat}>
                    {cat}
                  </option>
                ))}
              </Select>
              <Select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
                <option value="ALL">All Vault Statuses</option>
                {[
                  "AWAITING_HUMAN_APPROVAL",
                  "APPROVED",
                  "UPLOADED",
                  "REJECTED",
                  "FAILED",
                  "PROCESSING",
                  "TECHNICAL_TESTING",
                  "VISUAL_TESTING",
                ].map((st) => (
                  <option key={st} value={st}>
                    {st}
                  </option>
                ))}
              </Select>
              <Select
                value={driveFilter}
                onChange={(e) => setDriveFilter(e.target.value as "ALL" | "UNSYNCED" | "SYNCED" | "FAILED")}
              >
                <option value="ALL">All Drive States</option>
                <option value="UNSYNCED">Drive: Needs Sync</option>
                <option value="SYNCED">Drive: Synced / Verified</option>
                <option value="FAILED">Drive: Failed</option>
              </Select>
            </div>

            {/* Selection & Batch Action Strip */}
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border/60 pt-2.5 text-xs">
              <div className="flex flex-wrap items-center gap-1.5">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={filteredAssets.length === 0 || batchProgress !== null}
                  onClick={toggleSelectAllFiltered}
                >
                  {allFilteredSelected ? (
                    <CheckSquare className="h-3.5 w-3.5 text-primary" />
                  ) : (
                    <Square className="h-3.5 w-3.5" />
                  )}
                  {allFilteredSelected
                    ? `Deselect All (${filteredAssets.length})`
                    : `Select All (${filteredAssets.length})`}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={filteredAssets.length === 0 || batchProgress !== null}
                  onClick={() => selectByPredicate((a) => a.latest?.status === "AWAITING_HUMAN_APPROVAL")}
                >
                  Select Awaiting Approval
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={filteredAssets.length === 0 || batchProgress !== null}
                  onClick={() =>
                    selectByPredicate(
                      (a) =>
                        (a.latest?.status === "APPROVED" || a.latest?.status === "UPLOADED") &&
                        !isDriveSynced(a.latest?.drive_sync_status),
                    )
                  }
                >
                  Select Unsynced Approved
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={duplicateIds.length === 0 || batchProgress !== null}
                  onClick={() => setSelectedIds(duplicateIds)}
                  title="Select older/lower-priority duplicate copies while keeping the best copy of each name"
                >
                  Select Duplicates ({duplicateIds.length})
                </Button>
                {visibleSelectedAssets.length > 0 ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={batchProgress !== null}
                    onClick={() => setSelectedIds([])}
                  >
                    Clear ({visibleSelectedAssets.length})
                  </Button>
                ) : null}
              </div>

              {visibleSelectedAssets.length > 0 ? (
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant="default" className="font-mono">
                    {visibleSelectedAssets.length} selected
                  </Badge>
                  <Button
                    size="sm"
                    disabled={batchProgress !== null}
                    onClick={() => void runBatch("approve")}
                  >
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    Approve Selected
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={batchProgress !== null}
                    onClick={() => void runBatch("approve_sync")}
                  >
                    <CloudUpload className="h-3.5 w-3.5" />
                    Approve &amp; Sync Drive
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={batchProgress !== null}
                    onClick={() => void runBatch("sync_drive")}
                  >
                    <HardDrive className="h-3.5 w-3.5" />
                    Sync Drive
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={batchProgress !== null}
                    onClick={() =>
                      setConfirmBatch({ mode: "retry_qa", count: visibleSelectedAssets.length })
                    }
                  >
                    <RotateCcw className="h-3.5 w-3.5" />
                    Re-QA
                  </Button>
                  <Button
                    size="sm"
                    variant="destructive"
                    disabled={batchProgress !== null}
                    onClick={() =>
                      setConfirmBatch({ mode: "reject", count: visibleSelectedAssets.length })
                    }
                  >
                    <XCircle className="h-3.5 w-3.5" />
                    Reject
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="border-rose-500/40 text-rose-300 hover:bg-rose-500/15"
                    disabled={batchProgress !== null}
                    onClick={() =>
                      setConfirmBatch({ mode: "delete", count: visibleSelectedAssets.length })
                    }
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    Delete Selected
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
          <div className="py-12 text-center text-xs text-muted-foreground">Loading Branch 1 Vault...</div>
        ) : filteredAssets.length === 0 ? (
          <Card>
            <CardContent className="py-12 text-center text-xs text-muted-foreground">
              No matching assets in Vault. Adjust filters above, start a new asset in Generation Studio, or import a `.blend`/`.glb`/`.obj`/`.fbx` file.
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {filteredAssets.map((asset) => {
              const ver = asset.latest;
              const thumbs = extractPreviewImages(asset);
              const isSelected = selectedIds.includes(asset.asset_id);
              const isCardBusy = cardBusyId === asset.asset_id;
              const awaitingApproval = ver?.status === "AWAITING_HUMAN_APPROVAL";
              const readyForDrive =
                (ver?.status === "APPROVED" || ver?.status === "UPLOADED") &&
                !isDriveSynced(ver?.drive_sync_status);
              const isFailed = ver?.status === "FAILED";

              return (
                <Card
                  key={asset.asset_id}
                  className={`flex cursor-pointer flex-col overflow-hidden transition-colors hover:border-primary/50 ${isSelected ? "border-primary ring-1 ring-primary" : ""
                    }`}
                  onClick={() => void openAssetDetail(asset.asset_id)}
                >
                  <div className="relative flex h-40 items-center justify-center border-b border-border bg-[#0c0f15]">
                    {thumbs[0] ? (
                      <img src={thumbs[0].url} alt={asset.name} className="h-full w-full object-cover" />
                    ) : (
                      <Box className="h-9 w-9 text-muted-foreground/40" />
                    )}
                    <div className="absolute left-2 top-2 flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          toggleSelect(asset.asset_id);
                        }}
                        className={`flex h-5 w-5 items-center justify-center rounded border transition-colors ${isSelected
                            ? "border-primary bg-primary text-primary-foreground"
                            : "border-border/80 bg-card/85 text-muted-foreground hover:border-primary hover:text-foreground"
                          }`}
                        aria-label={`Select ${asset.name}`}
                      >
                        {isSelected ? <Check className="h-3 w-3" /> : null}
                      </button>
                      <StatusBadge status={ver?.status} />
                    </div>
                    <div className="absolute right-2 top-2">
                      <Badge variant="outline" className="bg-card/85 font-mono">
                        v{asset.current_version}
                      </Badge>
                    </div>
                  </div>
                  <CardContent className="flex flex-1 flex-col justify-between gap-2 p-3">
                    <div>
                      <p className="truncate text-sm font-semibold">{asset.name}</p>
                      <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
                        {asset.category} • {formatNumber(ver?.poly_count)} tris
                      </p>
                    </div>
                    <div className="flex items-center justify-between pt-1 text-[10px] text-muted-foreground">
                      <span>Tech QA: {ver?.technical_qa?.status || "PENDING"}</span>
                      <span>Drive: {ver?.drive_sync_status || "PENDING"}</span>
                    </div>

                    {/* Direct 1-Click Card Actions */}
                    <div
                      className="flex flex-wrap items-center gap-1.5 border-t border-border/60 pt-2"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {awaitingApproval ? (
                        <>
                          <Button
                            size="sm"
                            className="h-7 flex-1 text-[11px]"
                            disabled={isCardBusy || batchProgress !== null}
                            onClick={() => void handleQuickApprove(asset, "APPROVED")}
                          >
                            {isCardBusy ? (
                              <Loader2 className="h-3 w-3 animate-spin" />
                            ) : (
                              <CheckCircle2 className="h-3 w-3" />
                            )}
                            Approve
                          </Button>
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-7 px-2 text-[11px] text-rose-300 hover:bg-rose-500/15"
                            disabled={isCardBusy || batchProgress !== null}
                            onClick={() => void handleQuickApprove(asset, "REJECTED")}
                          >
                            <XCircle className="h-3 w-3" />
                          </Button>
                        </>
                      ) : null}

                      {readyForDrive ? (
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-7 flex-1 text-[11px]"
                          disabled={isCardBusy || batchProgress !== null}
                          onClick={() => void handleQuickDriveUpload(asset)}
                        >
                          {isCardBusy ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <CloudUpload className="h-3 w-3" />
                          )}
                          Sync Drive
                        </Button>
                      ) : null}

                      {isFailed ? (
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-7 flex-1 text-[11px]"
                          disabled={isCardBusy || batchProgress !== null}
                          onClick={() => void handleQuickRetryQa(asset)}
                        >
                          {isCardBusy ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : (
                            <RotateCcw className="h-3 w-3" />
                          )}
                          Re-Run QA
                        </Button>
                      ) : null}

                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 px-2 text-[11px]"
                        onClick={() => void openAssetDetail(asset.asset_id)}
                      >
                        Inspect
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 px-2 text-[11px] text-rose-300 hover:bg-rose-500/15"
                        title={`Delete ${asset.name}`}
                        disabled={isCardBusy || batchProgress !== null}
                        onClick={() => setConfirmDeleteAsset(asset)}
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}

        {/* Asset Detail & Human Review Dialog */}
        <Dialog
          open={Boolean(selectedAsset)}
          onOpenChange={(open) => {
            if (!open) setSelectedAsset(null);
          }}
          maxWidthClass="max-w-5xl"
          title={
            selectedAsset ? (
              <span className="inline-flex flex-wrap items-center gap-2">
                <span>{selectedAsset.name}</span>
                <StatusBadge status={latest?.status} />
                {selectedAsset.roblox_asset_id ? (
                  <Badge variant="outline" className="font-mono">
                    {selectedAsset.roblox_asset_id}
                  </Badge>
                ) : null}
              </span>
            ) : (
              ""
            )
          }
          description={selectedAsset ? `${selectedAsset.category} • ID ${selectedAsset.asset_id} • v${selectedAsset.current_version}` : ""}
          footer={
            selectedAsset && latest ? (
              <div className="flex flex-wrap items-center justify-between gap-2 w-full">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Button variant="outline" size="sm" onClick={() => void handleOpenBlender()} disabled={busyAction !== null}>
                    <FolderOpen className="h-3.5 w-3.5" />
                    Open in Blender
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => void handleRetryQa()} disabled={busyAction !== null}>
                    <RotateCcw className="h-3.5 w-3.5" />
                    Re-Run QA
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => void handleRunBenchmark()} disabled={busyAction !== null}>
                    <Award className="h-3.5 w-3.5" />
                    Benchmark vs Ref
                  </Button>
                  {latest.blend_path ? (
                    <a
                      href={`/api/assets/${selectedAsset.asset_id}/versions/${latest.version}/download/blend`}
                      className="inline-flex h-7 items-center rounded-md border border-border px-2.5 text-[11px] font-medium hover:bg-accent"
                    >
                      .blend
                    </a>
                  ) : null}
                  {latest.glb_path ? (
                    <a
                      href={`/api/assets/${selectedAsset.asset_id}/versions/${latest.version}/download/glb`}
                      className="inline-flex h-7 items-center rounded-md border border-border px-2.5 text-[11px] font-medium hover:bg-accent"
                    >
                      .glb
                    </a>
                  ) : null}
                </div>

                <div className="flex flex-wrap items-center gap-1.5">
                  {latest.status !== "APPROVED" && latest.status !== "UPLOADED" && latest.status !== "FAILED" ? (
                    <>
                      <Button
                        size="sm"
                        disabled={busyAction !== null}
                        onClick={() => void handleReview("APPROVED")}
                      >
                        <CheckCircle2 className="h-3.5 w-3.5" />
                        Approve
                      </Button>
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={busyAction !== null}
                        onClick={() => void handleApproveAndSync(false)}
                      >
                        <CloudUpload className="h-3.5 w-3.5" />
                        Approve &amp; Sync
                      </Button>
                    </>
                  ) : null}

                  {(latest.status === "APPROVED" || latest.status === "UPLOADED") &&
                    !isDriveSynced(latest.drive_sync_status) ? (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busyAction !== null}
                      onClick={() => void handleDriveUpload()}
                    >
                      {busyAction === "drive-upload" ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <CloudUpload className="h-3.5 w-3.5" />
                      )}
                      Sync Drive
                    </Button>
                  ) : null}

                  {latest.status !== "REJECTED" ? (
                    <Button
                      size="sm"
                      variant="destructive"
                      disabled={busyAction !== null}
                      onClick={() => void handleReview("REJECTED")}
                    >
                      <XCircle className="h-3.5 w-3.5" />
                      Reject
                    </Button>
                  ) : null}

                  <Button
                    size="sm"
                    variant="outline"
                    className="border-rose-500/40 text-rose-300 hover:bg-rose-500/15"
                    disabled={busyAction !== null}
                    onClick={() => setConfirmDeleteAsset(selectedAsset)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    Delete
                  </Button>

                  {hasNextAsset ? (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busyAction !== null}
                      onClick={() => openAdjacentAsset(selectedAsset.asset_id)}
                    >
                      Next
                      <ChevronRight className="h-3.5 w-3.5" />
                    </Button>
                  ) : null}

                  <Button variant="secondary" size="sm" onClick={() => setSelectedAsset(null)}>
                    Close
                  </Button>
                </div>
              </div>
            ) : null
          }
        >
          {selectedAsset && latest ? (
            <div className="space-y-4">
              {banner ? (
                <div
                  className={`flex items-center justify-between rounded-md border px-3 py-2 text-xs ${banner.type === "ok"
                      ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-200"
                      : "border-rose-500/40 bg-rose-500/10 text-rose-200"
                    }`}
                >
                  <span>{banner.text}</span>
                  <button type="button" onClick={() => setBanner(null)} className="text-xs opacity-70 hover:opacity-100">
                    Dismiss
                  </button>
                </div>
              ) : null}
              <Tabs
                value={detailTab}
                onChange={setDetailTab}
                items={[
                  { id: "viewer", label: "3D & Previews", badge: previews.length },
                  { id: "qa", label: "Technical & Visual QA", badge: latest.technical_qa?.status || "PENDING" },
                  { id: "approval", label: "Approval & Drive Sync", badge: latest.drive_sync_status || "PENDING" },
                  { id: "benchmark", label: "Reference Benchmark" },
                ]}
              />

              {detailTab === "viewer" ? (
                <div className="grid gap-4 lg:grid-cols-12">
                  <div className="space-y-3 lg:col-span-7">
                    <GlbViewer
                      glbUrl={
                        latest.glb_path
                          ? `/api/assets/${selectedAsset.asset_id}/versions/${latest.version}/download/glb`
                          : null
                      }
                      heightClass="h-80"
                    />
                    {previews.length > 0 ? (
                      <div className="grid grid-cols-3 gap-2">
                        {previews.map((img) => (
                          <a
                            key={img.name}
                            href={img.url}
                            target="_blank"
                            rel="noreferrer"
                            className="group overflow-hidden rounded border border-border bg-background/60 p-1"
                          >
                            <img src={img.url} alt={img.name} className="h-24 w-full rounded object-cover" />
                            <p className="mt-1 flex items-center justify-between truncate text-[10px] text-muted-foreground group-hover:text-foreground">
                              <span className="truncate">{img.name}</span>
                              <ExternalLink className="h-2.5 w-2.5 shrink-0" />
                            </p>
                          </a>
                        ))}
                      </div>
                    ) : null}
                  </div>
                  <div className="space-y-3 lg:col-span-5 text-xs">
                    <div className="rounded-md border border-border bg-background/60 p-3 space-y-2">
                      <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">Description</p>
                      <p className="leading-relaxed text-foreground">{selectedAsset.description}</p>
                      <div className="grid grid-cols-2 gap-2 pt-2 text-[11px]">
                        <div>
                          <span className="text-muted-foreground">Vertices</span>
                          <p className="font-mono font-semibold">{formatNumber(latest.vertex_count)}</p>
                        </div>
                        <div>
                          <span className="text-muted-foreground">Triangles</span>
                          <p className="font-mono font-semibold">{formatNumber(latest.poly_count)}</p>
                        </div>
                        <div>
                          <span className="text-muted-foreground">Dimensions (m)</span>
                          <p className="font-mono font-semibold">
                            {(latest.dimensions || []).map((d) => d.toFixed(2)).join(" × ") || "—"}
                          </p>
                        </div>
                        <div>
                          <span className="text-muted-foreground">Size</span>
                          <p className="font-mono font-semibold">{formatBytes(latest.file_size_bytes)}</p>
                        </div>
                      </div>
                    </div>
                    <div className="rounded-md border border-border bg-background/60 p-3 space-y-1.5">
                      <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                        Provenance & Rights
                      </p>
                      <p>Type: <span className="font-mono">{selectedAsset.provenance.provenance_type}</span></p>
                      <p>Author: {selectedAsset.provenance.author}</p>
                      <p>License: {selectedAsset.provenance.license_name}</p>
                    </div>
                  </div>
                </div>
              ) : null}

              {detailTab === "qa" ? (
                <div className="grid gap-4 lg:grid-cols-2 text-xs">
                  <div className="rounded-md border border-border bg-background/60 p-3 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="font-semibold">Technical QA ({latest.technical_qa?.blender_version || "Blender"})</span>
                      <StatusBadge status={latest.technical_qa?.status || "PENDING"} />
                    </div>
                    <p className="text-muted-foreground">{latest.technical_qa?.summary || "Not run yet."}</p>
                    <div className="max-h-64 space-y-1.5 overflow-y-auto pt-1">
                      {(latest.technical_qa?.checks || []).map((chk) => (
                        <div
                          key={chk.name}
                          className="flex items-start justify-between gap-2 rounded border border-border/70 px-2.5 py-1.5"
                        >
                          <div>
                            <p className="font-medium">{chk.name}</p>
                            <p className="text-[11px] text-muted-foreground">{chk.detail}</p>
                          </div>
                          {chk.passed ? (
                            <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" />
                          ) : (
                            <XCircle className="h-4 w-4 shrink-0 text-rose-400" />
                          )}
                        </div>
                      ))}
                    </div>
                  </div>

                  <div className="rounded-md border border-border bg-background/60 p-3 space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="font-semibold">
                        Visual QA ({latest.visual_qa?.provider || "none"})
                      </span>
                      <StatusBadge status={latest.visual_qa?.status || "PENDING"} />
                    </div>
                    <p className="text-lg font-bold font-mono text-primary">
                      Score: {latest.visual_qa?.score !== null && latest.visual_qa?.score !== undefined ? `${latest.visual_qa.score}/100` : "—"}
                    </p>
                    <p className="text-muted-foreground">{latest.visual_qa?.summary || "No visual review summary."}</p>
                    {(latest.visual_qa?.issues_found || []).length > 0 ? (
                      <ul className="list-disc space-y-1 pl-4 text-amber-200">
                        {latest.visual_qa?.issues_found?.map((iss, i) => (
                          <li key={i}>{iss}</li>
                        ))}
                      </ul>
                    ) : null}
                  </div>
                </div>
              ) : null}

              {detailTab === "approval" ? (
                <div className="grid gap-4 lg:grid-cols-2 text-xs">
                  <div className="rounded-md border border-border bg-background/60 p-3 space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="flex items-center gap-1.5 font-semibold">
                        <ShieldCheck className="h-4 w-4 text-primary" />
                        Stage 2 Human Approval Gate
                      </span>
                      <StatusBadge status={latest.human_review?.status || "PENDING"} />
                    </div>
                    <div>
                      <label className="mb-1 block text-[11px] text-muted-foreground">Reviewer Name</label>
                      <Input value={reviewer} onChange={(e) => setReviewer(e.target.value)} />
                    </div>
                    <div>
                      <label className="mb-1 block text-[11px] text-muted-foreground">Review Notes</label>
                      <Textarea
                        rows={3}
                        value={reviewNotes}
                        onChange={(e) => setReviewNotes(e.target.value)}
                        placeholder="Inspect geometry, UVs, and multi-angle renders before approving..."
                      />
                    </div>
                    <div className="flex gap-2">
                      <Button
                        className="flex-1"
                        disabled={busyAction !== null}
                        onClick={() => void handleReview("APPROVED")}
                      >
                        {busyAction === "review-APPROVED" ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <CheckCircle2 className="h-3.5 w-3.5" />
                        )}
                        Approve Version
                      </Button>
                      <Button
                        variant="destructive"
                        className="flex-1"
                        disabled={busyAction !== null}
                        onClick={() => void handleReview("REJECTED")}
                      >
                        <XCircle className="h-3.5 w-3.5" />
                        Reject Version
                      </Button>
                    </div>
                  </div>

                  <div className="rounded-md border border-border bg-background/60 p-3 space-y-3">
                    <div className="flex items-center justify-between">
                      <span className="flex items-center gap-1.5 font-semibold">
                        <HardDrive className="h-4 w-4 text-sky-400" />
                        Google Drive Vault Sync
                      </span>
                      <StatusBadge status={latest.drive_sync_status || "PENDING"} />
                    </div>
                    <div className="flex gap-2">
                      <Button size="sm" variant="outline" disabled={busyAction !== null} onClick={() => void handleDriveUpload()}>
                        <CloudUpload className="h-3.5 w-3.5" />
                        Upload to Drive
                      </Button>
                      <Button size="sm" variant="outline" disabled={busyAction !== null} onClick={() => void handleVerifyDrive()}>
                        Verify Remote SHA/MD5
                      </Button>
                    </div>
                    <div className="max-h-48 space-y-1.5 overflow-y-auto">
                      {(latest.drive_files || []).length === 0 ? (
                        <p className="text-muted-foreground">No files uploaded to Google Drive yet for this version.</p>
                      ) : (
                        latest.drive_files?.map((df) => (
                          <div
                            key={df.remote_file_id}
                            className="flex items-center justify-between rounded border border-border/70 px-2.5 py-1.5"
                          >
                            <div className="min-w-0">
                              <p className="truncate font-mono text-[11px]">{df.remote_name}</p>
                              <p className="text-[10px] text-muted-foreground">
                                {df.role} • {df.verified ? "Verified" : "Unverified"}
                              </p>
                            </div>
                            {df.web_view_link ? (
                              <a
                                href={df.web_view_link}
                                target="_blank"
                                rel="noreferrer"
                                className="text-sky-400 hover:underline"
                              >
                                Drive
                              </a>
                            ) : null}
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                </div>
              ) : null}

              {detailTab === "benchmark" ? (
                <div className="space-y-3 text-xs">
                  {benchmarks.length === 0 ? (
                    <div className="rounded-md border border-border bg-background/60 p-4 text-center">
                      <p className="text-muted-foreground">
                        Compare this asset against the top matching graded BlendSwap reference assets.
                      </p>
                      <Button className="mt-3" size="sm" onClick={() => void handleRunBenchmark()} disabled={busyAction !== null}>
                        <Award className="h-3.5 w-3.5" />
                        Run Live Reference Benchmark
                      </Button>
                    </div>
                  ) : (
                    benchmarks.map((b) => (
                      <div key={b.benchmark_id} className="rounded-md border border-border bg-background/60 p-3 space-y-2">
                        <div className="flex items-center justify-between">
                          <span className="font-semibold">
                            Benchmark {b.benchmark_id} ({formatDateTime(b.created_at)})
                          </span>
                          <Badge variant="default">
                            Score {b.local_grade?.total_score?.toFixed(1) ?? "—"}/100 ({b.local_grade?.grade_letter ?? "?"})
                          </Badge>
                        </div>
                        <p className="text-emerald-200">{b.efficiency_note}</p>
                        <div>
                          <p className="font-medium text-muted-foreground">Recommendations:</p>
                          <ul className="mt-1 list-disc space-y-1 pl-4">
                            {b.recommended_improvements.map((r, i) => (
                              <li key={i}>{r}</li>
                            ))}
                          </ul>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              ) : null}
            </div>
          ) : null}
        </Dialog>

        {/* Import Local 3D Model Dialog */}
        <Dialog
          open={importOpen}
          onOpenChange={setImportOpen}
          maxWidthClass="max-w-lg"
          title="Import Local 3D Asset"
          description="Imports .blend, .glb, .gltf, .obj, or .fbx into the existing QA and Drive pipeline."
        >
          <form onSubmit={(e) => void handleImportSubmit(e)} className="space-y-3 text-xs">
            <div>
              <label className="mb-1 block text-[11px] text-muted-foreground">3D File</label>
              <input
                type="file"
                accept=".blend,.glb,.gltf,.obj,.fbx"
                onChange={(e) => setImportFile(e.target.files?.[0] || null)}
                className="w-full rounded-md border border-input bg-background px-2.5 py-1.5 text-xs"
              />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label className="mb-1 block text-[11px] text-muted-foreground">Asset Name</label>
                <Input value={importName} onChange={(e) => setImportName(e.target.value)} placeholder="Optional override" />
              </div>
              <div>
                <label className="mb-1 block text-[11px] text-muted-foreground">Category</label>
                <Select value={importCategory} onChange={(e) => setImportCategory(e.target.value)}>
                  {["Furniture", "Architecture", "Characters", "Mechanical Objects", "Vehicles", "Household Objects", "Nature", "Miscellaneous"].map(
                    (c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ),
                  )}
                </Select>
              </div>
            </div>
            <div>
              <label className="mb-1 block text-[11px] text-muted-foreground">Description</label>
              <Textarea rows={2} value={importDesc} onChange={(e) => setImportDesc(e.target.value)} />
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label className="mb-1 block text-[11px] text-muted-foreground">Author</label>
                <Input value={importAuthor} onChange={(e) => setImportAuthor(e.target.value)} />
              </div>
              <div>
                <label className="mb-1 block text-[11px] text-muted-foreground">Declared License</label>
                <Input value={importLicense} onChange={(e) => setImportLicense(e.target.value)} />
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button type="button" variant="outline" size="sm" onClick={() => setImportOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" size="sm" disabled={!importFile || busyAction === "import"}>
                {busyAction === "import" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CloudUpload className="h-3.5 w-3.5" />}
                Import & Run QA
              </Button>
            </div>
          </form>
        </Dialog>

        {confirmBatch ? (
          <ConfirmDialog
            open={Boolean(confirmBatch)}
            onOpenChange={(open) => {
              if (!open) setConfirmBatch(null);
            }}
            title={
              confirmBatch.mode === "reject"
                ? `Reject ${confirmBatch.count} Selected Asset(s)?`
                : `Re-Run QA on ${confirmBatch.count} Selected Asset(s)?`
            confirmBatch.mode === "delete"
          ? `Delete ${confirmBatch.count} Selected Asset(s)?`
          : confirmBatch.mode === "reject"
            ? `Reject ${confirmBatch.count} Selected Asset(s)?`
            : `Re-Run QA on ${confirmBatch.count} Selected Asset(s)?`
          }
        description={
          confirmBatch.mode === "reject"
            ? "Marks the selected asset versions as REJECTED in the local Vault and mirrored Roblox database."
            : "Re-runs headless Blender inspection and multi-angle rendering sequentially for each selected asset."
            confirmBatch.mode === "delete"
        ? "Permanently deletes the selected assets, their local .blend/.glb/preview files, and their job records from the local Vault."
        : confirmBatch.mode === "reject"
        ? "Marks the selected asset versions as REJECTED in the local Vault and mirrored Roblox database."
        : "Re-runs headless Blender inspection and multi-angle rendering sequentially for each selected asset."
          }
        confirmLabel={confirmBatch.mode === "reject" ? "Reject Selected" : "Run QA Batch"}
        confirmVariant={confirmBatch.mode === "reject" ? "destructive" : "default"}
        confirmLabel={
          confirmBatch.mode === "delete"
            ? "Delete Permanently"
            : confirmBatch.mode === "reject"
              ? "Reject Selected"
              : "Run QA Batch"
        }
        confirmVariant={confirmBatch.mode === "retry_qa" ? "default" : "destructive"}
        busy={batchProgress !== null}
        onConfirm={async () => {
          const mode = confirmBatch.mode;
          setConfirmBatch(null);
          await runBatch(mode);
        }}
        />
      ) : null}

        {confirmDeleteAsset ? (
          <ConfirmDialog
            open={Boolean(confirmDeleteAsset)}
            onOpenChange={(open) => {
              if (!open) setConfirmDeleteAsset(null);
            }}
            title={`Delete "${confirmDeleteAsset.name}"?`}
            description={`Permanently deletes asset ${confirmDeleteAsset.asset_id} and its local .blend, .glb, and preview files from the Vault.`}
            confirmLabel="Delete Permanently"
            confirmVariant="destructive"
            busy={busyAction === `delete-${confirmDeleteAsset.asset_id}`}
            onConfirm={async () => {
              const target = confirmDeleteAsset;
              setConfirmDeleteAsset(null);
              await handleDeleteAsset(target);
            }}
          />
        ) : null}
      </div>
    );
  }
