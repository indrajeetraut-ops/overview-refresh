import { useEffect, useMemo, useState } from "react";
import Sidebar from "./components/Sidebar";
import type { ScreenId } from "./components/Sidebar";
import Overview from "./screens/Overview";
import UsageScreen from "./screens/UsageScreen";
import TeamScreen from "./screens/TeamScreen";
import PersonScreen from "./screens/PersonScreen";
import ProjectScreen from "./screens/ProjectScreen";
import FeatureScreen from "./screens/FeatureScreen";
import MyUsage from "./screens/MyUsage";
import EconomicsScreen from "./screens/EconomicsScreen";
import SessionScreen from "./screens/SessionScreen";
import PRScreen from "./screens/PRScreen";
import TeamsOverview from "./screens/TeamsOverview";
import ProjectsOverview from "./screens/ProjectsOverview";
import ConfigureScreen from "./screens/ConfigureScreen";
import type { PinnedCard } from "./screens/types";
import { people } from "./data/mock";
import { entityRoute, type EntityRef } from "./present/routing";
import { scopeLabels } from "./present/crumbs";
import type { CrumbTarget } from "./present/crumbs";
import { CrumbLabelsProvider } from "./shell/crumbs";
import TopBar, { STICKY_BARS_SCROLL_PADDING } from "./shell/TopBar";
import FilterBar from "./shell/FilterBar";
import { ScopesProvider, useScopes } from "./shell/scopes";
import { TimeframeGate, TimeframeProvider } from "./shell/timeframe";
import { TimezoneBanner } from "./shell/timezone";
import { ViewerProvider, canSeeManagerNav, useViewer } from "./shell/viewer";

type NavStack = ScreenId[];

// Screens that show the timeframe control (entity pages don't).
const ENTITY = new Set(["session", "pr", "feature"]);
// Manager surfaces a `dev` viewer may not open directly.
const MANAGER_ROOTS = new Set(["overview", "usage", "economics", "teams-overview", "projects-overview", "configure"]);
// "configure" is period-independent (same reasoning as scopes()) -- no filter bar on it.
const NO_TIMEFRAME = new Set(["configure"]);

function Shell() {
  const viewer = useViewer();
  const managerNav = canSeeManagerNav(viewer.role);

  // Names for the (inert) team filter and for team / project crumbs before their detail has
  // loaded; the one `scopes()` fetch lives in ScopesProvider (managers only).
  const scopesState = useScopes();
  const scopes = scopesState.status === "ready" ? scopesState.data : null;
  const crumbFallback = useMemo(() => scopeLabels(scopes), [scopes]);

  const [dark, setDark] = useState(false);
  const [stack, setStack] = useState<NavStack>([managerNav ? "overview" : "my-usage"]);
  const [pinnedCards, setPinnedCards] = useState<PinnedCard[]>([]);
  // U8 (mobile reflow): the sidebar is a persistent rail at `md`+ and an off-canvas drawer
  // below it, closed by default -- opened via the hamburger in the top bar, shown only under `md`.
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  useEffect(() => {
    if (!mobileNavOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setMobileNavOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileNavOpen]);

  const current = stack[stack.length - 1];
  const showTimeframe = typeof current === "string" ? !NO_TIMEFRAME.has(current) : !ENTITY.has(current.type);

  function navigate(screen: string, param?: string) {
    let next: ScreenId;
    if (!managerNav && MANAGER_ROOTS.has(screen)) return; // role gate (F2)
    if (screen === "overview") next = "overview";
    else if (screen === "usage") next = "usage";
    else if (screen === "economics") next = "economics";
    else if (screen === "my-usage") next = "my-usage";
    else if (screen === "teams-overview") next = "teams-overview";
    else if (screen === "projects-overview") next = "projects-overview";
    else if (screen === "configure") next = "configure";
    else if (screen === "team") next = { type: "team", id: param! };
    else if (screen === "person") {
      const person = people.find((p) => p.id === param);
      next = { type: "person", id: param!, teamId: person?.teamId ?? "platform" };
    }
    else if (screen === "project") next = { type: "project", id: param! };
    else if (screen === "feature") next = { type: "feature", id: param! };
    else if (screen === "session") next = { type: "session", id: param! };
    else if (screen === "pr") next = { type: "pr", id: param! };
    else return;
    setStack((prev) => [...prev, next]);
  }

  /** Navigate from a typed API entity ref (U2 routing). */
  function navigateRef(ref: EntityRef) {
    const r = entityRoute(ref);
    navigate(r.screen, r.param);
  }

  function goBack() {
    setStack((prev) => (prev.length > 1 ? prev.slice(0, -1) : prev));
  }
  /** A breadcrumb click: back to a stack entry (drops what came after it) or to a section root. */
  function navigateCrumb(target: CrumbTarget) {
    if (target.kind === "screen") handleSidebarNav(target.screen);
    else setStack((prev) => (target.index < prev.length ? prev.slice(0, target.index + 1) : prev));
  }
  function handleSidebarNav(s: ScreenId) {
    setStack([s]);
    setMobileNavOpen(false);
  }
  function addPin(card: PinnedCard) {
    setPinnedCards((prev) => [card, ...prev.filter((c) => c.id !== card.id)]);
  }

  const screenProps = { onNavigate: navigate, onNavigateRef: navigateRef, onBack: goBack, pinnedCards, onPin: addPin };

  function renderScreen() {
    if (current === "my-usage") return <MyUsage pinnedCards={pinnedCards} onPin={addPin} />;
    if (current === "overview") return <Overview {...screenProps} />;
    if (current === "usage") return <UsageScreen {...screenProps} />;
    if (current === "economics") return <EconomicsScreen {...screenProps} />;
    if (current === "teams-overview") return <TeamsOverview onNavigate={navigate} />;
    if (current === "projects-overview") return <ProjectsOverview onNavigate={navigate} />;
    if (current === "configure") return <ConfigureScreen onBack={goBack} />;
    if (typeof current === "object") {
      if (current.type === "team") return <TeamScreen teamId={current.id} {...screenProps} />;
      if (current.type === "person") return <PersonScreen personId={current.id} {...screenProps} />;
      if (current.type === "project") return <ProjectScreen projectId={current.id} {...screenProps} />;
      if (current.type === "feature") return (
        <FeatureScreen featureId={current.id} onNavigate={navigate} onBack={goBack} pinnedCards={pinnedCards} onPin={addPin} />
      );
      if (current.type === "session") return <SessionScreen sessionId={current.id} {...screenProps} />;
      if (current.type === "pr") return <PRScreen prId={current.id} {...screenProps} />;
    }
    return <MyUsage pinnedCards={pinnedCards} onPin={addPin} />;
  }

  return (
    <div className={dark ? "dark" : ""} style={{ height: "100%", display: "flex", background: "var(--background)" }}>
      {/* Persistent rail at md+ */}
      <div className="hidden md:flex">
        <Sidebar
          current={current}
          onNavigate={handleSidebarNav}
          dark={dark}
          onToggleDark={() => setDark((d) => !d)}
          showManagerNav={managerNav}
        />
      </div>

      {/* Off-canvas drawer below md */}
      {mobileNavOpen && (
        <div className="md:hidden" style={{ position: "fixed", inset: 0, zIndex: 40 }}>
          <div
            onClick={() => setMobileNavOpen(false)}
            style={{ position: "absolute", inset: 0, background: "rgba(0,0,0,0.4)" }}
            aria-hidden
          />
          <div style={{ position: "relative", height: "100%", width: 220 }}>
            <Sidebar
              current={current}
              onNavigate={handleSidebarNav}
              dark={dark}
              onToggleDark={() => setDark((d) => !d)}
              showManagerNav={managerNav}
            />
          </div>
        </div>
      )}

      {/* scrollPaddingTop: a focused control must not scroll under the sticky top bar and filter bar. */}
      <main className="flex-1 overflow-y-auto min-w-0" style={{ background: "var(--background)", scrollPaddingTop: STICKY_BARS_SCROLL_PADDING }}>
        <TimezoneBanner />
        {/* Outside the gate: the breadcrumb and Configure stay available while the periods load. The
            hamburger is the old mobile header, merged in (the drawer itself is unchanged). */}
        <TopBar
          stack={stack}
          fallbackLabels={crumbFallback}
          onCrumb={navigateCrumb}
          onOpenNav={() => setMobileNavOpen(true)}
          onConfigure={managerNav ? () => navigate("configure") : undefined}
          configureActive={current === "configure"}
        />
        {/* The gate holds back the filter bar and the screens until the budget periods have answered, so
            no rolling window is fetched first; the shell (nav stack, theme) stays mounted. */}
        <TimeframeGate>
          {showTimeframe && <FilterBar teams={managerNav ? scopes?.teams ?? [] : null} />}
          <div className="p-4 sm:p-9 md:p-12">
            {/* One content width for every screen: fills the pane up to a dashboard-sized cap, then
                centres. Prose inside keeps its own ~620px measure. */}
            <div className="mx-auto w-full max-w-[1440px]">
              {renderScreen()}
            </div>
            <div style={{ height: 80 }} />
          </div>
        </TimeframeGate>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <ViewerProvider>
      <ScopesProvider>
        <TimeframeProvider>
          <CrumbLabelsProvider>
            <Shell />
          </CrumbLabelsProvider>
        </TimeframeProvider>
      </ScopesProvider>
    </ViewerProvider>
  );
}
