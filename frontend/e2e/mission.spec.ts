import { expect, test, type Page } from '@playwright/test'
import type { Snapshot } from '../src/lib/types'

async function getAccess(page: Page) {
  return page.evaluate(() => JSON.parse(sessionStorage.getItem('alandi-dispatch-session-v1')!).access_token as string)
}

async function snapshot(page: Page): Promise<Snapshot> {
  const token = await getAccess(page)
  const response = await page.request.get('/api/dashboard', { headers: { Authorization: 'Bearer ' + token } })
  expect(response.ok()).toBeTruthy()
  return response.json()
}

test('Alandi SOS completes across two browser sessions and releases AMB3 to the next case', async ({ browser, baseURL }) => {
  const publicSession = await browser.newContext({ baseURL, viewport: { width: 1460, height: 1190 } })
  const staffSession = await browser.newContext({ baseURL, viewport: { width: 1460, height: 1190 } })
  const patient = await publicSession.newPage()
  const dispatcher = await staffSession.newPage()
  const driver = await staffSession.newPage()
  const nurse = await staffSession.newPage()
  const hospital = await staffSession.newPage()
  const websocketEvents: string[] = []
  dispatcher.on('websocket', socket => {
    if (!socket.url().includes('/api/socket.io')) return
    socket.on('framereceived', event => {
      if (String(event.payload).includes('domain_event')) websocketEvents.push(String(event.payload))
    })
  })

  await patient.goto('/')
  await expect(patient.getByText('Training workspace', { exact: true })).toBeVisible()
  await patient.getByRole('button', { name: 'Start training demo' }).click()
  await expect(patient.getByRole('heading', { name: 'Alandi training workspace' })).toBeVisible()
  const group = new URL(patient.url()).pathname
  await Promise.all([
    dispatcher.goto(group + '/dispatcher'),
    patient.getByRole('link', { name: 'Open patient', exact: true }).click(),
    driver.goto(group + '/driver'),
    hospital.goto(group + '/hospital'),
  ])
  await expect(dispatcher.getByText('Live updates', { exact: true })).toBeVisible()
  await expect(driver.getByText('Live updates', { exact: true })).toBeVisible()
  await expect(hospital.getByText('Live updates', { exact: true })).toBeVisible()
  const incoming = patient.getByRole('button', { name: 'Send training SOS' })
  await expect(incoming).toBeDisabled()
  await patient.getByRole('button', { name: /Alandi demo pickup/ }).click()
  await patient.getByRole('button', { name: 'Confirm pickup pin' }).click()
  await incoming.click()
  await expect(dispatcher.getByText(/AMB3 had the shortest road ETA/)).toBeVisible()
  await expect.poll(() => websocketEvents.length, { timeout: 5000 }).toBeGreaterThan(0)
  await expect(dispatcher.getByRole('heading', { name: 'ALD-0001' })).toBeVisible()
  await expect(driver.getByRole('heading', { name: 'ALD-0001' })).toBeVisible()
  await expect(hospital.getByRole('heading', { name: 'ALD-0001' })).toBeVisible()
  await expect(patient.getByRole('heading', { name: 'ALD-0001' })).toBeVisible()
  await expect(driver.getByText('Pickup leg', { exact: true })).toBeVisible()
  const assigned = (await snapshot(dispatcher)).incidents.find(i => i.code === 'ALD-0001')!
  expect(assigned.mission?.unit).toBe('AMB3')
  expect(assigned.mission?.dispatching_hospital).toBe('Sant Dnyaneshwar Hospital Alandi')
  expect(assigned.candidates.map(c => [c.name, c.eta_seconds])).toEqual([['AMB1', 2640], ['AMB2', 2940], ['AMB3', 300]])
  expect(assigned.mission?.pickup_route?.points.length).toBeGreaterThan(3)

  const screenshotPath = process.env.DEMO_SCREENSHOTS
  await expect(driver.getByText('En route to pickup', { exact: true })).toBeVisible({ timeout: 13000 })
  if (screenshotPath) await dispatcher.screenshot({ path: screenshotPath + '/dispatch-overview.png', animations: 'disabled' })
  await expect(driver.getByText('At pickup', { exact: true })).toBeVisible({ timeout: 18000 })

  // Sign in after the pickup on a new tab within the independent staff browser session.
  await nurse.goto(group + '/nurse')
  const tablet = nurse.getByRole('complementary', { name: 'Nurse intake' })
  await expect(tablet).toBeVisible()
  expect(await tablet.evaluate(node => {
    const box = node.getBoundingClientRect()
    return node.contains(document.elementFromPoint(box.left + 60, box.top + 24))
  })).toBe(true)
  await tablet.getByLabel('Name or alias').fill('Fictional patient A')
  await tablet.getByLabel('Age (optional)').fill('24')
  await tablet.getByLabel('Reported issue').selectOption('accident')
  if (screenshotPath) await nurse.screenshot({ path: screenshotPath + '/nurse-intake.png', animations: 'disabled' })
  await nurse.setViewportSize({ width: 390, height: 844 })
  await expect(tablet).toHaveCSS('position', 'relative')
  await tablet.getByRole('button', { name: 'Save intake' }).click()
  await expect(tablet).toHaveCount(0)
  await expect(driver.getByText('Receiving leg', { exact: true })).toBeVisible()
  await expect(driver.getByText('Transporting', { exact: true })).toBeVisible()
  const afterIntake = (await snapshot(dispatcher)).incidents.find(i => i.code === 'ALD-0001')!
  expect(afterIntake.mission?.route?.leg).toBe('receiving')
  expect(afterIntake.mission?.route?.points).not.toEqual(assigned.mission?.pickup_route?.points)
  expect(afterIntake.timeline.filter(e => e.kind === 'intake.saved')).toHaveLength(1)
  expect(afterIntake.mission?.intake).toBeNull()

  const token = await getAccess(dispatcher)
  const headers = { Authorization: 'Bearer ' + token }
  for (const unit of (await snapshot(dispatcher)).units.filter(u => u.name !== 'AMB3')) {
    const response = await dispatcher.request.put(`/api/units/${unit.id}/availability`, {
      headers, data: { status: 'offline', checked_in: true, reason: 'Queue handover test' },
    })
    expect(response.ok()).toBeTruthy()
  }
  await dispatcher.getByRole('button', { name: 'Log phone request' }).click()
  await dispatcher.getByLabel('Pickup location').fill('Second fictional request')
  await dispatcher.getByRole('button', { name: 'Save phone request' }).click()
  await expect(dispatcher.getByRole('button', { name: /ALD-0002.*Queued/ })).toBeVisible()

  await expect(hospital.getByRole('button', { name: 'Confirm handover' })).toBeVisible({ timeout: 18000 })
  await hospital.getByRole('button', { name: 'Confirm handover' }).click()
  await expect(patient.getByText('Completed', { exact: true })).toBeVisible()
  await expect(patient.getByText('ALD-0001', { exact: true })).toBeVisible()
  await expect.poll(async () => (await snapshot(dispatcher)).incidents.find(i => i.code === 'ALD-0002')?.mission?.unit).toBe('AMB3')
  for (const page of [dispatcher, driver, nurse, hospital]) {
    const roleCase = (await snapshot(page)).incidents.find(i => i.code === 'ALD-0001')!
    expect(roleCase.status).toBe('completed')
    expect(roleCase.mission?.id).toBe(assigned.mission?.id)
  }
  const complete = (await snapshot(dispatcher)).incidents.find(i => i.code === 'ALD-0001')!
  expect(complete.status).toBe('completed')
  expect(complete.timeline.filter(e => e.kind === 'mission.completed')).toHaveLength(1)
  expect((await snapshot(patient)).incidents.map(i => [i.code, i.status])).toEqual([['ALD-0001', 'completed']])
  expect((await snapshot(hospital)).hospitals[0].capacity?.occupied).toBe(8)
  await publicSession.close()
  await staffSession.close()
})

test('changed eligibility, simulated traffic and app recovery survive a tile failure and a socket interruption', async ({ browser, baseURL }) => {
  const context = await browser.newContext({ baseURL })
  await context.route('https://tile.openstreetmap.org/**', route => route.abort())
  const staff = await context.newPage()
  const patient = await context.newPage()
  await patient.addInitScript(() => {
    Object.defineProperty(navigator.geolocation, 'getCurrentPosition', {
      value: (_success: unknown, reject: (e: {code: number}) => void) => reject({ code: 1 }),
    })
  })
  await staff.goto('/')
  await staff.getByRole('button', { name: 'Start training demo' }).click()
  await expect(staff.getByRole('heading', { name: 'Alandi training workspace' })).toBeVisible()
  const group = new URL(staff.url()).pathname
  await Promise.all([staff.goto(group + '/dispatcher'), patient.goto(group + '/patient')])
  await expect(staff.getByText('Live updates', { exact: true })).toBeVisible()
  await patient.getByRole('button', { name: 'Use browser location' }).click()
  await expect(patient.getByRole('alert')).toContainText('Drag the pickup pin')
  await staff.getByRole('button', { name: 'Map tiles' }).click()
  await expect(staff.getByText('Tiles unavailable', { exact: true })).toBeVisible()

  await staff.getByRole('button', { name: 'Fleet & hospitals' }).click()
  const amb3 = staff.locator('.fleet-row').filter({ hasText: 'AMB3' })
  await amb3.getByText('Update unit', { exact: true }).click()
  await amb3.getByLabel('Availability').selectOption('offline')
  await amb3.getByLabel('Reason').fill('Alternative unit demonstration')
  await amb3.getByRole('button', { name: 'Save availability' }).click()
  await expect(amb3.getByText('offline', { exact: true })).toBeVisible()
  await staff.getByRole('button', { name: 'Overview', exact: true }).click()

  await patient.getByRole('button', { name: 'Confirm pickup pin' }).click()
  await patient.getByRole('button', { name: 'Send training SOS' }).click()
  await expect(staff.getByText('AMB1 had the shortest road ETA', { exact: false })).toBeVisible()
  await expect(staff.getByText('Unit offline', { exact: true })).toBeVisible()
  const earlier = (await snapshot(staff)).incidents[0].mission!
  await staff.getByRole('button', { name: 'Inject traffic' }).click()
  await expect(staff.getByRole('button', { name: 'Traffic injected' })).toBeDisabled()
  const later = (await snapshot(staff)).incidents[0].mission!
  expect(later.route_version).toBe(earlier.route_version + 1)
  expect(later.route!.eta_seconds).toBeLessThan(earlier.route!.eta_seconds)
  expect(later.route!.points).not.toEqual(earlier.route!.points)
  await expect(staff.getByRole('button', { name: 'Log phone request' })).toBeEnabled()

  let interrupted = true
  await staff.routeWebSocket(/\/api\/socket\.io\?/, ws => {
    if (interrupted) return ws.close({ code: 1001, reason: 'Test network interruption' })
    ws.connectToServer()
  })
  await staff.reload()
  await expect(staff.getByText(/Reconnecting · REST fallback/)).toBeVisible({ timeout: 12000 })
  await expect(staff.getByRole('heading', { name: 'ALD-0001' })).toBeVisible()
  const recovered = (await snapshot(staff)).incidents[0]
  expect(recovered.mission?.route_version).toBe(later.route_version)
  expect(recovered.timeline.filter(event => event.kind === 'route.changed')).toHaveLength(1)
  expect(new Set(recovered.timeline.map(event => event.id)).size).toBe(recovered.timeline.length)

  interrupted = false
  await expect(staff.getByText('Live updates', { exact: true })).toBeVisible({ timeout: 20000 })
  await staff.getByRole('button', { name: 'Analytics' }).click()
  await expect(staff.getByText(/trained on synthetic data/)).toBeVisible()
  await expect(staff.getByText('Pimpri-Chinchwad', { exact: true })).toBeVisible()
  await context.close()
})
