import { useEffect, useRef, useState, type ReactNode } from 'react'
import L from 'leaflet'
import { Layers3, LocateFixed, MapPin } from 'lucide-react'
import 'leaflet/dist/leaflet.css'
import { simulatedPosition } from '../lib/display'
import type { Hospital, Incident, MapFixture, Point, Unit } from '../lib/types'

const ALANDI: Point = [18.6725, 73.8949]
const TILE_URL = import.meta.env.VITE_MAP_TILE_URL ?? 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
const ATTRIBUTION = import.meta.env.VITE_MAP_ATTRIBUTION ?? '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'

interface Props {
  fixture: MapFixture | null
  units?: Unit[]
  hospitals?: Hospital[]
  incident?: Incident | null
  pin?: Point
  pinName?: string
  onPin?: (point: Point) => void
  serverTime?: string
  children?: ReactNode
  title?: string
  overview?: boolean
}

function label(text: string, className = 'map-tip') {
  const node = document.createElement('span')
  node.textContent = text
  node.className = className
  return node
}

function marker(point: Point, kind: 'hospital' | 'patient' | 'unit', name: string, drag = false) {
  const symbol = document.createElement('span')
  symbol.textContent = kind === 'hospital' ? '+' : kind === 'patient' ? '●' : '✚'
  const layer = L.marker(point, {
    title: name, alt: name, draggable: drag, riseOnHover: true,
    icon: L.divIcon({ html: symbol, className: 'map-marker ' + kind, iconSize: [31, 31], iconAnchor: [15, 16] }),
  }).bindTooltip(label(name), { direction: 'top', offset: [0, -15], className: 'map-tooltip' })
  layer.on('add', () => layer.getElement()?.setAttribute('aria-label', name))
  return layer
}

function schematic(target: L.LayerGroup, fixture: MapFixture) {
  const blocks: Point[][] = [
    [[18.6751, 73.8862], [18.6752, 73.8921], [18.6789, 73.8908], [18.6791, 73.8856]],
    [[18.6691, 73.8855], [18.6707, 73.8927], [18.667, 73.8928], [18.6668, 73.8869]],
    [[18.675, 73.8988], [18.6748, 73.9039], [18.6783, 73.9043], [18.6779, 73.9000]],
    [[18.6697, 73.8996], [18.6696, 73.9030], [18.6679, 73.9028], [18.6674, 73.8993]],
  ]
  for (const block of blocks) {
    L.polygon(block, { color: '#cbdcbc', fillColor: '#cfdfbf', weight: 1, fillOpacity: .6, interactive: false }).addTo(target)
  }
  L.polyline(fixture.river, { color: '#afd7e5', weight: 16, opacity: .92, interactive: false }).addTo(target)
  L.polyline(fixture.river, { color: '#a3cfde', weight: 4, opacity: .65, interactive: false }).addTo(target)
  for (const [index, road] of fixture.roads.entries()) {
    L.polyline(road, { color: '#d8dbcf', weight: index < 2 ? 10 : 7, interactive: false }).addTo(target)
    L.polyline(road, { color: '#fffdf9', weight: index < 2 ? 7 : 4, interactive: false }).addTo(target)
  }
  for (const [lat, lng, name] of fixture.labels) {
    L.marker([lat, lng], {
      icon: L.divIcon({ className: 'map-area-label', html: label(name, ''), iconSize: [100, 22], iconAnchor: [50, 11] }),
      keyboard: false, interactive: false,
    }).addTo(target)
  }
}

export function MissionMap({ fixture, units = [], hospitals = [], incident, pin, pinName, onPin,
  serverTime, children, title = 'Mission map', overview = false }: Props) {
  const element = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map | null>(null)
  const layers = useRef<L.LayerGroup | null>(null)
  const animation = useRef<L.Marker | null>(null)
  const unit = incident?.mission
  const route = unit?.route
  const [choice, setChoice] = useState(!fixture)
  const [tileError, setTileError] = useState(false)
  const [wide, setWide] = useState(false)
  const tileEnabled = choice && !tileError && Boolean(TILE_URL)
  const fitTarget = useRef('')
  const movePin = useRef(onPin)

  useEffect(() => { movePin.current = onPin }, [onPin])

  useEffect(() => {
    if (!element.current) return
    const instance = L.map(element.current, { zoomControl: true, scrollWheelZoom: false })
      .setView(ALANDI, 15)
    instance.zoomControl.setPosition('topright')
    map.current = instance
    layers.current = L.layerGroup().addTo(instance)
    const observer = new ResizeObserver(() => instance.invalidateSize({ pan: false }))
    observer.observe(element.current)
    return () => { observer.disconnect(); instance.remove(); map.current = null }
  }, [])

  useEffect(() => {
    if (!map.current || !tileEnabled) return
    const instance = map.current
    const tiles = L.tileLayer(TILE_URL, { maxZoom: 19, attribution: ATTRIBUTION, noWrap: true })
    tiles.once('tileerror', () => setTileError(true))
    tiles.addTo(instance)
    return () => { instance.removeLayer(tiles) }
  }, [tileEnabled])

  useEffect(() => {
    const instance = map.current
    if (!fixture || !instance || tileEnabled) return
    const base = L.layerGroup().addTo(instance)
    schematic(base, fixture)
    return () => { instance.removeLayer(base) }
  }, [fixture, tileEnabled])

  useEffect(() => {
    const group = layers.current
    const instance = map.current
    if (!group || !instance) return
    group.clearLayers()
    animation.current = null
    const bounds: Point[] = []
    const stops = new Set<string>()
    function putHospital(p: Point, name: string) {
      const key = p.join(',')
      if (stops.has(key)) return
      stops.add(key)
      marker(p, 'hospital', name).addTo(group!)
    }
    if (route?.points.length) {
      const manual = route.source.startsWith('Dispatcher')
      L.polyline(route.points, { color: '#ffffff', weight: 11, opacity: .96, interactive: false }).addTo(group)
      L.polyline(route.points, { color: '#365cf5', weight: 6, opacity: .97, dashArray: manual ? '6 8' : undefined,
        interactive: false }).addTo(group)
      bounds.push(...route.points)
    }
    for (const hospital of hospitals) {
      putHospital(hospital.location, hospital.name)
      if (wide) bounds.push(hospital.location)
    }
    if (unit) {
      putHospital(unit.dispatching_location, unit.dispatching_hospital)
      if (unit.receiving_location && unit.receiving_hospital) putHospital(unit.receiving_location, unit.receiving_hospital)
    }
    for (const item of units) {
      if (!item.location || (item.id === unit?.unit_id && unit.simulated_position)) continue
      const icon = marker(item.location, 'unit', item.name + (item.stale ? ' · stale GPS' : ' · ' + item.status))
      icon.setZIndexOffset(item.id === unit?.unit_id ? 800 : -100)
      icon.addTo(group)
      if (wide || item.id === unit?.unit_id) bounds.push(item.location)
    }
    if (unit?.simulated_position) {
      animation.current = marker(unit.simulated_position, 'unit', unit.unit + ' · simulated position').setZIndexOffset(1000).addTo(group)
    }
    const pickup = pin || incident?.pickup
    if (pickup) {
      const pickupIcon = marker(pickup, 'patient', pinName || incident?.pickup_name || 'Pickup pin', Boolean(pin && movePin.current))
      pickupIcon.setZIndexOffset(600).addTo(group)
      pickupIcon.on('dragend', () => {
        const point = pickupIcon.getLatLng()
        movePin.current?.([point.lat, point.lng])
      })
      bounds.push(pickup)
    }
    const key = [wide, incident?.id, route?.version, pin?.join(',')].join('|')
    if (key !== fitTarget.current) {
      fitTarget.current = key
      if (bounds.length > 1) instance.fitBounds(L.latLngBounds(bounds), { padding: [45, 60], maxZoom: 16, animate: false })
      else if (bounds.length === 1) instance.setView(bounds[0], 15)
      else instance.setView(ALANDI, 15)
    }
  }, [hospitals, incident?.id, incident?.pickup, incident?.pickup_name, pin, pinName, route, unit, units, wide])

  useEffect(() => {
    if (!unit?.simulated_position || !unit.leg_started_at) return
    const offset = serverTime ? Date.parse(serverTime) - Date.now() : 0
    const timer = window.setInterval(() => {
      const point = simulatedPosition(unit, offset)
      if (point) animation.current?.setLatLng(point)
    }, 220)
    return () => window.clearInterval(timer)
  }, [serverTime, unit])

  return <section className="panel map-panel" aria-label={title}>
    <div className="panel-head map-heading">
      <div><h2>{title}</h2><p>{fixture ? 'Alandi service area · Pune, Maharashtra' : 'Current route and fleet positions'}</p></div>
      <div className="map-tools">
        {overview && <button className="button subtle compact" onClick={() => setWide(!wide)}><LocateFixed size={15} />{wide ? 'Focus mission' : 'All fleet'}</button>}
        {TILE_URL && <button className="button subtle compact" onClick={() => { setTileError(false); setChoice(!tileEnabled) }} aria-pressed={tileEnabled}><Layers3 size={15} />{tileEnabled ? 'Schematic' : 'Map tiles'}</button>}
      </div>
    </div>
    <div className={'map-stage' + (children ? ' has-sheet' : '')}>
      <div className="leaflet-map" ref={element} aria-label="Interactive map. Use zoom controls and arrow keys to explore." />
      <div className="map-mode"><span className="dots" />{tileError ? 'Tiles unavailable' : tileEnabled ? 'Street map' : 'Schematic map'}</div>
      {children}
    </div>
    <div className="map-footer">
      <div className="map-legend"><span><i className="legend-unit" /> Ambulance</span><span><i className="legend-pickup" /> Pickup</span><span><i className="legend-hospital" /> Hospital</span><span><i className="legend-route" /> Route</span></div>
      <span className="map-source"><MapPin size={14} />{tileError ? 'Route and markers are still available' : fixture ? 'Training coordinates' : 'GPS when shared'}</span>
    </div>
  </section>
}
