import { Activity, Ambulance, BedDouble, CircleCheck, Clock3, ListFilter, Phone, Radio,
  RotateCcw, TrafficCone, X } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { MissionMap } from '../components/Map'
import { Empty, ErrorNotice, RouteDetails, Section, StatusBadge, Timeline } from '../components/ui'
import { useCommand } from '../lib/commands'
import { useSession } from '../lib/session'
import { ageOf, distance, minutes, terminal, time } from '../lib/display'
import type { Candidate, Incident, Meta, Snapshot, Unit } from '../lib/types'
import { Capacities } from './Hospital'

export function Metrics({ data }: { data: Snapshot }) {
  const { metrics } = data
  const beds = data.hospitals.reduce((sum, hospital) => {
    const c = hospital.capacity
    return sum + (c && c.accepting && !c.stale ? c.available : 0)
  }, 0)
  const cards = [
    { label: 'Active ambulances', value: metrics.active_units, detail: `${metrics.available_units} available in fleet`, icon: Ambulance, color: 'blue' },
    { label: 'Queued requests', value: metrics.queued, detail: metrics.queued ? 'Ordered by priority and time' : 'No requests waiting', icon: ListFilter, color: 'amber' },
    { label: 'Median pickup time', value: metrics.median_arrival_seconds === null ? '—' : minutes(metrics.median_arrival_seconds),
      detail: metrics.median_arrival_seconds === null ? 'No completed pickups yet' : 'Request to confirmed arrival', icon: Clock3, color: 'purple' },
    { label: 'ER beds available', value: beds, detail: 'Fresh reports from accepting hospitals', icon: BedDouble, color: 'green' },
  ]
  return <div className="metrics">{cards.map(c => <article className="metric panel" key={c.label}>
    <span className={'metric-icon ' + c.color}><c.icon size={20}/></span><div><p>{c.label}</p><strong>{c.value}</strong><small>{c.detail}</small></div></article>)}</div>
}

function Candidates({ rows, chosen }: { rows: Candidate[]; chosen?: string }) {
  if (!rows.length) return <p className="meta">Routing candidates are available after a dispatch attempt.</p>
  return <div className="candidate-list">{[...rows].sort((a, b) => (a.eta_seconds ?? Infinity) - (b.eta_seconds ?? Infinity) || a.name.localeCompare(b.name)).map(item =>
    <div className={'candidate ' + (item.id === chosen ? 'chosen' : '')} key={item.id}>
      <div><strong>{item.name}{item.id === chosen && <CircleCheck size={14}/>}</strong><small>{item.base || item.reason}</small>
        <small>{item.eta_seconds !== undefined ? `${item.source} · ${time(item.calculated_at!)}` : item.base ? item.reason : ''}</small></div>
      <div className="candidate-values"><strong>{item.eta_seconds !== undefined ? minutes(item.eta_seconds) : 'Excluded'}</strong>
        {item.distance_m !== undefined && <small>{distance(item.distance_m)}</small>}</div>
    </div>)}</div>
}

function PhoneDialog({ meta, close, refresh }: { meta: Meta; close: () => void; refresh: () => Promise<void> }) {
  const ref = useRef<HTMLDialogElement>(null)
  const command = useCommand(refresh)
  useEffect(() => {
    const dialog = ref.current
    dialog?.showModal()
    return () => dialog?.close()
  }, [])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    if (await command.run('/incidents', 'POST', {
      pickup_name: form.get('pickup'), lat: Number(form.get('lat')), lng: Number(form.get('lng')),
      reported_urgency: form.get('reported'), priority: form.get('priority') || null, source: 'phone',
      unit_requirement: form.get('unit') || null, receiving_requirement: form.get('receiving') || null,
    }, true)) close()
  }

  return <dialog ref={ref} onCancel={close} className="dialog">
    <div className="panel-head"><div><h2>Log phone request</h2><p>Record the pickup and staff-confirmed priority.</p></div>
      <button className="icon-button" aria-label="Close phone request" onClick={close}><X size={20}/></button></div>
    <form className="panel-body stack" onSubmit={event => void submit(event)}>
      <label>Pickup location<input name="pickup" required maxLength={140} defaultValue={meta.pickup?.name}/></label>
      <div className="form-grid"><label>Latitude<input name="lat" type="number" step="any" min="-90" max="90" required defaultValue={meta.pickup?.position[0]}/></label>
        <label>Longitude<input name="lng" type="number" step="any" min="-180" max="180" required defaultValue={meta.pickup?.position[1]}/></label></div>
      <div className="form-grid"><label>Reported urgency<select name="reported" defaultValue="normal"><option value="normal">Normal</option><option value="high">High</option><option value="critical">Critical</option></select></label>
        <label>Confirmed priority<select name="priority"><option value="">Unreviewed</option><option value="normal">Normal</option><option value="high">High</option><option value="critical">Critical</option></select></label></div>
      <div className="form-grid"><label>Unit requirement<select name="unit"><option value="">Any eligible unit</option><option value="advanced">Advanced equipment</option></select></label>
        <label>Receiving service<select name="receiving"><option value="">Any eligible hospital</option>{['trauma', 'cardiac', 'maternity', 'stroke'].map(r => <option key={r}>{r}</option>)}</select></label></div>
      <ErrorNotice value={command.error}/><button className="button primary" disabled={command.busy}><Phone size={17}/> Save phone request</button>
    </form>
  </dialog>
}

function IncidentControls({ incident, data, refresh }: { incident: Incident; data: Snapshot; refresh: () => Promise<void> }) {
  const command = useCommand(refresh)
  const mission = incident.mission
  const path = `/incidents/${incident.id}`
  const [feedback, setFeedback] = useState('')
  const [editing, setEditing] = useState(false)
  const mayRoute = mission && ['assigned','en_route_to_patient','transporting'].includes(mission.status)
  const mayChange = mission && ['assigned','en_route_to_patient','at_patient'].includes(mission.status)
  function staffForm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const get = (key: string) => String(form.get(key) || '') || null
    const reason = get('reason')
    if (get('operation') === 'review') void command.run(path + '/priority', 'PUT', {
      priority: get('priority'), receiving_requirement: get('requirement'), unit_requirement: get('unit_requirement'), reason,
    })
    if (get('operation') === 'assign') void command.run(path + '/dispatch', 'POST', {
      unit_id: get('unit_id'), reason, manual_eta_seconds: get('eta') ? Number(get('eta')) * 60 : null,
    })
    if (get('operation') === 'coordinate' && mission) void command.run(`/missions/${mission.id}/destination`, 'PUT', {
      hospital_id: get('hospital'), reason,
    })
    if (get('operation') === 'cancel') void (mission
      ? command.run(`/missions/${mission.id}/transition`, 'POST', { action: 'cancel', reason }, true)
      : command.run(path + '/cancel?reason=' + encodeURIComponent(reason || ''), 'POST'))
  }
  return <>
    {mayRoute && <button className="button subtle" disabled={command.busy || (data.session.mode === 'demo' && mission.traffic_injected)} onClick={() => {
      const inject = data.session.mode === 'demo'
      void command.run<{reason: string}>(`/missions/${mission.id}/route?inject=${inject}`, 'POST').then(result => setFeedback(result?.reason || ''))
    }}><TrafficCone size={16}/>{data.session.mode === 'demo' ? (mission.traffic_injected ? 'Traffic injected' : 'Inject traffic') : 'Recalculate active route'}</button>}
    {feedback && <p className="info" role="status">{feedback}</p>}
    {!terminal(incident.status) && <button className="button subtle" aria-expanded={editing} onClick={() => setEditing(!editing)}><Radio size={16}/> Dispatcher actions</button>}
    {editing && !terminal(incident.status) && <div className="stack small-gap staff-forms">
      {incident.status === 'queued' && <>
        <button className="button primary" disabled={command.busy} onClick={() => void command.run(path + '/dispatch', 'POST', {})}>Retry automatic dispatch</button>
        <form className="stack small-gap" onSubmit={staffForm}>
          <div className="form-grid"><label>Confirmed priority<select name="priority" defaultValue={incident.effective_priority}><option value="critical">Critical</option><option value="high">High</option><option value="normal">Normal</option></select></label>
            <label>Required equipment<select name="unit_requirement" defaultValue={incident.unit_requirement || ''}><option value="">Any</option><option value="advanced">Advanced</option></select></label></div>
          <label>Required receiving service<select name="requirement" defaultValue={incident.receiving_requirement || ''}><option value="">Any</option>{['trauma','cardiac','maternity','stroke'].map(x => <option key={x}>{x}</option>)}</select></label>
          <label>Review reason<input name="reason" required minLength={3} maxLength={180}/></label>
          <input type="hidden" name="operation" value="review"/><button className="button subtle" disabled={command.busy}>Confirm priority</button>
        </form>
      </>}
      {(incident.status === 'queued' || incident.status === 'assigned') && <form className="stack small-gap" onSubmit={staffForm}>
        <label>Available unit<select name="unit_id" required><option value="">Choose unit</option>{data.units.filter(u => u.status === 'available').map(u => <option key={u.id} value={u.id}>{u.name}</option>)}</select></label>
        <label>Manual ETA in minutes (if routing fails)<input name="eta" type="number" min="1" max="1440"/></label>
        <label>Assignment reason<input name="reason" required minLength={3} maxLength={180}/></label>
        <input type="hidden" name="operation" value="assign"/><button className="button subtle" disabled={command.busy}>{mission ? 'Reassign unit' : 'Assign unit'}</button>
      </form>}
      {mayChange && <form className="stack small-gap" onSubmit={staffForm}>
        <label>Receiving hospital<select name="hospital"><option value="">Retry ranked destinations</option>{data.hospitals.map(h => <option key={h.id} value={h.id}>{h.name}</option>)}</select></label>
        <label>Coordination reason<input name="reason" required minLength={3} maxLength={180}/></label>
        <input type="hidden" name="operation" value="coordinate"/><button className="button subtle" disabled={command.busy}>Coordinate destination</button>
      </form>}
      {['queued','assigned','en_route_to_patient'].includes(incident.status) && <form className="stack small-gap" onSubmit={staffForm}>
        <label>Cancellation reason<input name="reason" required minLength={3} maxLength={180}/></label>
        <input type="hidden" name="operation" value="cancel"/><button className="button subtle" disabled={command.busy}>Cancel mission</button>
      </form>}
    </div>}
    <ErrorNotice value={command.error}/>
  </>
}

function CaseDetail({ incident, data, refresh }: { incident: Incident; data: Snapshot; refresh: () => Promise<void> }) {
  const mission = incident.mission
  return <Section title={incident.code} detail={incident.pickup_name} action={<StatusBadge status={incident.status}/>}>
    <div className="panel-body stack small-gap">
      <div className="key-row"><span>Priority</span><strong>{incident.priority || 'Unreviewed · normal'}</strong></div>
      <div className="key-row"><span>Reported urgency</span><strong>{incident.reported_urgency}</strong></div>
      {incident.dispatch_note && <div className="selection-reason"><Radio size={17}/><p>{incident.dispatch_note}</p></div>}
      {mission && <>
        <div className="key-row"><span>Sending hospital</span><strong>{mission.dispatching_hospital}</strong></div>
        <div className="key-row"><span>Receiving hospital</span><strong>{mission.receiving_hospital || 'Coordination needed'}</strong></div>
        {mission.destination_status !== 'accepted' && <div className="info">{mission.destination_status === 'pending' ? 'Awaiting receiving hospital acceptance' : 'Dispatcher coordination needed. No eligible destination is currently selected.'}</div>}
        {mission.route && <RouteDetails route={mission.route}/>}
        <ErrorNotice value={mission.routing_issue || ''}/>
      </>}
      <div className="candidate-block"><h3>Pickup ETA comparison</h3><Candidates rows={incident.candidates} chosen={mission?.unit_id}/></div>
      {mission?.destination_candidates.length ? <details><summary>Receiving hospital candidates</summary><Candidates rows={mission.destination_candidates} chosen={mission.receiving_hospital_id ?? undefined}/></details> : null}
      <IncidentControls key={incident.id} incident={incident} data={data} refresh={refresh}/>
      <details><summary>Incident timeline</summary><Timeline incident={incident}/></details>
    </div>
  </Section>
}

function UnitRow({ unit, refresh }: { unit: Unit; refresh: () => Promise<void> }) {
  const command = useCommand(refresh)
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    void command.run(`/units/${unit.id}/availability`, 'PUT', {
      status: form.get('status'), checked_in: form.get('checked') === 'on', reason: form.get('reason'),
    })
  }
  return <article className="fleet-row"><span className="facility-icon"><Ambulance size={19}/></span>
    <div className="fleet-main"><strong>{unit.name}</strong><small>{unit.base}</small>
      <small>{unit.source}{unit.observed_at ? ` · ${time(unit.observed_at)} · ${ageOf(unit.observed_at)}` : ''}{unit.stale ? ' · Stale GPS' : ''}{unit.accuracy_m !== null ? ` · ±${Math.round(unit.accuracy_m)} m` : ''}</small></div>
    <span className={'unit-status ' + unit.status}><i/>{unit.status.replaceAll('_',' ')}</span>
    {unit.status !== 'busy' && <details className="fleet-edit"><summary>Update unit</summary><form className="stack small-gap" onSubmit={submit}>
      <label>Availability<select name="status" defaultValue={unit.status}><option value="available">Available</option><option value="offline">Offline</option><option value="out_of_service">Out of service</option></select></label>
      <label className="checkbox"><input type="checkbox" name="checked" defaultChecked={unit.checked_in}/> Checked in at base</label>
      <label>Reason<input name="reason" minLength={3} maxLength={180} required /></label>
      <ErrorNotice value={command.error}/><button className="button primary compact" disabled={command.busy}>Save availability</button>
    </form></details>}
  </article>
}

export function Fleet({ data, refresh }: { data: Snapshot; refresh: () => Promise<void> }) {
  return <div className="split-grid">
    <Section title="Fleet status" detail="Availability and checked-in bases" action={<Ambulance className="section-icon" size={19}/>}>
      {data.units.length ? <div className="fleet-list">{data.units.map(unit => <UnitRow unit={unit} key={unit.id} refresh={refresh}/>)}</div> :
        <Empty title="No units configured" detail="Units will appear after they are provisioned."/>}
    </Section>
    <Section title="Hospital capacity" detail="Operational bed reports" action={<BedDouble className="section-icon" size={19}/>}><Capacities hospitals={data.hospitals}/></Section>
  </div>
}

export function Dispatcher({ data, meta, refresh }: { data: Snapshot; meta: Meta; refresh: () => Promise<void> }) {
  const [selected, setSelected] = useState('')
  const [phone, setPhone] = useState(false)
  const [reset, setReset] = useState(false)
  const command = useCommand(refresh)
  const active = data.incidents.filter(i => !terminal(i.status))
  const queue = data.queue.map(id => active.find(i => i.id === id)!).filter(Boolean)
  const dispatched = active.filter(i => i.status !== 'queued')
  const incident = data.incidents.find(item => item.id === selected) ?? dispatched[0] ?? queue[0] ?? data.incidents[0]
  return <div className="stack dashboard-stack">
    <Metrics data={data}/>
    <div className="workspace-controls"><button className="button primary" onClick={() => setPhone(true)}><Phone size={16}/> Log phone request</button>
    </div>
    <div className="overview-grid dispatcher-grid">
      <div className="stack">
        <MissionMap fixture={meta.schematic} units={data.units} hospitals={data.hospitals} incident={incident}
          serverTime={data.server_time} title="Fleet map" overview/>
        {incident?.mission?.traffic_injected && <div className="info traffic-note"><TrafficCone size={18}/> Traffic detected. Ambulance switching to a faster route.</div>}
      </div>
      <div className="stack queue-stack">
        <Section title="Priority queue" detail="Confirmed priority, then oldest first" action={<span className="count">{queue.length}</span>}>
          {active.length === 0 ? <Empty title="No active incidents" detail="Send a training SOS from the patient workspace or log a phone request."/> : <div className="case-list">
            {queue.length === 0 && <p className="queue-empty">No requests waiting</p>}
            {[...queue, ...dispatched].map((item, index) => <div key={item.id}>
              {index === queue.length && <h3 className="queue-caption">Dispatched</h3>}
              <button className={'case-row ' + (incident?.id === item.id ? 'selected' : '')}
                aria-pressed={incident?.id === item.id} onClick={() => setSelected(item.id)}>
                <span><strong>{item.code}<span className={'priority ' + item.effective_priority}>{item.effective_priority}</span></strong><small>{item.pickup_name}</small></span><StatusBadge status={item.status}/>
              </button>
            </div>)}</div>}
        </Section>
        {incident && <CaseDetail incident={incident} data={data} refresh={refresh}/>}
      </div>
    </div>
    <Fleet data={data} refresh={refresh}/>
    {meta.mode === 'demo' && <Section title="Training controls" detail="Reset the cases, units and example capacities in this group."
      action={<button className="button subtle compact" onClick={() => setReset(!reset)} aria-expanded={reset}><RotateCcw size={15}/> Reset group</button>}>
      {reset && <div className="panel-body"><p>Reset this group for everyone using its role links?</p>
        <button className="button danger" disabled={command.busy} onClick={() => void command.run('/demo/reset', 'POST').then(result => { if (result) { setReset(false); setSelected('') } })}>Confirm reset</button><ErrorNotice value={command.error}/></div>}
    </Section>}
    {phone && <PhoneDialog meta={meta} close={() => setPhone(false)} refresh={refresh} />}
  </div>
}

export function Demand({ data }: { data: Snapshot }) {
  const { request } = useSession()
  const [report, setReport] = useState<{version: string; label: string; next_hour: string; mae: number; baseline_mae: number;
    zones: {zone: string; expected: number; observed: number}[]} | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let alive = true
    void request<NonNullable<typeof report>>('/forecast').then(value => { if (alive) setReport(value) })
      .catch(failure => { if (alive) setError(failure.message) })
    return () => { alive = false }
  }, [request, data.sequence])
  return <div className="stack"><Metrics data={data}/>
    <Section title="Next-hour demand by zone" detail={report ? `${time(report.next_hour)} · ${report.label}` : 'Poisson regression'}
      action={<Activity className="section-icon" size={20}/>}>
      <div className="panel-body stack"><ErrorNotice value={error}/>
        {report ? <>
          <div className="forecast-legend"><span><i/> Forecast next hour</span><span><i/> Observed previous hour</span></div>
          <div className="forecasts">{report.zones.map(row => <div key={row.zone}><strong>{row.zone}</strong>
            <div><span style={{ width: `${Math.max(4, 100 * row.expected / Math.max(...report.zones.map(z => Math.max(z.expected, z.observed)), 1))}%` }}>{row.expected.toFixed(2)}</span></div>
            <div><span style={{ width: `${Math.max(4, 100 * row.observed / Math.max(...report.zones.map(z => Math.max(z.expected, z.observed)), 1))}%` }}>{row.observed}</span></div>
          </div>)}</div>
          <p className="meta">Model {report.version} · Holdout MAE {report.mae.toFixed(3)} · Historical average MAE {report.baseline_mae.toFixed(3)}</p>
        </> : !error && <p role="status">Loading demand forecast…</p>}
      </div>
    </Section>
  </div>
}
