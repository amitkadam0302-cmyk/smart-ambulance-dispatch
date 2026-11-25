import { Ambulance, ArrowUpRight, CalendarDays, CheckCircle2, ClipboardCopy, FlaskConical,
  HeartPulse, Link2, MapPin, ShieldCheck, Waypoints } from 'lucide-react'
import { useEffect, useState, type FormEvent } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Link, Navigate, Route, Routes, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Brand, roleInfo, Shell } from './components/Shell'
import { ErrorNotice } from './components/ui'
import { Demand, Dispatcher, Fleet } from './features/Dispatcher'
import { Driver, Nurse } from './features/DriverNurse'
import { Hospital } from './features/Hospital'
import { History, Patient } from './features/Patient'
import { api, message } from './lib/api'
import { day } from './lib/display'
import { useDashboard } from './lib/live'
import { isRole, SessionProvider, useSession } from './lib/session'
import { roles, type Meta, type Tokens } from './lib/types'
import './style.css'

function PageHeader({ training }: { training: boolean }) {
  return <header className="topbar public-header"><Brand/><div className={'workspace-label ' + (training ? 'training' : '')}>
    {training ? <FlaskConical size={15}/> : <ShieldCheck size={15}/>}<span>{training ? 'Training workspace' : 'Connected workspace'}</span></div>
    <span className="public-location"><MapPin size={16}/> Alandi, Pune</span>
  </header>
}

function Landing({ meta }: { meta: Meta }) {
  const navigate = useNavigate()
  const { session } = useSession()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  if (meta.mode === 'connected') return <ConnectedEntry/>
  async function start() {
    setBusy(true); setError('')
    try {
      const group = await api<{code: string}>('/demo', { method: 'POST' })
      navigate(`/training/${group.code}`)
    } catch (failure) { setError(message(failure)); setBusy(false) }
  }
  return <>
    <PageHeader training />
    <main className="landing">
      <div className="landing-hero">
        <div className="landing-brand"><Ambulance size={32}/></div>
        <span className="landing-kicker"><MapPin size={15}/> ALANDI, PUNE, MAHARASHTRA</span>
        <h1>Smart Ambulance<br/>Dispatch System</h1>
        <p>Follow an Alandi pickup through dispatch, nurse intake and hospital handover. Open five role views in one shared training group.</p>
        <div className="button-row"><button className="button primary large" onClick={() => void start()} disabled={busy}><HeartPulse size={20}/>{busy ? 'Preparing training workspace…' : 'Start training demo'}</button>
          {session?.group && <Link className="button subtle large" to={`/training/${session.group}`}>Resume training group</Link>}</div>
        <ErrorNotice value={error}/>
      </div>
      <div className="landing-guide panel">
        <span className="metric-icon blue"><Waypoints size={22}/></span><h2>The Alandi mission</h2>
        <ol>
          <li><span><MapPin size={18}/></span><div><strong>Request a pickup</strong><p>MIT ACSC College, Alandi</p></div></li>
          <li><span><Ambulance size={18}/></span><div><strong>Compare road ETAs</strong><p>Three example units across Pune</p></div></li>
          <li><span><CheckCircle2 size={18}/></span><div><strong>Complete handover</strong><p>Intake, receiving bed and available unit</p></div></li>
        </ol>
        <div className="training-note"><FlaskConical size={19}/><span>Fictional people, simulated ambulances and example hospital capacities. Training SOS actions stay within this workspace.</span></div>
      </div>
      <footer>College hackathon demo · Amit Suresh Kadam</footer>
    </main>
  </>
}

function CopyLink({ group, compact = false }: { group: string; compact?: boolean }) {
  const [state, setState] = useState(false)
  const url = `${window.location.origin}/training/${group}`
  async function copy() {
    try { await navigator.clipboard.writeText(url); setState(true) }
    catch { setState(true) }
  }
  return <div className={'share-action ' + (compact ? 'compact-share' : '')}>
    <button className="button subtle" onClick={() => void copy()} aria-expanded={state}><ClipboardCopy size={16}/> {state ? 'Group link' : 'Share group link'}</button>
    {state && <input className="copy-url" aria-label="Training group link" value={url} readOnly onFocus={event => event.currentTarget.select()}/>}
  </div>
}

function TrainingHub() {
  const { group = '' } = useParams()
  return <><PageHeader training />
    <main className="hub">
      <div className="page-heading"><div><span className="landing-kicker"><FlaskConical size={15}/> ISOLATED TRAINING GROUP</span>
        <h1>Alandi training workspace</h1><p>Choose a role. Use the same group link in a second browser to follow the mission together.</p></div><CopyLink group={group}/></div>
      <div className="role-cards">{roles.map(role => {
        const info = roleInfo[role]
        return <article key={role} className="panel role-card"><span className={'metric-icon ' + (role === 'patient' ? 'rose' : 'blue')}><info.icon size={22}/></span>
          <h2>{info.name}</h2><p>{info.detail}</p>
          <div className="button-row"><Link className="button subtle" to={`/training/${group}/${role}`}>Open {info.name.toLowerCase()}</Link>
            <a className="icon-button" href={`/training/${group}/${role}`} target="_blank" rel="noopener" aria-label={`Open ${role} in a new tab`}><ArrowUpRight size={18}/></a></div>
        </article>
      })}</div>
      <div className="panel hub-details">
        <h2>Alternative fixture assignments</h2><p className="meta">The main driver and nurse links open AMB3. Use these links when another unit wins dispatch.</p>
        <div className="fixture-links">{['AMB1', 'AMB2', 'AMB3'].map((unit, i) => <div key={unit}>
          <strong>{unit}</strong>
          <Link to={`/training/${group}/driver?unit=${unit}`}>Driver</Link>
          <Link to={`/training/${group}/nurse?unit=${unit}`}>Nurse</Link>
          <Link to={`/training/${group}/hospital?hospital=${['RUBY','SASSOON','ALANDI'][i]}`}>{['Ruby Hall', 'Sassoon', 'Alandi hospital'][i]}</Link>
        </div>)}</div>
      </div>
    </main>
  </>
}

function TrainingEntry({ meta }: { meta: Meta }) {
  const { group = '', role } = useParams()
  const [params] = useSearchParams()
  const unit = params.get('unit')
  const hospital = params.get('hospital')
  const entry = `${group}/${role}/${unit || ''}/${hospital || ''}`
  const { session, setSession } = useSession()
  const ready = session?.entry === entry
  const [error, setError] = useState('')
  useEffect(() => {
    if (ready || !isRole(role)) return
    const controller = new AbortController()
    void api<Tokens>('/auth/demo', { method: 'POST', signal: controller.signal, body: { code: group, role, unit, hospital } })
      .then(tokens => { if (!controller.signal.aborted) setSession({ ...tokens, group, role, entry }) })
      .catch(failure => { if (!controller.signal.aborted) setError(message(failure)) })
    return () => controller.abort()
  }, [entry, group, hospital, ready, role, setSession, unit])
  if (!isRole(role) || meta.mode !== 'demo') return <Navigate to="/" replace/>
  if (ready) return <DashboardPage key={entry} meta={meta}/>
  return <><PageHeader training/><main className="state-page">{error ? <>
    <ErrorNotice value={error}/><Link className="button subtle" to="/">Start a new training group</Link>
  </> : <p role="status">Opening the {role} workspace…</p>}</main></>
}

function ConnectedEntry() {
  const { session, setSession } = useSession()
  const navigate = useNavigate()
  const [register, setRegister] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  if (session && !session.group) return <Navigate to="/workspace" replace/>
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('')
    const form = new FormData(event.currentTarget)
    const role = register ? 'patient' : form.get('role')
    const nextRole = String(role)
    if (!isRole(nextRole)) return
    try {
      const tokens = await api<Tokens>(`/auth/${register ? 'register' : 'login'}`, { method: 'POST', body: {
        workspace: form.get('workspace'), email: form.get('email'), password: form.get('password'),
        ...(register ? { name: form.get('name') } : { role: nextRole }),
      } })
      setSession({ ...tokens, role: nextRole })
      navigate('/workspace')
    } catch (failure) { setError(message(failure)) }
    finally { setBusy(false) }
  }
  return <><PageHeader training={false}/><main className="state-page">
    <form className="panel auth-form stack" onSubmit={event => void submit(event)}>
      <h1>{register ? 'Register as a patient' : 'Sign in'}</h1>
      <label>Workspace<input name="workspace" required autoComplete="organization" placeholder="Workspace slug"/></label>
      {register && <label>Display name<input name="name" required maxLength={100} autoComplete="name"/></label>}
      <label>Email<input name="email" required type="email" autoComplete="username"/></label>
      <label>Password<input name="password" required type="password" minLength={register ? 12 : 1} autoComplete={register ? 'new-password' : 'current-password'}/></label>
      {!register && <label>Role<select name="role">{roles.map(role => <option key={role} value={role}>{roleInfo[role].name}</option>)}</select></label>}
      <ErrorNotice value={error}/><button className="button primary" disabled={busy}>{register ? 'Create patient account' : 'Sign in'}</button>
      <button className="button subtle" type="button" onClick={() => setRegister(!register)}>{register ? 'Back to sign in' : 'Register as a patient'}</button>
    </form>
  </main></>
}

const pageTitles = {
  dispatcher: 'Dispatch overview',
  patient: 'Patient requests',
  driver: 'Driver mission',
  nurse: 'Nurse intake workspace',
  hospital: 'Hospital operations',
}
const otherTitles: Record<string, string> = { fleet: 'Fleet and hospitals', history: 'Mission history', analytics: 'Operations analytics' }

function DashboardPage({ meta }: { meta: Meta }) {
  const dashboard = useDashboard()
  const { session } = useSession()
  const [tab, setTab] = useState('overview')
  const data = dashboard.data
  if (!session) return <Navigate to="/" replace/>
  const role = data?.session.role || session.role
  const props = data ? { data, meta, refresh: dashboard.refresh } : null
  return <Shell dashboard={dashboard} tab={tab} setTab={setTab}>
    <a className="skip-link" href="#main-content">Skip to workspace content</a>
    <div className="page-heading"><div><h1>{tab === 'overview' ? pageTitles[role] : otherTitles[tab]}</h1>
      <p><MapPin size={15}/> Alandi, Pune <span className="separator"/> <CalendarDays size={15}/> {day(data?.server_time || new Date().toISOString())} · IST</p></div>
      {session.group && <div className="button-row heading-actions"><Link className="button subtle" to={`/training/${session.group}`}><Link2 size={16}/> Role links</Link><CopyLink group={session.group} compact/></div>}
    </div>
    <ErrorNotice value={dashboard.error}/>
    {!props && !dashboard.error && <p role="status" className="loading">Loading workspace…</p>}
    {props && <div key={data?.generation}>
      {tab === 'history' ? <History data={props.data}/> : tab === 'fleet' ? <Fleet {...props}/> : tab === 'analytics' ? <Demand data={props.data}/> :
        role === 'dispatcher' ? <Dispatcher {...props}/> : role === 'patient' ? <Patient {...props}/> :
        role === 'driver' ? <Driver {...props}/> : role === 'nurse' ? <Nurse {...props}/> : <Hospital {...props}/>}
    </div>}
  </Shell>
}

function App() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [error, setError] = useState('')
  useEffect(() => { void api<Meta>('/meta').then(setMeta).catch(failure => setError(message(failure))) }, [])
  if (!meta) return <main className="state-page">{error ? <><ErrorNotice value={error}/><button className="button subtle" onClick={() => window.location.reload()}>Retry</button></> : <p role="status">Loading Alandi Dispatch…</p>}</main>
  return <Routes>
    <Route path="/" element={<Landing meta={meta}/>}/>
    <Route path="/training/:group" element={meta.mode === 'demo' ? <TrainingHub/> : <Navigate to="/"/>}/>
    <Route path="/training/:group/:role" element={<TrainingEntry meta={meta}/>}/>
    <Route path="/workspace" element={meta.mode === 'connected' ? <DashboardPage meta={meta}/> : <Navigate to="/"/>}/>
    <Route path="*" element={<Navigate to="/" replace/>}/>
  </Routes>
}

createRoot(document.getElementById('root')!).render(<BrowserRouter><SessionProvider><App/></SessionProvider></BrowserRouter>)
