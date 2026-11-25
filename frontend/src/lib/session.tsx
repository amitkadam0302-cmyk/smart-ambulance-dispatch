import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from 'react'
import { api, ApiError, type RequestOptions } from './api'
import type { Role, SessionInfo, Tokens } from './types'

const STORAGE = 'alandi-dispatch-session-v1'

interface SessionState {
  session: SessionInfo | null
  setSession: (value: SessionInfo | null) => void
  token: (renew?: boolean) => Promise<string>
  request: <T>(path: string, options?: RequestOptions) => Promise<T>
  signOut: () => Promise<void>
}

const Context = createContext<SessionState | null>(null)

function restore(): SessionInfo | null {
  try { return JSON.parse(sessionStorage.getItem(STORAGE) || 'null') }
  catch { return null }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setValue] = useState(restore)
  const latest = useRef(session)
  const rotation = useRef<{ slot: string; pending: Promise<string> } | null>(null)

  const setSession = useCallback((value: SessionInfo | null) => {
    latest.current = value
    setValue(value)
    if (value) sessionStorage.setItem(STORAGE, JSON.stringify(value))
    else sessionStorage.removeItem(STORAGE)
  }, [])

  const token = useCallback(async (renew = false) => {
    const active = latest.current
    if (!active) throw new ApiError(401, 'signed_out', 'Sign in to continue.')
    if (!renew && active.expires_at > Date.now() / 1000 + 20) return active.access_token
    if (rotation.current?.slot !== active.slot) {
      const pending = api<Tokens>('/auth/refresh', { method: 'POST', body: { slot: active.slot } })
        .then(next => {
          if (latest.current?.slot === active.slot) setSession({ ...active, ...next })
          return next.access_token
        })
        .catch(error => {
          if (error instanceof ApiError && error.status === 401 && latest.current?.slot === active.slot) {
            setSession(null)
          }
          throw error
        })
        .finally(() => { if (rotation.current?.slot === active.slot) rotation.current = null })
      rotation.current = { slot: active.slot, pending }
    }
    return rotation.current.pending
  }, [setSession])

  const request = useCallback(async <T,>(path: string, options: RequestOptions = {}) => {
    const access = await token()
    try {
      return await api<T>(path, { ...options, token: access })
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 401) throw error
      const renewed = latest.current?.access_token === access ? await token(true) : await token()
      return api<T>(path, { ...options, token: renewed })
    }
  }, [token])

  const signOut = useCallback(async () => {
    const current = latest.current
    setSession(null)
    if (current) await api('/auth/logout', { method: 'POST', body: { slot: current.slot } })
  }, [setSession])

  return <Context.Provider value={{ session, setSession, request, token, signOut }}>{children}</Context.Provider>
}

export function useSession() {
  const state = useContext(Context)
  if (!state) throw new Error('SessionProvider is required.')
  return state
}

export function isRole(value: string | undefined): value is Role {
  return value === 'dispatcher' || value === 'hospital' || value === 'driver' || value === 'patient' || value === 'nurse'
}
