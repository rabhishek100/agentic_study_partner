"use client";

import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";

const API_BASE = "/api";

const STARTERS = [
  "What sections are present in Chapter 1?",
  "Summarize Chapter 1",
  "What causes training-serving skew?",
];

function diagnosticValue(value, fallback = "Not applicable") {
  return value || fallback;
}

export default function App() {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState([]);
  const [conversation, setConversation] = useState(null);
  const [retrievalMode, setRetrievalMode] = useState("hybrid");
  const [bookId, setBookId] = useState(1);
  const [lastResult, setLastResult] = useState(null);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  const activeScope = useMemo(
    () => conversation?.active_scope?.display_path || "No active scope",
    [conversation],
  );

  async function sendQuestion(event, suggestedQuestion) {
    event?.preventDefault();
    const submitted = (suggestedQuestion ?? question).trim();
    if (!submitted || isLoading) return;

    setQuestion("");
    setError("");
    setIsLoading(true);
    setMessages((current) => [
      ...current,
      { role: "user", content: submitted },
    ]);

    try {
      const response = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: submitted,
          retrieval_mode: retrievalMode,
          book_id: Number(bookId),
          state: conversation,
        }),
      });
      const rawBody = await response.text();
      let payload;
      try {
        payload = rawBody ? JSON.parse(rawBody) : {};
      } catch {
        throw new Error(
          response.ok
            ? "The study API returned an unreadable response."
            : `The study request failed (${response.status}): ${rawBody.slice(0, 200) || response.statusText}`,
        );
      }
      if (!response.ok) {
        throw new Error(payload.detail || "The study request failed.");
      }
      setConversation(payload.state);
      setLastResult(payload.result);
      setMessages((current) => [
        ...current,
        { role: "assistant", content: payload.result.answer },
      ]);
    } catch (requestError) {
      setError(requestError.message || "Could not reach the study API.");
    } finally {
      setIsLoading(false);
    }
  }

  function clearConversation() {
    setMessages([]);
    setConversation(null);
    setLastResult(null);
    setError("");
    setQuestion("");
  }

  return (
    <main className="shell">
      <header className="masthead">
        <div>
          <p className="eyebrow">Grounded in your book</p>
          <h1>Agentic Study Partner</h1>
          <p className="subtitle">
            Ask a question, explore a chapter, or continue from your last
            answer. Important claims include book references.
          </p>
        </div>
        <div className="status" aria-label="Conversation status">
          <span className="status-dot" />
          {conversation ? "Conversation active" : "Ready to study"}
        </div>
      </header>

      <section className="workspace">
        <aside className="settings" aria-label="Study settings">
          <div>
            <p className="section-label">Study settings</p>
            <label htmlFor="book-id">Book ID</label>
            <input
              id="book-id"
              min="1"
              type="number"
              value={bookId}
              onChange={(event) => setBookId(event.target.value)}
            />

            <label htmlFor="retrieval-mode">Search method</label>
            <select
              id="retrieval-mode"
              value={retrievalMode}
              onChange={(event) => setRetrievalMode(event.target.value)}
            >
              <option value="hybrid">Hybrid (recommended)</option>
              <option value="bm25">Keyword</option>
              <option value="vector">Meaning-based</option>
              <option value="hybrid_rerank">Hybrid + reranking</option>
            </select>
          </div>

          <div className="details">
            <p className="section-label">Last turn</p>
            <dl>
              <div>
                <dt>Route</dt>
                <dd>{diagnosticValue(lastResult?.route)}</dd>
              </div>
              <div>
                <dt>Search query</dt>
                <dd>{diagnosticValue(lastResult?.standalone_query)}</dd>
              </div>
              <div>
                <dt>Active scope</dt>
                <dd>{activeScope}</dd>
              </div>
              <div>
                <dt>Outcome</dt>
                <dd>{diagnosticValue(lastResult?.outcome)}</dd>
              </div>
            </dl>
          </div>

          <button className="clear" type="button" onClick={clearConversation}>
            Clear conversation
          </button>
        </aside>

        <section className="chat" aria-label="Study conversation">
          <div className="messages" aria-live="polite">
            {messages.length === 0 ? (
              <div className="welcome">
                <p className="welcome-mark">ASP</p>
                <h2>What would you like to understand?</h2>
                <p>Start with one of these, or ask your own question.</p>
                <div className="starters">
                  {STARTERS.map((starter) => (
                    <button
                      type="button"
                      key={starter}
                      onClick={(event) => sendQuestion(event, starter)}
                    >
                      {starter}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((message, index) => (
                <article
                  className={`message ${message.role}`}
                  key={`${message.role}-${index}`}
                >
                  <p className="message-role">
                    {message.role === "user" ? "You" : "Study partner"}
                  </p>
                  <div className="message-body">
                    <ReactMarkdown>{message.content}</ReactMarkdown>
                  </div>
                </article>
              ))
            )}
            {isLoading && (
              <div className="thinking" role="status">
                <span />
                <span />
                <span />
                Checking the book…
              </div>
            )}
          </div>

          {error && <div className="error">{error}</div>}

          <form className="composer" onSubmit={sendQuestion}>
            <label className="sr-only" htmlFor="question">
              Ask about the book
            </label>
            <textarea
              id="question"
              rows="2"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  sendQuestion(event);
                }
              }}
              placeholder="Ask about the book…"
              disabled={isLoading}
            />
            <button
              className="send"
              type="submit"
              disabled={isLoading || !question.trim()}
            >
              {isLoading ? "Working…" : "Send"}
            </button>
          </form>
          <p className="footnote">
            Answers are limited to the evidence found in the selected book.
          </p>
        </section>
      </section>
    </main>
  );
}
