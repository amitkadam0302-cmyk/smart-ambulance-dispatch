import { Ambulance, BedDouble, Building2, CheckCircle2, ChevronDown } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { MissionMap } from '../components/Map'
import { Empty, ErrorNotice, Section, StatusBadge } from '../components/ui'
import { useCommand } from '../lib/commands'
import { ageOf, terminal, time } from '../lib/display'
import type { Hospital as Facility, Incident, Meta, Snapshot } from '../lib/types'
import { MissionSummary } from './DriverNurse'

export function Capacities({ hospitals }: { hospitals: Facility[] }) {
  if (hospitals.length === 0) return <Empty title="No capacity sources" detail="Configured hospital reports will appear here." />
  return <div className="capacity-list">{hospitals.map(hospital => {
    const c = hospital.capacity
    const used = c ? c.occupied + c.reserved : 0
    return <article key={hospital.id} className="capacity-row">
      <span className="facility-icon"><Building2 size={18}/></span>
      <div className="capacity-main"><strong>{hospital.name}</strong><small>{c ? `${c.source} · ${ageOf(c.reported_at)} · ${time(c.reported_at)}` : 'No report received'}</small>
        {c && <progress value={used} max={Math.max(c.total, 1)} aria-label={hospital.name + ' occupied and reserved beds'} />}</div>
      <div className="capacity-numbers"><strong>{c ? `${c.available} of ${c.total} available` : 'Unknown'}</strong>
        {c && <small>{c.occupied} occupied · {c.reserved} reserved · {c.stale ? 'Stale' : c.accepting ? 'Accepting' : 'Not accepting'}</small>}</div>
    </article>
  })}</div>
}

function CapacityForm({ hospital, refresh }: { hospital: Facility; refresh: () => Promise<void> }) {
  const command = useCommand(refresh)
  const [editing, setEditing] = useState(false)
  const c = hospital.capacity

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const fields = new FormData(event.currentTarget)
    const result = await command.run(`/hospitals/${hospital.id}/capacity`, 'PUT', {
      total: Number(fields.get('total')), occupied: Number(fields.get('occupied')),
      accepting: fields.get('accepting') === 'on', source: fields.get('source'),
    }, true)
    if (result) setEditing(false)
  }

  return <Section title="ER capacity" detail={c ? `${c.available} beds available · ${c.reserved} reserved` : 'Awaiting a named capacity report'}
    action={<button className="button subtle compact" aria-expanded={editing} onClick={() => setEditing(!editing)}><BedDouble size={16}/> Update capacity</button>}>
    <Capacities hospitals={[hospital]}/>
    {editing && <form className="panel-body stack small-gap" onSubmit={event => void submit(event)}>
      <div className="form-grid"><label>Total beds<input name="total" type="number" min="0" required defaultValue={c?.total ?? 0}/></label>
        <label>Occupied beds<input name="occupied" type="number" min="0" required defaultValue={c?.occupied ?? 0}/></label></div>
      <label>Report source<input name="source" maxLength={100} required defaultValue={c?.source || ''} placeholder="ER bed board"/></label>
      <label className="checkbox"><input type="checkbox" name="accepting" defaultChecked={c?.accepting ?? false}/> Accepting incoming patients</label>
      <ErrorNotice value={command.error}/><button className="button primary" disabled={command.busy}>Save capacity report</button>
    </form>}
  </Section>
}

export function Hospital({ data, meta, refresh }: { data: Snapshot; meta: Meta; refresh: () => Promise<void> }) {
  const facility = data.hospitals[0]
  const active = data.incidents.filter(i => i.mission && !terminal(i.status))
  const [selected, setSelected] = useState('')
  const selectedIncident = active.find(item => item.id === selected) ?? active[0] ?? data.incidents.find(i => i.mission)
  const command = useCommand(refresh)
  const [decline, setDecline] = useState(false)
  const [declineReason, setDeclineReason] = useState('')
  const hospitalId = facility?.id
  const outgoing = active.filter(i => i.mission?.dispatching_hospital_id === hospitalId)
  const incoming = active.filter(i => i.mission?.receiving_hospital_id === hospitalId)
  const mission = selectedIncident?.mission
  const sending = mission?.dispatching_hospital_id === hospitalId
  const receiving = mission?.receiving_hospital_id === hospitalId
  const canDecide = mission && receiving && ['assigned', 'en_route_to_patient', 'at_patient'].includes(mission.status)

  function missions(items: Incident[], empty: string) {
    return items.length ? <div className="case-list">{items.map(item =>
      <button key={item.id} className={'case-row ' + (selectedIncident?.id === item.id ? 'selected' : '')}
        onClick={() => setSelected(item.id)} aria-pressed={selectedIncident?.id === item.id}>
        <span><strong>{item.code}</strong><small>{item.mission?.unit} · {item.pickup_name}</small></span><StatusBadge status={item.status}/>
      </button>)}</div> : <Empty title={empty} detail="Assigned missions will appear here." />
  }

  return <div className="stack">
    <div className="overview-grid">
      <MissionMap fixture={meta.schematic} units={data.units} hospitals={data.hospitals} incident={selectedIncident}
        serverTime={data.server_time} title="Hospital mission map"/>
      <div className="stack">
        <Section title="Incoming patients" detail="Receiving hospital" action={<span className="count">{incoming.length}</span>}>{missions(incoming, 'No incoming patients')}</Section>
        <Section title="Outgoing missions" detail="Sending hospital" action={<span className="count">{outgoing.length}</span>}>{missions(outgoing, 'No outgoing missions')}</Section>
      </div>
    </div>
    <div className="split-grid"><div className="stack">
      {facility && <CapacityForm hospital={facility} refresh={refresh}/>}
      <Section title="Hospital units" detail="Units owned by this sending hospital">
        {data.units.filter(unit => unit.hospital_id === hospitalId).length ? <div className="fleet-list">
          {data.units.filter(unit => unit.hospital_id === hospitalId).map(unit => <article className="fleet-row" key={unit.id}>
            <span className="facility-icon"><Ambulance size={18}/></span><div className="fleet-main"><strong>{unit.name}</strong>
              <small>{unit.checked_in ? 'Checked in at base' : 'Away from base'}</small></div>
            <span className={'unit-status ' + unit.status}><i/>{unit.status.replaceAll('_',' ')}</span>
          </article>)}</div> : <Empty title="No units checked in" detail="This hospital has no provisioned units."/>}
      </Section>
    </div>
      {selectedIncident && mission && <MissionSummary incident={selectedIncident}>
        <div className="button-row"><span className="tag">{sending ? 'Outgoing mission' : 'Receiving facility'}</span>{receiving && <span className="tag">Incoming patient</span>}</div>
        {sending && mission.status === 'assigned' && !mission.released && <button className="button primary" disabled={command.busy} onClick={() => void command.run(
          `/missions/${mission.id}/transition`, 'POST', { action: mission.acknowledged ? 'release' : 'acknowledge' },
        )}>{mission.acknowledged ? 'Release assigned unit' : 'Acknowledge assignment'}</button>}
        {canDecide && mission.destination_status === 'pending' && <button className="button primary" disabled={command.busy}
          onClick={() => void command.run(`/missions/${mission.id}/destination`, 'POST', { decision: 'accept' })}>Accept receiving destination</button>}
        {canDecide && <><button className="button subtle" aria-expanded={decline} onClick={() => setDecline(!decline)}><ChevronDown size={16}/> Decline destination</button>
          {decline && <form className="stack small-gap" onSubmit={event => { event.preventDefault(); void command.run(`/missions/${mission.id}/destination`, 'POST', { decision: 'decline', reason: declineReason }).then(result => { if (result) setDecline(false) }) }}>
            <label>Decline reason<input required minLength={3} maxLength={180} value={declineReason} onChange={event => setDeclineReason(event.target.value)}/></label>
            <button className="button danger" disabled={command.busy}>Confirm decline</button>
          </form>}</>}
        {receiving && mission.status === 'at_hospital' && <button className="button primary large" disabled={command.busy}
          onClick={() => void command.run(`/missions/${mission.id}/transition`, 'POST', { action: 'handover' }, true)}><CheckCircle2 size={19}/> Confirm handover</button>}
        {mission.status === 'completed' && <div className="info"><CheckCircle2 size={17}/> Handover confirmed.</div>}
        {receiving && mission.intake && <details><summary>Nurse intake</summary><div className="stack small-gap">
          <p><strong>{mission.intake.alias}</strong>{mission.intake.age !== null && ` · Age ${mission.intake.age}`}</p><p>{mission.intake.issue}</p>
          {mission.intake.observations && <p>{mission.intake.observations}</p>}</div></details>}
        <ErrorNotice value={command.error}/>
      </MissionSummary>}
    </div>
  </div>
}
