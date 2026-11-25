const base = new URL(process.argv[2] || 'http://localhost:3000')

async function get(path, expected, expectedType) {
  const response = await fetch(new URL(path, base))
  const type = response.headers.get('content-type') || ''
  if (response.status !== expected || !type.includes(expectedType)) {
    throw new Error(`${path} returned ${response.status} ${type}, expected ${expected} ${expectedType}`)
  }
  console.log(`${response.status} ${path} (${type})`)
  return response
}

const index = await (await get('/', 200, 'text/html')).text()
const script = index.match(/src="(\/assets\/[^"]+\.js)"/)?.[1]
const css = index.match(/href="(\/assets\/[^"]+\.css)"/)?.[1]
if (!script || !css) throw new Error('Expected a Vite production build with hashed assets.')
await get(script, 200, 'javascript')
await get(css, 200, 'text/css')
await get('/training/route-check-example/nurse', 200, 'text/html')
await get('/workspace', 200, 'text/html')
const health = await (await get('/api/health', 200, 'application/json')).json()
if (!health.database || !health.redis || health.mode !== 'demo' || health.socket_path !== '/api/socket.io') {
  throw new Error('Expected healthy PostgreSQL and Redis in demo mode.')
}
const openapi = await (await get('/api/openapi.json', 200, 'application/json')).json()
if (!openapi.paths?.['/api/missions/{mission_id}/intake']) throw new Error('Missing the nested mission intake contract.')
await get('/api/session', 401, 'application/json')
await get('/api/unknown-route', 404, 'application/json')
