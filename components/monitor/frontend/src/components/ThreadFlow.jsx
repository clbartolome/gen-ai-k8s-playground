import TraceCanvas from './TraceCanvas'
import NodeModal from './NodeModal'
import { parseThreadPreview } from '../utils/nodeVisual'

export default function ThreadFlow({
  trace,
  loading,
  error,
  animationDone,
  replayKey,
  selectedNode,
  onBack,
  onReplay,
  onAnimationDone,
  onSelectNode,
  onCloseModal,
}) {
  const { category, message } = parseThreadPreview(trace || {})

  return (
    <div className="flow-screen">
      <header className="flow-header">
        <div className="flow-header-toolbar">
          <button type="button" className="back-btn" onClick={onBack}>
            ← Threads
          </button>
          <button type="button" className="ghost" onClick={onReplay} disabled={!trace}>
            Replay
          </button>
        </div>
        <div className="flow-header-main">
          {trace ? (
            <>
              <h1 className="flow-title">{message || 'Process'}</h1>
              <p className="flow-meta">
                {category ? <span className="tag tag-category">{category}</span> : null}
                <span className={`tag tag-status tag-${trace.status}`}>{trace.status}</span>
                <span className="flow-meta-text">
                  {new Date(trace.updated_at || trace.created_at).toLocaleString()}
                </span>
              </p>
            </>
          ) : (
            <h1 className="flow-title">Loading…</h1>
          )}
        </div>
      </header>

      {error ? <div className="banner error">{error}</div> : null}
      {loading && !trace ? <p className="home-hint">Loading process…</p> : null}

      {trace ? (
        <>
          {!animationDone ? (
            <p className="flow-hint">Replaying the internal path…</p>
          ) : (
            <p className="flow-hint">Tap any step to inspect details.</p>
          )}
          <TraceCanvas
            key={`${trace.thread_id}-${trace.updated_at}-${replayKey}`}
            nodes={trace.nodes || []}
            interactive={animationDone}
            selectedId={selectedNode?.id}
            onAnimationDone={onAnimationDone}
            onSelectNode={onSelectNode}
          />
        </>
      ) : null}

      <NodeModal node={selectedNode} open={Boolean(selectedNode)} onClose={onCloseModal} />
    </div>
  )
}
