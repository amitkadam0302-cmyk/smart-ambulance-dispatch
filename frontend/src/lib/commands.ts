import { useRef, useState } from 'react'
import { commandKey, message } from './api'
import { useSession } from './session'

export function useCommand(refresh: () => Promise<void>) {
  const { request } = useSession()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const inFlight = useRef(false)
  const prior = useRef(new Map<string, {body: string; key: string}>())

  async function run<T>(path: string, method: string, body?: unknown, idempotent = false): Promise<T | null> {
    if (inFlight.current) return null
    inFlight.current = true
    setBusy(true)
    setError('')
    const signature = JSON.stringify(body) ?? ''
    const previous = prior.current.get(path)
    const key = idempotent ? previous?.body === signature ? previous.key : commandKey() : undefined
    if (key) prior.current.set(path, { body: signature, key })
    try {
      const result = await request<T>(path, { method, body, key })
      prior.current.delete(path)
      await refresh()
      return result
    } catch (failure) {
      setError(message(failure))
      return null
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }

  return { run, busy, error, setError }
}
