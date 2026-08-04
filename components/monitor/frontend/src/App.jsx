import { useCallback, useEffect, useState } from 'react'
import { fetchTrace, fetchTraces } from './api'
import TraceList from './components/TraceList'
import TraceCanvas from './components/TraceCanvas'
import NodeDetail from './components/NodeDetail'
import './App.css'

export default function App() {
  const [traces, setTraces] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [trace, setTrace] = useState(null)
  const [selectedNode, setSelectedNode] = useState(null)
  const [animationDone, setAnimationDone] = useState(false)
  const [replayKey, setReplayKey] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const refreshList = useCallback(async () => {
    try {
      const items = await fetchTraces()
      setTraces(items)
      setError(null)
      return items
    } catch (err) {
      setError(err.message || 'Could not load traces')
      return []
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoading(true)
      const items = await refreshList()
      if (!cancelled && items.length && !selectedId) {
        setSelectedId(items[0].thread_id)
      }
      if (!cancelled) setLoading(false)
    })()
    const timer = setInterval(refreshList, 5000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [refreshList, selectedId])

  useEffect(() => {
    if (!selectedId) {
      setTrace(null)
      return
    }
    let cancelled = false
    ;(async () => {
      try {
        const data = await fetchTrace(selectedId)
        if (cancelled) return
        setTrace(data)
        setSelectedNode(null)
        setAnimationDone(false)
        setError(null)
      } catch (err) {
        if (!cancelled) setError(err.message || 'Could not load trace')
      }
    })()
    return () => {
      cancelled = true
    }
  }, [selectedId, replayKey])

  const onSelectTrace = (threadId) => {
    setSelectedId(threadId)
    setReplayKey((k) => k + 1)
  }

  const onReplay = () => {
    setSelectedNode(null)
    setAnimationDone(false)
    setReplayKey((k) => k + 1)
  }

  return (
    <div className="shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Gen AI Playground</p>
          <h1>Process Monitor</h1>
        </div>
        <p className="tagline">
          One timeline per conversation thread — follow every turn of the process.
        </p>
      </header>

      <div className="layout">
        <TraceList
          traces={traces}
          selectedId={selectedId}
          onSelect={onSelectTrace}
          loading={loading}
        />

        <main className="stage">
          {error && <div className="banner error">{error}</div>}
          {!trace && !error && (
            <div className="empty">
              <h2>No threads yet</h2>
              <p>Send a message from the chat. Each Slack thread becomes one process timeline.</p>
            </div>
          )}
          {trace && (
            <>
              <div className="stage-toolbar">
                <div>
                  <p className="mono run-id">{trace.thread_id}</p>
                  <p className="meta">
                    {trace.category || '…'} · {trace.status} ·{' '}
                    {new Date(trace.updated_at || trace.created_at).toLocaleString()}
                  </p>
                </div>
                <button type="button" className="ghost" onClick={onReplay}>
                  Replay
                </button>
              </div>
              <TraceCanvas
                key={`${trace.thread_id}-${trace.updated_at}-${replayKey}`}
                nodes={trace.nodes || []}
                interactive={animationDone}
                selectedId={selectedNode?.id}
                onAnimationDone={() => setAnimationDone(true)}
                onSelectNode={(node) => {
                  if (!animationDone) return
                  setSelectedNode(node)
                }}
              />
            </>
          )}
        </main>

        <NodeDetail node={selectedNode} locked={!animationDone} />
      </div>
    </div>
  )
}
