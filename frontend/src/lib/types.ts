export const roles = ['patient', 'dispatcher', 'driver', 'nurse', 'hospital'] as const
export type Role = typeof roles[number]
export type Point = [number, number]
export type Priority = 'normal' | 'high' | 'critical'
export type Status = 'queued' | 'assigned' | 'en_route_to_patient' | 'at_patient' | 'transporting' | 'at_hospital' | 'completed' | 'cancelled'

export interface Actor {
  id: string
  name: string
  workspace_id: string
  workspace_name: string
  role: Role
  mode: 'demo' | 'connected'
  unit_id: string | null
  hospital_id: string | null
}

export interface Tokens {
  access_token: string
  slot: string
  expires_at: number
}

export interface SessionInfo extends Tokens {
  group?: string
  entry?: string
  role: Role
}

export interface MapFixture {
  roads: Point[][]
  river: Point[]
  labels: [number, number, string][]
}

export interface Meta {
  mode: Actor['mode']
  timezone: string
  pickup: { name: string; position: Point } | null
  schematic: MapFixture | null
}

export interface Route {
  version: number
  leg: 'pickup' | 'receiving'
  points: Point[]
  directions: string[]
  eta_seconds: number
  distance_m: number
  delay_seconds: number | null
  source: string
  calculated_at: string
}

export interface Candidate {
  id: string
  name: string
  base?: string
  reason: string
  distance_m?: number
  eta_seconds?: number
  source?: string
  calculated_at?: string
}

export interface Intake {
  alias: string
  age: number | null
  issue: string
  observations?: string | null
}

export interface Mission {
  id: string
  status: Status
  unit: string
  unit_id: string
  dispatching_hospital: string
  dispatching_hospital_id: string
  dispatching_location: Point
  receiving_hospital: string | null
  receiving_hospital_id: string | null
  receiving_location: Point | null
  destination_status: 'coordination' | 'pending' | 'accepted'
  released: boolean
  acknowledged: boolean
  route: Route | null
  pickup_route: Route | null
  receiving_route: Route | null
  route_version: number
  routing_issue: string | null
  destination_candidates: Candidate[]
  leg_seconds: number | null
  leg_started_at: string | null
  simulated_position: Point | null
  traffic_injected: boolean
  intake_saved: boolean
  intake: Intake | null
  updated_at: string
}

export interface DomainEvent {
  id: string
  seq: number
  kind: string
  reason: string
  occurred_at: string
  incident_id: string | null
  mission_id: string | null
  payload: Record<string, unknown>
  read?: boolean
}

export interface Incident {
  id: string
  code: string
  status: Status
  priority: Priority | null
  effective_priority: Priority
  reported_urgency: Priority
  pickup_name: string
  pickup: Point
  zone: string
  channel: 'sos' | 'phone'
  dispatch_note: string
  candidates: Candidate[]
  mission: Mission | null
  queue_position: number | null
  timeline: DomainEvent[]
  created_at: string
  arrived_at: string | null
  unit_requirement: string | null
  receiving_requirement: string | null
}

export interface Capacity {
  total: number
  occupied: number
  reserved: number
  available: number
  accepting: boolean
  source: string
  reported_at: string
  stale: boolean
}

export interface Hospital {
  id: string
  code: string
  name: string
  location: Point
  capabilities: string[]
  capacity: Capacity | null
}

export interface Unit {
  id: string
  name: string
  status: 'available' | 'busy' | 'offline' | 'out_of_service'
  base: string
  hospital_id: string
  location: Point | null
  checked_in: boolean
  stale: boolean
  source: string
  observed_at: string | null
  accuracy_m: number | null
}

export interface Snapshot {
  sequence: number
  generation: number
  server_time: string
  session: Actor
  incidents: Incident[]
  queue: string[]
  units: Unit[]
  hospitals: Hospital[]
  notifications: DomainEvent[]
  metrics: {
    active_units: number
    available_units: number
    queued: number
    median_arrival_seconds: number | null
    completed: number
  }
}
