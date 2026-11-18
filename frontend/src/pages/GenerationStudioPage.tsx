import * as React from "react";
import {
  Check,
  ExternalLink,
  Loader2,
  Play,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Square,
  X,
} from "lucide-react";
import { GlbViewer } from "@/components/GlbViewer";
import { StageIcon, StatusBadge } from "@/components/StatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Select, Textarea } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { studioApi } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/utils";
import type { GenerationJob, GenerationTemplate, NavSection } from "@/types/studio";

export function GenerationStudioPage({
  onOpenAsset,
}: {
  onOpenAsset: (assetId: string, navTarget?: NavSection) => void;
}) {
  const [templates, setTemplates] = React.useState<GenerationTemplate[]>([]);
  const [categories, setCategories] = React.useState<string[]>([
    "Furniture",
    "Architecture",
    "Characters",
    "Mechanical Objects",
    "Vehicles",
    "Household Objects",
    "Nature",
    "Miscellaneous",
  ]);
  const [visualStyles, setVisualStyles] = React.useState<string[]>(["LP", "REALISTIC", "STYLIZED", "MINIMAL"]);
  const [complexities, setComplexities] = React.useState<string[]>(["low", "medium", "high"]);

  const [selectedTemplate, setSelectedTemplate] = React.useState<string>("");
  const [name, setName] = React.useState("");
  const [prompt, setPrompt] = React.useState(
    "Create a sturdy oak wooden dining chair with four tapered legs, a sculpted seat, and a curved slat backrest",
  );
  const [category, setCategory] = React.useState("Furniture");
  const [visualStyle, setVisualStyle] = React.useState("LP");
  const [complexity, setComplexity] = React.useState("medium");
  const [polyBudget, setPolyBudget] = React.useState<string>("16000");
  const [tagsText, setTagsText] = React.useState("procedural, studio-generated");
  const [additionalInstructions, setAdditionalInstructions] = React.useState("");

  const [submitting, setSubmitting] = React.useState(false);
  const [formError, setFormError] = React.useState<string | null>(null);
  const [formNotice, setFormNotice] = React.useState<string | null>(null);

  const [jobs, setJobs] = React.useState<GenerationJob[]>([]);
  const [statusFilter, setStatusFilter] = React.useState<string>("ALL");
  const [selectedJobId, setSelectedJobId] = React.useState<string | null>(null);
  const [jobActionBusy, setJobActionBusy] = React.useState<string | null>(null);

  const loadTemplates = React.useCallback(async () => {
    try {
      const res = await studioApi.getGenerationTemplates();
      setTemplates(res.templates);
      if (res.categories.length) setCategories(res.categories);
      if (res.visual_styles.length) setVisualStyles(res.visual_styles);
      if (res.complexities.length) setComplexities(res.complexities);
    } catch {
      // Keep default options if template fetch fails temporarily
    }
  }, []);

  const loadJobs = React.useCallback(async () => {
    try {
      const res = await studioApi.listGenerationJobs(60, statusFilter);
      setJobs(res.jobs);
      setSelectedJobId((prev) => {
        if (prev && res.jobs.some((j) => j.job_id === prev)) return prev;
        return res.jobs[0]?.job_id || null;
      });
    } catch {
      // Ignore transient polling error
    }
  }, [statusFilter]);

  React.useEffect(() => {
    void loadTemplates();
  }, [loadTemplates]);

  React.useEffect(() => {
    void loadJobs();
  }, [loadJobs]);

  // Real DB-backed polling (fast while jobs are QUEUED or RUNNING)
  const hasActiveJobs = jobs.some((j) => j.status === "QUEUED" || j.status === "RUNNING");
  React.useEffect(() => {
    const interval = window.setInterval(() => {
      void loadJobs();
    }, hasActiveJobs ? 1500 : 6000);
    return () => window.clearInterval(interval);
  }, [hasActiveJobs, loadJobs]);

  const applyTemplate = (templateId: string) => {
    setSelectedTemplate(templateId);
    const tpl = templates.find((t) => t.template_id === templateId);
    if (!tpl) return;
    const isTemplateLabel = templates.some((t) => t.label === name.trim());
    if (!name.trim() || isTemplateLabel) setName(tpl.label);
    setPrompt(tpl.default_prompt);
    setCategory(tpl.category);
    setPolyBudget(String(tpl.default_poly_budget));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    setFormNotice(null);
    if (!prompt.trim()) {
      setFormError("Please enter a detailed asset description.");
      return;
    }

    setSubmitting(true);
    try {
      const parsedBudget = polyBudget.trim() ? Number.parseInt(polyBudget.trim(), 10) : null;
      const res = await studioApi.createGenerationJob({
        prompt: prompt.trim(),
        name: name.trim() || undefined,
        category,
        tags: tagsText
          .split(",")
          .map((t) => t.trim())
          .filter(Boolean),
        template_id: selectedTemplate || undefined,
        visual_style: visualStyle,
        complexity,
        poly_budget: parsedBudget && Number.isFinite(parsedBudget) ? parsedBudget : null,
        additional_instructions: additionalInstructions.trim(),
      });
      setFormNotice(`Queued generation job ${res.job.job_id} for "${res.asset.name}".`);
      setSelectedJobId(res.job.job_id);
      await loadJobs();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to enqueue generation job.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleCancelJob = async (jobId: string) => {
    setJobActionBusy(jobId);
    try {
      await studioApi.cancelGenerationJob(jobId);
      await loadJobs();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to cancel job.");
    } finally {
      setJobActionBusy(null);
    }
  };

  const handleRetryJob = async (jobId: string) => {
    setJobActionBusy(jobId);
    try {
      await studioApi.retryGenerationJob(jobId);
      await loadJobs();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to retry job.");
    } finally {
      setJobActionBusy(null);
    }
  };

  const handleInlineDecision = async (assetId: string, version: number, decision: "APPROVED" | "REJECTED") => {
    setJobActionBusy(assetId);
    try {
      await studioApi.reviewAssetVersion(assetId, version, {
        decision,
        reviewer: "Studio Operator",
        notes: `${decision} directly from Generation Studio`,
      });
      await loadJobs();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : "Failed to submit approval decision.");
    } finally {
      setJobActionBusy(null);
    }
  };

  const selectedJob = jobs.find((j) => j.job_id === selectedJobId) || jobs[0] || null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Generation Studio</h1>
          <p className="text-xs text-muted-foreground">
            Initiate real headless Blender 3D asset generation jobs and monitor live backend QA stages.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void loadJobs()}>
          <RefreshCw className="h-3.5 w-3.5" />
          Refresh Queue
        </Button>
      </div>

      <div className="grid gap-4 xl:grid-cols-12">
        {/* Generation Form */}
        <Card className="xl:col-span-5">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-primary" />
              Single Asset Generation
            </CardTitle>
            <CardDescription>
              Uses the existing Blender procedural engine, automated Roblox QA, render views, and Drive vault registration.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={(e) => void handleSubmit(e)} className="space-y-3">
              <div>
                <label className="mb-1 block text-[11px] font-medium text-muted-foreground">
                  Quick Template Preset (Optional)
                </label>
                <div className="flex flex-wrap gap-1.5">
                  <button
                    type="button"
                    onClick={() => setSelectedTemplate("")}
                    className={`rounded border px-2 py-1 text-[11px] ${
                      selectedTemplate === ""
                        ? "border-primary bg-primary/15 text-primary"
                        : "border-border text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    Prompt Match
                  </button>
                  {templates.map((tpl) => (
                    <button
                      key={tpl.template_id}
                      type="button"
                      onClick={() => applyTemplate(tpl.template_id)}
                      className={`rounded border px-2 py-1 text-[11px] ${
                        selectedTemplate === tpl.template_id
                          ? "border-primary bg-primary/15 text-primary"
                          : "border-border text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      {tpl.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block text-[11px] font-medium text-muted-foreground">Asset Name</label>
                  <Input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="e.g. Carved Oak Chair"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-[11px] font-medium text-muted-foreground">Category</label>
                  <Select value={category} onChange={(e) => setCategory(e.target.value)}>
                    {categories.map((cat) => (
                      <option key={cat} value={cat}>
                        {cat}
                      </option>
                    ))}
                  </Select>
                </div>
              </div>

              <div>
                <label className="mb-1 block text-[11px] font-medium text-muted-foreground">
                  Detailed Asset Description
                </label>
                <Textarea
                  rows={3}
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder="Describe parts, silhouette, materials, and proportions..."
                />
              </div>

              <div className="grid gap-3 sm:grid-cols-3">
                <div>
                  <label className="mb-1 block text-[11px] font-medium text-muted-foreground">Visual Style</label>
                  <Select value={visualStyle} onChange={(e) => setVisualStyle(e.target.value)}>
                    {visualStyles.map((st) => (
                      <option key={st} value={st}>
                        {st}
                      </option>
                    ))}
                  </Select>
                </div>
                <div>
                  <label className="mb-1 block text-[11px] font-medium text-muted-foreground">
                    Geometry Complexity
                  </label>
                  <Select value={complexity} onChange={(e) => setComplexity(e.target.value)}>
                    {complexities.map((c) => (
                      <option key={c} value={c}>
                        {c.toUpperCase()}
                      </option>
                    ))}
                  </Select>
                </div>
                <div>
                  <label className="mb-1 block text-[11px] font-medium text-muted-foreground">
                    Polygon Budget (Tris)
                  </label>
                  <Input
                    type="number"
                    min={100}
                    max={500000}
                    step={1}
                    value={polyBudget}
                    onChange={(e) => setPolyBudget(e.target.value)}
                    placeholder="16000"
                  />
                </div>
              </div>

              <div>
                <label className="mb-1 block text-[11px] font-medium text-muted-foreground">
                  Additional Generation Instructions
                </label>
                <Textarea
                  rows={2}
                  value={additionalInstructions}
                  onChange={(e) => setAdditionalInstructions(e.target.value)}
                  placeholder="e.g. 5 backrest slats, walnut finish, keep clean quad bevels..."
                />
              </div>

              <div>
                <label className="mb-1 block text-[11px] font-medium text-muted-foreground">
                  Tags (comma-separated)
                </label>
                <Input
                  value={tagsText}
                  onChange={(e) => setTagsText(e.target.value)}
                  placeholder="furniture, chair, roblox"
                />
              </div>

              {formError ? (
                <div className="rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
                  {formError}
                </div>
              ) : null}
              {formNotice ? (
                <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-200">
                  {formNotice}
                </div>
              ) : null}

              <Button type="submit" className="w-full" size="lg" disabled={submitting}>
                {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                {submitting ? "Dispatching Blender Worker..." : "Start Asset Generation"}
              </Button>
            </form>
          </CardContent>
        </Card>

        {/* Live Job Queue + Selected Execution Inspector */}
        <div className="space-y-4 xl:col-span-7">
          <Card>
            <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
              <div>
                <CardTitle>Generation Job Queue</CardTitle>
                <CardDescription>Real backend execution status from SQLite job ledger</CardDescription>
              </div>
              <div className="flex flex-wrap gap-1">
                {["ALL", "QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"].map((st) => (
                  <button
                    key={st}
                    type="button"
                    onClick={() => setStatusFilter(st)}
                    className={`rounded px-2 py-1 text-[10px] font-semibold ${
                      statusFilter === st
                        ? "bg-primary/20 text-primary"
                        : "bg-background/60 text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    {st}
                  </button>
                ))}
              </div>
            </CardHeader>
            <CardContent className="p-0">
              <div className="max-h-72 overflow-y-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Asset / Prompt</TableHead>
                      <TableHead>Current Stage</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Updated</TableHead>
                      <TableHead className="text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {jobs.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={5} className="py-8 text-center text-muted-foreground">
                          No generation jobs found. Submit the form on the left to start your first asset.
                        </TableCell>
                      </TableRow>
                    ) : (
                      jobs.map((job) => {
                        const selected = selectedJob?.job_id === job.job_id;
                        const active = job.status === "QUEUED" || job.status === "RUNNING";
                        return (
                          <TableRow
                            key={job.job_id}
                            onClick={() => setSelectedJobId(job.job_id)}
                            className={`cursor-pointer ${selected ? "bg-primary/10" : ""}`}
                          >
                            <TableCell>
                              <div className="max-w-[220px]">
                                <p className="truncate font-medium">
                                  {job.asset_summary?.name || String(job.params?.name || job.job_id)}
                                </p>
                                <p className="truncate font-mono text-[10px] text-muted-foreground">
                                  {job.job_id} • {String(job.params?.category || job.asset_summary?.category || "—")}
                                </p>
                              </div>
                            </TableCell>
                            <TableCell>
                              <span className="font-mono text-[11px]">{job.stage || "queued"}</span>
                            </TableCell>
                            <TableCell>
                              <StatusBadge status={job.status} />
                            </TableCell>
                            <TableCell className="font-mono text-[10px] text-muted-foreground">
                              {formatDateTime(job.updated_at)}
                            </TableCell>
                            <TableCell className="text-right" onClick={(e) => e.stopPropagation()}>
                              <div className="inline-flex items-center gap-1">
                                {active ? (
                                  <Button
                                    variant="destructive"
                                    size="sm"
                                    disabled={jobActionBusy === job.job_id}
                                    onClick={() => void handleCancelJob(job.job_id)}
                                  >
                                    <Square className="h-3 w-3" />
                                    Cancel
                                  </Button>
                                ) : null}
                                {job.status === "FAILED" || job.status === "CANCELLED" ? (
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    disabled={jobActionBusy === job.job_id}
                                    onClick={() => void handleRetryJob(job.job_id)}
                                  >
                                    <RotateCcw className="h-3 w-3" />
                                    Retry
                                  </Button>
                                ) : null}
                                {job.asset_summary?.asset_id ? (
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    onClick={() => onOpenAsset(job.asset_summary!.asset_id, "vault")}
                                  >
                                    <ExternalLink className="h-3 w-3" />
                                    Vault
                                  </Button>
                                ) : null}
                              </div>
                            </TableCell>
                          </TableRow>
                        );
                      })
                    )}
                  </TableBody>
                </Table>
              </div>
            </CardContent>
          </Card>

          {selectedJob ? (
            <Card>
              <CardHeader className="flex-row flex-wrap items-center justify-between gap-2 space-y-0">
                <div>
                  <CardTitle className="flex items-center gap-2">
                    <span>{selectedJob.asset_summary?.name || selectedJob.job_id}</span>
                    <StatusBadge status={selectedJob.status} />
                  </CardTitle>
                  <CardDescription>{selectedJob.message}</CardDescription>
                </div>
                {selectedJob.asset_summary?.roblox_asset_id ? (
                  <Badge variant="outline" className="font-mono">
                    {selectedJob.asset_summary.roblox_asset_id}
                  </Badge>
                ) : null}
              </CardHeader>
              <CardContent className="space-y-4">
                {/* Discrete Pipeline Execution Stages (No Fake %) */}
                <div>
                  <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    Real Backend Pipeline Stages
                  </p>
                  <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    {(selectedJob.stages || []).map((st, idx) => (
                      <div
                        key={st.id || st.key || String(idx)}
                        className="flex items-start gap-2 rounded-md border border-border/70 bg-background/60 p-2"
                      >
                        <StageIcon status={st.status} />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center justify-between gap-1">
                            <span className="truncate text-[11px] font-medium">{st.label}</span>
                            <span className="font-mono text-[9px] text-muted-foreground">{st.status}</span>
                          </div>
                          {st.detail ? (
                            <p className="mt-0.5 line-clamp-2 text-[10px] text-muted-foreground">{st.detail}</p>
                          ) : null}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Output Preview & QA/Approval Handoff */}
                {selectedJob.asset_summary ? (
                  <div className="grid gap-4 lg:grid-cols-12">
                    <div className="space-y-2 lg:col-span-7">
                      <GlbViewer glbUrl={selectedJob.asset_summary.glb_url} heightClass="h-64" />
                      {selectedJob.asset_summary.preview_url ? (
                        <div className="overflow-hidden rounded-md border border-border bg-background/60 p-2">
                          <p className="mb-1 text-[10px] font-medium text-muted-foreground">
                            Multi-Angle Rendered Inspection Sheet
                          </p>
                          <img
                            src={selectedJob.asset_summary.preview_url}
                            alt={selectedJob.asset_summary.name}
                            className="max-h-40 w-full rounded object-contain"
                          />
                        </div>
                      ) : null}
                    </div>
                    <div className="space-y-3 lg:col-span-5">
                      <div className="rounded-md border border-border bg-background/60 p-3 text-xs space-y-2">
                        <div className="flex items-center justify-between">
                          <span className="text-muted-foreground">Vault Status</span>
                          <StatusBadge status={selectedJob.asset_summary.status} />
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="text-muted-foreground">Technical QA</span>
                          <StatusBadge status={selectedJob.asset_summary.tech_qa_status || "PENDING"} />
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="text-muted-foreground">Visual QA Score</span>
                          <span className="font-mono font-semibold">
                            {selectedJob.asset_summary.qa_score !== null &&
                            selectedJob.asset_summary.qa_score !== undefined
                              ? `${selectedJob.asset_summary.qa_score}/100`
                              : "—"}
                          </span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="text-muted-foreground">Triangle / Poly Count</span>
                          <span className="font-mono">{formatNumber(selectedJob.asset_summary.poly_count)}</span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="text-muted-foreground">Human Approval</span>
                          <StatusBadge status={selectedJob.asset_summary.human_decision || "PENDING"} />
                        </div>
                      </div>

                      {selectedJob.asset_summary.status === "AWAITING_HUMAN_APPROVAL" ? (
                        <div className="flex gap-2">
                          <Button
                            className="flex-1"
                            size="sm"
                            disabled={jobActionBusy === selectedJob.asset_summary.asset_id}
                            onClick={() =>
                              void handleInlineDecision(
                                selectedJob.asset_summary!.asset_id,
                                selectedJob.asset_summary!.current_version,
                                "APPROVED",
                              )
                            }
                          >
                            <Check className="h-3.5 w-3.5" />
                            Approve
                          </Button>
                          <Button
                            variant="destructive"
                            className="flex-1"
                            size="sm"
                            disabled={jobActionBusy === selectedJob.asset_summary.asset_id}
                            onClick={() =>
                              void handleInlineDecision(
                                selectedJob.asset_summary!.asset_id,
                                selectedJob.asset_summary!.current_version,
                                "REJECTED",
                              )
                            }
                          >
                            <X className="h-3.5 w-3.5" />
                            Reject
                          </Button>
                        </div>
                      ) : null}

                      <Button
                        variant="outline"
                        size="sm"
                        className="w-full"
                        onClick={() => onOpenAsset(selectedJob.asset_summary!.asset_id, "vault")}
                      >
                        <ShieldCheck className="h-3.5 w-3.5" />
                        Inspect Full QA & Drive Sync in Vault
                      </Button>
                    </div>
                  </div>
                ) : null}
              </CardContent>
            </Card>
          ) : null}
        </div>
      </div>
    </div>
  );
}
