import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useMemo, useState } from 'react'
import { getNodeVisual } from '../utils/nodeVisual'
import NodeIcon, { LaneLogo } from './NodeIcon'

const STEP_MS = 520

function buildRows(nodes) {
  const rows = []
  let i = 0
  while (i < nodes.length) {
    const node = nodes[i]
    if (node.type === 'step' && node.parallel_group) {
      const laneNodes = []
      while (
        i < nodes.length &&
        nodes[i].type === 'step' &&
        nodes[i].parallel_group
      ) {
        laneNodes.push(nodes[i])
        i += 1
      }
      rows.push({ kind: 'lanes', nodes: laneNodes })
      continue
    }
    rows.push({ kind: 'single', nodes: [node] })
    i += 1
  }
  return rows
}

function groupByLane(nodes) {
  const order = []
  const map = new Map()
  for (const node of nodes) {
    const key = node.parallel_group || 'OTHER'
    if (!map.has(key)) {
      map.set(key, [])
      order.push(key)
    }
    map.get(key).push(node)
  }
  return order.map((key) => ({ lane: key, nodes: map.get(key) }))
}

export default function TraceCanvas({
  nodes,
  interactive,
  selectedId,
  onSelectNode,
  onAnimationDone,
}) {
  const rows = useMemo(() => buildRows(nodes || []), [nodes])
  const [visibleCount, setVisibleCount] = useState(0)

  useEffect(() => {
    setVisibleCount(0)
    if (!rows.length) {
      onAnimationDone?.()
      return undefined
    }
    let n = 0
    const timer = setInterval(() => {
      n += 1
      setVisibleCount(n)
      if (n >= rows.length) {
        clearInterval(timer)
        onAnimationDone?.()
      }
    }, STEP_MS)
    return () => clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows])

  const visibleRows = rows.slice(0, visibleCount)

  return (
    <div className="canvas-wrap">
      <div className="flow">
        <AnimatePresence>
          {visibleRows.map((row, index) => (
            <motion.div
              key={`row-${index}-${row.nodes[0]?.id}`}
              className="flow-row"
              initial={{ opacity: 0, y: 18, scale: 0.96 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              transition={{ duration: 0.38, ease: [0.22, 1, 0.36, 1] }}
            >
              {index > 0 && <Connector prev={visibleRows[index - 1]?.nodes} next={row.nodes} />}
              {row.kind === 'single' ? (
                <TraceNode
                  node={row.nodes[0]}
                  interactive={interactive}
                  selected={selectedId === row.nodes[0].id}
                  onSelect={onSelectNode}
                />
              ) : (
                <div className="lanes">
                  {groupByLane(row.nodes).map((group, gIndex) => (
                    <motion.div
                      key={group.lane}
                      className={`lane ${group.lane}`}
                      initial={{ opacity: 0, y: 12 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ delay: gIndex * 0.12, duration: 0.35 }}
                    >
                      <p className="lane-label">
                        <LaneLogo domain={group.lane} />
                        {group.lane}
                      </p>
                      {group.nodes.map((node, nIndex) => (
                        <motion.div
                          key={node.id}
                          initial={{ opacity: 0, x: -8 }}
                          animate={{ opacity: 1, x: 0 }}
                          transition={{ delay: gIndex * 0.12 + nIndex * 0.08 }}
                        >
                          <TraceNode
                            node={node}
                            lane
                            interactive={interactive}
                            selected={selectedId === node.id}
                            onSelect={onSelectNode}
                          />
                        </motion.div>
                      ))}
                    </motion.div>
                  ))}
                </div>
              )}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </div>
  )
}

function Connector({ prev, next }) {
  const from = prev?.[prev.length - 1]
  const to = next?.[0]
  const fromVisual = from ? getNodeVisual(from) : null
  const toVisual = to ? getNodeVisual(to) : null
  const color = toVisual?.color || fromVisual?.color || 'var(--accent)'
  return (
    <div
      className="connector"
      aria-hidden
      style={{
        background: `linear-gradient(180deg, ${fromVisual?.color || color}55, ${color})`,
      }}
    />
  )
}

function TraceNode({ node, interactive, selected, onSelect, lane = false }) {
  const status = node.status || 'ok'
  const visual = getNodeVisual(node)

  return (
    <button
      type="button"
      className={[
        'node',
        `node-role-${visual.role}`,
        visual.domain ? `node-domain-${visual.domain}` : '',
        lane ? 'lane-node' : '',
        interactive ? 'clickable' : '',
        selected ? 'selected' : '',
        `status-${status}`,
      ]
        .filter(Boolean)
        .join(' ')}
      onClick={() => onSelect?.(node)}
      disabled={!interactive}
      style={{
        '--node-accent': visual.color,
        '--node-glow': visual.glow,
      }}
    >
      <span className="status-dot" aria-hidden />
      <div className="node-head">
        <NodeIcon visual={visual} />
        <p className="type">{visual.label}</p>
      </div>
      <p className="label">{node.label}</p>
      <p className="node-kind">{node.type?.replaceAll('_', ' ')}</p>
    </button>
  )
}
