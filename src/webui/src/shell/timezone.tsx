/**
 * TZ track (mvp-gap-closure-plan): detect the browser's own IANA zone
 * (`Intl.DateTimeFormat().resolvedOptions().timeZone` -- a zero-permission JS call, no
 * geolocation prompt) and compare it against `users.timezone`. On a mismatch (including "never
 * set"), a small dismissible banner offers to update it -- the same shape Slack/Linear/GitHub
 * use, minus the always-on toggle: this pass is detect-and-confirm only, no manual override UI.
 *
 * All time bucketing this unlocks (peak hour, streaks, the rhythm heatmap) happens
 * server-side once the preference is saved (`single_user/service.py`) -- this component's own
 * job stops at "does the browser's zone match what's stored," nothing more.
 */
import { useEffect, useState } from "react";
import { getDataSource } from "../data/source";
import { useViewer } from "./viewer";

// Keyed by the *detected* zone, not a bare boolean -- a dismissal while in SFO should not
// silently suppress the prompt again after a later flight to a different zone.
const DISMISS_KEY = "talium.tz-banner.dismissed-for";

function detectZone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null; // very old/unusual environments -- no prompt is safer than a bad guess
  }
}

function readDismissed(): string | null {
  try {
    return window.localStorage.getItem(DISMISS_KEY);
  } catch {
    return null; // private browsing / storage blocked -- just don't remember dismissals
  }
}

function rememberDismissed(zone: string): void {
  try {
    window.localStorage.setItem(DISMISS_KEY, zone);
  } catch {
    // per-viewer convenience only -- losing it just means the banner may reappear
  }
}

type SyncState = {
  show: boolean;
  detected: string | null;
  accept: () => void;
  dismiss: () => void;
};

function useTimezoneSync(): SyncState {
  const viewer = useViewer();
  const [detected] = useState(detectZone);
  const [stored, setStored] = useState<string | null | undefined>(undefined); // undefined = not loaded yet
  const [dismissedFor, setDismissedFor] = useState<string | null>(readDismissed);

  useEffect(() => {
    let live = true;
    getDataSource()
      .getTimezone(viewer.userId)
      .then((r) => live && setStored(r.timezone))
      .catch(() => live && setStored(null)); // no stored row yet -- same as "never set"
    return () => {
      live = false;
    };
  }, [viewer.userId]);

  function accept() {
    if (!detected) return;
    getDataSource()
      .setTimezone(viewer.userId, detected)
      .then((r) => setStored(r.timezone))
      .catch(() => {
        /* the banner just stays up -- the next mount retries the same comparison */
      });
  }

  function dismiss() {
    if (!detected) return;
    rememberDismissed(detected);
    setDismissedFor(detected);
  }

  const show = stored !== undefined && detected != null && detected !== stored && dismissedFor !== detected;
  return { show, detected, accept, dismiss };
}

export function TimezoneBanner() {
  const { show, detected, accept, dismiss } = useTimezoneSync();
  if (!show || !detected) return null;

  return (
    <div
      role="status"
      style={{
        display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12,
        padding: "10px 16px", background: "var(--card)", borderBottom: "1px solid var(--border)",
        fontSize: 13, color: "var(--foreground)",
      }}
    >
      <span>
        You appear to be in <strong>{detected}</strong> — update your timezone so times match
        where you are?
      </span>
      <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
        <button
          onClick={accept}
          style={{
            padding: "4px 10px", borderRadius: 6, border: "1px solid var(--border)",
            background: "var(--foreground)", color: "var(--background)", fontSize: 13, cursor: "pointer",
          }}
        >
          Update
        </button>
        <button
          onClick={dismiss}
          aria-label="Dismiss"
          style={{
            padding: "4px 10px", borderRadius: 6, border: "1px solid var(--border)",
            background: "transparent", color: "var(--foreground)", fontSize: 13, cursor: "pointer",
          }}
        >
          Not now
        </button>
      </div>
    </div>
  );
}
