export default function TraceList({ traces, selectedId, onSelect, loading }) {
  return (
    <aside className="panel">
      <div className="panel-header">
        <h2>Threads</h2>
        <span className="badge">{traces.length}</span>
      </div>
      {loading && !traces.length ? (
        <div className="detail-body">
          <p className="hint">Loading traces…</p>
        </div>
      ) : (
        <ul className="trace-list">
          {traces.map((item) => (
            <li key={item.thread_id}>
              <button
                type="button"
                className={`trace-item${selectedId === item.thread_id ? ' active' : ''}`}
                onClick={() => onSelect(item.thread_id)}
              >
                <p className="preview">{item.preview || item.user_message}</p>
                <p className="sub">
                  <span className={`badge ${item.status}`}>{item.status}</span>
                  {item.category && <span className="badge">{item.category}</span>}
                  <span>{formatWhen(item.updated_at || item.created_at)}</span>
                </p>
              </button>
            </li>
          ))}
          {!traces.length && (
            <li className="detail-body">
              <p className="hint">No thread traces stored yet.</p>
            </li>
          )}
        </ul>
      )}
    </aside>
  )
}

function formatWhen(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}
