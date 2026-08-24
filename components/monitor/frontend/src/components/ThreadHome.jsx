import { parseThreadPreview } from '../utils/nodeVisual'

export default function ThreadHome({ traces, loading, onOpen }) {
  return (
    <main className="home">
      <div className="home-inner">
        <header className="home-header">
          <p className="eyebrow">Gen AI Playground</p>
          <h1>Process Monitor</h1>
          <p className="home-sub">
            Each Slack thread is one process. Pick a conversation to replay what happened inside the agent.
          </p>
        </header>

        {loading && !traces.length ? (
          <p className="home-hint">Loading threads…</p>
        ) : null}

        {!loading && !traces.length ? (
          <div className="home-empty">
            <h2>No threads yet</h2>
            <p>Send a message from the chat. Timelines appear here as the agent works.</p>
          </div>
        ) : (
          <ul className="thread-grid">
            {traces.map((item) => (
              <ThreadCard key={item.thread_id} item={item} onOpen={onOpen} />
            ))}
          </ul>
        )}
      </div>
    </main>
  )
}

function ThreadCard({ item, onOpen }) {
  const { category, message } = parseThreadPreview(item)
  const when = formatWhen(item.updated_at || item.created_at)

  return (
    <li>
      <button type="button" className="thread-card" onClick={() => onOpen(item.thread_id)}>
        <div className="thread-card-top">
          <StatusBadge status={item.status} />
          {category ? <span className="tag tag-category">{category}</span> : null}
          <time className="thread-date" dateTime={item.updated_at || item.created_at}>
            {when}
          </time>
        </div>
        <p className="thread-message">{message || 'Conversation'}</p>
        {item.response ? (
          <p className="thread-snippet">{truncate(item.response, 120)}</p>
        ) : null}
        <span className="thread-cta">View process →</span>
      </button>
    </li>
  )
}

function StatusBadge({ status }) {
  const labels = {
    done: 'Completed',
    pending: 'Waiting',
    running: 'Running',
    error: 'Error',
  }
  return (
    <span className={`tag tag-status tag-${status || 'done'}`}>
      {labels[status] || status || 'done'}
    </span>
  )
}

function formatWhen(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleString(undefined, {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

function truncate(text, max) {
  const clean = String(text).replace(/\s+/g, ' ').trim()
  if (clean.length <= max) return clean
  return `${clean.slice(0, max - 1).trim()}…`
}
