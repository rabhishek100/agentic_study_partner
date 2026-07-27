"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";

import { apiFetch } from "../lib/api";
import { accessToken } from "../lib/supabase";
import AuthGate, { signOut, useSession } from "./components/AuthGate";
import UploadPanel from "./components/UploadPanel";

const API_BASE = "/api";
const STREAM_IDLE_TIMEOUT_MS = 60_000;

const STARTERS = [
  "What sections are present in Chapter 1?",
  "Summarize Chapter 1",
  "What causes training-serving skew?",
];

function diagnosticValue(value, fallback = "Not applicable") {
  return value || fallback;
}

export default function App() {
  const { session, sessionLoading } = useSession();
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState([]);
  const [conversation, setConversation] = useState(null);
  const [retrievalMode, setRetrievalMode] = useState("hybrid");
  const [books, setBooks] = useState([]);
  const [booksLoaded, setBooksLoaded] = useState(false);
  const [selectedBookId, setSelectedBookId] = useState(null);
  const [lastResult, setLastResult] = useState(null);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  const activeScope = useMemo(
    () => conversation?.active_scope?.display_path || "No active scope",
    [conversation],
  );

  const loadBooks = useCallback(async () => {
    try {
      const payload = await apiFetch("/books");
      setBooks(payload.books);
      setSelectedBookId(
        (current) =>
          current ?? (payload.books.length ? payload.books[0].book_id : null),
      );
    } catch {
      // The library panel shows its empty state; chat stays disabled.
    } finally {
      setBooksLoaded(true);
    }
  }, []);

  useEffect(() => {
    if (session) loadBooks();
  }, [session, loadBooks]);

  async function sendQuestion(event, suggestedQuestion) {
    event?.preventDefault();
    const submitted = (suggestedQuestion ?? question).trim();
    if (!submitted || isLoading || !selectedBookId) return;

    setQuestion("");
    setError("");
    setIsLoading(true);
    setMessages((current) => [
      ...current,
      { role: "user", content: submitted },
    ]);

    let assistantIndex = -1;
    setMessages((current) => {
      assistantIndex = current.length;
      return [...current, { role: "assistant", content: "" }];
    });

    function setAssistantContent(content) {
      setMessages((current) => {
        const next = [...current];
        next[assistantIndex] = { role: "assistant", content };
        return next;
      });
    }

    const controller = new AbortController();
    let idleTimer = setTimeout(
      () => controller.abort(),
      STREAM_IDLE_TIMEOUT_MS,
    );
    function resetIdleTimer() {
      clearTimeout(idleTimer);
      idleTimer = setTimeout(() => controller.abort(), STREAM_IDLE_TIMEOUT_MS);
    }

    try {
      const token = await accessToken();
      const response = await fetch(`${API_BASE}/chat/stream`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          question: submitted,
          retrieval_mode: retrievalMode,
          book_id: Number(selectedBookId),
          state: conversation,
        }),
        signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        const rawBody = await response.text();
        let detail;
        try {
          detail = rawBody ? JSON.parse(rawBody).detail : null;
        } catch {
          detail = null;
        }
        throw new Error(
          detail ||
            `The study request failed (${response.status}): ${rawBody.slice(0, 200) || response.statusText}`,
        );
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let streamedText = "";
      let settled = false;

      readLoop: while (true) {
        const { done, value } = await reader.read();
        resetIdleTimer();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let boundary;
        while ((boundary = buffer.indexOf("\n\n")) !== -1) {
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);

          let eventName = "message";
          let dataLine = "";
          for (const line of block.split("\n")) {
            if (line.startsWith("event: ")) eventName = line.slice(7);
            else if (line.startsWith("data: ")) dataLine = line.slice(6);
          }
          if (!dataLine) continue;
          const data = JSON.parse(dataLine);

          if (eventName === "token") {
            streamedText += data.text;
            setAssistantContent(streamedText);
          } else if (eventName === "final") {
            settled = true;
            setConversation(data.state);
            setLastResult(data.result);
            setAssistantContent(data.result.answer);
            break readLoop;
          } else if (eventName === "error") {
            throw new Error(data.detail || "The study request failed.");
          }
        }
      }
      if (!settled) {
        throw new Error("The study API closed the stream unexpectedly.");
      }
    } catch (requestError) {
      setMessages((current) => current.slice(0, assistantIndex));
      setError(
        requestError.name === "AbortError"
          ? "The study API stopped responding and the request timed out."
          : requestError.message || "Could not reach the study API.",
      );
    } finally {
      clearTimeout(idleTimer);
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

  function selectBook(bookId) {
    setSelectedBookId(bookId);
    // Conversation state is scoped to one book; switching starts fresh.
    clearConversation();
  }

  if (sessionLoading) {
    return (
      <main className="shell centered">
        <div className="thinking" role="status">
          <span />
          <span />
          <span />
          Loading…
        </div>
      </main>
    );
  }

  if (!session) {
    return (
      <main className="shell centered">
        <AuthGate />
      </main>
    );
  }

  const selectedBook = books.find((book) => book.book_id === selectedBookId);
  const hasBooks = books.length > 0;

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
        <div className="masthead-side">
          <div className="status" aria-label="Conversation status">
            <span className="status-dot" />
            {conversation ? "Conversation active" : "Ready to study"}
          </div>
          <div className="account">
            <span className="account-email">{session.user.email}</span>
            <button className="clear" type="button" onClick={() => signOut()}>
              Sign out
            </button>
          </div>
        </div>
      </header>

      <section className="workspace">
        <aside className="settings" aria-label="Study settings">
          <div>
            <p className="section-label">Your library</p>
            {hasBooks ? (
              <>
                <label htmlFor="book-select">Book</label>
                <select
                  id="book-select"
                  value={selectedBookId ?? ""}
                  onChange={(event) => selectBook(Number(event.target.value))}
                >
                  {books.map((book) => (
                    <option key={book.book_id} value={book.book_id}>
                      {book.title}
                      {book.author ? ` — ${book.author}` : ""}
                    </option>
                  ))}
                </select>
                {selectedBook && !selectedBook.retrieval_complete && (
                  <p className="upload-note">
                    Search data for this book is still building.
                  </p>
                )}
              </>
            ) : (
              <p className="upload-note">
                {booksLoaded
                  ? "No books yet. Upload a PDF to get started."
                  : "Loading your library…"}
              </p>
            )}

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

          <UploadPanel onBookReady={loadBooks} />

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
                <h2>
                  {hasBooks
                    ? "What would you like to understand?"
                    : "Upload a book to begin"}
                </h2>
                <p>
                  {hasBooks
                    ? "Start with one of these, or ask your own question."
                    : "Add a PDF from the panel on the left. It becomes selectable once processing and verification finish."}
                </p>
                {hasBooks && (
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
                )}
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
              placeholder={
                hasBooks ? "Ask about the book…" : "Upload a book first…"
              }
              disabled={isLoading || !hasBooks}
            />
            <button
              className="send"
              type="submit"
              disabled={isLoading || !question.trim() || !selectedBookId}
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
