import * as React from "react";
import {
  Activity,
  BarChart3,
  Box,
  Database,
  ExternalLink,
  FolderKanban,
  LayoutDashboard,
  Settings,
  Sparkles,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { NavSection } from "@/types/studio";

export interface NavItem {
  id: NavSection;
  label: string;
  subtitle: string;
  icon: React.ComponentType<{ className?: string }>;
  badge?: string | number;
}

export function Sidebar({
  activeSection,
  onSelect,
  activeJobsCount,
  collectionRunning,
  backendOnline,
}: {
  activeSection: NavSection;
  onSelect: (section: NavSection) => void;
  activeJobsCount?: number;
  collectionRunning?: boolean;
  backendOnline: boolean;
}) {
  const navItems: NavItem[] = [
    {
      id: "overview",
      label: "OVERVIEW",
      subtitle: "System & pipeline status",
      icon: LayoutDashboard,
    },
    {
      id: "generation",
      label: "GENERATION STUDIO",
      subtitle: "Create & track 3D jobs",
      icon: Sparkles,
      badge: activeJobsCount && activeJobsCount > 0 ? activeJobsCount : undefined,
    },
    {
      id: "vault",
      label: "ASSET VAULT",
      subtitle: "Approve & sync assets",
      icon: Box,
    },
    {
      id: "references",
      label: "REFERENCE LIBRARY",
      subtitle: "BlendSwap benchmark vault",
      icon: Database,
    },
    {
      id: "collection",
      label: "COLLECTION CONTROL",
      subtitle: "Daily quota, rules & runs",
      icon: FolderKanban,
      badge: collectionRunning ? "LIVE" : undefined,
    },
    {
      id: "quality",
      label: "QUALITY INTELLIGENCE",
      subtitle: "QA grades & comparisons",
      icon: BarChart3,
    },
    {
      id: "settings",
      label: "SYSTEM SETTINGS",
      subtitle: "Blender, Drive & schedulers",
      icon: Settings,
    },
  ];

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-border bg-card/80">
      <div className="flex items-center gap-2.5 border-b border-border px-4 py-3.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary/15 text-primary ring-1 ring-primary/30">
          <Box className="h-4 w-4" />
        </div>
        <div className="min-w-0">
          <p className="truncate text-xs font-bold tracking-wide text-foreground">BLENDER ASSET VAULT</p>
          <p className="truncate text-[11px] text-muted-foreground">Unified Control Studio</p>
        </div>
      </div>

      <nav className="flex-1 space-y-1 overflow-y-auto p-2.5">
        {navItems.map((item) => {
          const Icon = item.icon;
          const active = activeSection === item.id;
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onSelect(item.id)}
              className={cn(
                "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left transition-colors",
                active
                  ? "bg-primary/15 text-foreground ring-1 ring-primary/35"
                  : "text-muted-foreground hover:bg-accent/70 hover:text-foreground",
              )}
            >
              <Icon className={cn("h-4 w-4 shrink-0", active ? "text-primary" : "text-muted-foreground")} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-1.5">
                  <span className="truncate text-[11px] font-semibold tracking-wider">{item.label}</span>
                  {item.badge !== undefined ? (
                    <span className="rounded bg-primary/20 px-1.5 py-0.5 font-mono text-[10px] font-semibold text-primary">
                      {item.badge}
                    </span>
                  ) : null}
                </div>
                <p className="truncate text-[10px] text-muted-foreground">{item.subtitle}</p>
              </div>
            </button>
          );
        })}
      </nav>

      <div className="border-t border-border p-3 text-[11px]">
        <div className="flex items-center justify-between rounded-md bg-background/60 px-2.5 py-1.5">
          <span className="inline-flex items-center gap-1.5 text-muted-foreground">
            <Activity className={cn("h-3.5 w-3.5", backendOnline ? "text-emerald-400" : "text-rose-400")} />
            Backend API
          </span>
          <span className={cn("font-mono text-[10px] font-semibold", backendOnline ? "text-emerald-300" : "text-rose-300")}>
            {backendOnline ? "CONNECTED" : "OFFLINE"}
          </span>
        </div>
        <a
          href="/legacy"
          target="_blank"
          rel="noreferrer"
          className="mt-2 flex items-center justify-between rounded-md px-2 py-1 text-[11px] text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <span>Legacy Vault View</span>
          <ExternalLink className="h-3 w-3" />
        </a>
      </div>
    </aside>
  );
}
