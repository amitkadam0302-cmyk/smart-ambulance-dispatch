import { ArrowDownRight, CalendarClock, CircleAlert, ClipboardList, Clock3, Route as RouteIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { ageOf, distance, minutes, statuses, time } from '../lib/display'
import type { Incident, Route, Status } from '../lib/types'

export function StatusBadge({ status }: { status: Status }) {
  return <span className={'status status-' + status}><i />{statuses[status]}</span>
}

export function Empty({ title, detail }: { title: string; detail: string }) {
  return <div className="empty"><div className="empty-icon"><ClipboardList size={21} /></div><strong>{title}</strong><p>{detail}</p></div>
}

export function ErrorNotice({ value }: { value: string }) {
  return value ? <div className="error" role="alert"><CircleAlert size={17} /><span>{value}</span></div> : null
}

export function Section({ title, detail, action, children, className = '' }: {
  title: string; detail?: string; action?: ReactNode; children: ReactNode; className?: string
}) {
  return <section className={'panel ' + className}><div className="panel-head"><div><h2>{title}</h2>{detail && <p>{detail}</p>}</div>{action}</div>{children}</section>
}

export function Timeline({ incident }: { incident: Incident }) {
  return <ol className="timeline" aria-label="Mission timeline">
    {[...incident.timeline].reverse().map(item => <li key={item.id}><span className="timeline-dot"/><div><strong>{item.reason}</strong><span>{time(item.occurred_at)}</span></div></li>)}
  </ol>
}

export function RouteDetails({ route, label }: { route: Route; label?: string }) {
  return <div className="route-details">
    <div className="route-top"><span><RouteIcon size={17} />{label || (route.leg === 'pickup' ? 'Pickup leg' : 'Receiving leg')}</span><span className="route-version">v{route.version}</span></div>
    <div className="route-values"><span><Clock3 size={17}/><strong>{minutes(route.eta_seconds)}</strong> ETA</span><span><ArrowDownRight size={17}/><strong>{distance(route.distance_m)}</strong></span></div>
    <p>{route.source}{route.delay_seconds === null ? ' · Traffic data unavailable' : route.source !== 'Simulated traffic' ? ` · ${minutes(route.delay_seconds)} traffic delay` : ''}</p>
    <p className="meta"><CalendarClock size={13} /> {time(route.calculated_at)} · {ageOf(route.calculated_at)}</p>
  </div>
}
