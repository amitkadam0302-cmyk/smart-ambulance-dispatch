import { Crosshair, MapPin, Radio, Send, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { MissionMap } from '../components/Map'
import { Empty, ErrorNotice, RouteDetails, Section, StatusBadge, Timeline } from '../components/ui'
import { useCommand } from '../lib/commands'
import { terminal } from '../lib/display'
import type { Meta, Point, Priority, Snapshot } from '../lib/types'

type PickupSource = 'alandi' | 'pin' | 'gps'
const defaultPin: Point = [18.6716, 73.8914]

export function Patient({ data, meta, refresh }: { data: Snapshot; meta: Meta; refresh: () => Promise<void> }) {
  const active = data.incidents.find(item => !terminal(item.status))
  const [pin, setPin] = useState<Point>(meta.pickup?.position ?? defaultPin)
  const [source, setSource] = useState<PickupSource>(meta.pickup ? 'alandi' : 'pin')
  const [pickupName, setPickupName] = useState(meta.pickup?.name ?? 'Map pickup pin')
  const [confirmed, setConfirmed] = useState(false)
  const [urgency, setUrgency] = useState<Priority>('normal')
  const [gps, setGps] = useState('')
  const [gpsBusy, setGpsBusy] = useState(false)
  const command = useCommand(refresh)

  function pick(point: Point, method: PickupSource, name: string) {
    setPin(point); setSource(method); setPickupName(name); setConfirmed(false); setGps('')
  }

  function locate() {
    if (!navigator.geolocation || !window.isSecureContext) {
      setGps('Location requires HTTPS or localhost. Use the map pin to continue.')
      return
    }
    setGpsBusy(true)
    setGps('')
    navigator.geolocation.getCurrentPosition(result => {
      setGpsBusy(false)
      pick([result.coords.latitude, result.coords.longitude], 'gps', 'Browser location')
    }, () => {
      setGpsBusy(false)
      setGps('Location permission was denied or unavailable. Drag the pickup pin or use the Alandi demo pickup.')
    }, { timeout: 8000, enableHighAccuracy: true, maximumAge: 3000 })
  }

  return <div className="overview-grid patient-grid">
    <MissionMap fixture={meta.schematic} units={data.units} incident={active} pin={active ? undefined : pin}
      pinName={pickupName} onPin={point => pick(point, 'pin', 'Map pickup pin')} serverTime={data.server_time}
      title={active ? 'Pickup and ambulance' : 'Choose your pickup'} />
    <div className="stack">
      {active ? <Section title={active.code} detail="Your SOS was received" action={<StatusBadge status={active.status} />}>
        <div className="panel-body stack small-gap">
          <div className="key-row"><span>Pickup</span><strong>{active.pickup_name}</strong></div>
          <div className="key-row"><span>Assigned unit</span><strong>{active.mission?.unit || 'Awaiting assignment'}</strong></div>
          {active.queue_position && <div className="info"><Radio size={16}/> Queue position {active.queue_position}. Dispatch will retry when a unit becomes available.</div>}
          {active.mission?.route && <RouteDetails route={active.mission.route} />}
          <ErrorNotice value={active.mission?.routing_issue || command.error} />
          {(active.status === 'queued' || active.status === 'assigned') && <button className="button subtle" disabled={command.busy} onClick={() => void (
            active.mission ? command.run(`/missions/${active.mission.id}/transition`, 'POST', { action: 'cancel', reason: 'Patient cancelled request' }) :
              command.run(`/incidents/${active.id}/cancel?reason=Patient%20cancelled%20request`, 'POST')
          )}>Cancel request</button>}
          <Timeline incident={active} />
        </div>
      </Section> : <Section title={meta.mode === 'demo' ? 'Request a training pickup' : 'Request an ambulance'} detail="Confirm where the ambulance should meet you.">
        <div className="panel-body stack">
          {meta.pickup && <button className={'choice ' + (source === 'alandi' ? 'selected' : '')} onClick={() => pick(meta.pickup!.position, 'alandi', meta.pickup!.name)}><span className="choice-icon"><MapPin size={19}/></span><span><strong>Alandi demo pickup</strong><small>{meta.pickup.name}</small></span><i /></button>}
          <div className="button-row">
            <button className="button subtle" disabled={gpsBusy} onClick={locate}><Crosshair size={16}/>{gpsBusy ? 'Locating…' : 'Use browser location'}</button>
            <button className="button subtle" onClick={() => pick(pin, 'pin', 'Map pickup pin')}><MapPin size={16}/> Use map pin</button>
          </div>
          <ErrorNotice value={gps} />
          {source === 'pin' && <div className="form-grid"><label>Latitude<input type="number" step="any" min="-90" max="90" value={pin[0]} onChange={event => pick([Number(event.target.value), pin[1]], 'pin', 'Map pickup pin')}/></label><label>Longitude<input type="number" step="any" min="-180" max="180" value={pin[1]} onChange={event => pick([pin[0], Number(event.target.value)], 'pin', 'Map pickup pin')}/></label></div>}
          <button className="button subtle confirm-pin" aria-pressed={confirmed} onClick={() => setConfirmed(true)}><ShieldCheck size={17}/>{confirmed ? 'Pickup pin confirmed' : 'Confirm pickup pin'}</button>
          <label>Reported urgency<select value={urgency} onChange={event => setUrgency(event.target.value as Priority)}><option value="normal">Normal</option><option value="high">High</option><option value="critical">Critical</option></select></label>
          <ErrorNotice value={command.error} />
          <button className="button danger large" disabled={!confirmed || command.busy || Math.abs(pin[0]) > 90 || Math.abs(pin[1]) > 180}
            onClick={() => void command.run('/incidents', 'POST', { lat: pin[0], lng: pin[1], pickup_name: pickupName, reported_urgency: urgency, source }, true)}>
            <Send size={19}/>{command.busy ? 'Submitting…' : meta.mode === 'demo' ? 'Send training SOS' : 'Send SOS'}
          </button>
          {meta.mode === 'demo' && <small className="meta">This SOS stays inside your training group.</small>}
        </div>
      </Section>}
      {!active && data.incidents[0] && <Section title="Latest request" detail={data.incidents[0].code} action={<StatusBadge status={data.incidents[0].status}/>}>
        <div className="panel-body"><Timeline incident={data.incidents[0]} /></div>
      </Section>}
    </div>
  </div>
}

export function History({ data }: { data: Snapshot }) {
  const [open, setOpen] = useState<string | null>(null)
  const cases = data.incidents
  return <Section title="Mission history" detail={data.session.role === 'driver' ? `${data.metrics.completed} completed · ${data.metrics.median_arrival_seconds ?? '—'} sec median pickup` : 'Records visible to your role'}>
    {cases.length ? <div className="history-list">{cases.map(item => <div className="history-item" key={item.id}>
      <button onClick={() => setOpen(open === item.id ? null : item.id)} aria-expanded={open === item.id}><span><strong>{item.code}</strong><small>{item.pickup_name} · {item.mission?.unit || 'Unassigned'}</small></span><StatusBadge status={item.status}/></button>
      {open === item.id && <Timeline incident={item}/>}
    </div>)}</div> : <Empty title="No missions yet" detail="Mission activity will be saved here."/>}
  </Section>
}
