export const MINERALS = { REE: 'Rare earth elements', Ni: 'Nickel', Co: 'Cobalt' };
export const CONTEXT_LEVELS = new Set(['district_area', 'operation_complex']);
export const SCOPE_NAMES = {
  site: 'Deposit or mine', subsite: 'Named subsite', district_area: 'District or area',
  operation_complex: 'Operation or geological complex', unknown: 'Source scope unconfirmed',
};

export function normalize(value) {
  return String(value ?? '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
}

export function withinBounds(record, bounds) {
  if (!bounds) return true;
  const [west, south, east, north] = bounds;
  const longitude = ((record.lon + 180) % 360 + 360) % 360 - 180;
  if (record.lat < south || record.lat > north) return false;
  if (east - west >= 360) return true;
  const w = ((west + 180) % 360 + 360) % 360 - 180;
  const e = ((east + 180) % 360 + 360) % 360 - 180;
  return w <= e ? longitude >= w && longitude <= e : longitude >= w || longitude <= e;
}

export function filterRecords(records, filters, bounds = null) {
  const words = normalize(filters.q).trim().split(/\s+/).filter(Boolean);
  return records.filter(record => {
    if (!filters.context && CONTEXT_LEVELS.has(record.entity_level)) return false;
    if (filters.mineral && !record.minerals.includes(filters.mineral)) return false;
    if (filters.country && (record.country || 'unknown') !== filters.country) return false;
    if (filters.source && record.source_id !== filters.source) return false;
    if (!withinBounds(record, bounds)) return false;
    if (!words.length) return true;
    const text = normalize([record.name, record.country_name, record.country,
      record.source_title, record.record_id, ...(record.aliases || []),
      ...(record.context_names || [])].join(' '));
    return words.every(word => text.includes(word));
  });
}

export function parseView(search) {
  const query = new URLSearchParams(search);
  const center = (query.get('center') || '').split(',').map(Number);
  const zoom = Number(query.get('zoom'));
  return {
    q: (query.get('q') || '').slice(0, 200),
    mineral: Object.hasOwn(MINERALS, query.get('mineral') || '') ? query.get('mineral') : '',
    country: query.get('country') || '', source: query.get('source') || '',
    context: query.get('context') === '1', visible: query.get('visible') === '1', selected: query.get('record') || '',
    center: center.length === 2 && center.every(Number.isFinite)
      && Math.abs(center[0]) <= 180 && Math.abs(center[1]) <= 85 ? center : null,
    zoom: query.has('zoom') && Number.isFinite(zoom) && zoom >= -1 && zoom <= 18 ? zoom : null,
  };
}

export function serializeView(filters, view = {}) {
  const query = new URLSearchParams();
  for (const key of ['q', 'mineral', 'country', 'source']) {
    if (filters[key]) query.set(key, filters[key]);
  }
  if (filters.context) query.set('context', '1');
  if (filters.visible) query.set('visible', '1');
  if (filters.selected) query.set('record', filters.selected);
  if (view.center) query.set('center', view.center.map(value => Number(value.toFixed(4))).join(','));
  if (Number.isFinite(view.zoom)) query.set('zoom', Number(view.zoom.toFixed(2)).toString());
  return query.toString();
}

export function safeSourceUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && url.username === '' && url.password === '' ? url.href : null;
  } catch { return null; }
}

export function recordsCsv(records) {
  const fields = ['record_id', 'source_id', 'name', 'country', 'minerals', 'entity_level',
    'lat', 'lon', 'location_precision', 'status', 'source_url', 'source_date', 'source_license'];
  const cell = value => {
    let text = Array.isArray(value) ? value.join('; ') : String(value ?? '');
    if (typeof value === 'string' && /^[\s]*[=+@-]/.test(text)) text = "'" + text;
    return '"' + text.replaceAll('"', '""') + '"';
  };
  return '\uFEFF' + [fields.map(cell).join(','), ...records.map(record =>
    fields.map(field => cell(record[field])).join(','))].join('\r\n') + '\r\n';
}
