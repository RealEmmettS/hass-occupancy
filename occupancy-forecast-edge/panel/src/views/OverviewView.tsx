import { useEffect, useState, type ReactNode } from 'react'
import { getForecast } from '../api'
import type { Forecast, NextChange, Status, SubjectForecast } from '../types'
import { Card } from '../components/Card'
import { Chip } from '../components/Chip'
import { Row } from '../components/Row'
import { Curves, type Curve } from '../components/Curves'
import { Horizons } from '../components/Horizons'
import { Training } from '../components/Training'
import { VerificationCard } from '../components/VerificationCard'
import { NearTermCard } from '../components/NearTermCard'
import { relativeTime } from '../format'

/** Published presence and forecasts come first; accuracy and model maintenance
 * follow in reading order at every viewport width. Values come from the same
 * published payload as Home Assistant's entities. */

const CURVE_ACCENT = ['blue', 'orange', 'aqua', 'red'] as const

/** `alice` -> `Alice`. The slug is derived from the person's entity id, so it is
 *  the closest thing to a name the add-on has without asking Home Assistant. */
const pretty = (slug: string) => slug.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

const percent = (v: number) => `${(v * 100).toFixed(1)}%`

/**
 * The one sentence a row shows: what is coming, and when.
 *
 * The model decides WHETHER and the routine decides WHEN -- see
 * `predict._next_change`. Two lines here used to read "Expected back in 3 h."
 * over "Expected back around 18:00.", which is one event with two answers 2.4 h
 * apart and reads as a fault however it is worded.
 *
 * The date is named when it is not today: a bare "08:00" on a row about a change
 * fifteen hours away is worse than the relative form it replaced.
 */
function changeSentence(change: NextChange): string {
  if (change.direction === null || change.at === null) {
    return 'No arrival or departure time is predicted.'
  }
  const at = new Date(change.at)
  if (Number.isNaN(at.getTime())) return 'No arrival or departure time is predicted.'

  const clock = at.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  const sameDay = at.toDateString() === new Date().toDateString()
  const when = sameDay ? clock : `${clock} ${dayWord(at)}`
  return change.direction === 'leaving'
    ? `Expected to leave around ${when}.`
    : `Expected back around ${when}.`
}

/** "tomorrow", or a weekday name past that. */
function dayWord(at: Date): string {
  const days = Math.round(
    (new Date(at.toDateString()).getTime() - new Date(new Date().toDateString()).getTime())
    / 86_400_000)
  return days === 1 ? 'tomorrow'
    : at.toLocaleDateString(undefined, { weekday: 'long' })
}

/** "in 3 h", "within the hour", from a moment rather than the raw crossing --
 *  so the chip and the sentence cannot disagree about the same event. */
function untilLabel(at: string | null): string {
  if (at === null) return 'no estimate'
  const hours = (new Date(at).getTime() - Date.now()) / 3_600_000
  if (!Number.isFinite(hours)) return 'no estimate'
  if (hours <= 1) return 'within the hour'
  return `in ${Math.round(hours)} h`
}


function SubjectRows({ subjects, house }: { subjects: SubjectForecast[]; house: string }) {
  return (
    <div className="presence-list">
      {subjects.map((s) => {
        const homeNow = s.current !== null && s.current >= 0.5
        const soon = s.curve['1']
        return (
          <article className="presence-person" key={s.subject}>
            <h3>{s.subject === house ? 'Tracked residents' : pretty(s.subject)}</h3>
            <p className={`presence-state ${s.current === null ? 'unknown' : homeNow ? 'home' : 'away'}`}>
              <i aria-hidden="true" />{s.current === null ? 'Unknown' : homeNow ? 'Home' : 'Away'}
            </p>
            <p className="presence-next">{soon === undefined ? 'No one-hour prediction'
              : `${percent(soon)} chance of home in an hour`}</p>
          </article>
        )
      })}
    </div>
  )
}

function ChangeRows({ subjects, house }: { subjects: SubjectForecast[]; house: string }) {
  return (
    <>
      {subjects.map((s) => {
        const change: NextChange = s.next_change
          ?? { direction: null, in_hours: null, at: null, at_from: null }
        const leaving = change.direction === 'leaving'
        const arriving = change.direction === 'arriving'
        const name = s.subject === house ? 'Tracked residents' : pretty(s.subject)
        // The ETA earns its place only while they are demonstrably travelling:
        // `eta_minutes` is null otherwise, which is why the old "if already on
        // the way" hedge could be dropped.
        const secondary = changeSentence(change) +
          (arriving && s.eta_minutes !== null
            ? ` On the way, ${Math.round(s.eta_minutes)} min out.`
            : '')
        return (
          <Row
            key={s.subject}
            icon={leaving || arriving ? 'clock' : 'model'}
            accent={leaving ? 'orange' : arriving ? 'blue' : 'aqua'}
            primary={name}
            secondary={secondary}
            trailing={
              <Chip
                label={untilLabel(change.at)}
                icon={leaving || arriving ? 'clock' : 'model'}
                accent={leaving ? 'orange' : arriving ? 'blue' : 'aqua'}
              />
            }
          />
        )
      })}
    </>
  )
}

export function OverviewView({ status, refreshStatus }: {
  status: Status | null
  refreshStatus: () => Promise<void>
}) {
  const [forecast, setForecast] = useState<Forecast | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    const load = () =>
      getForecast()
        .then((f) => { if (live) { setForecast(f); setError(null) } })
        .catch((e) => { if (live) setError(String(e)) })
    load()
    // The forecast is republished every cycle; a minute is well inside that and
    // far outside anything that would make this tab a load.
    const timer = setInterval(load, 60_000)
    return () => { live = false; clearInterval(timer) }
  }, [])

  // One sentence standing in for whichever forecast cards cannot be drawn, and
  // null once there is a forecast. Deliberately NOT an early return: on day one
  // "nothing published yet" and "not trained yet" are the same story told from
  // both ends, and returning here would show only the first half of it while
  // hiding the two cards that explain why.
  const missing: ReactNode | null = error
    ? <p className="empty error">{error}</p>
    : !forecast
      ? <p className="empty">Loading…</p>
      : !forecast.available
        ? (
          <p className="empty">
            Nothing published yet — the first forecast lands a few minutes after start.
          </p>
        )
        : null

  const ready = forecast?.available ? forecast : null
  const house = ready?.house ?? ''
  const ordered = ready
    ? [...ready.subjects.filter((s) => s.subject !== house),
       ...ready.subjects.filter((s) => s.subject === house)]
    : []

  // `?? null`, never `?? 0`. `curve` is sparse -- a horizon no model serves is
  // simply not in it -- and a zero here is not a missing value, it is the
  // strongest claim the chart can make: certainly away, for exactly the hours
  // the add-on has nothing to say about.
  const curves: Curve[] = ordered.map((s, i) => ({
    key: s.subject,
    label: s.subject === house ? 'Tracked residents' : pretty(s.subject),
    accent: CURVE_ACCENT[i % CURVE_ACCENT.length]!,
    values: (ready?.horizons ?? []).map((h) => s.curve[String(h)] ?? null),
  }))

  // A cycle ran, but no horizon has a model. Distinct from `missing` above,
  // which is "no cycle has run": the two cards on the right explain this one,
  // so only the chart needs replacing.
  const nothingServed = ready !== null && curves.length > 0
    && curves.every((c) => c.values.every((v) => v === null))

  return (
    <div className="stack overview-stack">
      <Card title="Right now" subtitle={ready ? `Latest readings published ${relativeTime(ready.predicted_at)}.` : undefined}>
        {missing ?? <SubjectRows subjects={ordered} house={house} />}
        <p className="scope-note">Tracked residents means at least one of the selected people is home.</p>
      </Card>

      <NearTermCard subjects={ordered} house={house} />
      <Card title="The next 48 hours">
        {missing ?? (nothingServed ? (
          <p className="empty">
            No horizon has a trained model yet, so there is nothing to draw —
            see Published horizons below.
          </p>
        ) : (
          <Curves
            curves={curves}
            hours={ready?.horizons ?? []}
            night={ready?.night ?? []}
            // The slot the horizons are measured from, so the axis can show
            // clock times. Every subject shares it -- they come off one row of
            // one feature table -- so the first is the household's.
            at={ordered[0]?.observed_at}
            label={`Probability of being home over the next ${
              ready?.horizons[ready.horizons.length - 1]} hours, `
              + `for ${curves.map((c) => c.label).join(', ')}.`}
          />
        ))}
      </Card>

      {/* Under the forecast, and that order is the argument: what it says, then
          what happened last time it said it. Its own slider, because the
          horizon worth checking is rarely the one you were just looking at. */}
      <Card title="Next expected change" subtitle="Timing estimates based on the hourly forecast and observed routines.">
        {missing ?? <ChangeRows subjects={ordered} house={house} />}
      </Card>

      <VerificationCard status={status} />

      <div className="section-heading"><h2>Behind the forecast</h2><p>Model coverage and collection controls.</p></div>
      <div className="cards model-support">
        <Card title="Published horizons">
          {status ? <Horizons served={status.served_by} kinds={status.model_kind} beatenBy={status.best_baseline} />
            : <p className="empty">Loading…</p>}
        </Card>
        <Card title="Training & collection">
          {status ? <Training status={status} refresh={refreshStatus} /> : <p className="empty">Loading…</p>}
        </Card>
      </div>
    </div>
  )
}
