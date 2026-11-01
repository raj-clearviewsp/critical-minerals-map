import 'maplibre-gl/dist/maplibre-gl.css';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';
import { MINERALS, SCOPE_NAMES, CONTEXT_LEVELS, filterRecords, parseView, serializeView, safeSourceUrl, recordsCsv } from './preview-model.mjs';

const dataset = JSON.parse(document.getElementById('atlas-preview-data').textContent);
const records = dataset.records.toSorted((a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id));
const byId = new Map(records.map(record => [record.id, record]));
const sources = new Map(dataset.sources.map(source => [source.id, source]));
const el = id => document.getElementById(id);
const count = number => number.toLocaleString('en-US');
const element = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
};
let filters = parseView(location.search);
let page = 0;
let filtered = [];
let listed = [];
let map;
let mapReady = false;
const pageSize = 40;
let savedView = { center: filters.center, zoom: filters.zoom };

function hydrateFilters() {
  for (const id of ['mineral', 'country', 'source']) {
    const control = el(id);
    if (![...control.options].some(option => option.value === filters[id])) filters[id] = '';
    control.value = filters[id];
  }
  if (!byId.has(filters.selected)) filters.selected = '';
  if (filters.selected && CONTEXT_LEVELS.has(byId.get(filters.selected).entity_level)) filters.context = true;
  el('search').value = filters.q;
  el('context').checked = filters.context;
  el('visible-only').checked = filters.visible;
}

function writeUrl() {
  const query = serializeView(filters, savedView);
  history.replaceState(null, '', `${location.pathname}${query ? '?' + query : ''}`);
}

function mapBounds() {
  if (!mapReady || !el('visible-only').checked) return null;
  const bounds = map.getBounds();
  return [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()];
}

function renderList() {
  listed = filterRecords(filtered, { context: true }, mapBounds());
  const pages = Math.max(1, Math.ceil(listed.length / pageSize));
  page = Math.min(page, pages - 1);
  const start = page * pageSize;
  const rows = listed.slice(start, start + pageSize).map(record => {
    const row = element('tr');
    if (record.id === filters.selected) row.className = 'selected-row';
    const nameCell = element('td');
    const button = element('button', record.name, 'record-link');
    button.type = 'button';
    button.setAttribute('aria-label', `View ${record.name}, ${record.source_title}`);
    button.addEventListener('click', () => selectRecord(record.id, true, true));
    nameCell.append(button, element('span', SCOPE_NAMES[record.entity_level] || 'Source scope unconfirmed', 'record-scope'));
    row.append(nameCell, element('td', record.minerals.map(code => code === 'REE' ? 'Rare earths' : MINERALS[code]).join(', ')),
      element('td', record.country_name || record.country || 'Not stated'), element('td', record.source_title));
    return row;
  });
  el('record-rows').replaceChildren(...rows);
  el('no-results').hidden = listed.length !== 0;
  el('list-summary').textContent = listed.length
    ? `${count(start + 1)}–${count(Math.min(start + pageSize, listed.length))} of ${count(listed.length)} source records${el('visible-only').checked ? ' in the map area' : ''}`
    : 'No matching source records';
  el('page-status').textContent = `Page ${count(page + 1)} of ${count(pages)}`;
  el('previous-page').disabled = page === 0;
  el('next-page').disabled = page >= pages - 1;
  el('export-csv').disabled = !listed.length;
}

function detailFact(list, title, text) {
  const row = element('div');
  row.append(element('dt', title), element('dd', text || 'Not stated'));
  list.append(row);
}

function renderDetail() {
  const panel = el('record-detail');
  const record = byId.get(filters.selected);
  if (!record) {
    const placeholder = element('div', undefined, 'detail-empty');
    placeholder.append(element('p', 'Source record', 'eyebrow'), element('h2', 'Select a record'),
      element('p', 'Choose a point on the map or a name in the results to read the source evidence.'),
      element('p', 'Records may describe the same asset. Identity review is in progress; this preview keeps each source observation separate.', 'muted'));
    panel.replaceChildren(placeholder);
    return;
  }
  const source = sources.get(record.source_id);
  const heading = element('div', undefined, 'detail-heading');
  const close = element('button', 'Close', 'text-button');
  close.type = 'button';
  close.addEventListener('click', () => { filters.selected = ''; renderDetail(); renderList(); updateSelection(); writeUrl(); });
  heading.append(element('p', 'Source record', 'eyebrow'), close);
  const title = element('h2', record.name);
  title.tabIndex = -1;
  const subtitle = element('p', record.minerals.map(code => MINERALS[code]).join(' · '), 'detail-subtitle');
  const facts = element('dl', undefined, 'record-facts');
  detailFact(facts, 'Country', record.country_name || record.country);
  detailFact(facts, 'Record scope', SCOPE_NAMES[record.entity_level] || 'Source scope unconfirmed');
  detailFact(facts, 'Reported status', record.status || 'Not reported');
  detailFact(facts, 'Source date', record.source_date || 'Not stated; current status is not established');
  detailFact(facts, 'Coordinate precision', record.location_precision || 'Unknown');
  detailFact(facts, 'Source coordinates', `${record.lat}, ${record.lon}`);
  if (record.deposit_types?.length) detailFact(facts, 'Deposit description', record.deposit_types.join('; '));
  if (record.aliases?.length) detailFact(facts, 'Other source names', record.aliases.join('; '));
  if (record.context_names?.length) detailFact(facts, 'Related names in the source', record.context_names.join('; '));
  const citation = element('section', undefined, 'record-citation');
  citation.append(element('h3', 'Source evidence'), element('p', record.source_title));
  const sourceFacts = element('dl', undefined, 'record-facts');
  detailFact(sourceFacts, 'Original record ID', record.record_id);
  detailFact(sourceFacts, 'License or terms', record.source_license);
  detailFact(sourceFacts, 'Source retrieved', source?.retrieved_at);
  citation.append(sourceFacts);
  if (record.provenance) citation.append(element('p', record.provenance));
  if (record.identity_review) citation.append(element('p', record.identity_review, 'identity-note'));
  const sourceUrl = safeSourceUrl(record.source_url);
  if (sourceUrl) {
    const link = element('a', 'Open the publisher source ↗');
    link.href = sourceUrl; link.target = '_blank'; link.rel = 'noopener noreferrer';
    citation.append(link);
  }
  const locationButton = element('button', 'Show on map', 'detail-map-button');
  locationButton.type = 'button';
  locationButton.addEventListener('click', () => zoomToRecord(record));
  panel.replaceChildren(heading, title, subtitle, locationButton, facts, citation);
}

function zoomToRecord(record) {
  if (mapReady) map.flyTo({ center: [record.lon, record.lat], zoom: Math.max(map.getZoom(), 7), duration: 600 });
}

function selectRecord(id, zoom = true, reveal = false) {
  filters.selected = id;
  renderDetail(); renderList(); updateSelection(); writeUrl();
  if (zoom) zoomToRecord(byId.get(id));
  if (reveal) {
    const heading = el('record-detail').querySelector('h2');
    heading.focus({ preventScroll: true });
    heading.scrollIntoView({ block: 'nearest', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
  }
}

function pointCollection(items) {
  return { type: 'FeatureCollection', features: items.map(record => ({
    type: 'Feature', geometry: { type: 'Point', coordinates: [record.lon, record.lat] },
    properties: { id: record.id, mineral: filters.mineral || ['REE', 'Ni', 'Co'].find(code => record.minerals.includes(code)) },
  })) };
}

function updateSelection() {
  if (!mapReady) return;
  const selected = byId.get(filters.selected);
  map.getSource('selection').setData(pointCollection(selected ? [selected] : []));
}

function fitResults() {
  if (!mapReady || !filtered.length) return;
  if (filtered.length === 1) { zoomToRecord(filtered[0]); return; }
  const longitudes = filtered.map(record => record.lon);
  const latitudes = filtered.map(record => record.lat);
  const west = Math.min(...longitudes), east = Math.max(...longitudes);
  if (east - west > 300) {
    map.fitBounds([[-180, -60], [180, 80]], { padding: 24, maxZoom: 2, duration: 500 });
    return;
  }
  map.fitBounds([[west, Math.max(-80, Math.min(...latitudes))], [east, Math.min(80, Math.max(...latitudes))]],
    { padding: 44, maxZoom: 8, duration: 500 });
}

function applyFilters(fit = false) {
  filtered = filterRecords(records, filters);
  if (filters.selected && !filtered.some(record => record.id === filters.selected)) filters.selected = '';
  el('result-summary').textContent = `${count(filtered.length)} source record${filtered.length === 1 ? '' : 's'}${filters.context ? ', including broader areas' : ' · districts and complexes hidden'}`;
  el('fit-map').disabled = !filtered.length;
  renderList(); renderDetail();
  if (mapReady) { map.getSource('records').setData(pointCollection(filtered)); updateSelection(); if (fit) fitResults(); }
  writeUrl();
}

el('filters').addEventListener('submit', event => event.preventDefault());
for (const id of ['mineral', 'country', 'source']) el(id).addEventListener('change', () => {
  filters[id] = el(id).value; page = 0; applyFilters(true);
});
el('context').addEventListener('change', () => { filters.context = el('context').checked; page = 0; applyFilters(true); });
let searchTimer;
el('search').addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { filters.q = el('search').value.trim(); page = 0; applyFilters(true); }, 180);
});
el('clear-filters').addEventListener('click', () => {
  clearTimeout(searchTimer); filters = parseView(''); page = 0;
  el('visible-only').checked = false; hydrateFilters(); applyFilters(true);
});
el('visible-only').addEventListener('change', () => { filters.visible = el('visible-only').checked; page = 0; renderList(); writeUrl(); });
el('previous-page').addEventListener('click', () => { page--; renderList(); });
el('next-page').addEventListener('click', () => { page++; renderList(); });
el('fit-map').addEventListener('click', fitResults);
el('export-csv').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob([recordsCsv(listed)], { type: 'text/csv;charset=utf-8' }));
  const link = element('a'); link.href = url; link.download = 'critical-minerals-source-records.csv';
  document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
window.addEventListener('popstate', () => {
  filters = parseView(location.search); savedView = { center: filters.center, zoom: filters.zoom };
  page = 0; hydrateFilters(); applyFilters();
  if (mapReady && filters.center) map.jumpTo({ center: filters.center, zoom: filters.zoom ?? map.getZoom() });
});
hydrateFilters(); applyFilters();

async function startMap() {
  try {
    const maplibregl = await import('maplibre-gl');
    maplibregl.setWorkerUrl(workerUrl);
    const selected = byId.get(filters.selected);
    map = new maplibregl.Map({
      container: 'map', center: savedView.center || (selected ? [selected.lon, selected.lat] : [15, 12]),
      zoom: savedView.zoom ?? (selected ? 7 : 1.3), minZoom: -1, maxZoom: 16,
      attributionControl: false,
      style: { version: 8, sources: { countries: { type: 'geojson', data: '/maps/countries.geojson' } }, layers: [
        { id: 'water', type: 'background', paint: { 'background-color': '#e7eeeb' } },
        { id: 'land', type: 'fill', source: 'countries', paint: { 'fill-color': '#f6f5ed' } },
        { id: 'boundaries', type: 'line', source: 'countries', paint: { 'line-color': '#c7cfc5', 'line-width': 0.65 } },
      ] },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    map.on('load', () => {
      map.addSource('records', { type: 'geojson', data: pointCollection(filtered), cluster: true, clusterMaxZoom: 12, clusterRadius: 36 });
      map.addLayer({ id: 'clusters', type: 'circle', source: 'records', filter: ['has', 'point_count'], paint: {
        'circle-color': '#4c6b5c', 'circle-opacity': 0.82, 'circle-stroke-color': '#f7f8f1', 'circle-stroke-width': 2,
        'circle-radius': ['step', ['get', 'point_count'], 10, 20, 15, 100, 21, 500, 27],
      } });
      map.addLayer({ id: 'points', type: 'circle', source: 'records', filter: ['!', ['has', 'point_count']], paint: {
        'circle-color': ['match', ['get', 'mineral'], 'REE', '#945e37', 'Ni', '#3f7565', 'Co', '#526e9b', '#4c6b5c'],
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 1, 4, 8, 6],
        'circle-stroke-color': '#ffffff', 'circle-stroke-width': 1.25, 'circle-opacity': 0.95,
      } });
      map.addSource('selection', { type: 'geojson', data: pointCollection([]) });
      map.addLayer({ id: 'selected-point', type: 'circle', source: 'selection', paint: {
        'circle-radius': 12, 'circle-color': '#ffffff', 'circle-opacity': 0.12,
        'circle-stroke-color': '#1a3429', 'circle-stroke-width': 3,
      } });
      mapReady = true; el('map-loading')?.remove(); updateSelection();
      if (!savedView.center && !selected) {
        if (filtered.length) fitResults();
        else map.fitBounds([[-180, -60], [180, 80]], { padding: 24, maxZoom: 2, duration: 0 });
      }
      map.on('click', 'points', event => {
        const id = event.features?.[0]?.properties?.id;
        if (byId.has(id)) selectRecord(id, false);
      });
      map.on('click', 'clusters', async event => {
        const feature = event.features?.[0]; if (!feature) return;
        try {
          const zoom = await map.getSource('records').getClusterExpansionZoom(feature.properties.cluster_id);
          map.easeTo({ center: feature.geometry.coordinates, zoom, duration: 450 });
        } catch { /* The filter may have changed while the cluster was expanding. */ }
      });
      for (const layer of ['points', 'clusters']) {
        map.on('mouseenter', layer, () => { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', layer, () => { map.getCanvas().style.cursor = ''; });
      }
      map.on('moveend', () => {
        const center = map.getCenter();
        savedView = { center: [((center.lng + 180) % 360 + 360) % 360 - 180, center.lat], zoom: map.getZoom() };
        writeUrl(); if (el('visible-only').checked) { page = 0; renderList(); }
      });
      renderList();
    });
    map.on('error', () => {
      if (!mapReady && el('map-loading')) el('map-loading').textContent = 'Map background unavailable. Source records remain available in the list below.';
    });
  } catch {
    el('map').classList.add('map-unavailable');
    const notice = el('map-loading') || element('p');
    notice.textContent = 'The map is unavailable in this browser. All records remain available in the list below.';
    if (!notice.parentNode) el('map').append(notice);
    el('visible-only').checked = false; el('visible-only').disabled = true;
    el('fit-map').disabled = true; renderList();
  }
}
startMap();
