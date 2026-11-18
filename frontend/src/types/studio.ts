export type NavSection =
  | "overview"
  | "generation"
  | "vault"
  | "references"
  | "collection"
  | "quality"
  | "settings";

export interface StageStep {
  id?: string;
  key?: string;
  label: string;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED" | "SKIPPED" | string;
  detail?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at?: string | null;
}

export interface SystemHealth {
  backend: {
    ok: boolean;
    status: string;
    timestamp: string;
  };
  blender: {
    available: boolean;
    version?: string | null;
    executable?: string | null;
    meets_minimum?: boolean;
    error?: string | null;
  };
  google_drive: {
    client_secrets_configured: boolean;
    client_secrets_path: string;
    token_present: boolean;
    authenticated: boolean;
    user_email?: string | null;
    scopes: string[];
    root_folder_id?: string | null;
    root_folder_name?: string | null;
    error?: string | null;
  };
  blendswap: {
    key_configured: boolean;
    allow_credit_spend: boolean;
    connectivity: {
      connected?: boolean;
      error?: string | null;
      username?: string | null;
      plan?: string | null;
      credits?: number;
      free_downloads_remaining?: number;
      free_downloads_limit?: number;
      checked?: boolean;
    };
    api_usage?: Record<string, unknown>;
  };
  scheduler: {
    enabled: boolean;
    state?: string;
    frequency?: string;
    collection_time?: string;
    timezone?: string;
    target_downloads?: number;
    resume_missed_jobs?: boolean;
    free_downloads_only?: boolean;
    automatic_credit_spending?: boolean;
    next_run_at?: string | null;
    last_run_at?: string | null;
    last_error?: string | null;
    is_running?: boolean;
    windows_task?: Record<string, unknown>;
  };
  vision_qa: {
    configured: boolean;
    provider: string;
    gemini_configured: boolean;
    openai_configured: boolean;
    gemini_model?: string;
    openai_model?: string;
    ollama_model?: string;
  };
  databases: {
    vault: { ok: boolean; path: string; assets_count: number; jobs_count: number };
    reference: { ok: boolean; path: string; assets_count: number };
    roblox: { ok: boolean; path: string; assets_count: number };
  };
  storage_paths: {
    vault_dir: string;
    reference_library_dir: string;
    roblox_reports_dir: string;
  };
}

export interface AssetSummaryInJob {
  asset_id: string;
  name: string;
  category: string;
  roblox_asset_id?: string | null;
  current_version: number;
  status: string;
  qa_score?: number | null;
  tech_qa_status?: string | null;
  visual_qa_status?: string | null;
  human_decision?: string | null;
  drive_sync_status?: string | null;
  preview_url?: string | null;
  glb_url?: string | null;
  poly_count: number;
}

export interface GenerationJob {
  job_id: string;
  asset_id?: string | null;
  job_type: string;
  status: string;
  stage: string;
  message: string;
  params: Record<string, unknown>;
  stages: StageStep[];
  started_at?: string | null;
  completed_at?: string | null;
  cancel_requested?: boolean;
  created_at: string;
  updated_at: string;
  asset_summary?: AssetSummaryInJob;
}

export interface GenerationTemplate {
  template_id: string;
  label: string;
  category: string;
  default_prompt: string;
  default_poly_budget: number;
}

export interface DashboardOverview {
  system_status: SystemHealth;
  generation: {
    total_generated: number;
    awaiting_qa: number;
    awaiting_human_approval: number;
    approved: number;
    failed: number;
    active_jobs_count: number;
    active_jobs: GenerationJob[];
    recent_jobs: GenerationJob[];
    status_breakdown: Record<string, number>;
    category_breakdown: Record<string, number>;
  };
  reference_collection: {
    today_date: string;
    today_downloaded: number;
    today_failed: number;
    daily_target: number;
    free_downloads_remaining: number;
    free_downloads_limit: number;
    credits: number;
    allow_credit_spend: boolean;
    next_collection_cycle?: string | null;
    latest_cycle?: CollectionCycle | null;
    recent_cycles?: CollectionCycle[];
    total_successful_downloads?: number;
    total_failed_downloads?: number;
    control: CollectionControlState;
  };
  quality: {
    average_generation_qa_score?: number | null;
    average_reference_score?: number | null;
    average_benchmark_score?: number | null;
    reference_library_size: number;
    benchmark_count: number;
    roblox_assets_count: number;
    reference_grade_distribution: Record<string, number>;
    recent_benchmarks: BenchmarkReport[];
  };
  recent_activity: Array<{
    timestamp: string;
    source: string;
    type: string;
    status: string;
    title: string;
    message: string;
    asset_id?: string;
    cycle_id?: string;
  }>;
}

export interface TechnicalQACheck {
  name: string;
  passed: boolean;
  severity: string;
  detail: string;
}

export interface TechnicalQAReport {
  status: string;
  geometry_profile: string;
  blender_version: string;
  file_opened_ok: boolean;
  object_count: number;
  mesh_object_count: number;
  total_vertices: number;
  total_edges: number;
  total_faces: number;
  total_tris: number;
  dimensions: number[];
  materials: string[];
  collections: string[];
  missing_dependencies: string[];
  warnings_count: number;
  failures_count: number;
  summary: string;
  checks?: TechnicalQACheck[];
  inspected_at: string;
}

export interface VisualQAReport {
  provider: string;
  model: string;
  status: string;
  score?: number | null;
  confidence?: number | null;
  recommendation?: string | null;
  summary: string;
  matches_description?: boolean | null;
  issues_found?: string[];
  render_paths?: string[] | Record<string, string>;
  turntable_path?: string | null;
  contact_sheet_path?: string | null;
  render_sanity_ok?: boolean;
  analyzed_at?: string;
}

export interface AssetVersion {
  asset_id: string;
  version: number;
  status: string;
  blend_path?: string | null;
  glb_path?: string | null;
  source_original_path?: string | null;
  blender_version?: string | null;
  file_formats: string[];
  file_size_bytes: number;
  sha256?: string | null;
  poly_count: number;
  vertex_count: number;
  dimensions: number[];
  materials: string[];
  technical_qa?: TechnicalQAReport | null;
  visual_qa?: VisualQAReport | null;
  human_review?: {
    status: string;
    reviewer?: string | null;
    notes: string;
    reviewed_at?: string | null;
  } | null;
  drive_sync_status?: string | null;
  drive_folder_id?: string | null;
  drive_files?: Array<{
    role: string;
    remote_name: string;
    remote_file_id: string;
    web_view_link?: string;
    verified?: boolean;
  }>;
  error_message?: string | null;
  failed_stage?: string | null;
  created_at: string;
  updated_at: string;
}

export interface VaultAsset {
  asset_id: string;
  slug: string;
  name: string;
  description: string;
  category: string;
  geometry_profile: string;
  tags: string[];
  provenance: {
    provenance_type: string;
    author: string;
    source_url?: string | null;
    license_name: string;
    usage_restrictions?: string;
    notes?: string;
  };
  current_version: number;
  roblox_asset_id?: string | null;
  created_at: string;
  updated_at: string;
  latest?: AssetVersion | null;
  versions?: AssetVersion[];
}

export interface ReferenceAsset {
  blendswap_id: string;
  title: string;
  url: string;
  description: string;
  author: {
    username: string;
    profile_url?: string;
  };
  license_assessment: {
    license_key: string;
    license_name?: string;
    requires_attribution: boolean;
    non_commercial_only?: boolean;
    share_alike_obligation?: boolean;
    verification_disclaimer: string;
  };
  blendswap_category: string;
  primary_category: string;
  technical_categories: string[];
  tags: string[];
  blender_versions: string[];
  counts: {
    downloads: number;
    likes: number;
    views: number;
  };
  primary_format: string;
  is_native_blender: boolean;
  file_size_bytes: number;
  status: string;
  error_message?: string | null;
  popularity_score: number;
  selection_score: number;
  technical_quality_score?: number | null;
  reference_usefulness_score?: number | null;
  grade_letter?: string | null;
  drive_sync_status?: string | null;
  downloaded_at?: string | null;
  created_at: string;
  poly_complexity?: string;
  modeling_styles?: string[];
  date_collected?: string;
  preview_urls?: string[];
  preview_paths?: string[];
  inspection?: {
    source_format: string;
    blend_header_version?: string | null;
    blender_runtime_version?: string | null;
    file_opened_ok: boolean;
    object_count: number;
    mesh_object_count: number;
    vertex_count: number;
    face_count: number;
    polygon_count: number;
    triangle_count: number;
    is_manifold: boolean;
    quad_ratio: number;
    ngon_ratio: number;
    subdivision_ready: boolean;
    has_uv_map: boolean;
    uv_coverage_ratio: number;
    uv_overlapping_islands_detected: boolean;
    material_count: number;
    material_names: string[];
    principled_bsdf_count: number;
    procedural_shader_count: number;
    missing_image_textures: number;
    has_rig: boolean;
    bone_count: number;
    collection_count: number;
    dimensions: number[];
  } | null;
  grade?: {
    total_score: number;
    grade_letter: string;
    technical_quality_score: number;
    reference_usefulness_score: number;
    dimensions: Record<string, number>;
    strengths: string[];
    weaknesses: string[];
  } | null;
}

export interface CollectionRules {
  daily_target: number;
  preferred_categories: string[];
  excluded_categories: string[];
  preferred_licenses: string[];
  min_downloads: number;
  preferred_modeling_styles: string[];
  preferred_poly_complexity: string;
  preferred_file_format: string;
  diversity_enabled: boolean;
  max_per_category_per_cycle: number;
  collection_time: string;
  timezone: string;
  excluded_candidate_ids: string[];
  scope?: string;
}

export interface CollectionRulesResponse {
  active_rules: CollectionRules;
  permanent_rules: CollectionRules;
  next_run_override?: CollectionRules | null;
  has_next_run_override: boolean;
  default_rules: CollectionRules;
  options?: {
    categories: string[];
    licenses: string[];
    modeling_styles: string[];
    poly_complexities: string[];
    file_formats: string[];
    timezones: string[];
  };
}

export interface CollectionCycle {
  cycle_id: string;
  cycle_date: string;
  timezone: string;
  trigger_type: string;
  status: string;
  target_count: number;
  free_allowance_start?: number | null;
  free_allowance_end?: number | null;
  credits_spent: number;
  downloaded_count: number;
  failed_count: number;
  shortage_count: number;
  shortage_reason?: string | null;
  error_message?: string | null;
  current_stage: string;
  stages: StageStep[];
  paused: boolean;
  cancel_requested: boolean;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at: string;
}

export interface CollectionControlState {
  is_running: boolean;
  active_cycle_id?: string | null;
  paused: boolean;
  cancel_requested: boolean;
  current_stage?: string | null;
  stages: StageStep[];
}

export interface CollectionQueueItem {
  item_id: string;
  cycle_id: string;
  blendswap_id: string;
  title: string;
  primary_category: string;
  license_key: string;
  primary_format: string;
  selection_score: number;
  status: string;
  retry_count: number;
  error_message?: string | null;
}

export interface CollectionStatusResponse {
  control: CollectionControlState;
  today_cycle?: CollectionCycle | null;
  queue: CollectionQueueItem[];
  scheduler: SystemHealth["scheduler"];
  overview: DashboardOverview["reference_collection"];
  rules: CollectionRulesResponse;
}

export interface BenchmarkReport {
  benchmark_id: string;
  branch1_asset_id: string;
  branch1_version: number;
  branch1_name: string;
  branch1_category: string;
  local_grade?: {
    total_score: number;
    grade_letter: string;
    dimensions?: Record<string, number>;
  };
  selected_references: Array<{
    blendswap_id: string;
    title: string;
    grade_letter?: string;
    technical_quality_score?: number;
    triangle_count?: number;
    match_reason?: string;
  }>;
  geometry_differences: string[];
  topology_weaknesses: string[];
  uv_weaknesses: string[];
  material_weaknesses: string[];
  recommended_improvements: string[];
  efficiency_note: string;
  created_at: string;
}

export interface QualityReportsResponse {
  branch1_assets: Array<{
    asset_id: string;
    name: string;
    category: string;
    status: string;
    roblox_asset_id?: string | null;
    poly_count: number;
    tech_qa_status: string;
    visual_qa_status: string;
    visual_score?: number | null;
    benchmark_score?: number | null;
    grade: string;
    preview_url?: string | null;
    updated_at: string;
  }>;
  roblox_reports: Array<{
    asset_id: string;
    legacy_vault_id?: string | null;
    asset_name: string;
    visual_style: string;
    production_status: string;
    qa_score?: number | null;
    qa_status?: string | null;
    qa_grade?: string | null;
    triangle_count: number;
    human_approved: boolean;
    report_id?: number | null;
    has_html_report: boolean;
    has_json_report: boolean;
    dimension_scores?: Record<string, number> | null;
    updated_at: string;
  }>;
  benchmarks: BenchmarkReport[];
  reference_assets: Array<{
    blendswap_id: string;
    title: string;
    category: string;
    author: string;
    grade_letter?: string | null;
    technical_quality_score?: number | null;
    usefulness_score?: number | null;
    triangle_count: number;
    quad_ratio: number;
    uv_coverage_ratio: number;
  }>;
  category_comparisons: Array<{
    category: string;
    generated_count: number;
    reference_count: number;
    generated_avg_score?: number | null;
    reference_avg_score?: number | null;
    generated_avg_tris?: number | null;
    reference_avg_tris?: number | null;
  }>;
  grade_distribution: {
    generated: Record<string, number>;
    reference: Record<string, number>;
  };
}
