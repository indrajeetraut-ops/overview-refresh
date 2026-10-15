export type ScreenId =
  | "my-usage"
  | "overview"
  | "usage"
  | "economics"
  | "teams-overview"
  | "projects-overview"
  // Reached only via the top bar's Configure button (lead/admin), never a sidebar nav item.
  | "configure"
  | { type: "team"; id: string }
  | { type: "person"; id: string; teamId: string }
  | { type: "project"; id: string }
  | { type: "feature"; id: string }
  | { type: "session"; id: string }
  | { type: "pr"; id: string };

type Props = {
  current: ScreenId;
  onNavigate: (s: ScreenId) => void;
  dark: boolean;
  onToggleDark: () => void;
  /** U4/F2: a `dev` viewer sees only "My usage" — the manager nav is hidden. */
  showManagerNav?: boolean;
};

const isStr = (c: ScreenId, s: string) => typeof c === "string" && c === s;

export default function Sidebar({ current, onNavigate, dark, onToggleDark, showManagerNav = true }: Props) {
  // section-level active: highlight Teams while anywhere in the teams area, etc.
  const inTeams = isStr(current, "teams-overview") || (typeof current === "object" && (current.type === "team" || current.type === "person"));
  const inProjects = isStr(current, "projects-overview") || (typeof current === "object" && (current.type === "project" || current.type === "feature"));

  const item = (id: string, active: boolean, label: string, icon: React.ReactNode) => (
    <button className={`sidebar-link ${active ? "active" : ""}`} onClick={() => onNavigate(id as ScreenId)}>
      {icon}{label}
    </button>
  );

  return (
    <nav className="flex flex-col h-full select-none" style={{ width: 200, background: "var(--card)", borderRight: "1px solid var(--border)", padding: "20px 10px" }}>
      <div className="px-2 mb-8">
        <span className="font-serif" style={{ fontSize: 20, color: "var(--foreground)", letterSpacing: "-0.01em" }}>Talium</span>
      </div>

      <div className="flex flex-col gap-0.5">
        {/* TODO(my-usage-viewer-id): hidden from lead/admin viewers until My Usage resolves the
            real viewer -- it requests the placeholder id "me", which never matches a directory
            user, so every tile renders $0. Fix: `GET /v1/me` + ViewerProvider fetching it, then
            MyUsage/TimezoneBanner read `useViewer().userId`. Restore this item for everyone once
            that lands. Still shown to `dev` viewers, whose only screen it is. */}
        {!showManagerNav && item("my-usage", isStr(current, "my-usage"), "My usage",
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><circle cx="7" cy="4.5" r="2.5" stroke="currentColor" strokeWidth="1.3" /><path d="M2 12c0-2.8 2.2-5 5-5s5 2.2 5 5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" /></svg>)}

        {!showManagerNav && (
          <div className="mt-3 px-2 text-xs" style={{ color: "var(--muted-foreground)", fontSize: 11, lineHeight: 1.4 }}>
            Team and org views are available to leads and admins.
          </div>
        )}

        {showManagerNav && <>
        <div className="mt-3 mb-1 px-2 text-xs font-medium uppercase tracking-widest" style={{ color: "var(--muted-foreground)", fontSize: 10 }}>Org</div>

        {item("overview", isStr(current, "overview"), "Overview",
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><rect x="1.5" y="1.5" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.3" /><rect x="7.5" y="1.5" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.3" /><rect x="1.5" y="7.5" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.3" /><rect x="7.5" y="7.5" width="5" height="5" rx="1" stroke="currentColor" strokeWidth="1.3" /></svg>)}

        {item("usage", isStr(current, "usage"), "Usage",
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M1.5 12.5V5.5l3-3 3 3 3-5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" /><path d="M1.5 12.5h11" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" /></svg>)}

        {item("economics", isStr(current, "economics"), "Economics",
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M2 2v10h10" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" /><path d="M4.5 8.5l2-2.5 2 1.5 2.5-3.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" /></svg>)}

        <div className="mt-3 mb-1 px-2 text-xs font-medium uppercase tracking-widest" style={{ color: "var(--muted-foreground)", fontSize: 10 }}>Explore</div>

        {item("teams-overview", inTeams, "Teams",
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><circle cx="5" cy="5" r="2" stroke="currentColor" strokeWidth="1.3" /><circle cx="10" cy="6" r="1.6" stroke="currentColor" strokeWidth="1.3" /><path d="M1.5 12c0-2 1.6-3.4 3.5-3.4S8.5 10 8.5 12" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" /></svg>)}

        {item("projects-overview", inProjects, "Projects",
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M2 3h5l1.5 1.5H12v7H2V3z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" /></svg>)}
        </>}
      </div>

      <div className="flex-1" />

      <button onClick={onToggleDark} className="sidebar-link mt-4">
        {dark ? (
          <>
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><circle cx="7" cy="7" r="2.5" stroke="currentColor" strokeWidth="1.3" /><path d="M7 1v1M7 12v1M1 7h1M12 7h1M2.8 2.8l.7.7M10.5 10.5l.7.7M2.8 11.2l.7-.7M10.5 3.5l.7-.7" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" /></svg>
            Light mode
          </>
        ) : (
          <>
            <svg width="14" height="14" viewBox="0 0 14 14" fill="none"><path d="M12 7.5A5.5 5.5 0 016.5 2 5.5 5.5 0 1012 7.5z" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" /></svg>
            Dark mode
          </>
        )}
      </button>
    </nav>
  );
}
