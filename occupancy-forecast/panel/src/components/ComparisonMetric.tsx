/** A ratio against an almost perfect baseline can be arbitrarily large.
 * Keep the raw scores available, but wait for a useful comparison to display. */
export function ComparisonMetric({ count, baseline, skill }: {
  count: number
  baseline: number | null
  skill: number | null
}) {
  const enough = count >= 30
  const known = baseline !== null && Number.isFinite(baseline)
  const useful = known && baseline > 0.000001
  const ready = enough && useful && skill !== null && Number.isFinite(skill)
  return <div>
    <span>Compared with staying put</span>
    <strong>{ready ? `${Math.abs(skill).toFixed(1)}% ${skill >= 0 ? 'better' : 'worse'}`
      : !enough ? 'Early data' : !known ? 'Unavailable' : !useful ? 'Near-perfect baseline' : 'Unavailable'}</strong>
    <small>{!enough ? `${count.toLocaleString()} of 30 matched forecasts before a percentage comparison`
      : !known ? 'A matched baseline score is not available'
        : !useful ? 'The baseline is near perfect; a percentage would be misleading'
        : `${count.toLocaleString()} matched forecasts. Lower error is better.`}</small>
  </div>
}
