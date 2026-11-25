export class ApiError extends Error {
  constructor(readonly status: number, readonly code: string, detail: string) {
    super(detail)
    this.name = 'ApiError'
  }
}

export interface RequestOptions {
  method?: string
  body?: unknown
  token?: string
  key?: string
  signal?: AbortSignal
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: options.method || 'GET',
    headers: {
      ...(options.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(options.key ? { 'Idempotency-Key': options.key } : {}),
      ...(options.token ? { Authorization: `Bearer ${options.token}` } : {}),
    },
    credentials: 'same-origin',
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    signal: options.signal,
  })
  if (!response.headers.get('Content-Type')?.includes('application/json')) {
    throw new ApiError(response.status, 'unexpected_response', 'The API returned an unexpected response. Check /api/health.')
  }
  const json = await response.json()
  if (!response.ok) throw new ApiError(response.status, json.code, json.detail || 'The command failed.')
  return json as T
}

export function commandKey() {
  if (crypto.randomUUID) return crypto.randomUUID()
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('')
}

export function message(error: unknown) {
  return error instanceof Error ? error.message : 'The request could not be completed.'
}
