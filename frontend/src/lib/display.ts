import type { Mission, Point, Status } from './types'

export const IST = 'Asia/Kolkata'
const clock = new Intl.DateTimeFormat('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: IST })
const date = new Intl.DateTimeFormat('en-IN', { weekday: 'long', day: 'numeric', month: 'short', timeZone: IST })
const age = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })

export const time = (stamp: string) => clock.format(new Date(stamp)) + ' IST'
export const day = (stamp: string) => date.format(new Date(stamp))
export const ageOf = (stamp: string) => {
  const minutes = Math.max(0, Math.round((Date.now() - Date.parse(stamp)) / 60000))
  return minutes < 1 ? 'Just updated' : minutes < 60 ? age.format(-minutes, 'minute') : age.format(-Math.floor(minutes / 60), 'hour')
}
export const minutes = (seconds: number) => seconds < 60 ? `${Math.round(seconds)} sec` : `${Math.round(seconds / 60)} min`
export const distance = (meters: number) => meters < 1000 ? `${meters} m` : `${(meters / 1000).toFixed(1)} km`

export const statuses: Record<Status, string> = {
  queued: 'Queued', assigned: 'Assigned', en_route_to_patient: 'En route to pickup',
  at_patient: 'At pickup', transporting: 'Transporting', at_hospital: 'At hospital',
  completed: 'Completed', cancelled: 'Cancelled',
}
export const terminal = (status: Status) => status === 'completed' || status === 'cancelled'

export function simulatedPosition(mission: Mission | null, serverOffset: number, at = Date.now()): Point | null {
  if (!mission?.leg_started_at || !mission.leg_seconds || !mission.route) return mission?.simulated_position ?? null
  const points = mission.route.points
  const measure = (a: Point, b: Point) => Math.hypot(a[0] - b[0], (a[1] - b[1]) * Math.cos(a[0] * Math.PI / 180))
  const lengths = points.slice(1).map((p, i) => measure(p, points[i]))
  const ratio = Math.max(0, Math.min(1, (at + serverOffset - Date.parse(mission.leg_started_at)) / (mission.leg_seconds * 1000)))
  let remaining = lengths.reduce((sum, n) => sum + n, 0) * ratio
  for (let i = 0; i < lengths.length; i += 1) {
    if (lengths[i] > 0 && remaining <= lengths[i]) {
      const factor = remaining / lengths[i]
      const [a, b] = [points[i], points[i + 1]]
      return [a[0] + (b[0] - a[0]) * factor, a[1] + (b[1] - a[1]) * factor]
    }
    remaining -= lengths[i]
  }
  return points.at(-1) ?? null
}
