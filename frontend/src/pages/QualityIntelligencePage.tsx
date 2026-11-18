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
import { Award, CheckCircle2, ExternalLink, RefreshCw, ShieldCheck, XCircle } from "lucide-react";
import { StatusBadge } from "@/components/StatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs } from "@/components/ui/controls";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { studioApi } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/utils";
import type { NavSection, QualityReportsResponse } from "@/types/studio";

export function QualityIntelligencePage({
  onOpenAsset,
}: {
  onOpenAsset: (assetId: string, navTarget?: NavSection) => void;
}) {
  const [data, setData] = React.useState<QualityReportsResponse | null>(null);
  const [tab, setTab] = React.useState<"roblox" | "benchmarks" | "references">("roblox");
  const [loading, setLoading] = React.useState(true);
  const [busyId, setBusyId] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    try {
      const res = await studioApi.getQualityReports();
      setData(res);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Failed to load quality intelligence.");
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  const handleAction = async (key: string, fn: () => Promise<unknown>, successText: string) => {
    setBusyId(key);
    try {
      await fn();
      setNotice(successText);
      await load();
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Action failed.");
    } finally {
      setBusyId(null);
    }
  };

  if (loading && !data) {
    return <div className="p-6 text-xs text-muted-foreground">Loading Quality Intelligence...</div>;
  }

  const gradeChart = ["A", "B", "C", "D", "F"].map((grade) => ({
    grade,
    Generated: data?.grade_distribution?.generated?.[grade] ?? 0,
    Reference: data?.grade_distribution?.reference?.[grade] ?? 0,
  }));

  const categoryChart = (data?.category_comparisons || []).map((c) => ({
    category: c.category,
    Generated: c.generated_avg_score ?? 0,
    Reference: c.reference_avg_score ?? 0,
  }));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Quality Intelligence</h1>
          <p className="text-xs text-muted-foreground">
            Compare generated Branch 1 / Roblox 100-point QA inspections directly against BlendSwap reference grades.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Tabs
            value={tab}
            onChange={setTab}
            items={[
              { id: "roblox", label: "Roblox & Generated QA", badge: data?.roblox_reports.length ?? 0 },
              { id: "benchmarks", label: "Benchmarks", badge: data?.benchmarks.length ?? 0 },
              { id: "references", label: "Reference Quality", badge: data?.reference_assets.length ?? 0 },
            ]}
          />
          <Button variant="outline" size="sm" onClick={() => void load()}>
            <RefreshCw className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      {notice ? (
        <div className="flex items-center justify-between rounded-md border border-border bg-card px-3 py-2 text-xs">
          <span>{notice}</span>
          <button type="button" onClick={() => setNotice(null)} className="opacity-70 hover:opacity-100">
            Close
          </button>
        </div>
      ) : null}

      {/* Charts */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Grade Distribution: Generated vs Reference</CardTitle>
            <CardDescription>Quality letter spread across Branch 1/Roblox QA and Branch 2 references</CardDescription>
          </CardHeader>
          <CardContent className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={gradeChart} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#242b38" />
                <XAxis dataKey="grade" tick={{ fill: "#8f9bb3", fontSize: 11 }} />
                <YAxis allowDecimals={false} tick={{ fill: "#8f9bb3", fontSize: 10 }} />
                <Tooltip contentStyle={{ backgroundColor: "#141821", borderColor: "#283040", fontSize: 11 }} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar dataKey="Generated" fill="#f58220" radius={[4, 4, 0, 0]} />
                <Bar dataKey="Reference" fill="#34d399" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Category Avg QA Score Comparison</CardTitle>
            <CardDescription>Average technical score by asset category</CardDescription>
          </CardHeader>
          <CardContent className="h-56">
            {categoryChart.length === 0 ? (
              <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
                No category comparisons available yet.
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={categoryChart} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#242b38" />
                  <XAxis dataKey="category" tick={{ fill: "#8f9bb3", fontSize: 10 }} />
                  <YAxis domain={[0, 100]} tick={{ fill: "#8f9bb3", fontSize: 10 }} />
                  <Tooltip contentStyle={{ backgroundColor: "#141821", borderColor: "#283040", fontSize: 11 }} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Bar dataKey="Generated" fill="#f58220" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="Reference" fill="#38bdf8" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>
      </div>

      {tab === "roblox" ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-primary" />
              100-Point Roblox & Blender QA Reports
            </CardTitle>
            <CardDescription>Inspect permanent Roblox IDs, open full HTML QA reports, or execute Stage 2 approval</CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Roblox Asset ID</TableHead>
                  <TableHead>Name</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>QA Score / Grade</TableHead>
                  <TableHead>Tris</TableHead>
                  <TableHead className="text-right">Actions & Reports</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {!data?.roblox_reports.length ? (
                  <TableRow>
                    <TableCell colSpan={6} className="py-8 text-center text-muted-foreground">
                      No Roblox QA reports recorded yet.
                    </TableCell>
                  </TableRow>
                ) : (
                  data.roblox_reports.map((rep) => (
                    <TableRow key={rep.asset_id}>
                      <TableCell className="font-mono text-[11px]">{rep.asset_id}</TableCell>
                      <TableCell>
                        <p className="font-medium">{rep.asset_name}</p>
                        <p className="text-[10px] text-muted-foreground">{rep.visual_style}</p>
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={rep.production_status} />
                      </TableCell>
                      <TableCell className="font-mono">
                        {rep.qa_score !== null && rep.qa_score !== undefined ? `${rep.qa_score.toFixed(1)}/100` : "—"}{" "}
                        {rep.qa_grade ? <Badge variant="default">Grade {rep.qa_grade}</Badge> : null}
                      </TableCell>
                      <TableCell className="font-mono">{formatNumber(rep.triangle_count)}</TableCell>
                      <TableCell className="text-right">
                        <div className="inline-flex flex-wrap items-center justify-end gap-1">
                          {rep.has_html_report ? (
                            <a
                              href={`/api/roblox/assets/${encodeURIComponent(rep.asset_id)}/report/html`}
                              target="_blank"
                              rel="noreferrer"
                              className="inline-flex h-7 items-center gap-1 rounded border border-border px-2 text-[11px] hover:bg-accent"
                            >
                              HTML Report <ExternalLink className="h-3 w-3" />
                            </a>
                          ) : null}
                          {rep.has_json_report ? (
                            <a
                              href={`/api/roblox/assets/${encodeURIComponent(rep.asset_id)}/report/json`}
                              target="_blank"
                              rel="noreferrer"
                              className="inline-flex h-7 items-center gap-1 rounded border border-border px-2 text-[11px] hover:bg-accent"
                            >
                              JSON
                            </a>
                          ) : null}
                          {rep.legacy_vault_id ? (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => onOpenAsset(rep.legacy_vault_id!, "vault")}
                            >
                              Vault
                            </Button>
                          ) : null}
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={busyId === rep.asset_id}
                            onClick={() =>
                              void handleAction(
                                rep.asset_id,
                                () => studioApi.runRobloxQa(rep.asset_id),
                                `Re-ran Roblox QA on ${rep.asset_id}`,
                              )
                            }
                          >
                            Re-QA
                          </Button>
                          {!rep.human_approved ? (
                            <>
                              <Button
                                size="sm"
                                disabled={busyId === rep.asset_id}
                                onClick={() =>
                                  void handleAction(
                                    rep.asset_id,
                                    () => studioApi.approveRobloxAsset(rep.asset_id),
                                    `Approved ${rep.asset_id}`,
                                  )
                                }
                              >
                                <CheckCircle2 className="h-3 w-3" />
                              </Button>
                              <Button
                                variant="destructive"
                                size="sm"
                                disabled={busyId === rep.asset_id}
                                onClick={() =>
                                  void handleAction(
                                    rep.asset_id,
                                    () => studioApi.rejectRobloxAsset(rep.asset_id),
                                    `Rejected ${rep.asset_id}`,
                                  )
                                }
                              >
                                <XCircle className="h-3 w-3" />
                              </Button>
                            </>
                          ) : null}
                        </div>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      ) : null}

      {tab === "benchmarks" ? (
        <div className="space-y-3">
          {!data?.benchmarks.length ? (
            <Card>
              <CardContent className="py-10 text-center text-xs text-muted-foreground">
                No reference benchmarks run yet. Open any Branch 1 asset in Asset Vault and click "Benchmark vs Ref".
              </CardContent>
            </Card>
          ) : (
            data.benchmarks.map((b) => (
              <Card key={b.benchmark_id}>
                <CardContent className="space-y-2 p-4 text-xs">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <Award className="h-4 w-4 text-primary" />
                      <span className="font-semibold">{b.branch1_name}</span>
                      <Badge variant="outline">{b.branch1_category}</Badge>
                      <Badge variant="default">
                        {b.local_grade?.total_score?.toFixed(1) ?? "—"}/100 ({b.local_grade?.grade_letter ?? "?"})
                      </Badge>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[10px] text-muted-foreground">{formatDateTime(b.created_at)}</span>
                      <Button size="sm" variant="outline" onClick={() => onOpenAsset(b.branch1_asset_id, "vault")}>
                        Open Asset
                      </Button>
                    </div>
                  </div>
                  <p className="text-emerald-200">{b.efficiency_note}</p>
                  {b.recommended_improvements.length > 0 ? (
                    <ul className="list-disc space-y-0.5 pl-4 text-muted-foreground">
                      {b.recommended_improvements.map((imp, idx) => (
                        <li key={idx}>{imp}</li>
                      ))}
                    </ul>
                  ) : null}
                </CardContent>
              </Card>
            ))
          )}
        </div>
      ) : null}

      {tab === "references" ? (
        <Card>
          <CardHeader>
            <CardTitle>Reference Library Quality Leaderboard</CardTitle>
            <CardDescription>Technical quality, quad topology ratio, and UV utilization across collected references</CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Asset</TableHead>
                  <TableHead>Category</TableHead>
                  <TableHead>Author</TableHead>
                  <TableHead>Grade / Score</TableHead>
                  <TableHead>Tris</TableHead>
                  <TableHead>Quad Ratio</TableHead>
                  <TableHead>UV Coverage</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {!data?.reference_assets.length ? (
                  <TableRow>
                    <TableCell colSpan={7} className="py-8 text-center text-muted-foreground">
                      No graded reference assets found yet.
                    </TableCell>
                  </TableRow>
                ) : (
                  data.reference_assets.map((r) => (
                    <TableRow key={r.blendswap_id}>
                      <TableCell>
                        <p className="font-medium">{r.title}</p>
                        <p className="font-mono text-[10px] text-muted-foreground">#{r.blendswap_id}</p>
                      </TableCell>
                      <TableCell>{r.category}</TableCell>
                      <TableCell>@{r.author}</TableCell>
                      <TableCell className="font-mono">
                        {r.grade_letter ? <StatusBadge status={r.grade_letter} label={`Grade ${r.grade_letter}`} /> : "—"}{" "}
                        {r.technical_quality_score?.toFixed(1) ?? "—"}
                      </TableCell>
                      <TableCell className="font-mono">{formatNumber(r.triangle_count)}</TableCell>
                      <TableCell className="font-mono">{(r.quad_ratio * 100).toFixed(1)}%</TableCell>
                      <TableCell className="font-mono">{(r.uv_coverage_ratio * 100).toFixed(0)}%</TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
