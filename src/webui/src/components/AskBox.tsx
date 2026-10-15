import { useState } from "react";
import { askSuggestions, askResponses } from "../data/mock";
import { MONO_FONT } from "../present/fonts";

type PinnedCard = {
  id: string;
  question: string;
  answer: string;
  receipt: string;
};

type AskBoxProps = {
  pinnedCards: PinnedCard[];
  onPin: (card: PinnedCard) => void;
};

export default function AskBox({ pinnedCards, onPin }: AskBoxProps) {
  const [query, setQuery] = useState("");
  const [activeAnswer, setActiveAnswer] = useState<(typeof askResponses)[string] & { question: string } | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [showReceipt, setShowReceipt] = useState(false);

  function handleAsk(q: string) {
    const key = q.trim();
    const response = askResponses[key];
    if (response) {
      setActiveAnswer({ ...response, question: key });
      setExpanded(true);
      setShowReceipt(false);
    } else {
      setActiveAnswer({
        question: key,
        answer: "I don't have enough data to answer that precisely right now. Try one of the suggestions below.",
        receipt: "No sessions found for this query",
        sessions: 0,
        people: 0,
        followups: askSuggestions.slice(0, 2),
      });
      setExpanded(true);
    }
    setQuery("");
  }

  function handlePin() {
    if (!activeAnswer) return;
    const id = `pin-${Date.now()}`;
    onPin({ id, question: activeAnswer.question, answer: activeAnswer.answer, receipt: activeAnswer.receipt });
    setActiveAnswer(null);
    setExpanded(false);
  }

  return (
    <div className="ask-box">
      {/* Pinned cards */}
      {pinnedCards.length > 0 && (
        <div className="px-4 pt-4 flex flex-col gap-3">
          {pinnedCards.map((c) => (
            <div key={c.id} className="pinned-answer">
              <div className="text-xs font-mono mb-1.5" style={{ color: "var(--muted-foreground)" }}>
                Pinned · {c.question}
              </div>
              <div className="text-sm" style={{ color: "var(--foreground)", lineHeight: 1.55 }}>
                {c.answer}
              </div>
              <div className="text-xs font-mono mt-2" style={{ color: "var(--muted-foreground)" }}>
                {c.receipt}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Input row */}
      <div className="flex items-center gap-2 px-2">
        <svg width="15" height="15" viewBox="0 0 15 15" fill="none" className="ml-2 shrink-0" style={{ color: "var(--muted-foreground)" }}>
          <circle cx="6.5" cy="6.5" r="4.5" stroke="currentColor" strokeWidth="1.3" />
          <path d="M10 10l3 3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
        </svg>
        <input
          className="ask-input"
          placeholder="Ask anything about cost, usage, or output…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && query.trim()) handleAsk(query);
          }}
        />
        {query.trim() && (
          <button
            onClick={() => handleAsk(query)}
            className="shrink-0 px-3 py-1.5 rounded text-xs font-medium mr-2"
            style={{ background: "var(--primary)", color: "var(--primary-foreground)" }}
          >
            Ask
          </button>
        )}
      </div>

      {/* Suggestion chips */}
      {!expanded && (
        <div className="flex items-center gap-2 flex-wrap px-4 pb-3 pt-1">
          {askSuggestions.map((s) => (
            <button
              key={s}
              onClick={() => handleAsk(s)}
              className="chip chip-muted cursor-pointer hover:opacity-80 transition-opacity"
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {/* Answer */}
      {expanded && activeAnswer && (
        <div className="answer-card">
          <div className="text-sm mb-1" style={{ color: "var(--muted-foreground)", fontSize: 11.5, fontFamily: MONO_FONT }}>
            {activeAnswer.question}
          </div>
          <p className="text-sm leading-relaxed mb-3" style={{ color: "var(--foreground)" }}>
            {activeAnswer.answer}
          </p>

          {/* Receipt */}
          <div className="flex items-center gap-2 mb-3">
            <span className="text-xs font-mono" style={{ color: "var(--muted-foreground)" }}>
              {activeAnswer.receipt}
            </span>
            {activeAnswer.sessions > 0 && (
              <button
                className="text-xs font-medium underline"
                style={{ color: "var(--primary)" }}
                onClick={() => setShowReceipt(!showReceipt)}
              >
                {showReceipt ? "hide" : "show how this was worked out"}
              </button>
            )}
          </div>

          {showReceipt && (
            <div className="rounded px-3 py-2 mb-3 text-xs font-mono" style={{ background: "var(--muted)", color: "var(--muted-foreground)" }}>
              Analyzed {activeAnswer.sessions} sessions across {activeAnswer.people} people.
              Spend was summed per category and compared against the org total. Recoverable spend
              was estimated using session replay analysis — context re-reads, model tier mismatches,
              and rework loop detection.
            </div>
          )}

          {/* Follow-ups */}
          {activeAnswer.followups.length > 0 && (
            <div className="flex items-center gap-2 flex-wrap mb-3">
              {activeAnswer.followups.map((f) => (
                <button
                  key={f}
                  onClick={() => handleAsk(f)}
                  className="chip chip-blue cursor-pointer hover:opacity-80 transition-opacity"
                >
                  {f}
                </button>
              ))}
            </div>
          )}

          {/* Actions */}
          <div className="flex items-center gap-3">
            <button
              onClick={handlePin}
              className="text-xs font-medium flex items-center gap-1.5"
              style={{ color: "var(--primary)" }}
            >
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                <path d="M6 1v6M3 4l3-3 3 3M2 11h8" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
              Pin to dashboard
            </button>
            <button
              onClick={() => { setExpanded(false); setActiveAnswer(null); }}
              className="text-xs"
              style={{ color: "var(--muted-foreground)" }}
            >
              Dismiss
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
