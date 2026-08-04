export async function fetchTraces() {
  const res = await fetch('/api/traces?limit=100')
  if (!res.ok) throw new Error(`Failed to list traces (${res.status})`)
  const data = await res.json()
  return data.traces || []
}

export async function fetchTrace(threadId) {
  const res = await fetch(`/api/traces/${encodeURIComponent(threadId)}`)
  if (!res.ok) throw new Error(`Failed to load trace (${res.status})`)
  return res.json()
}
