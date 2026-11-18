import * as React from "react";
import {
  Cpu,
  Database,
  ExternalLink,
  FolderCheck,
  HardDrive,
  RefreshCw,
  Shield,
  Sparkles,
  Trash2,
  Wifi,
} from "lucide-react";
import { ConfirmDialog, StatusBadge } from "@/components/StatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Select } from "@/components/ui/input";
import { studioApi } from "@/lib/api";
import type { SystemHealth } from "@/types/studio";

export function SystemSettingsPage() {
  const [health, setHealth] = React.useState<SystemHealth | null>(null);
  const [folders, setFolders] = React.useState<Array<{ id: string; name: string }>>([]);
  const [newFolderName, setNewFolderName] = React.useState("Blender Asset Vault");
  const [selectedFolderId, setSelectedFolderId] = React.useState("");
  const [manualFolderInput, setManualFolderInput] = React.useState("");
  const [busy, setBusy] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<{ type: "ok" | "err"; text: string } | null>(null);
  const [confirmAction, setConfirmAction] = React.useState<{
    open: boolean;
    title: string;
    desc: string;
    label: string;
    variant: "default" | "destructive";
    fn: () => Promise<void>;
  } | null>(null);

  const loadHealth = React.useCallback(async (checkLive = false) => {
    try {
      const res = await studioApi.getSystemHealth(checkLive);
      setHealth(res);
      if (res.google_drive.authenticated) {
        try {
          const fl = await studioApi.listGdriveFolders();
          setFolders(fl.folders || []);
        } catch {
          // Ignore folder listing failure when offline
        }
      }
    } catch (err) {
      setNotice({ type: "err", text: err instanceof Error ? err.message : "Failed to fetch system diagnostics." });
    }
  }, []);

  React.useEffect(() => {
    void loadHealth(false);
  }, [loadHealth]);

  const runAction = async (key: string, fn: () => Promise<void>) => {
    setBusy(key);
    try {
      await fn();
      await loadHealth(false);
    } catch (err) {
      setNotice({ type: "err", text: err instanceof Error ? err.message : "Operation failed." });
    } finally {
      setBusy(null);
    }
  };

  const handleConnectDrive = async () => {
    await runAction("connect-drive", async () => {
      const { auth_url } = await studioApi.getGdriveAuthUrl();
      window.open(auth_url, "_blank", "noopener,noreferrer");
      setNotice({
        type: "ok",
        text: "Opened Google OAuth 2.0 consent screen in a new tab. Complete authorization and click Refresh.",
      });
    });
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">System Settings & Diagnostics</h1>
          <p className="text-xs text-muted-foreground">
            Verify Blender worker runtime, Google Drive `drive.file` folder tree, BlendSwap connectivity, and Windows scheduler.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => void loadHealth(false)}>
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh
          </Button>
          <Button variant="outline" size="sm" onClick={() => void loadHealth(true)}>
            <Wifi className="h-3.5 w-3.5" />
            Verify Live Services
          </Button>
        </div>
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
            Close
          </button>
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        {/* Google Drive Setup */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <HardDrive className="h-4 w-4 text-sky-400" />
              Google Drive Official API v3
            </CardTitle>
            <CardDescription>
              Least-privilege `drive.file` scope — creates and indexes an app-managed vault folder tree.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-xs">
            <div className="grid grid-cols-2 gap-2 rounded-md border border-border bg-background/60 p-3">
              <div>
                <p className="text-[10px] text-muted-foreground">Client Secrets</p>
                <StatusBadge
                  status={health?.google_drive.client_secrets_configured ? "COMPLETED" : "ERROR"}
                  label={health?.google_drive.client_secrets_configured ? "FOUND" : "MISSING"}
                />
              </div>
              <div>
                <p className="text-[10px] text-muted-foreground">OAuth Status</p>
                <StatusBadge
                  status={health?.google_drive.authenticated ? "CONNECTED" : "PENDING"}
                  label={
                    health?.google_drive.authenticated
                      ? health.google_drive.user_email || "CONNECTED"
                      : "DISCONNECTED"
                  }
                />
              </div>
              <div className="col-span-2 pt-1">
                <p className="text-[10px] text-muted-foreground">Current Root Folder</p>
                <p className="font-mono text-[11px]">
                  {health?.google_drive.root_folder_name || "None"} (
                  {health?.google_drive.root_folder_id || "not selected"})
                </p>
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              <Button size="sm" disabled={busy !== null} onClick={() => void handleConnectDrive()}>
                <ExternalLink className="h-3.5 w-3.5" />
                Connect Google Drive (OAuth 2.0)
              </Button>
              {health?.google_drive.token_present ? (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy !== null}
                  onClick={() =>
                    setConfirmAction({
                      open: true,
                      title: "Disconnect Google Drive Token?",
                      desc: "Removes stored encrypted OAuth credentials locally. Local Vault & SQLite files remain untouched.",
                      label: "Disconnect",
                      variant: "destructive",
                      fn: async () => {
                        await studioApi.disconnectGdrive();
                        setNotice({ type: "ok", text: "Disconnected Google Drive credentials." });
                      },
                    })
                  }
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  Disconnect
                </Button>
              ) : null}
            </div>

            <div className="space-y-2 rounded-md border border-border/70 p-3">
              <p className="font-medium">Option 1: Create & Initialize App-Managed Vault Folder</p>
              <div className="flex gap-2">
                <Input
                  value={newFolderName}
                  onChange={(e) => setNewFolderName(e.target.value)}
                  placeholder="Blender Asset Vault"
                />
                <Button
                  size="sm"
                  disabled={busy !== null || !newFolderName.trim()}
                  onClick={() =>
                    void runAction("create-folder", async () => {
                      const res = await studioApi.selectGdriveFolder({ folder_name: newFolderName.trim() });
                      setNotice({
                        type: "ok",
                        text: `Initialized Google Drive folder (${String(res.root_folder_id || "OK")}).`,
                      });
                    })
                  }
                >
                  <FolderCheck className="h-3.5 w-3.5" />
                  Initialize Folder
                </Button>
              </div>

              <p className="pt-2 font-medium">Or Choose Existing App-Created Folder / Paste Folder ID or URL</p>
              <div className="grid gap-2 sm:grid-cols-2">
                <Select
                  value={selectedFolderId}
                  onChange={(e) => {
                    setSelectedFolderId(e.target.value);
                    if (e.target.value) setManualFolderInput(e.target.value);
                  }}
                >
                  <option value="">-- App-created Root Folders --</option>
                  {folders.map((f) => (
                    <option key={f.id} value={f.id}>
                      {f.name} ({f.id})
                    </option>
                  ))}
                </Select>
                <div className="flex gap-2">
                  <Input
                    value={manualFolderInput}
                    onChange={(e) => setManualFolderInput(e.target.value)}
                    placeholder="Folder ID or drive.google.com URL"
                  />
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy !== null || !manualFolderInput.trim()}
                    onClick={() =>
                      void runAction("select-folder", async () => {
                        await studioApi.selectGdriveFolder({ folder_id: manualFolderInput.trim() });
                        setNotice({ type: "ok", text: "Selected existing Drive root folder cleanly." });
                      })
                    }
                  >
                    Set
                  </Button>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Blender, Vision QA, BlendSwap & Scheduler */}
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Cpu className="h-4 w-4 text-primary" />
                Blender Runtime & Vision QA
              </CardTitle>
              <CardDescription>Headless Blender execution & optional multimodal QA provider</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2.5 text-xs">
              <div className="flex items-center justify-between rounded border border-border bg-background/60 p-2.5">
                <div>
                  <p className="font-medium">Blender Executable ({health?.blender.version || "—"})</p>
                  <p className="font-mono text-[10px] text-muted-foreground">
                    {health?.blender.executable || "Not configured"}
                  </p>
                </div>
                <StatusBadge status={health?.blender.available ? "CONNECTED" : "ERROR"} />
              </div>
              <div className="flex items-center justify-between rounded border border-border bg-background/60 p-2.5">
                <div>
                  <p className="font-medium flex items-center gap-1.5">
                    <Sparkles className="h-3.5 w-3.5 text-primary" />
                    Vision QA Provider: {health?.vision_qa.provider || "none"}
                  </p>
                  <p className="text-[10px] text-muted-foreground">
                    Gemini Key: {health?.vision_qa.gemini_configured ? "Configured" : "Unset"} • OpenAI Key:{" "}
                    {health?.vision_qa.openai_configured ? "Configured" : "Unset"}
                  </p>
                </div>
                <StatusBadge
                  status={health?.vision_qa.configured ? "CONNECTED" : "PENDING"}
                  label={health?.vision_qa.configured ? "ENABLED" : "LOCAL QA ONLY"}
                />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Shield className="h-4 w-4 text-emerald-400" />
                BlendSwap & Windows Task Scheduler
              </CardTitle>
              <CardDescription>API key secrets remain server-side only; zero credit spending enforced</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2.5 text-xs">
              <div className="flex items-center justify-between rounded border border-border bg-background/60 p-2.5">
                <div>
                  <p className="font-medium">BlendSwap REST API v1</p>
                  <p className="text-[10px] text-muted-foreground">
                    API Key: {health?.blendswap.key_configured ? "Configured" : "Missing"} • Credit Spend:{" "}
                    {health?.blendswap.allow_credit_spend ? "Allowed" : "Locked Off"}
                  </p>
                </div>
                <Badge variant={health?.blendswap.key_configured ? "success" : "warning"}>
                  {health?.blendswap.key_configured ? "KEY SET" : "UNSET"}
                </Badge>
              </div>
              <div className="flex flex-wrap items-center justify-between gap-2 rounded border border-border bg-background/60 p-2.5">
                <div>
                  <p className="font-medium">Windows Scheduled Task</p>
                  <p className="text-[10px] text-muted-foreground">
                    Triggers automated daily collection even when desktop web server is closed
                  </p>
                </div>
                <div className="flex gap-1.5">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy !== null}
                    onClick={() =>
                      void runAction("win-install", async () => {
                        await studioApi.installWindowsTask();
                        setNotice({ type: "ok", text: "Windows scheduled task installed/updated." });
                      })
                    }
                  >
                    Install Task
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy !== null}
                    onClick={() =>
                      void runAction("win-uninstall", async () => {
                        await studioApi.uninstallWindowsTask();
                        setNotice({ type: "ok", text: "Windows scheduled task removed." });
                      })
                    }
                  >
                    Remove
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Database className="h-4 w-4 text-amber-400" />
                Databases & Storage Index
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-xs">
              <p className="font-mono text-[10px] text-muted-foreground">
                Vault Dir: {health?.storage_paths.vault_dir}
              </p>
              <p className="font-mono text-[10px] text-muted-foreground">
                Reference Library: {health?.storage_paths.reference_library_dir}
              </p>
              <p className="font-mono text-[10px] text-muted-foreground">
                Roblox Reports: {health?.storage_paths.roblox_reports_dir}
              </p>
              <Button
                size="sm"
                variant="outline"
                disabled={busy !== null}
                onClick={() =>
                  void runAction("reindex", async () => {
                    const res = await studioApi.reindexCatalogue();
                    setNotice({
                      type: "ok",
                      text: `Reindexed ${res.assets_indexed} asset(s) and ${res.versions_indexed} version(s).`,
                    });
                  })
                }
              >
                <RefreshCw className="h-3.5 w-3.5" />
                Reindex Local Vault Catalogue
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>

      {confirmAction ? (
        <ConfirmDialog
          open={confirmAction.open}
          onOpenChange={(open) => {
            if (!open) setConfirmAction(null);
          }}
          title={confirmAction.title}
          description={confirmAction.desc}
          confirmLabel={confirmAction.label}
          confirmVariant={confirmAction.variant}
          busy={busy !== null}
          onConfirm={async () => {
            await confirmAction.fn();
            setConfirmAction(null);
            await loadHealth(false);
          }}
        />
      ) : null}
    </div>
  );
}
