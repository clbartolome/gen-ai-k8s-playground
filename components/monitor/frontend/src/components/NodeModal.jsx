import { AnimatePresence, motion } from 'framer-motion'
import { getNodeVisual } from '../utils/nodeVisual'
import NodeIcon from './NodeIcon'

const PRIORITY_KEYS = [
  'message',
  'question',
  'category',
  'intent',
  'decision',
  'phase',
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

export default function NodeModal({ node, open, onClose }) {
  const visual = node ? getNodeVisual(node) : null

  return (
    <AnimatePresence>
      {open && node ? (
        <motion.div
          className="modal-backdrop"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
          role="presentation"
        >
          <motion.div
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="modal-title"
            initial={{ opacity: 0, y: 24, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16, scale: 0.98 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
            onClick={(e) => e.stopPropagation()}
            style={
              visual
                ? {
                    '--modal-accent': visual.color,
                    '--modal-glow': visual.glow,
                  }
                : undefined
            }
          >
            <div className="modal-header">
              <div className="modal-badges">
                {visual ? (
                  <span
                    className="node-pill node-pill-logo"
                    style={{ borderColor: visual.color, color: visual.color }}
                  >
                    <NodeIcon visual={visual} />
                    {visual.label}
                  </span>
                ) : null}
                <span className="node-pill muted">{node.type?.replaceAll('_', ' ')}</span>
              </div>
              <button type="button" className="modal-close" onClick={onClose} aria-label="Close">
                ×
              </button>
            </div>
            <h2 id="modal-title" className="modal-title">
              {node.label}
            </h2>
            <p className="modal-sub">
              Status: {node.status || 'ok'}
              {node.parallel_group ? ` · ${node.parallel_group}` : ''}
            </p>
            <div className="kv modal-kv">
              {orderedEntries(node.detail || {}).map(([key, value]) => (
                <div className="kv-item" key={key}>
                  <p className="k">{key.replaceAll('_', ' ')}</p>
                  {renderValue(value)}
                </div>
              ))}
              {!Object.keys(node.detail || {}).length ? (
                <p className="hint">No extra detail for this step.</p>
              ) : null}
            </div>
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
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
