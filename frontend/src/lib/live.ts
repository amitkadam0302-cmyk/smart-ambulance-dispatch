import { useCallback, useEffect, useRef, useState } from 'react'
import { io } from 'socket.io-client'
import { message } from './api'
import { useSession } from './session'
import type { DomainEvent, Snapshot } from './types'

type Connection = 'connecting' | 'live' | 'reconnecting'

export function useDashboard() {
  const { session, request, token } = useSession()
  const slot = session?.slot
  const [data, setData] = useState<Snapshot | null>(null)
  const [connection, setConnection] = useState<Connection>('connecting')
  const [error, setError] = useState('')
  const load = useRef<() => Promise<void>>(async () => {})
  const refresh = useCallback(() => load.current(), [])

  useEffect(() => {
    if (!slot) return
    let stopped = false
    let pending: Promise<void> | null = null
    let replenish = false
    let sequence = Number(sessionStorage.getItem(`alandi-seq-${slot}`) || 0)
    let highestSignal = sequence
    let demo = false
    let simulating = false
    let connected = false
    let lastSync = 0
    let lastAuthRetry = 0
    const socket = io({
      path: '/api/socket.io', addTrailingSlash: false, transports: ['websocket'],
      timeout: 4500, reconnectionDelay: 600, reconnectionDelayMax: 7000, autoConnect: false,
      auth: callback => { void token().then(access => callback({ token: access })).catch(() => callback({})) },
    })

    function synchronize() {
      if (stopped) return Promise.resolve()
      if (pending) {
        replenish = true
        return pending
      }
      pending = (async () => {
        do {
          replenish = false
          try {
            const incoming = await request<Snapshot>('/dashboard')
            if (stopped) return
            if (incoming.sequence >= sequence) {
              sequence = incoming.sequence
              sessionStorage.setItem(`alandi-seq-${slot}`, String(sequence))
              demo = incoming.session.mode === 'demo'
              simulating = incoming.incidents.some(i => i.status === 'assigned' ||
                i.status === 'en_route_to_patient' || i.status === 'transporting')
              setData(incoming)
              setError('')
            }
            lastSync = Date.now()
            if (highestSignal > incoming.sequence) replenish = true
          } catch (failure) {
            if (!stopped) setError(message(failure))
            replenish = false
          }
        } while (!stopped && replenish)
      })().finally(() => { pending = null })
      return pending
    }

    async function reconnectSnapshot() {
      try {
        const replay = await request<{events: DomainEvent[]; next: number}>(`/events?after=${sequence}`)
        for (const event of replay.events) highestSignal = Math.max(highestSignal, event.seq)
      } catch {
        // The canonical snapshot recovers outages in event delivery.
      }
      if (!stopped) await synchronize()
    }

    load.current = synchronize
    socket.on('connect', () => {
      connected = true
      setConnection('live')
      socket.emit('subscribe', { room: 'self' })
      void reconnectSnapshot()
    })
    socket.on('disconnect', reason => {
      connected = false
      setConnection('reconnecting')
      if (reason === 'io server disconnect') void token(true).then(() => socket.connect()).catch(() => {})
    })
    socket.on('connect_error', () => {
      setConnection('reconnecting')
      if (!socket.active && Date.now() - lastAuthRetry > 5000) {
        lastAuthRetry = Date.now()
        void token(true).then(() => {
          if (!stopped) socket.connect()
        }).catch(() => {})
      }
    })
    socket.on('domain_event', ({ seq }: { seq: number }) => {
      if (seq > sequence && seq > highestSignal) {
        highestSignal = seq
        void synchronize()
      }
    })
    void synchronize()
    socket.connect()
    const watch = window.setInterval(() => {
      if (Date.now() - lastSync >= (connected ? 20000 : 7000)) void synchronize()
      if (demo && simulating && document.visibilityState === 'visible') {
        void request<{changed: boolean}>('/simulation/tick', { method: 'POST' })
          .then(result => { if (result.changed) void synchronize() })
          .catch(() => {})
      }
    }, 1800)
    function onVisible() {
      if (document.visibilityState === 'visible') void synchronize()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      stopped = true
      socket.removeAllListeners()
      socket.disconnect()
      window.clearInterval(watch)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [request, slot, token])

  return { data, connection, error, refresh }
}

export type Dashboard = ReturnType<typeof useDashboard>
