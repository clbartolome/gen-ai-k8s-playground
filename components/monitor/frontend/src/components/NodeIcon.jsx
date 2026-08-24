import { getLogoSrc } from '../utils/nodeVisual'

export default function NodeIcon({ visual }) {
  const logoSrc = getLogoSrc(visual?.logoId)

  if (logoSrc) {
    return (
      <span className="node-icon has-logo" aria-hidden>
        <img src={logoSrc} alt="" />
      </span>
    )
  }

  return (
    <span className="node-icon" aria-hidden>
      {visual?.short || '?'}
    </span>
  )
}

export function LaneLogo({ domain }) {
  const logoSrc = getLogoSrc(domain)
  if (!logoSrc) return null
  return (
    <span className="lane-logo" aria-hidden>
      <img src={logoSrc} alt="" />
    </span>
  )
}
