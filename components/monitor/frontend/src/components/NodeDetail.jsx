const PRIORITY_KEYS = [
  'message',
  'question',
  'category',
  'intent',
  'article_id',
  'tool',
  'domain',
  'arguments',
  'missing_parameters',
  'known_parameters',
  'result_summary',
  'error',
  'response',
  'action',
  'procedure',
  'steps',
  'detail',
  'summary',
]

export default function NodeDetail({ node, locked }) {
  return (
    <aside className="panel">
      <div className="panel-header">
        <h2>Details</h2>
      </div>
      <div className="detail-body">
        {locked && !node && (
          <div className="locked">
            <p>Watch the path unfold. When the replay finishes, click any box.</p>
          </div>
        )}
        {!locked && !node && (
          <p className="hint">Select a node to inspect tool calls, missing fields, and responses.</p>
        )}
        {node && (
          <>
            <h3>{node.label}</h3>
            <p className="muted">
              {node.type} · {node.status || 'ok'}
              {node.parallel_group ? ` · ${node.parallel_group}` : ''}
            </p>
            <div className="kv">
              {orderedEntries(node.detail || {}).map(([key, value]) => (
                <div className="kv-item" key={key}>
                  <p className="k">{key.replaceAll('_', ' ')}</p>
                  {renderValue(value)}
                </div>
              ))}
              {!Object.keys(node.detail || {}).length && (
                <p className="hint">No extra detail for this step.</p>
              )}
            </div>
          </>
        )}
      </div>
    </aside>
  )
}

function orderedEntries(detail) {
  const keys = Object.keys(detail)
  keys.sort((a, b) => {
    const ia = PRIORITY_KEYS.indexOf(a)
    const ib = PRIORITY_KEYS.indexOf(b)
    if (ia === -1 && ib === -1) return a.localeCompare(b)
    if (ia === -1) return 1
    if (ib === -1) return -1
    return ia - ib
  })
  return keys.map((key) => [key, detail[key]])
}

function renderValue(value) {
  if (value == null || value === '') {
    return <p className="v">—</p>
  }
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return <p className="v">{String(value)}</p>
  }
  return <pre className="mono">{JSON.stringify(value, null, 2)}</pre>
}
