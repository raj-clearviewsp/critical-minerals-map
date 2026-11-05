export const MINERALS = { REE: 'Rare earth elements', Ni: 'Nickel', Co: 'Cobalt' };
export const RUNG_LABELS = { 1: 'Occurrence', 2: 'Historical or non-compliant estimate', 3: 'Code-compliant resource', 4: 'Reserve', 5: 'Operating' };
export const reviewName = value => ({reviewed_physical_site:'Reviewed group',single_source_provisional:'Provisional single-source entry'}[value] || (value || '').replaceAll('_',' '));
export const statusName = value => {
  const text = String(value || '').replaceAll('_',' ');
  return text ? text[0].toUpperCase()+text.slice(1) : '';
};
export const rungName = rung => RUNG_LABELS[rung] || 'Unassigned';
export function selectedProductionLabel(record, mineral) {
  if (record.maturity_rung !== 5 || !Object.hasOwn(MINERALS, mineral)) return '';
  const name = mineral === 'REE' ? 'rare earths' : MINERALS[mineral].toLowerCase();
  return record.production_families?.includes(mineral)
    ? `Documented production of ${name}`
    : `Production of ${name} unconfirmed`;
}

export function canPlot(record) {
  return Number.isFinite(record.lat) && Number.isFinite(record.lon)
    && Math.abs(record.lat) <= 90 && Math.abs(record.lon) <= 180
    && !(record.lat === 0 && record.lon === 0)
    && !['none', 'country centroid', 'country_centroid'].includes(record.location_precision);
}
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
  if (!Number.isFinite(record.lat) || !Number.isFinite(record.lon)) return false;
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
    if (filters.country && !(record.countries?.length ? record.countries : [record.country || 'unknown']).includes(filters.country)) return false;
    if (filters.exclude) {
      const countries = (record.countries || [record.country]).filter(Boolean);
      if (!countries.length || countries.includes(filters.exclude)) return false;
    }
    if (filters.minimum && !(Number.isInteger(record.maturity_rung) && record.maturity_rung >= Number(filters.minimum))) return false;
    if (filters.source && !(record.source_ids || [record.source_id]).includes(filters.source)) return false;
    if (filters.rung && String(record.maturity_rung ?? 'unknown') !== filters.rung) return false;
    if (filters.status && record.status !== filters.status) return false;
    if (filters.kind && record.kind !== filters.kind) return false;
    if ((bounds && !canPlot(record)) || !withinBounds(record, bounds)) return false;
    if (!words.length) return true;
    const text = normalize([record.name, record.country_name, record.country,
      record.source_title, record.record_id, ...(record.countries || []), ...(record.aliases || []),
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
    exclude: /^[A-Z]{3}$/.test(query.get('exclude') || '') ? query.get('exclude') : '',
    minimum: /^[1-5]$/.test(query.get('minimum') || '') ? query.get('minimum') : '',
    rung: /^(?:[1-5]|unknown)$/.test(query.get('rung') || '') ? query.get('rung') : '',
    status: query.get('status') || '', kind: query.get('kind') || '',
    coverage: query.get('coverage') === '1', context: query.get('context') === '1', visible: query.get('visible') === '1', selected: query.get('record') || '',
    center: center.length === 2 && center.every(Number.isFinite)
      && Math.abs(center[0]) <= 180 && Math.abs(center[1]) <= 85 ? center : null,
    zoom: query.has('zoom') && Number.isFinite(zoom) && zoom >= -1 && zoom <= 18 ? zoom : null,
  };
}

export function serializeView(filters, view = {}) {
  const query = new URLSearchParams();
  for (const key of ['q', 'mineral', 'country', 'source', 'rung', 'status', 'kind', 'exclude', 'minimum']) {
    if (filters[key]) query.set(key, filters[key]);
  }
  if (filters.context) query.set('context', '1');
  if (filters.coverage) query.set('coverage','1');
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

export function recordsCsv(records, reviewed = false) {
  const fields = reviewed ? ['record_id', 'name', 'countries', 'minerals', 'kind', 'maturity_rung', 'maturity_reason', 'status', 'status_as_of', 'reporting_system', 'operator', 'production_families', 'review_state', 'source_ids', 'lat', 'lon', 'location_precision', 'href'] : ['record_id', 'source_id', 'name', 'country', 'minerals', 'entity_level',
    'lat', 'lon', 'location_precision', 'status', 'source_url', 'source_date', 'source_license'];
  const cell = value => {
    let text = Array.isArray(value) ? value.join('; ') : String(value ?? '');
    if ((typeof value === 'string' || Array.isArray(value)) && /^[\s]*[=+@-]/.test(text)) text = "'" + text;
    return '"' + text.replaceAll('"', '""') + '"';
  };
  return '\uFEFF' + [fields.map(cell).join(','), ...records.map(record =>
    fields.map(field => cell(record[field])).join(','))].join('\r\n') + '\r\n';
}
