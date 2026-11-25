import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { Mission, Snapshot } from '../lib/types'
import { IntakeTablet } from './DriverNurse'
import { Patient } from './Patient'

const { request } = vi.hoisted(() => ({ request: vi.fn() }))
vi.mock('../lib/session', () => ({ useSession: () => ({ request }) }))
vi.mock('../components/Map', () => ({ MissionMap: () => <div aria-label="Test map" /> }))

afterEach(() => { cleanup(); request.mockReset() })

test('patient confirms a pin before asking for dispatch and cannot double-click a pending SOS', async () => {
  const data = { incidents: [], units: [], server_time: new Date().toISOString() } as unknown as Snapshot
  const refresh = vi.fn(async () => {})
  let finish: (value: unknown) => void = () => {}
  request.mockReturnValue(new Promise(resolve => { finish = resolve }))
  render(<Patient data={data} meta={{ mode: 'demo', timezone: 'Asia/Kolkata', schematic: null,
    pickup: { name: 'MIT ACSC College, Alandi', position: [18.6716, 73.8914] } }} refresh={refresh} />)
  const button = screen.getByRole('button', { name: 'Send training SOS' })
  expect(button).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Confirm pickup pin' }))
  expect(button).toBeEnabled()
  fireEvent.click(button)
  fireEvent.click(button)
  expect(request).toHaveBeenCalledTimes(1)
  expect(request.mock.calls[0][1]).toMatchObject({ body: { source: 'alandi', pickup_name: 'MIT ACSC College, Alandi' }, key: expect.any(String) })
  finish({ id: 'fixture' })
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1))
})

test('failed nurse intake keeps the fields and idempotency key and closes after a successful retry', async () => {
  const mission = { id: 'example-id', unit: 'AMB3' } as Mission
  const refresh = vi.fn(async () => {})
  request.mockRejectedValueOnce(new Error('Service interrupted')).mockResolvedValueOnce({ status: 'transporting' })
  render(<IntakeTablet mission={mission} code="ALD-0001" refresh={refresh}/>)
  fireEvent.change(screen.getByLabelText('Name or alias'), { target: { value: 'Example person' } })
  fireEvent.change(screen.getByLabelText('Age (optional)'), { target: { value: '24' } })
  fireEvent.change(screen.getByLabelText('Reported issue'), { target: { value: 'pregnancy' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save intake' }))
  await screen.findByRole('alert')
  expect(screen.getByLabelText('Name or alias')).toHaveValue('Example person')
  expect(screen.getByLabelText('Age (optional)')).toHaveValue(24)
  fireEvent.click(screen.getByRole('button', { name: 'Save intake' }))
  await waitFor(() => expect(screen.queryByRole('complementary', { name: 'Nurse intake' })).toBeNull())
  expect(request.mock.calls[0][1].key).toBe(request.mock.calls[1][1].key)
  expect(request.mock.calls[1][1].body).toMatchObject({ alias: 'Example person', age: 24, issue: 'pregnancy' })
})
