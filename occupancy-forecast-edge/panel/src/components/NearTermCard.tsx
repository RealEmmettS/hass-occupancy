import { useEffect, useState } from 'react'
import { getNearTermVerification } from '../api'
import type { NearTermVerification, SubjectForecast } from '../types'
import { Card } from './Card'
import { relativeTime } from '../format'

const pretty = (s: string) => s.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
const clock = (s: string) => new Date(s).toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })

export function NearTermCard({ subjects, house }: { subjects: SubjectForecast[]; house: string }) {
  const [chosen, setChosen] = useState('')
  const [minutes, setMinutes] = useState(15)
  const [record, setRecord] = useState<NearTermVerification | null>(null)
  const [error, setError] = useState<string | null>(null)
  const subject = subjects.find(s => s.subject === chosen) ?? subjects[0]
  const forecast = subject?.near_term
  useEffect(() => {
    if (!subject) return
    let live = true
    setRecord(null)
    setError(null)
    getNearTermVerification(subject.subject, minutes)
      .then(r => { if (live) setRecord(r) })
      .catch((e: Error) => { if (live) setError(e.message) })
    return () => { live = false }
  }, [subject?.subject, minutes, forecast?.issued_at])
  const point = forecast?.points.find(p => p.minutes === minutes)
  return <Card title="The next two hours" subtitle="Chance of being home in five-minute windows, every 15 minutes.">
    <div className="near-subjects" aria-label="Near-term subject">
      {subjects.map(s => <button key={s.subject} type="button" aria-pressed={s.subject === subject?.subject}
        onClick={() => setChosen(s.subject)}>{s.subject === house ? 'Tracked residents' : pretty(s.subject)}</button>)}
    </div>
    {forecast ? <>
      <p className="verification-note">Issued {relativeTime(forecast.issued_at)} · {clock(forecast.issued_at)}.
        {' '}{subject?.subject === house ? 'Chance that at least one tracked resident is home.' : 'Select a time to see its live accuracy.'}</p>
      <div className="near-points" aria-label="Near-term probabilities">
        {forecast.points.map(p => <button key={p.minutes} type="button"
          aria-pressed={minutes === p.minutes} onClick={() => setMinutes(p.minutes)}
          aria-label={`In ${p.minutes} minutes, ${clock(p.target_at)}, ${p.p === null ? 'not published' : `${Math.round(p.p * 100)} percent chance of being home`}`}>
          <span>+{p.minutes} min</span><strong>{p.p === null ? '—' : `${Math.round(p.p * 100)}%`}</strong>
          <small>{clock(p.target_at)}</small>
        </button>)}
      </div>
      <p className="verification-note" aria-live="polite">{point?.reason
        ? `+${minutes} min: ${point.reason}.`
        : `+${minutes} min predicts whether most of the five minutes starting at ${point ? clock(point.target_at) : 'that time'} will be spent home.`}
        {' '}A blank means the model has not qualified for that person and time.</p>
    </> : <p className="empty">Waiting for the first near-term forecast cycle.</p>}
    <div className="near-record">
      <h3>Live accuracy at +{minutes} min <small>Last 7 days</small></h3>
      {error ? <p role="alert">Could not load accuracy: {error}</p>
        : !record ? <p className="verification-note">Loading the prediction record…</p>
        : record.scored === 0 ? <p className="verification-note">Collecting completed predictions. {record.attempts} issued attempts recorded; accuracy will appear after published target windows have been observed.</p>
        : <div className="verification-stats">
          <div><span>Probability error</span><strong>{record.brier?.toFixed(3)}</strong><small>Brier score · lower is better</small></div>
          <div><span>Published coverage</span><strong>{record.completed ? Math.round(record.published / record.completed * 100) : 0}%</strong><small>{record.scored} scored / {record.completed} completed attempts</small></div>
          <div><span>Against staying put</span><strong>{record.skill_pct === null ? '—' : `${record.skill_pct > 0 ? '+' : ''}${record.skill_pct.toFixed(1)}%`}</strong><small>{record.skill_pct === null ? 'No baseline errors to compare yet' : 'Positive means less probability error'}</small></div>
        </div>}
      <p className="verification-note">Compared with later tracker readings. Repeated predictions overlap and are not independent trials.</p>
    </div>
  </Card>
}
