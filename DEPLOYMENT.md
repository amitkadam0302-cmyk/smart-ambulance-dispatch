# Deploy the Alandi training app to Vercel

Deploy the repository root as **one Vercel project** with two Services. The root `vercel.json` sends `/api`, nested `/api/…` routes and `/api/socket.io` to the FastAPI ASGI service. The web service handles the Vite output and React Router history fallback for `/training/…` and `/workspace`. PostgreSQL and Redis live outside Vercel functions. Keep **Fluid compute** enabled. Each upgraded WebSocket stays on its accepting instance until it closes or reaches the function’s duration limit. The client rejoins and fetches durable state after reconnecting. [Vercel Services](https://vercel.com/docs/services) · [Public routing example](https://vercel.com/docs/services/routing) · [Vercel WebSockets](https://vercel.com/docs/functions/websockets) · [Python runtime](https://vercel.com/docs/functions/runtimes/python) · [FastAPI on Vercel](https://vercel.com/docs/frameworks/backend/fastapi) · [Function limits](https://vercel.com/docs/functions/limitations)

## 1. Prepare separate data services

1. In Neon, create a project in a region near the Vercel function region you’ll use. Create separate databases or branches for **preview** and **production**. Use a branch without existing patient data. From **Connect**, copy each branch’s PostgreSQL URI with **connection pooling** on for the API. Copy a **direct** URI for the same branch for release migrations. Retain the provider’s TLS options in both URLs. The app uses SQLAlchemy with a three-connection default cap, no overflow and prepared statements disabled for pooled PostgreSQL. [Neon connection pooling](https://neon.com/docs/connect/connection-pooling)
2. In Upstash, create a Redis database and select the **native Redis endpoint** in the connection details. Use its credentials and TLS hostname in the `rediss://default:<encoded-password>@<endpoint>:6379/0` form. Use native Pub/Sub and choose different `REDIS_NAMESPACE` values for Preview and Production. Separate instances provide separate billing and quota limits. If choosing another Redis provider, confirm it supports native TLS, `PUBLISH` and `SUBSCRIBE` over a long-lived connection. [Upstash Redis compatibility](https://upstash.com/docs/redis/overall/compatibility) · [Upstash TLS](https://upstash.com/docs/redis/features/security) · [Python Socket.IO server and Redis manager](https://python-socketio.readthedocs.io/en/stable/server.html)
3. The training deployment uses the bundled Alandi routes. For a separate connected environment, create a TomTom developer project, enable Routing API access and copy the key to backend settings. The API requests driving geometry, ETA, traffic delay and guidance. [TomTom Calculate Route](https://docs.tomtom.com/routing-api/documentation/tomtom-maps/v1/calculate-route) · [TomTom guidance](https://docs.tomtom.com/routing-api/documentation/tomtom-maps/v1/guidance-instructions)
4. Review provider dashboards for the current starter-plan quotas. A small college demo can use Vercel Hobby where the deployment meets its plan terms and introductory Neon and Upstash plans where available. Function activity, function memory, outbound data, database compute and storage, Redis commands, and TomTom requests may be usage-metered. The OSM street-tile toggle allows ordinary interactive viewing with attribution. The default schematic uses bundled geometry. [Vercel pricing](https://vercel.com/pricing) · [Neon pricing](https://neon.com/pricing) · [Upstash pricing](https://upstash.com/pricing) · [OSM tile policy](https://operations.osmfoundation.org/policies/tiles/)

## 2. Set backend variables for each environment

| Variable | Where it is read | Preview and Production values |
| --- | --- | --- |
| `DATABASE_URL` | API and seed command | Pooled PostgreSQL URI for the target branch or database |
| `MIGRATION_DATABASE_URL` | Alembic release command | Direct migration connection for the same target. Set in the release terminal. Alembic falls back to `DATABASE_URL` when this is omitted. |
| `REDIS_URL` | API and Socket.IO | Native TLS `rediss://` connection for the target |
| `REDIS_NAMESPACE` | API | `alandi-preview` and `alandi-production` or unique equivalents |
| `JWT_SECRET` | API | Different random secrets in Preview and Production |
| `APP_MODE` | API | `demo` for both public training deployments |
| `ALLOWED_ORIGINS` | API | Exact allowed browser origins, comma-separated, including scheme and any port. Add the Production custom domain when used. |
| `TOMTOM_API_KEY` | API | Optional and can be empty for both training targets |
| `SECURE_COOKIES` | API | Optional outside Vercel. Vercel automatically enables secure cookies. |
| `DATABASE_POOL_SIZE` | API | Optional. Defaults to 3 connections per instance. |
| `DEMO_SPEED`, `CAPACITY_TTL_SECONDS`, `GPS_TTL_SECONDS`, `REROUTE_IMPROVEMENT_SECONDS`, `REROUTE_COOLDOWN_SECONDS` | API | Optional runtime tunables. Defaults are in `backend/dispatch/config.py`. |
| `VITE_MAP_TILE_URL`, `VITE_MAP_ATTRIBUTION` | Vite build | Optional public values. Default to attributed OpenStreetMap tiles. Set the tile URL to an empty value to disable tiles. |
| `API_PROXY_TARGET` | Vite development server | Optional local API target. Hosted browser calls are same-origin. |

Leave Vercel’s system environment variables exposed so the backend can also allow the exact Vercel-generated deployment, Git branch and production project origins through `VERCEL_URL`, `VERCEL_BRANCH_URL` and `VERCEL_PROJECT_PRODUCTION_URL`. Configure additional allowed origins explicitly. Use Vercel Secret variables for the signing key, PostgreSQL, Redis and TomTom credentials. The API reads these credential names. The web service reads only the public `VITE_` map values. [Vite environment-variable exposure](https://vite.dev/guide/env-and-mode) · [FastAPI JWT and Argon2](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)

Generate the signing secret in PowerShell. Run it again for the second environment. Paste the clipboard into Vercel, then clear the clipboard.

```powershell
$bytes = New-Object byte[] 48
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$rng.Dispose()
[Convert]::ToBase64String($bytes) | Set-Clipboard
# Paste into Vercel as JWT_SECRET. Then clear the clipboard.
Set-Clipboard -Value ''
```

## 3. Import, migrate, deploy Preview, and promote

For a new remote, create an empty GitHub repository and run these commands from the source repository in PowerShell. For an already linked checkout, keep its existing remote and push the branch you are releasing.

```powershell
$githubRepo = Read-Host 'GitHub repository HTTPS URL'
git remote add origin $githubRepo
git push -u origin main
```

### Dashboard

1. Push the committed source to a GitHub repository you control. In Vercel choose **Add New → Project → Import Git Repository** and select your repository. Keep the **Root Directory** at `.` so Vercel sees `vercel.json`. See the [Vercel Services walkthrough](https://vercel.com/kb/guide/vercel-services) if the project does not show both services.
2. Configure the Preview and Production variables from the table. Enable **Fluid compute** in **Settings → Functions**. Keep `APP_MODE=demo` for the hackathon.
3. Before the formal preview check, run the release migration and seed commands below against Preview. If the import already triggered a deployment, create or redeploy the Preview after migrations and variable setup.
4. Open the deployment’s HTTPS Preview URL. Run the production routing smoke script and Playwright demo below. Make the Preview accessible to the test browser under the project’s deployment protection settings.
5. Repeat the release migration and seed commands against Production. Use the same verified revision with Production settings. Stage a Production deployment, smoke-test its HTTPS URL and promote it to the project’s public domain. Repeat the health check against the final domain. [Promoting a deployment](https://vercel.com/docs/deployments/promoting-a-deployment)

### CLI and one-time release commands

Run from the repository root in PowerShell. Link the same project as the dashboard import, or create an empty project first and connect its Git repository through **Settings → Git**. CLI 66.6.0 supports the flags below.

```powershell
npx --yes vercel@66.6.0 login
npx --yes vercel@66.6.0 link
$secrets = @('DATABASE_URL','REDIS_URL','JWT_SECRET')
$config = @('REDIS_NAMESPACE','APP_MODE','ALLOWED_ORIGINS')
foreach ($stage in @('preview','production')) {
  foreach ($name in $secrets) {
    npx --yes vercel@66.6.0 env add $name --environment $stage --type secret
  }
  foreach ($name in $config) {
    npx --yes vercel@66.6.0 env add $name --environment $stage --type config
  }
}
```

Use `vercel env update` to replace an existing value. Use `--type secret` for a connected deployment’s optional `TOMTOM_API_KEY`. The CLI reads values at its prompt. Use the project’s assigned Vercel origins for Preview and a custom domain if applicable for Production.

Run this release block once per target from the repository root. Fill the hidden prompts with the **same target branch’s** pooled and direct Neon URIs. The commands use the root clone’s `backend` folder. Run the block first for Preview and again for Production just before staging.

```powershell
Push-Location .\backend
try {
  if (-not (Test-Path .\.venv\Scripts\python.exe)) { py -3.12 -m venv .venv }
  .\.venv\Scripts\python.exe -m pip install -r requirements.txt
  $env:DATABASE_URL = [System.Net.NetworkCredential]::new('',(Read-Host 'Pooled URI for this target' -AsSecureString)).Password
  $env:MIGRATION_DATABASE_URL = [System.Net.NetworkCredential]::new('',(Read-Host 'Direct URI for the same target' -AsSecureString)).Password
  $env:APP_MODE = 'demo'
  .\.venv\Scripts\python.exe -m alembic upgrade head
  if ($LASTEXITCODE -ne 0) { throw 'Migration failed' }
  .\.venv\Scripts\python.exe -m dispatch.manage seed-demo
  if ($LASTEXITCODE -ne 0) { throw 'Training seed failed' }
} finally {
  Remove-Item Env:DATABASE_URL,Env:MIGRATION_DATABASE_URL,Env:APP_MODE -ErrorAction SilentlyContinue
  Pop-Location
}
```

The release uses a PostgreSQL advisory lock. API startup performs normal requests against the already migrated database. Each **Start training demo** click creates an isolated group with freshly reported example capacities.

Deploy and check the Preview, then run the release block for Production before continuing. The first command assumes the project has already been linked or imported. Vercel may classify the first deployment for a new project as Production. In that case use explicit `--environment preview` and check its target in the deployment details before testing.

```powershell
npx --yes vercel@66.6.0 deploy --environment preview
$preview = Read-Host 'Preview HTTPS URL'
node .\scripts\check-deployment.mjs $preview
Push-Location .\frontend
npm ci
npx playwright install chromium
$env:E2E_BASE_URL = $preview
npm run test:e2e
Remove-Item Env:E2E_BASE_URL
Pop-Location
```

After Production migration and seed, stage and smoke-test with the Production values before routing the public domain. Run the full browser test against the staged HTTPS URL using the same commands above with its URL.

```powershell
npx --yes vercel@66.6.0 deploy --prod --skip-domain
$staged = Read-Host 'Staged Production HTTPS URL'
node .\scripts\check-deployment.mjs $staged
npx --yes vercel@66.6.0 promote $staged
$publicUrl = Read-Host 'Public Vercel URL'
node .\scripts\check-deployment.mjs $publicUrl
```

The smoke script checks production assets, deep links, API JSON, and the nested intake contract. The Playwright mission verifies the authenticated WebSocket upgrade, cross-session event delivery and the complete training flow. Confirm the app opens as **Training workspace** on the public URL. See [Verification](docs/VERIFICATION.md) for the current deployment state.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Blank street map | Switch to **Schematic**. Verify the tile URL, provider quota and visible attribution. In training the schematic, route, fleet pins and controls remain usable when tile requests fail. |
| API returns HTML | Request `/api/health` and `/api/openapi.json`. Verify root `.`, top-level API rewrites ahead of the web catch-all, and React fallback scoped to the web service. |
| Socket handshake fails | Look for a WebSocket `101` upgrade on `/api/socket.io?EIO=4&transport=websocket`. Check Fluid compute, the exact backend path, allowed HTTPS origin, Preview protection and a valid JWT in the Socket.IO connect payload. |
| Missing cross-tab updates | Confirm both tabs have the same group link. Check health for Redis, use the same native Pub/Sub URL and namespace for all instances in the same environment, and verify event replay when the browser shows reconnecting. |
| Stale GPS | Start sharing from the assigned driver tab over HTTPS and keep it in the foreground. Check permission, observation time and accuracy. Confirm departure or arrival manually if needed. |
| Database connection limit | Use Neon’s pooled URL for API traffic and direct migration access only in the release terminal. Reduce the per-instance pool if needed and keep the function and database regions close. |

Backend credentialed live routing, Vercel routing and function lifetime, TLS Redis Pub/Sub and Neon pooling require account-backed verification. Keep the public project in demo mode while doing the first release.
