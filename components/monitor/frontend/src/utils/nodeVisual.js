import ocpLogo from '../assets/logos/ocp.png'
import aapLogo from '../assets/logos/aap.png'
import itsmLogo from '../assets/logos/itsm.png'
import llmLogo from '../assets/logos/llm.png'

/** Visual role + MCP domain for timeline nodes. */

const MCP_DOMAINS = new Set(['OPENSHIFT', 'AAP', 'ITSM'])

export const NODE_LOGOS = {
  OPENSHIFT: ocpLogo,
  AAP: aapLogo,
  ITSM: itsmLogo,
  llm: llmLogo,
}

const DOMAIN_STYLES = {
  OPENSHIFT: {
    label: 'OpenShift',
    short: 'OCP',
    logoId: 'OPENSHIFT',
    color: '#ee0000',
    glow: 'rgba(238, 0, 0, 0.22)',
  },
  AAP: {
    label: 'AAP',
    short: 'AAP',
    logoId: 'AAP',
    color: '#3dd6c6',
    glow: 'rgba(61, 214, 198, 0.22)',
  },
  ITSM: {
    label: 'ITSM',
    short: 'ITSM',
    logoId: 'ITSM',
    // Logo is teal; align accent with asset instead of yellow.
    color: '#6ec4b8',
    glow: 'rgba(110, 196, 184, 0.22)',
  },
}

const LLM_STYLE = {
  label: 'LLM',
  short: 'AI',
  logoId: 'llm',
  // Logo uses blue/cyan gradient; accent harmonizes without losing AI feel.
  color: '#6b9fff',
  glow: 'rgba(107, 159, 255, 0.22)',
}

const USER_STYLE = {
  label: 'User',
  short: 'U',
  logoId: null,
  color: '#9aa8bc',
  glow: 'rgba(148, 176, 214, 0.08)',
  neutral: true,
}

const INCIDENT_STYLE = {
  label: 'Incident',
  short: '⚠',
  logoId: null,
  color: '#e8913a',
  glow: 'rgba(232, 145, 58, 0.22)',
}

const SYSTEM_STYLE = {
  label: 'System',
  short: '!',
  logoId: null,
  color: '#8fa3c1',
  glow: 'rgba(143, 163, 193, 0.15)',
}

function resolveDomain(node) {
  const detail = node.detail || {}
  const fromGroup = node.parallel_group
  const fromDetail = detail.domain || detail.category
  const label = (node.label || '').toUpperCase()

  if (fromGroup && MCP_DOMAINS.has(fromGroup)) return fromGroup
  if (typeof fromDetail === 'string' && MCP_DOMAINS.has(fromDetail.toUpperCase())) {
    return fromDetail.toUpperCase()
  }
  if (label.includes('OPENSHIFT') || label.includes('OCP')) return 'OPENSHIFT'
  if (label.includes('AAP') || label.includes('ANSIBLE')) return 'AAP'
  if (label.includes('ITSM')) return 'ITSM'
  if (node.type === 'article' || detail.tool === 'get_kb_article' || detail.tool === 'rag_search_kb') {
    return 'ITSM'
  }
  return null
}

function resolveRole(node) {
  const type = node.type || ''
  if (type === 'incident') return 'incident'
  if (type === 'user_message' || type === 'user_input') return 'user'
  if (type === 'tool_call' || type === 'step') return 'mcp'
  if (type === 'error') return 'system'
    if (
    type === 'classified' ||
    type === 'missing_info' ||
    type === 'procedure' ||
    type === 'procedure_confirm' ||
    type === 'final'
  ) {
    return 'llm'
  }
  if (type === 'article') return 'mcp'
  return 'llm'
}

export function getNodeVisual(node) {
  const role = resolveRole(node)
  const domain = role === 'mcp' ? resolveDomain(node) : null

  if (role === 'incident') {
    return { role, domain: null, ...INCIDENT_STYLE }
  }
  if (role === 'user') {
    return { role, domain: null, ...USER_STYLE }
  }
  if (role === 'system') {
    return { role, domain: null, ...SYSTEM_STYLE }
  }
  if (role === 'mcp' && domain && DOMAIN_STYLES[domain]) {
    return { role, domain, ...DOMAIN_STYLES[domain] }
  }
  if (role === 'mcp') {
    return {
      role,
      domain: null,
      label: 'MCP',
      short: 'MCP',
      logoId: null,
      color: '#8fa3c1',
      glow: 'rgba(143, 163, 193, 0.15)',
    }
  }
  return { role: 'llm', domain: null, ...LLM_STYLE }
}

export function getLogoSrc(logoId) {
  if (!logoId) return null
  return NODE_LOGOS[logoId] || null
}

export function formatNodeType(type) {
  if (type === 'user_message') return 'User message'
  if (type === 'user_input') return 'User input'
  if (type === 'incident') return 'Incident'
  return String(type || '').replaceAll('_', ' ')
}

export function parseThreadPreview(item) {
  const raw = item?.preview || item?.user_message || ''
  const match = String(raw).match(/^\[([^\]]+)\]\s*(.*)$/s)
  if (match) {
    return {
      category: match[1],
      message: match[2].trim() || raw,
    }
  }
  return {
    category: item?.category || null,
    message: String(raw).trim(),
  }
}
