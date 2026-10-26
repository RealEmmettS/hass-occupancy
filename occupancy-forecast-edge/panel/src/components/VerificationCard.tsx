import { useEffect, useState } from 'react'
import { getVerification } from '../api'
import type { Status, Verification as VerificationData } from '../types'
import { Card } from './Card'
import { HorizonSlider } from './HorizonSlider'
import { Row } from './Row'
import { Select } from './Select'
import { Verification } from './Verification'
import { useDebounced } from '../hooks'
import { absoluteTime } from '../format'

/**
 * "Was it right?" -- the same card on both tabs, each owning its own controls.
 *
 * It answers the one question nothing else here can. Every score on the Judge
 * step is rolling-origin cross-validation computed at TRAINING time over the
 * feature table: "how would a model fitted on folds [0,k) have scored on fold
 * k". That is a real number and it is not this one. It cannot see the nowcast
 * pin, a tracker that went quiet at 07:00, or the ship gate deciding a horizon
 * is not worth publishing -- and it is computed from rows the deployed add-on
 * may never have served.
 *
 * So the live Brier here and the backtest Brier there are different quantities,
 * and a gap between them is a finding about the deployment rather than a bug in
 * either. Saying so beside the backtest number is half of why this card exists.
 *
 * Rendered on both Overview and Data, with its own slider on each: on Overview
 * it sits under the 48-hour forecast, which is the natural order -- what the
 * forecast says, then what happened last time it said it. On Data it closes
 * step five, whose subtitle already asks the question.
 */

const DAY_OPTIONS = [
  { value: '1', label: 'last 24 hours' },
  { value: '7', label: 'last 7 days' },
  { value: '30', label: 'last 30 days' },
]

const pretty = (slug: string) => slug.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

export function VerificationCard({ status, defaultHorizon = 6 }: {
  status: Status | null
  /** 6 h by default because that is the window the Lovelace card watches and
   *  the one a heating decision rests on. */
  defaultHorizon?: number
}) {
  const [subject, setSubject] = useState<string>('')
  // Debounced for the same reason the Data tab's slider is: dragging end to end
  // is otherwise 48 requests, each of which reads the archive and rebuilds a
  // grid, and this endpoint is deliberately not cached.
  const [horizonInput, setHorizonInput] = useState<number>(defaultHorizon)
  const horizon = useDebounced(horizonInput, 250)
  const [days, setDays] = useState<string>('7')
  const [data, setData] = useState<VerificationData | null>(null)
  const [error, setError] = useState<string | null>(null)

  const subjects = ['house', ...(status?.people ?? [])]
  const chosen = subject || subjects[0] || ''

  useEffect(() => {
    if (!chosen) return
    let live = true
    setData(null)
    setError(null)
    getVerification(chosen, horizon, Number(days))
      .then((v) => { if (live) setData(v) })
      .catch((e: Error) => { if (live) setError(e.message) })
    return () => { live = false }
  }, [chosen, horizon, days])

  // What the slider's own line says. It is about THIS horizon's record rather
  // than the chart's contents, so it stays useful while the fetch is in flight.
  const sliderSummary = (() => {
    if (horizonInput !== horizon) return 'reading…'
    const serving = status?.served_by?.[String(horizonInput)]
    if (serving === 'none') {
      const beaten = status?.best_baseline?.[String(horizonInput)]
      return beaten
        ? `not published — ${beaten.replace(/_/g, ' ')} beat the model here`
        : 'not published — no model has been trained for this horizon yet'
    }
    if (!data) return 'reading…'
    if (!data.available) return data.reason
    return `${data.scored} of ${data.slots} slots could be scored`
  })()

  return (
    <Card title="Was it right?" subtitle="Published predictions compared with completed observations. Lower probability error is better.">
      {/* The same control rows the Data tab's cards use, rather than a bare
          pair of selects: a `Select` is wide enough to need a line of its own,
          which is exactly what `Row control` is for. */}
      <Row
        icon="people"
        control
        accent="blue"
        primary="Subject"
        trailing={
          <Select
            label="Subject"
            value={chosen}
            onChange={setSubject}
            options={subjects.length
              ? subjects.map((s) => ({ value: s, label: s === 'house' ? 'Tracked residents' : pretty(s) }))
              : [{ value: '', label: '— no subjects configured —' }]}
          />
        }
      />
      <Row
        icon="clock"
        control
        accent="blue"
        primary="Window"
        secondary={`Kept for ${data?.available ? data.retention_days : 30} days.`}
        trailing={
          <Select label="Time window" value={days} onChange={setDays}
                  options={DAY_OPTIONS} />
        }
      />

      <HorizonSlider value={horizonInput} onChange={setHorizonInput}
                     summary={sliderSummary} />

      {data?.available && <div className="verification-stats">
        <div><span>Probability error</span><strong>{data.brier?.toFixed(3) ?? '—'}</strong><small>Brier score · lower is better</small></div>
        <div><span>Forecast coverage</span><strong>{data.slots ? `${Math.round(100 * data.served / data.slots)}%` : '—'}</strong><small>{data.scored} completed observations scored</small></div>
        <div><span>Compared with staying put</span><strong>{data.skill_vs_persistence_pct == null ? 'Collecting' : `${Math.abs(data.skill_vs_persistence_pct).toFixed(0)}% ${data.skill_vs_persistence_pct >= 0 ? 'better' : 'worse'}`}</strong><small>{data.comparison_scored ? `Same ${data.comparison_scored} forecasts · issue-time baseline` : 'Comparison starts with this update'}</small></div>
      </div>}
      {data?.available && data.lead_minutes_min != null && <p className="verification-note">
        These forecasts were issued {Math.round(data.lead_minutes_min)}–{Math.round(data.lead_minutes_max ?? data.lead_minutes_min)} minutes before their target slot. Only completed slots are scored.
      </p>}

      {error ? <p className="empty">{error}</p>
        : !data ? <p className="empty">Loading…</p>
          : !data.available ? <p className="empty">{data.reason}</p>
            : (
              <Verification
                points={data.points}
                horizon={data.horizon_h}
                summary={data.summary}
                startLabel={absoluteTime(data.start)}
                endLabel={absoluteTime(data.stop)}
              />
            )}
    </Card>
  )
}
