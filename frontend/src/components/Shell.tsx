import { Activity, Ambulance, Bell, Building2, ChartNoAxesCombined, ClipboardList, FlaskConical, HeartPulse,
  LayoutDashboard, LogOut, Map, Radio, X } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { time } from '../lib/display'
import type { Dashboard } from '../lib/live'
import { useSession } from '../lib/session'
import { roles, type Role } from '../lib/types'
import { Empty } from './ui'

export const roleInfo = {
  patient: { name: 'Patient', icon: HeartPulse, detail: 'Request a pickup' },
  dispatcher: { name: 'Dispatcher', icon: Radio, detail: 'Manage the queue and fleet' },
  driver: { name: 'Driver', icon: Ambulance, detail: 'Follow the active mission' },
  nurse: { name: 'Nurse', icon: ClipboardList, detail: 'Record pickup intake' },
  hospital: { name: 'Hospital', icon: Building2, detail: 'Receive and release units' },
}

const roleNavigation = {
  dispatcher: [{ key: 'overview', text: 'Overview', icon: LayoutDashboard },
    { key: 'fleet', text: 'Fleet & hospitals', icon: Map },
    { key: 'history', text: 'Incident history', icon: ClipboardList },
    { key: 'analytics', text: 'Analytics', icon: ChartNoAxesCombined }],
  patient: [{ key: 'overview', text: 'My requests', icon: HeartPulse }, { key: 'history', text: 'History', icon: ClipboardList }],
  hospital: [{ key: 'overview', text: 'Hospital overview', icon: Building2 }, { key: 'history', text: 'Mission history', icon: ClipboardList }],
  driver: [{ key: 'overview', text: 'Active mission', icon: Ambulance }, { key: 'history', text: 'History & stats', icon: ClipboardList }],
  nurse: [{ key: 'overview', text: 'Intake workspace', icon: ClipboardList }, { key: 'history', text: 'Mission history', icon: Activity }],
}

export function Brand() {
  return <Link className="brand" to="/" aria-label="Alandi Dispatch home"><span className="brand-icon"><Ambulance size={23}/></span><span><strong>Alandi Dispatch</strong><small>SMART AMBULANCE SYSTEM</small></span></Link>
}

export function Shell({ dashboard, tab, setTab, children }: {
  dashboard: Dashboard; tab: string; setTab: (value: string) => void; children: ReactNode
}) {
  const { session, signOut, request } = useSession()
  const navigate = useNavigate()
  const [inbox, setInbox] = useState(false)
  const role: Role = dashboard.data?.session.role ?? session?.role ?? 'patient'
  const group = session?.group
  const notices = dashboard.data?.notifications ?? []
  const unread = notices.filter(n => !n.read).length
  return <div className="workspace">
    <header className="topbar">
      <Brand />
      <div className={'workspace-label ' + (group ? 'training' : 'connected-label')}>{group ? <FlaskConical size={15}/> : <Activity size={15}/>}<span>{group ? 'Training workspace' : 'Connected workspace'}</span></div>
      <div className="header-right">
        <span className={'connection ' + dashboard.connection} aria-live="polite"><i />{dashboard.connection === 'live' ? 'Live updates' : dashboard.connection === 'reconnecting' ? 'Reconnecting · REST fallback' : 'Connecting'}</span>
        <button className="icon-button bell" onClick={() => setInbox(!inbox)} aria-label={`Notifications, ${unread} unread`} aria-expanded={inbox}><Bell size={19} />{unread > 0 && <b>{unread > 9 ? '9+' : unread}</b>}</button>
        <span className="avatar" title={dashboard.data?.session.name || roleInfo[role].name}>{roleInfo[role].name.slice(0, 2).toUpperCase()}</span>
      </div>
    </header>
    <div className="workspace-layout">
      <aside className="sidebar">
        <div className="sidebar-context"><span className="context-icon"><Map size={17}/></span><div><strong>Alandi, Pune</strong><small>{group ? 'Hackathon training' : 'Dispatch network'}</small></div></div>
        <p className="side-label">WORKSPACE</p>
        <nav className="side-nav" aria-label="Workspace navigation">{roleNavigation[role].map(item => <button className={tab === item.key ? 'active' : ''} aria-current={tab === item.key ? 'page' : undefined} key={item.key} onClick={() => setTab(item.key)}><item.icon size={18} />{item.text}</button>)}</nav>
        {group && <>
          <p className="side-label">DEMO ROLES</p>
          <nav className="side-nav role-nav" aria-label="Training roles">{roles.map(item => {
            const Icon = roleInfo[item].icon
            return <Link key={item} className={item === role ? 'current-role' : ''}
              to={`/training/${group}/${item}`}><Icon size={17}/>{roleInfo[item].name}{item === role && <i />}</Link>
          })}</nav>
        </>}
        <div className="sidebar-foot">
          {group && <p><FlaskConical size={14}/> Simulated units and example capacities</p>}
          <button className="button subtle signout" onClick={() => { navigate(group ? `/training/${group}` : '/'); void signOut().catch(() => {}) }}><LogOut size={16}/> Sign out</button>
        </div>
      </aside>
      <main id="main-content" className="main-content">{children}</main>
    </div>
    {inbox && <aside className="inbox panel" aria-label="Notification inbox"><div className="panel-head"><div><h2>Notifications</h2><p>{unread} unread</p></div><button className="icon-button" onClick={() => setInbox(false)} aria-label="Close notifications"><X size={20}/></button></div>
      {notices.length === 0 ? <Empty title="No notifications yet" detail="Mission and capacity updates will appear here."/> :
        <ul>{notices.map(item => <li className={item.read ? '' : 'unread'} key={item.id}><button onClick={() => {
          if (!item.read) void request(`/notifications/${item.id}/read`, { method: 'PUT' }).then(dashboard.refresh).catch(() => {})
        }}><span>{item.reason}</span><small>{time(item.occurred_at)}{item.read ? ' · Read' : ' · Mark read'}</small></button></li>)}</ul>}
    </aside>}
  </div>
}
