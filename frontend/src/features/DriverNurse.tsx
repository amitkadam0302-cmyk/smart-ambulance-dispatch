import { ClipboardCheck, Crosshair, Navigation, Radio, Square, Stethoscope } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { MissionMap } from '../components/Map'
import { Empty, ErrorNotice, RouteDetails, Section, StatusBadge, Timeline } from '../components/ui'
import { message } from '../lib/api'
import { useCommand } from '../lib/commands'
import { ageOf, terminal, time } from '../lib/display'
import { useSession } from '../lib/session'
import type { Incident, Meta, Mission, Snapshot } from '../lib/types'

const nextAction = (mission: Mission) => {
  switch (mission.status) {
    case 'assigned': return mission.released ? 'Confirm departure for pickup' : 'Waiting for sending hospital to release the unit'
    case 'en_route_to_patient': return 'Confirm arrival at pickup'
    case 'at_patient': return !mission.intake_saved ? 'Waiting for nurse intake' :
      mission.destination_status === 'accepted' ? 'Confirm departure for receiving hospital' : 'Waiting for receiving hospital acceptance'
    case 'transporting': return 'Confirm arrival at receiving hospital'
    case 'at_hospital': return 'Receiving hospital to confirm handover'
    default: return 'Mission finished'
  }
}

export function MissionSummary({ incident, children }: { incident: Incident; children?: React.ReactNode }) {
  const mission = incident.mission
  return <Section title={incident.code} detail={mission ? `${mission.unit} · ${incident.pickup_name}` : incident.pickup_name}
    action={<StatusBadge status={incident.status} />}>
    <div className="panel-body stack small-gap">
      {mission && <>
        <div className="key-row"><span>Sending hospital</span><strong>{mission.dispatching_hospital}</strong></div>
        <div className="key-row"><span>Receiving hospital</span><strong>{mission.receiving_hospital || 'Dispatcher coordination needed'}</strong></div>
        {mission.destination_status === 'pending' && <div className="info"><Radio size={16}/> Destination pending hospital acceptance.</div>}
        {mission.route && <RouteDetails route={mission.route} />}
        <ErrorNotice value={mission.routing_issue || ''} />
      </>}
      {children}
      <Timeline incident={incident}/>
    </div>
  </Section>
}

export function Driver({ data, meta, refresh }: { data: Snapshot; meta: Meta; refresh: () => Promise<void> }) {
  const command = useCommand(refresh)
  const { request } = useSession()
  const incident = data.incidents.find(i => i.mission && !terminal(i.status)) ?? data.incidents.find(i => i.mission)
  const mission = incident?.mission
  const missionId = mission && !terminal(mission.status) ? mission.id : null
  const watch = useRef<number | null>(null)
  const last = useRef(0)
  const [sharing, setSharing] = useState(false)
  const [gpsError, setGpsError] = useState('')
  const [routeFeedback, setRouteFeedback] = useState('')
  const unit = data.units.find(u => u.id === mission?.unit_id)
  const stop = () => {
    if (watch.current !== null) navigator.geolocation?.clearWatch(watch.current)
    watch.current = null
    setSharing(false)
  }

  useEffect(() => {
    return () => {
      if (watch.current !== null) navigator.geolocation?.clearWatch(watch.current)
      watch.current = null
      setSharing(false)
    }
  }, [missionId])

  useEffect(() => {
    function checkVisibility() {
      if (document.visibilityState !== 'visible') stop()
    }
    document.addEventListener('visibilitychange', checkVisibility)
    return () => document.removeEventListener('visibilitychange', checkVisibility)
  }, [])

  function share() {
    if (!missionId) return
    if (!navigator.geolocation || !window.isSecureContext) {
      setGpsError('GPS sharing requires HTTPS or localhost. Use the manual arrival and departure controls.')
      return
    }
    setSharing(true); setGpsError(''); last.current = 0
    watch.current = navigator.geolocation.watchPosition(position => {
      if (position.timestamp - last.current < 5000 || document.visibilityState !== 'visible') return
      last.current = position.timestamp
      void request(`/missions/${missionId}/location`, { method: 'POST', body: {
        lat: position.coords.latitude, lng: position.coords.longitude,
        accuracy_m: position.coords.accuracy, observed_at: new Date(position.timestamp).toISOString(),
      } }).catch(error => setGpsError(message(error)))
    }, () => {
      stop()
      setGpsError('GPS permission was denied or unavailable. Use the manual arrival and departure controls.')
    }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 2000 })
  }

  if (!mission || !incident) return <div className="overview-grid">
    <MissionMap fixture={meta.schematic} units={data.units} title="Assigned route" />
    <Section title="Driver mission"><Empty title="Standing by for dispatch" detail="Your unit assignment and route will appear here." /></Section>
  </div>

  const action = mission.status === 'assigned' && mission.released ? 'depart_pickup' :
    mission.status === 'en_route_to_patient' ? 'arrive_pickup' :
      mission.status === 'at_patient' && mission.intake_saved && mission.destination_status === 'accepted' ? 'depart_hospital' :
        mission.status === 'transporting' ? 'arrive_hospital' : null
  return <div className="overview-grid">
    <MissionMap fixture={meta.schematic} units={data.units} incident={incident} serverTime={data.server_time} title="Assigned route" />
    <MissionSummary incident={incident}>
      <div className="info"><Navigation size={17}/>{nextAction(mission)}</div>
      {action && <button className="button primary large" disabled={command.busy} onClick={() =>
        void command.run(`/missions/${mission.id}/transition`, 'POST', { action }, true)}>{nextAction(mission)}</button>}
      {missionId && meta.mode === 'connected' && <div className="stack small-gap">
        {sharing ? <button className="button subtle" onClick={stop}><Square size={15}/> Stop GPS sharing</button> :
          <button className="button subtle" onClick={share}><Crosshair size={15}/> Start GPS sharing</button>}
        <small className="meta">{unit?.source || 'Position unavailable'}{unit?.observed_at ? ` · ${time(unit.observed_at)} · ${ageOf(unit.observed_at)}` : ''}{unit?.stale ? ' · Stale GPS' : ''}</small>
        <ErrorNotice value={gpsError} />
        {['assigned', 'en_route_to_patient', 'transporting'].includes(mission.status) &&
          <button className="button subtle" disabled={command.busy} onClick={() => void command.run<{reason?: string}>(`/missions/${mission.id}/route`, 'POST').then(result => setRouteFeedback(result?.reason || ''))}>Recalculate route</button>}
        {routeFeedback && <p role="status" className="meta">{routeFeedback}</p>}
      </div>}
      <ErrorNotice value={command.error} />
      {mission.route?.directions.length ? <details><summary>Road directions</summary><ol className="directions">{mission.route.directions.map((step, index) => <li key={index}>{step}</li>)}</ol></details> : null}
    </MissionSummary>
  </div>
}

const issues = ['accident', 'reported chest pain', 'suspected stroke', 'pregnancy', 'other']

export function IntakeTablet({ mission, code, refresh }: { mission: Mission; code: string; refresh: () => Promise<void> }) {
  const command = useCommand(refresh)
  const [saved, setSaved] = useState(false)
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { heading.current?.focus() }, [])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const age = String(form.get('age'))
    const result = await command.run(`/missions/${mission.id}/intake`, 'POST', {
      alias: form.get('alias'), age: age === '' ? null : Number(age), issue: form.get('issue'),
      observations: form.get('observations') || null,
    }, true)
    if (result) setSaved(true)
  }

  if (saved) return null
  return <aside className="intake-tablet" aria-labelledby="intake-heading">
    <div className="tablet-head"><span className="tablet-icon"><Stethoscope size={21} /></span><div><h3 id="intake-heading" tabIndex={-1} ref={heading}>Nurse intake</h3><p>{code} · {mission.unit} · At pickup</p></div></div>
    <form className="stack small-gap" onSubmit={event => void submit(event)}>
      <label>Name or alias<input name="alias" autoComplete="off" maxLength={100} required placeholder="e.g. Demo patient A" /></label>
      <div className="form-grid intake-grid">
        <label>Age (optional)<input name="age" type="number" min="0" max="125" placeholder="Unknown" /></label>
        <label>Reported issue<select name="issue" defaultValue="accident">{issues.map(issue => <option key={issue} value={issue}>{issue[0].toUpperCase() + issue.slice(1)}</option>)}</select></label>
      </div>
      <label>Observations (optional)<textarea name="observations" rows={2} maxLength={1500} placeholder="What the patient reported" /></label>
      <ErrorNotice value={command.error} />
      <button className="button primary" disabled={command.busy}><ClipboardCheck size={17}/>{command.busy ? 'Saving intake…' : 'Save intake'}</button>
    </form>
  </aside>
}

export function Nurse({ data, meta, refresh }: { data: Snapshot; meta: Meta; refresh: () => Promise<void> }) {
  const incident = data.incidents.find(i => i.mission && !terminal(i.status)) ?? data.incidents.find(i => i.mission)
  const mission = incident?.mission
  return <div className="overview-grid">
    <MissionMap fixture={meta.schematic} units={data.units} incident={incident} serverTime={data.server_time} title="Patient pickup map">
      {incident && mission?.status === 'at_patient' && !mission.intake_saved &&
        <IntakeTablet key={mission.id} mission={mission} code={incident.code} refresh={refresh} />}
    </MissionMap>
    {incident ? <MissionSummary incident={incident}>{mission?.intake_saved ?
      <div className="info"><ClipboardCheck size={17}/> Intake saved{mission.destination_status !== 'accepted' ? ' · waiting for a receiving hospital' : ''}.</div> : null}</MissionSummary> :
      <Section title="Intake workspace"><Empty title="Waiting for pickup arrival" detail="The intake tablet opens when your assigned ambulance arrives at the patient." /></Section>}
  </div>
}
