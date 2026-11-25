# API and connected operation

Open `/api/docs` for FastAPI’s OpenAPI request bodies and errors. Browser REST and Socket.IO use the same origin. All authenticated operations resolve the role and workspace membership from PostgreSQL. They also resolve hospital and unit assignments there. Include `Authorization: Bearer <access_token>` and an exact allowed `Origin` for browser POST and PUT calls. Auth and demo-group creation require a matching Origin. The refresh cookie is HttpOnly and SameSite Strict, with Secure set on hosted deployments. [FastAPI JWT and Argon2](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)

| Contract | Role and normal input |
| --- | --- |
| GET `/api/health`, GET `/api/meta` | Public service checks and workspace mode. |
| POST `/api/demo`, POST `/api/auth/demo` | In demo mode create an isolated group and enter it with `code` and `role`. Optional `unit` or `hospital` picks an alternate fixture. The group link grants access to its training roles. |
| POST `/api/auth/register`, POST `/api/auth/login` | Connected signup accepts `workspace`, `email`, `password` and `name` for patients. Login uses an existing membership and `role`. |
| POST `/api/auth/refresh`, POST `/api/auth/logout`, GET `/api/session` | Send `slot` and its cookie to rotate or revoke a tab session. Session reports your current server-authorized role. |
| POST `/api/incidents` | Patient SOS or dispatcher phone intake with `lat`, `lng`, `pickup_name`, `source` and `reported_urgency`. Only staff can set confirmed priority and requirements. |
| GET `/api/queue`, PUT `/api/incidents/{id}/priority` | Dispatcher. Priority body includes `priority`, `reason` and optional unit and service requirements for a queued case. |
| POST `/api/incidents/{id}/dispatch`, POST `/api/incidents/{id}/cancel?reason=…` | Dispatcher re-runs allocation with `{}`, or sets `unit_id`, `reason` and optional `manual_eta_seconds`. A dispatcher or the case owner can cancel a queued case. |
| PUT `/api/units/{id}/availability` | Dispatcher or sending hospital for its own unit. Use `status`, `checked_in` and `reason`. |
| POST `/api/missions/{id}/transition` | Assigned sending hospital acknowledges/releases, assigned driver departs/arrives, receiving hospital hands over, and dispatcher or owner cancels eligible missions. Send `action` and `reason` for cancellation. |
| POST `/api/missions/{id}/intake` | Assigned nurse at pickup. Send `alias`, `issue`, and optional `age` and `observations`. |
| POST `/api/missions/{id}/destination` | Current receiving hospital accepts or declines with `decision` and a reason for refusal. |
| PUT `/api/missions/{id}/destination` | Dispatcher changes or retries receiving coordination with `reason` and optional `hospital_id`. |
| POST `/api/missions/{id}/location`, POST `/api/missions/{id}/route` | Assigned driver submits `lat`, `lng`, `accuracy_m` and ISO `observed_at`. Dispatcher or assigned driver can recalculate an active leg. Use `?inject=true` in demo mode. |
| PUT `/api/hospitals/{id}/capacity`, PUT `/api/integrations/hospitals/{id}/capacity` | Own hospital or its opted-in feed membership. Send `total`, `occupied`, `accepting`, `source` and optional UTC `reported_at`. |
| GET `/api/dashboard`, GET `/api/metrics`, GET `/api/events?after=N` | Role-scoped records. Replay returns `events`, `next` and `more`. |
| GET `/api/forecast`, PUT `/api/notifications/{id}/read` | Dispatcher forecast, and authenticated inbox marking for the current member. |
| POST `/api/simulation/tick`, POST `/api/demo/reset` | Demo members catch up due simulation steps. Only the demo dispatcher resets that group. |

SOS, intake, capacity and handover require an `Idempotency-Key` of 8 to 100 characters. Repeat the same body and key after a network error. If the original command committed, the API returns its original result. The response to an SOS records acknowledgment and gives the incident ID for the next snapshot. Expected errors include `401 invalid_access`, `403 forbidden`, `404 not_found`, `409 queue_order`, `409 capacity_conflict`, `409 stale_observation`, `409 key_reused` and `422 invalid_input`.

## Socket.IO contract

Connect to the same origin with `path: '/api/socket.io'`, `addTrailingSlash: false` and `transports: ['websocket']`. Pass `auth: { token: '<access JWT>' }`. The server joins only the membership room derived from the database. [Python Socket.IO](https://python-socketio.readthedocs.io/en/stable/server.html)

| Event | Direction and payload |
| --- | --- |
| `subscribe` | Client requests `{"room":"self"}`. Ack contains `{"ok":true}` or an error code. |
| `domain_event` | Server sends `{"id":"<event UUID>","seq":42}` after commit to eligible memberships. The browser fetches a role-scoped snapshot. |
| `location_update` | Assigned connected driver can send `{"mission_id":"<id>","fix":{"lat":18.6731,"lng":73.8984,"accuracy_m":15,"observed_at":"2026-11-25T09:00:00Z"}}`. The ack reports `ok` and `accepted` or an error. The browser uses the equivalent REST action. |

Replay returns only entries recorded for the authenticated membership. The workspace sequence may have gaps in an individual member’s event stream. Use the server’s replay cursor or a newer snapshot sequence. The browser deduplicates signals by sequence, rejoins with current credentials, and polls the snapshot while live updates are interrupted. Dispatcher audit responses identify membership actors. Events with no actor ID were generated by the system. Events carry no nurse intake or identifying patient data. The assigned nurse and accepting receiving hospital can read intake in their authenticated dashboard.

## Provision a separate connected workspace

Run the admin commands locally with database access. This powers the connected REST and UI pathways using explicitly provisioned staff, a named capacity source and foreground GPS. Use a separate deployment and database from the public demo. Commands prompt for the staff passwords.

```powershell
Set-Location .\backend
$env:APP_MODE = 'connected'
.\.venv\Scripts\python.exe -m dispatch.manage workspace alandi-connected 'Alandi connected'
.\.venv\Scripts\python.exe -m dispatch.manage hospital alandi-connected ALANDI 'Alandi test hospital' 18.6731 73.8984 --capabilities 'general,trauma' --enable-feed
.\.venv\Scripts\python.exe -m dispatch.manage unit alandi-connected ALANDI UNIT01 --capabilities 'general,advanced'
.\.venv\Scripts\python.exe -m dispatch.manage member alandi-connected dispatcher@example.invalid Dispatcher dispatcher
.\.venv\Scripts\python.exe -m dispatch.manage member alandi-connected hospital@example.invalid Hospital hospital --hospital ALANDI
.\.venv\Scripts\python.exe -m dispatch.manage member alandi-connected driver@example.invalid Driver driver --unit UNIT01
.\.venv\Scripts\python.exe -m dispatch.manage member alandi-connected nurse@example.invalid Nurse nurse --unit UNIT01
.\.venv\Scripts\python.exe -m dispatch.manage member alandi-connected feed@example.invalid 'Example bed board' feed --hospital ALANDI
```

In the connected Dispatcher workspace, check the unit in and set it Available. Submit a fresh report in Hospital or use this PowerShell feed example against the same connected origin. Use the generated hospital ID from the provision command or the logged-in feed session. Keep the request key for a retry.

```powershell
$base = 'http://localhost:3000'
$feedPassword = [System.Net.NetworkCredential]::new('',(Read-Host 'Feed password' -AsSecureString)).Password
$loginBody = @{workspace='alandi-connected'; email='feed@example.invalid'; password=$feedPassword; role='feed'}
$access = Invoke-RestMethod -Method Post -Uri "$base/api/auth/login" -Headers @{Origin=$base} -ContentType 'application/json' -Body ($loginBody | ConvertTo-Json)
Remove-Variable feedPassword,loginBody
$headers = @{Authorization="Bearer $($access.access_token)"; Origin=$base}
$member = Invoke-RestMethod "$base/api/session" -Headers $headers
$headers['Idempotency-Key'] = [Guid]::NewGuid().ToString()
$report = @{total=12; occupied=7; accepting=$true; source='Example bed board'; reported_at=[DateTimeOffset]::UtcNow.ToString('o')}
Invoke-RestMethod -Method Put -Uri "$base/api/integrations/hospitals/$($member.hospital_id)/capacity" -Headers $headers -ContentType 'application/json' -Body ($report | ConvertTo-Json)
```

In this example, available beds equal five before reservations. Active reservations are included in availability and are maintained by mission commands.
