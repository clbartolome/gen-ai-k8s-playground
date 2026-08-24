import { useCallback, useEffect, useState } from 'react'
import { fetchTrace, fetchTraces } from './api'
import ThreadHome from './components/ThreadHome'
import ThreadFlow from './components/ThreadFlow'
import './App.css'

export default function App() {
  const [view, setView] = useState('home')
  const [traces, setTraces] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [trace, setTrace] = useState(null)
  const [selectedNode, setSelectedNode] = useState(null)
  const [animationDone, setAnimationDone] = useState(false)
  const [replayKey, setReplayKey] = useState(0)
  const [loadingList, setLoadingList] = useState(true)
  const [loadingTrace, setLoadingTrace] = useState(false)
  const [error, setError] = useState(null)

  const refreshList = useCallback(async () => {
    try {
      const items = await fetchTraces()
      setTraces(items)
      if (view === 'home') setError(null)
      return items
    } catch (err) {
      if (view === 'home') setError(err.message || 'Could not load threads')
      return []
    }
  }, [view])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      setLoadingList(true)
      await refreshList()
      if (!cancelled) setLoadingList(false)
    })()
    const timer = setInterval(refreshList, 5000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [refreshList])

  useEffect(() => {
    if (view !== 'flow' || !selectedId) {
      return undefined
    }
    let cancelled = false
    ;(async () => {
      setLoadingTrace(true)
      try {
        const data = await fetchTrace(selectedId)
        if (cancelled) return
        setTrace(data)
        setSelectedNode(null)
        setAnimationDone(false)
        setError(null)
      } catch (err) {
        if (!cancelled) setError(err.message || 'Could not load process')
      } finally {
        if (!cancelled) setLoadingTrace(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [selectedId, replayKey, view])

  const openThread = (threadId) => {
    setSelectedId(threadId)
    setView('flow')
    setReplayKey((k) => k + 1)
    setTrace(null)
    setSelectedNode(null)
    setAnimationDone(false)
    setError(null)
  }

  const goHome = () => {
    setView('home')
    setSelectedId(null)
    setTrace(null)
    setSelectedNode(null)
    setError(null)
  }

  const onReplay = () => {
    setSelectedNode(null)
    setAnimationDone(false)
    setReplayKey((k) => k + 1)
  }

  if (view === 'home') {
    return (
      <div className="shell shell-home">
        {error ? <div className="banner error home-banner">{error}</div> : null}
        <ThreadHome traces={traces} loading={loadingList} onOpen={openThread} />
      </div>
    )
  }

  return (
    <div className="shell shell-flow">
      <ThreadFlow
        trace={trace}
        loading={loadingTrace}
        error={error}
        animationDone={animationDone}
        replayKey={replayKey}
        selectedNode={selectedNode}
        onBack={goHome}
        onReplay={onReplay}
        onAnimationDone={() => setAnimationDone(true)}
        onSelectNode={(node) => {
          if (!animationDone) return
          setSelectedNode(node)
        }}
        onCloseModal={() => setSelectedNode(null)}
      />
    </div>
  )
}
