import * as React from "react";
import { Sidebar } from "@/components/Sidebar";
import { studioApi } from "@/lib/api";
import { AssetVaultPage } from "@/pages/AssetVaultPage";
import { CollectionControlPage } from "@/pages/CollectionControlPage";
import { GenerationStudioPage } from "@/pages/GenerationStudioPage";
import { OverviewPage } from "@/pages/OverviewPage";
import { QualityIntelligencePage } from "@/pages/QualityIntelligencePage";
import { ReferenceLibraryPage } from "@/pages/ReferenceLibraryPage";
import { SystemSettingsPage } from "@/pages/SystemSettingsPage";
import type { NavSection } from "@/types/studio";

const PATH_TO_SECTION: Record<string, NavSection> = {
  "/": "overview",
  "/overview": "overview",
  "/generation": "generation",
  "/vault": "vault",
  "/references": "references",
  "/collection": "collection",
  "/quality": "quality",
  "/settings": "settings",
};

const SECTION_TO_PATH: Record<NavSection, string> = {
  overview: "/overview",
  generation: "/generation",
  vault: "/vault",
  references: "/references",
  collection: "/collection",
  quality: "/quality",
  settings: "/settings",
};

function readInitialSection(): NavSection {
  return PATH_TO_SECTION[window.location.pathname] || "overview";
}

export function App() {
  const [section, setSection] = React.useState<NavSection>(readInitialSection);
  const [vaultFocusAssetId, setVaultFocusAssetId] = React.useState<string | null>(null);
  const [activeJobsCount, setActiveJobsCount] = React.useState(0);
  const [collectionRunning, setCollectionRunning] = React.useState(false);
  const [backendOnline, setBackendOnline] = React.useState(true);

  const navigate = React.useCallback((next: NavSection) => {
    setSection(next);
    const targetPath = SECTION_TO_PATH[next] || "/";
    if (window.location.pathname !== targetPath) {
      window.history.pushState({ section: next }, "", targetPath);
    }
  }, []);

  React.useEffect(() => {
    const onPop = () => {
      setSection(readInitialSection());
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  React.useEffect(() => {
    let alive = true;
    const pollBadges = async () => {
      try {
        const ov = await studioApi.getOverview(false);
        if (!alive) return;
        setActiveJobsCount(ov.generation.active_jobs_count || 0);
        setCollectionRunning(Boolean(ov.reference_collection.control?.is_running));
        setBackendOnline(true);
      } catch {
        if (alive) setBackendOnline(false);
      }
    };
    void pollBadges();
    const id = window.setInterval(() => void pollBadges(), 6000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  const handleOpenAsset = React.useCallback(
    (assetId: string, target: NavSection = "vault") => {
      setVaultFocusAssetId(assetId);
      navigate(target);
    },
    [navigate],
  );

  return (
    <div className="flex min-h-screen bg-background text-foreground">
      <Sidebar
        activeSection={section}
        onSelect={navigate}
        activeJobsCount={activeJobsCount}
        collectionRunning={collectionRunning}
        backendOnline={backendOnline}
      />
      <main className="flex-1 overflow-y-auto px-5 py-4 lg:px-7 lg:py-5">
        <div className="mx-auto max-w-[1440px]">
          {section === "overview" ? <OverviewPage onNavigate={navigate} /> : null}
          {section === "generation" ? <GenerationStudioPage onOpenAsset={handleOpenAsset} /> : null}
          {section === "vault" ? (
            <AssetVaultPage
              initialAssetId={vaultFocusAssetId}
              onClearInitialAsset={() => setVaultFocusAssetId(null)}
            />
          ) : null}
          {section === "references" ? <ReferenceLibraryPage /> : null}
          {section === "collection" ? <CollectionControlPage /> : null}
          {section === "quality" ? <QualityIntelligencePage onOpenAsset={handleOpenAsset} /> : null}
          {section === "settings" ? <SystemSettingsPage /> : null}
        </div>
      </main>
    </div>
  );
}

export default App;
