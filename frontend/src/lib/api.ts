import type {
  AssetVersion,
  BenchmarkReport,
  CollectionCycle,
  CollectionQueueItem,
  CollectionRules,
  CollectionRulesResponse,
  CollectionStatusResponse,
  DashboardOverview,
  GenerationJob,
  GenerationTemplate,
  QualityReportsResponse,
  ReferenceAsset,
  SystemHealth,
  VaultAsset,
} from "@/types/studio";

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

async function apiFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers || {});
  if (!(init?.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const resp = await fetch(url, { ...init, headers });
  const text = await resp.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }
  if (!resp.ok) {
    const detail =
      typeof parsed === "object" && parsed !== null && "detail" in parsed
        ? String((parsed as { detail: unknown }).detail)
        : typeof parsed === "string" && parsed
          ? parsed
          : `Request failed with HTTP ${resp.status}`;
    throw new ApiError(resp.status, detail);
  }
  return parsed as T;
}

export const studioApi = {
  getOverview: (checkLive = false) =>
    apiFetch<DashboardOverview>(`/api/dashboard/overview?check_live=${checkLive}`),
  getSystemHealth: (checkLive = false) =>
    apiFetch<SystemHealth>(`/api/system/health?check_live=${checkLive}`),

  // Generation Studio
  getGenerationTemplates: () =>
    apiFetch<{
      templates: GenerationTemplate[];
      categories: string[];
      visual_styles: string[];
      complexities: string[];
    }>("/api/generation/templates"),
  listGenerationJobs: (limit = 50, status?: string) => {
    const qs = new URLSearchParams({ limit: String(limit) });
    if (status && status !== "ALL") qs.set("status", status);
    return apiFetch<{ count: number; jobs: GenerationJob[] }>(`/api/generation/jobs?${qs.toString()}`);
  },
  getGenerationJob: (jobId: string) =>
    apiFetch<{ job: GenerationJob; asset: VaultAsset | null; benchmarks: BenchmarkReport[] }>(
      `/api/generation/jobs/${encodeURIComponent(jobId)}`,
    ),
  createGenerationJob: (payload: {
    prompt: string;
    name?: string;
    category: string;
    tags: string[];
    template_id?: string | null;
    visual_style: string;
    complexity: string;
    poly_budget?: number | null;
    additional_instructions: string;
  }) =>
    apiFetch<{ status: string; job: GenerationJob; asset: VaultAsset }>("/api/generation/create", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  cancelGenerationJob: (jobId: string) =>
    apiFetch<{ job: GenerationJob }>(`/api/generation/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: "POST",
    }),
  retryGenerationJob: (jobId: string) =>
    apiFetch<Record<string, unknown>>(`/api/generation/jobs/${encodeURIComponent(jobId)}/retry`, {
      method: "POST",
    }),

  // Branch 1 Asset Vault
  listVaultAssets: (params?: { search?: string; category?: string; status?: string }) => {
    const qs = new URLSearchParams();
    if (params?.search) qs.set("search", params.search);
    if (params?.category && params.category !== "ALL") qs.set("category", params.category);
    if (params?.status && params.status !== "ALL") qs.set("status", params.status);
    return apiFetch<{ assets: VaultAsset[]; total: number }>(`/api/assets?${qs.toString()}`);
  },
  getVaultAsset: (assetId: string) =>
    apiFetch<VaultAsset>(`/api/assets/${encodeURIComponent(assetId)}`),
  deleteVaultAsset: (assetId: string) =>
    apiFetch<{ deleted: boolean; asset_id: string; name: string }>(
      `/api/assets/${encodeURIComponent(assetId)}`,
      { method: "DELETE" },
    ),
  reviewAssetVersion: (
    assetId: string,
    version: number,
    payload: { decision: "APPROVED" | "REJECTED"; reviewer?: string; notes?: string; category?: string },
  ) =>
    apiFetch<VaultAsset>(
      `/api/assets/${encodeURIComponent(assetId)}/versions/${version}/review`,
      { method: "POST", body: JSON.stringify(payload) },
    ),
  retryAssetVersion: (assetId: string, version: number) =>
    apiFetch<VaultAsset>(
      `/api/assets/${encodeURIComponent(assetId)}/versions/${version}/retry`,
      { method: "POST" },
    ),
  openAssetInBlender: (assetId: string, version: number) =>
    apiFetch<Record<string, unknown>>(
      `/api/assets/${encodeURIComponent(assetId)}/versions/${version}/open-blender`,
      { method: "POST" },
    ),
  uploadAssetToDrive: (assetId: string, version: number) =>
    apiFetch<{ version: AssetVersion; asset: VaultAsset }>(
      `/api/assets/${encodeURIComponent(assetId)}/versions/${version}/upload-drive`,
      { method: "POST" },
    ),
  verifyAssetDrive: (assetId: string, version: number) =>
    apiFetch<Record<string, unknown>>(
      `/api/assets/${encodeURIComponent(assetId)}/versions/${version}/verify-drive`,
      { method: "POST" },
    ),
  importAssetFile: (formData: FormData) =>
    apiFetch<VaultAsset>("/api/assets/import", {
      method: "POST",
      body: formData,
    }),

  // Reference Library (Branch 2)
  listReferenceLibrary: (params?: {
    search?: string;
    category?: string;
    license_key?: string;
    grade_letter?: string;
    poly_complexity?: string;
    modeling_style?: string;
    date_collected?: string;
    limit?: number;
  }) => {
    const qs = new URLSearchParams();
    if (params?.search) qs.set("search", params.search);
    if (params?.category && params.category !== "all") qs.set("category", params.category);
    if (params?.license_key && params.license_key !== "all") qs.set("license_key", params.license_key);
    if (params?.grade_letter && params.grade_letter !== "all") qs.set("grade_letter", params.grade_letter);
    if (params?.poly_complexity && params.poly_complexity !== "all") qs.set("poly_complexity", params.poly_complexity);
    if (params?.modeling_style && params.modeling_style !== "all") qs.set("modeling_style", params.modeling_style);
    if (params?.date_collected && params.date_collected !== "all") qs.set("date_collected", params.date_collected);
    qs.set("limit", String(params?.limit || 150));
    return apiFetch<{
      total: number;
      count: number;
      items: ReferenceAsset[];
      facets: {
        categories: string[];
        licenses: string[];
        grades: string[];
        modeling_styles: string[];
        poly_complexities: string[];
        dates_collected: string[];
      };
    }>(`/api/reference/library?${qs.toString()}`);
  },
  getReferenceAsset: (blendswapId: string) =>
    apiFetch<ReferenceAsset>(`/api/reference/assets/${encodeURIComponent(blendswapId)}`),
  openReferenceInBlender: (blendswapId: string) =>
    apiFetch<Record<string, unknown>>(
      `/api/reference/assets/${encodeURIComponent(blendswapId)}/open-blender`,
      { method: "POST" },
    ),
  reinspectReference: (blendswapId: string) =>
    apiFetch<ReferenceAsset>(
      `/api/reference/assets/${encodeURIComponent(blendswapId)}/reinspect`,
      { method: "POST" },
    ),
  syncReferenceToDrive: (blendswapId: string) =>
    apiFetch<Record<string, unknown>>(
      `/api/reference/assets/${encodeURIComponent(blendswapId)}/sync-drive`,
      { method: "POST" },
    ),
  syncPendingReferenceDrive: () =>
    apiFetch<Record<string, unknown>>("/api/reference/drive/sync-pending", { method: "POST" }),

  // Collection Control
  getCollectionStatus: (checkLive = false) =>
    apiFetch<CollectionStatusResponse>(`/api/collection/status?check_live=${checkLive}`),
  getCollectionHistory: (limit = 30) =>
    apiFetch<{ cycles: CollectionCycle[]; download_history: Array<Record<string, unknown>> }>(
      `/api/collection/history?limit=${limit}`,
    ),
  getCollectionCycle: (cycleId: string) =>
    apiFetch<{ cycle: CollectionCycle; queue: CollectionQueueItem[] }>(
      `/api/collection/history/${encodeURIComponent(cycleId)}`,
    ),
  getCollectionRules: () => apiFetch<CollectionRulesResponse>("/api/collection/rules"),
  saveCollectionRules: (rules: CollectionRules & { scope: "permanent" | "next_run" }) =>
    apiFetch<CollectionRulesResponse>("/api/collection/rules", {
      method: "PUT",
      body: JSON.stringify(rules),
    }),
  restoreDefaultRules: () =>
    apiFetch<CollectionRulesResponse>("/api/collection/rules/restore-defaults", {
      method: "POST",
      body: JSON.stringify({ clear_next_run: true }),
    }),
  previewCandidates: (rules?: Partial<CollectionRules>) =>
    apiFetch<{
      total_candidates: number;
      eligible_count: number;
      rejected_count: number;
      selected_count: number;
      allowance: {
        free_downloads_remaining: number;
        credits: number;
        allow_credit_spend: boolean;
      };
      selected: Array<{
        blendswap_id: string;
        title: string;
        author: string;
        primary_category: string;
        license_key: string;
        downloads: number;
        primary_format: string;
        poly_complexity: string;
        selection_score: number;
        reason: string;
        excluded?: boolean;
      }>;
      rejected: Array<{
        blendswap_id: string;
        title: string;
        primary_category: string;
        reason: string;
      }>;
    }>("/api/collection/preview", {
      method: "POST",
      body: rules ? JSON.stringify(rules) : undefined,
    }),
  runCollection: (payload?: { max_downloads?: number; sync_to_drive?: boolean }) =>
    apiFetch<Record<string, unknown>>("/api/collection/run", {
      method: "POST",
      body: JSON.stringify(payload || { sync_to_drive: true }),
    }),
  pauseCollection: () => apiFetch<Record<string, unknown>>("/api/collection/pause", { method: "POST" }),
  resumeCollection: () => apiFetch<Record<string, unknown>>("/api/collection/resume", { method: "POST" }),
  cancelCollection: () => apiFetch<Record<string, unknown>>("/api/collection/cancel", { method: "POST" }),
  retryFailedCollection: (cycleId?: string) =>
    apiFetch<Record<string, unknown>>("/api/collection/retry-failed", {
      method: "POST",
      body: JSON.stringify({ cycle_id: cycleId, sync_to_drive: true }),
    }),

  // Quality & Benchmarks
  getQualityReports: () => apiFetch<QualityReportsResponse>("/api/quality/reports"),
  runBenchmark: (assetId: string) =>
    apiFetch<BenchmarkReport>(`/api/reference/benchmarks/run/${encodeURIComponent(assetId)}`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  approveRobloxAsset: (robloxAssetId: string, reviewer = "Local Reviewer", notes = "") =>
    apiFetch<Record<string, unknown>>(`/api/roblox/assets/${encodeURIComponent(robloxAssetId)}/approve`, {
      method: "POST",
      body: JSON.stringify({ reviewer, notes }),
    }),
  rejectRobloxAsset: (robloxAssetId: string, reviewer = "Local Reviewer", reason = "Rejected from Studio") =>
    apiFetch<Record<string, unknown>>(`/api/roblox/assets/${encodeURIComponent(robloxAssetId)}/reject`, {
      method: "POST",
      body: JSON.stringify({ reviewer, reason }),
    }),
  runRobloxQa: (robloxAssetId: string) =>
    apiFetch<Record<string, unknown>>(`/api/roblox/assets/${encodeURIComponent(robloxAssetId)}/qa`, {
      method: "POST",
      body: JSON.stringify({ render_previews: true }),
    }),

  // System Settings & Drive
  getGdriveAuthUrl: () => apiFetch<{ auth_url: string; redirect_uri: string }>("/api/gdrive/auth-url"),
  disconnectGdrive: () => apiFetch<{ status: string }>("/api/gdrive/disconnect", { method: "POST" }),
  listGdriveFolders: () =>
    apiFetch<{ folders: Array<{ id: string; name: string }> }>("/api/gdrive/folders"),
  selectGdriveFolder: (payload: { folder_id?: string | null; folder_name?: string | null }) =>
    apiFetch<Record<string, unknown>>("/api/gdrive/select-folder", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  reindexCatalogue: () =>
    apiFetch<{ assets_indexed: number; versions_indexed: number }>("/api/catalogue/reindex", {
      method: "POST",
    }),
  installWindowsTask: () =>
    apiFetch<Record<string, unknown>>("/api/reference/scheduler/windows-task/install", { method: "POST" }),
  uninstallWindowsTask: () =>
    apiFetch<Record<string, unknown>>("/api/reference/scheduler/windows-task/uninstall", { method: "POST" }),
};
