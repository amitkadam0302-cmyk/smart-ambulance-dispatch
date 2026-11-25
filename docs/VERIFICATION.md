# Verification

The implementation was exercised on Linux on 25 November 2026. Every browser-run case used an independently seeded training group and fictional data. Playwright used installed Chromium 156.0.8078.79, two independent browser contexts for patient and staff, and four separate staff tabs. The screenshots in the README were captured from that flow.

## Checks

| Check | Command or evidence | State |
| --- | --- | --- |
| Empty PostgreSQL migration and seed | `python -m alembic upgrade head`, `python -m dispatch.manage seed-demo` twice, and `python -m alembic check` | Passed. Applied revision 0001, repeat seed returned the same template, and Alembic detected no schema changes. |
| Domain and real PostgreSQL/Redis | `python -m pytest -q --tb=short` in `backend` | 20 passed. Includes actual contention for one free unit, same-key SOS retry, queue priority and age, both assignment constraints, reservation movement, full sending ER, refusal, intake and handover, event isolation, GPS validation, and a restart with two Uvicorn processes sharing Redis. |
| Forecast training and later holdout | `python -m ml.train` in `backend` | Passed. 5,040 training rows, 1,008 later holdout rows and 3,103 zero-count rows. Poisson MAE 0.665060 versus historical-average MAE 0.712925. |
| Frontend critical controls | `npm test` in `frontend` | 2 passed. Pin confirmation gates SOS and ignores double-click during submission. Failed nurse intake preserves fields and reuses the submission key. |
| Backend and frontend lint | `ruff check backend scripts` and `npm run lint` | Passed. |
| Type checks and production build | `npm run typecheck` and `npm run build` | Passed. Vite built the hashed JavaScript and CSS bundles. |
| Alandi complete mission | Playwright mission specification against Vite and Uvicorn, then Compose | Passed. AMB3 won against the 44- and 49-minute candidates. A separate session received a genuine WebSocket event. A nurse signed in after arrival and submitted once. Hospital handover occupied one bed and released AMB3 to a queued phone request. |
| Compose production bundle | Sequential Compose image builds, then `docker compose up --no-build -d` on port 3000 | Passed. PostgreSQL, Redis and API healthy. Migration/seed container exited 0. Nginx served the built web bundle. The cloud Docker builds used a temporary BuildKit CA mount for the package proxy. |
| Production routing smoke | `node scripts/check-deployment.mjs http://localhost:3000` after the API health check | Passed. Static assets and deep links returned 200. Health and OpenAPI returned JSON. Unauthorized session and unknown nested API path returned JSON 401 and 404. |
| Complete production-bundle browser run | `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm --prefix frontend run test:e2e` against the Compose app | 2 passed in 52.0 seconds. Includes the full AMB3 mission, the desktop intake overlay and mobile sheet, AMB3 offline leading to AMB1, changed fixture geometry and ETA, simulated browser permission denial and tile failure, interrupted socket, refresh, authenticated reconnect and the analytics view. |
| Vercel configuration and source | `python scripts/validate_vercel.py` with the official downloaded schema | Passed. Validated schema properties and structural routing for assets, deep links, nested API routes and WebSocket paths. |
| TomTom error handling and alternatives | Test cases in `backend/tests/test_routing.py` and `test_http.py` | Passed with mock provider responses. Geometry, ETA, traffic delay, alternatives, cache ordering, timeouts, quota, missing key and timestamped route retention were exercised. |
| Preview and Production Vercel | Target and account credentials required | Pending. Link the GitHub repository in Vercel and configure the separate Neon branches, native TLS Redis and JWT secrets. Migrate and seed each target, then run the smoke and Playwright commands in `DEPLOYMENT.md` before promoting. |
| Credentialed external integrations | TomTom and hosted data services required | Pending. Run a connected test using a TomTom Routing key, and use the Preview test to verify Neon pooling and TLS Redis Pub/Sub against the actual account endpoints. |
| Windows and physical GPS device | Windows PowerShell and a device with geolocation permission | Pending device run. Equivalent startup and browser commands passed on Linux. Browser denial and server GPS validation were exercised locally. |

The local smoke rerun passed after the restarted API became healthy. pytest reported one upstream TestClient deprecation warning.

## Re-run from Windows

Start the Compose app from the repository root. Create a test-only PostgreSQL database before the first backend test run.

```powershell
docker compose up --build -d
docker compose exec db createdb -U dispatch dispatch_test
node .\scripts\check-deployment.mjs http://localhost:3000
```

Install uv from its official distribution or use `py -3.12 -m pip install uv`. Tests apply migrations to the test database. Set `TEST_DATABASE_URL` and `TEST_REDIS_URL` if using different local test service addresses. The test runner isolates the Redis namespace from the running application.

```powershell
Push-Location .\backend
uv sync --locked
uv run python -m pytest -q --tb=short
uv run python -m alembic check
uv run python -m ml.train
uv run ruff check . ..\scripts
uv run python ..\scripts\validate_vercel.py
Pop-Location

Push-Location .\frontend
npm ci
npm run lint
npm run typecheck
npm test
npm run build
npx playwright install chromium
npm run test:e2e
Pop-Location
```

Set `E2E_BASE_URL` to a different origin to test a deployed Preview or staged Production. Set `PLAYWRIGHT_CHROMIUM_EXECUTABLE` if using an existing compatible Chromium build. Set `DEMO_SCREENSHOTS` to an absolute directory to recapture the README images from the main mission.
