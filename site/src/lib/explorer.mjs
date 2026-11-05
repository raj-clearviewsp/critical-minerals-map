import 'maplibre-gl/dist/maplibre-gl.css';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';
import { MINERALS, SCOPE_NAMES, CONTEXT_LEVELS, rungName, selectedProductionLabel, reviewName, statusName, canPlot, filterRecords, parseView, serializeView, safeSourceUrl, recordsCsv } from './preview-model.mjs';

const dataset = JSON.parse(document.getElementById('atlas-preview-data').textContent);
const reviewed = dataset.mode === 'sites';
const noun = reviewed ? 'site entries' : 'source records';
const rungColors = ['#79776b','#bdc9b7','#98b4a2','#659284','#3d7062','#174e43'];
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
let overviewMode = true;
let mapStartPending = false;
const pageSize = 40;
let savedView = { center: filters.center, zoom: filters.zoom };

function hydrateFilters() {
  for (const id of ['mineral', 'country', 'source','exclude','status','kind']) {
    const control = el(id); if (!control) continue;
    if (![...control.options].some(option => option.value === filters[id])) filters[id] = '';
    control.value = filters[id];
  }
  if (el('maturity')) el('maturity').value = filters.minimum ? 'minimum-'+filters.minimum : filters.rung ? 'rung-'+filters.rung : '';
  if (!byId.has(filters.selected)) filters.selected = '';
  if (filters.selected && CONTEXT_LEVELS.has(byId.get(filters.selected).entity_level)) filters.context = true;
  el('search').value = filters.q;
  if(el('context')) el('context').checked = filters.context; else filters.context = false;
  el('visible-only').checked = filters.visible;
  if (el('coverage')) el('coverage').checked = filters.coverage; else filters.coverage = false;
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

function resultCell(label, text) {
  const cell=element('td');
  const heading=element('span',label,'mobile-cell-label');
  heading.setAttribute('aria-hidden','true'); cell.append(heading,document.createTextNode(text ?? ''));
  return cell;
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
    const button = element(reviewed ? 'a' : 'button', record.name, 'record-link');
    if (reviewed) button.href = record.href;
    if (!reviewed) button.type = 'button';
    button.setAttribute('aria-label', `View ${record.name}, ${record.source_title}`);
    if (!reviewed) button.addEventListener('click', () => selectRecord(record.id, true, true));
    nameCell.append(button, element('span', reviewed ? (reviewName(record.review_state) || record.kind) : SCOPE_NAMES[record.entity_level] || 'Source scope unconfirmed', 'record-scope'));
    if (reviewed && record.coincident_named_entries > 1) nameCell.append(element('span',record.source_title,'record-scope'));
    const stageCell = resultCell(reviewed ? 'Maturity' : 'Source',
      reviewed ? rungName(record.maturity_rung) : record.source_title);
    const production = reviewed && selectedProductionLabel(record, filters.mineral);
    if (production) stageCell.append(element('span', production, 'record-scope'));
    row.append(nameCell, resultCell('Minerals', record.minerals.map(code => code === 'REE' ? 'Rare earths' : MINERALS[code]).join(', ')),
      resultCell('Country',record.country_name || record.country || 'Not stated'), stageCell);
    if (reviewed) {
      const cell=resultCell('Reported operator',record.operator || 'Unconfirmed');
      if (record.operator && record.operator_reference) {
        const citation=element('a','Source evidence','operator-source');
        citation.href=record.href+'#operator-evidence'; cell.append(citation);
      }
      row.append(cell);
    }
    return row;
  });
  el('record-rows').replaceChildren(...rows);
  el('no-results').hidden = listed.length !== 0;
  el('list-summary').textContent = listed.length
    ? `${count(start + 1)}–${count(Math.min(start + pageSize, listed.length))} of ${count(listed.length)} ${noun}${el('visible-only').checked ? ' in the map area' : ''}`
    : 'No matching ' + noun;
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
    panel.hidden = true; panel.replaceChildren(); return;
  }
  panel.hidden = false;
  const source = sources.get(record.source_id);
  const heading = element('div', undefined, 'detail-heading');
  const close = element('button', 'Close', 'text-button');
  close.type = 'button';
  close.addEventListener('click', () => { filters.selected = ''; renderDetail(); renderList(); updateSelection(); writeUrl(); });
  heading.append(close);
  const title = element('h2', record.name);
  title.tabIndex = -1;
  const subtitle = element('p', record.minerals.map(code => MINERALS[code]).join(' · '), 'detail-subtitle');
  const facts = element('dl', undefined, 'record-facts');
  detailFact(facts, 'Country', record.country_name || record.country);
  if (reviewed) {
    detailFact(facts, 'Maturity', rungName(record.maturity_rung));
    if (record.maturity_reason) detailFact(facts, 'Maturity evidence', record.maturity_reason);
    detailFact(facts, 'Reporting system', record.reporting_system || 'Unassigned');
    detailFact(facts, 'Identity review', reviewName(record.review_state) || 'Unreported');
    detailFact(facts, 'Reported operator', record.operator || 'Not established');
    if (record.operator_reference) {
      const link=element('a','Operator evidence'); link.href=record.href+'#operator-evidence'; facts.append(link);
    }
  }
  detailFact(facts, 'Record scope', SCOPE_NAMES[record.entity_level] || 'Source scope unconfirmed');
  detailFact(facts, 'Reported status', record.status ? (reviewed ? statusName(record.status) : record.status) + (record.status_as_of ? ' · '+record.status_as_of : '') : 'Unreported');
  if (!reviewed) detailFact(facts, 'Source date', record.source_date || 'Not stated by source');
  detailFact(facts, 'Coordinate precision', record.location_precision || 'Unknown');
  detailFact(facts, 'Coordinates', canPlot(record) ? `${record.lat}, ${record.lon}` : 'No map point');
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
  panel.replaceChildren(heading, title, subtitle);
  if (record.href) {
    const link = element('a', 'Open site profile', 'profile-link'); link.href = record.href; panel.append(link);
  }
  if (canPlot(record)) panel.append(locationButton);
  panel.append(facts);
  if (!reviewed) panel.append(citation);
}

function zoomToRecord(record) {
  if (!canPlot(record)) return;
  if (!mapReady) { savedView = {center:[record.lon, record.lat],zoom:7}; startMap(); return; }
  map.flyTo({ center: [record.lon, record.lat], zoom: Math.max(map.getZoom(), 7), duration: 600 });
}

function selectRecord(id, zoom = true, reveal = false) {
  if (!filtered.some(record=>record.id === id)) return;
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
  return { type: 'FeatureCollection', features: items.filter(canPlot).map(record => ({
    type: 'Feature', geometry: { type: 'Point', coordinates: [record.lon, record.lat] },
    properties: { id: record.id, rung: record.maturity_rung || 0, facility: reviewed && ['mine','facility'].includes(record.kind), mineral: filters.mineral || ['REE', 'Ni', 'Co'].find(code => record.minerals.includes(code)) },
  })) };
}

function updateSelection() {
  if (!mapReady) return;
  const selected = byId.get(filters.selected);
  map.getSource('selection').setData(pointCollection(selected ? [selected] : []));
}

function fitResults() {
  const located = filtered.filter(canPlot);
  if (!mapReady || !located.length) return;
  if (located.length === 1) { zoomToRecord(located[0]); return; }
  const longitudes = located.map(record => record.lon);
  const latitudes = located.map(record => record.lat);
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
  el('result-summary').textContent = count(filtered.length) + ' ' + noun + ' · ' + count(filtered.filter(canPlot).length) + ' mapped';
  el('fit-map').disabled = mapStartPending || !filtered.some(canPlot);
  const extra = ['source','exclude','status','kind','context'].filter(key=>filters[key]).length;
  const primary = ['mineral','country','minimum','rung'].filter(key=>filters[key]).length;
  const shown = extra + (compactFilters.matches ? primary : 0);
  el('filter-count').textContent = shown ? ' ('+shown+')' : '';
  const active = [...(filters.q ? ['“'+filters.q+'”'] : []), MINERALS[filters.mineral],
    filters.country && el('country').selectedOptions[0]?.textContent,
    filters.exclude && 'Outside '+el('exclude').selectedOptions[0]?.textContent,
    filters.minimum && rungName(filters.minimum)+' or better', filters.rung && rungName(filters.rung),
    filters.source && sources.get(filters.source)?.title, statusName(filters.status), filters.kind,
    filters.context && 'Broader areas included'].filter(Boolean);
  el('active-filters').hidden = !active.length;
  el('active-criteria').textContent = active.join(' · ');
  const example = document.querySelector('.example-query');
  if (example) example.hidden = !filters.q && primary+extra===3 && filters.mineral==='REE' && filters.exclude==='CHN' && filters.minimum==='3';
  renderList(); renderDetail();
  if (overviewMode) renderOverview();
  if (mapReady) { map.getSource('records').setData(pointCollection(filtered)); updateSelection(); updateCoverage(); if (fit) fitResults(); }
  writeUrl();
}

el('filters').addEventListener('submit', event => event.preventDefault());
for (const id of ['mineral', 'country', 'source','exclude','status','kind']) el(id)?.addEventListener('change', () => {
  filters[id] = el(id).value; page = 0; applyFilters(true);
});
el('maturity')?.addEventListener('change',()=>{
  const [mode,value]=el('maturity').value.split('-');
  filters.minimum=mode==='minimum'?value:'';filters.rung=mode==='rung'?value:'';
  page=0;applyFilters(true);
});
el('context')?.addEventListener('change', () => { filters.context = el('context').checked; page = 0; applyFilters(true); });
let searchTimer;
el('search').addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { filters.q = el('search').value.trim(); page = 0; applyFilters(true); }, 180);
});
el('clear-filters').addEventListener('click', () => {
  clearTimeout(searchTimer); filters = parseView(''); page = 0;
  el('visible-only').checked = false; hydrateFilters(); applyFilters(true);
});
el('coverage')?.addEventListener('change', () => {
  filters.coverage=el('coverage').checked; if (filters.coverage && !mapReady) startMap(); else updateCoverage(); writeUrl();
});
el('visible-only').addEventListener('change', () => { filters.visible = el('visible-only').checked; if (filters.visible && !mapReady) startMap(); page = 0; renderList(); writeUrl(); });
el('previous-page').addEventListener('click', () => { page--; renderList(); });
el('next-page').addEventListener('click', () => { page++; renderList(); });
el('fit-map').addEventListener('click', ()=>mapReady ? fitResults() : startMap());
el('export-csv').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob([recordsCsv(listed, reviewed)], { type: 'text/csv;charset=utf-8' }));
  const link = element('a'); link.href = url; link.download = reviewed ? 'critical-mineral-sites.csv' : 'critical-minerals-source-records.csv';
  document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
window.addEventListener('popstate', () => {
  filters = parseView(location.search); savedView = { center: filters.center, zoom: filters.zoom };
  page = 0; hydrateFilters(); applyFilters();
  if (mapReady && filters.center) map.jumpTo({ center: filters.center, zoom: filters.zoom ?? map.getZoom() });
});
const compactFilters=matchMedia('(max-width: 860px)');
function layoutFilters() {
  const panel=el('filter-panel'), primary=el('primary-filters');
  panel.open=false;
  el('filter-label').textContent = compactFilters.matches ? 'Filters' : 'More filters';
  if (compactFilters.matches) panel.insertBefore(primary,el('secondary-filters'));
  else el('filters').insertBefore(primary,panel);
}
compactFilters.addEventListener('change',()=>{ layoutFilters(); applyFilters(); }); layoutFilters();
hydrateFilters(); applyFilters();

function renderOverview() {
  el('map').querySelector('.overview-points')?.remove();
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox','0 0 960 480'); svg.setAttribute('class','overview-points');
  svg.setAttribute('aria-hidden','true');
  const bins = new Map();
  for (const r of filtered) {
    if (!canPlot(r)) continue;
    const x = (r.lon+180)*960/360, y = (90-r.lat)*480/180;
    const key = Math.round(x/6)+','+Math.round(y/6);
    const bin = bins.get(key) || {x:0,y:0,n:0,record:r};
    bin.x += x; bin.y += y; bin.n++; bins.set(key,bin);
  }
  for (const {x,y,n,record} of bins.values()) {
    const cx=x/n, cy=y/n;
    const diamond = n===1 && reviewed && ['mine','facility'].includes(record.kind);
    const dot = document.createElementNS(ns,diamond?'rect':'circle');
    if (diamond) {
      dot.setAttribute('x',String(cx-2.3)); dot.setAttribute('y',String(cy-2.3));
      dot.setAttribute('width','4.6'); dot.setAttribute('height','4.6');
      dot.setAttribute('transform','rotate(45 '+cx+' '+cy+')');
    } else {
      dot.setAttribute('cx',String(cx)); dot.setAttribute('cy',String(cy));
      dot.setAttribute('r',String(Math.min(9, 2.2 + Math.log2(n))));
    }
    dot.setAttribute('fill',n>1?'#4c6b5c':reviewed?rungColors[record.maturity_rung || 0]:
      ({REE:'#945e37', Ni:'#3f7565', Co:'#526e9b'}[filters.mineral || record.minerals[0]] || '#4c6b5c'));
    dot.setAttribute('stroke',reviewed && n===1?'#40584b':'#f7f8f1'); dot.setAttribute('stroke-width','.7');
    const title=document.createElementNS(ns,'title');
    title.textContent=n>1 ? n+' nearby '+noun : record.name; dot.append(title);
    dot.addEventListener('click',()=>{
      if(n===1) selectRecord(record.id,false,true);
      else { savedView={center:[(x/n)*360/960-180,90-(y/n)*180/480],zoom:5}; startMap(); }
    });
    svg.append(dot);
  }
  el('map').prepend(svg); if(el('map-loading')) el('map-loading').hidden = true;
}

let tileProtocol;
let coverageTask;
async function updateCoverage() {
  const message=el('coverage-status');
  if (!message || !dataset.tile_path) return;
  if (!filters.coverage) {
    message.hidden=true;
    for (const layer of ['sites','facilities']) if (map?.getLayer('coverage-'+layer)) map.setLayoutProperty('coverage-'+layer,'visibility','none');
    return;
  }
  message.hidden=false;
  if (!mapReady) { message.textContent='The bulk-download outline requires the interactive map.'; return; }
  try {
    if (!coverageTask) coverageTask = (async()=>{
      const [{Protocol}, maplibregl]=await Promise.all([import('pmtiles'),import('maplibre-gl')]);
      tileProtocol=new Protocol();
      maplibregl.addProtocol('pmtiles',tileProtocol.tile);
      const tileUrl=new URL('/'+dataset.tile_path,location.origin);
      map.addSource('download-map',{type:'vector',url:'pmtiles://'+tileUrl.href});
      for (const layer of ['sites','facilities']) map.addLayer({id:'coverage-'+layer,type:'circle',source:'download-map','source-layer':layer,minzoom:5,
        layout:{visibility:'none'},paint:{'circle-radius':10,'circle-color':'#ffffff','circle-opacity':0,'circle-stroke-color':'#242a26','circle-stroke-width':1.5}});
    })();
    await coverageTask;
    if (!filters.coverage) return;
    const ids=filtered.map(r=>r.id);
    for (const layer of ['sites','facilities']) {
      map.setFilter('coverage-'+layer,ids.length?['in',['coalesce',['get','site_id'],['get','id']],['literal',ids]]:['==',1,0]);
      map.setLayoutProperty('coverage-'+layer,'visibility','visible');
    }
    message.textContent='Zoom in to see outlines around features included in the downloadable map.';
  } catch {
    message.textContent='The downloadable map overlay is unavailable. The site index is still displayed.';
  }
}
async function startMap() {
  if (mapReady || mapStartPending) return;
  mapStartPending=true; el('fit-map').disabled=true; el('fit-map').textContent='Loading map…';
  try {
    const maplibregl = await import('maplibre-gl');
    maplibregl.setWorkerUrl(workerUrl);
    const selection = byId.get(filters.selected);
    const selected = selection && canPlot(selection) ? selection : null;
    overviewMode=false; el('map').replaceChildren();
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
    const observer = new ResizeObserver(() => map.resize()); observer.observe(el('map'));
    map.on('load', () => {
      map.addSource('records', { type: 'geojson', data: pointCollection(filtered), cluster: true, clusterMaxZoom: 12, clusterRadius: 36 });
      map.addLayer({ id: 'clusters', type: 'circle', source: 'records', filter: ['has', 'point_count'], paint: {
        'circle-color': '#4c6b5c', 'circle-opacity': 0.82, 'circle-stroke-color': '#f7f8f1', 'circle-stroke-width': 2,
        'circle-radius': ['step', ['get', 'point_count'], 10, 20, 15, 100, 21, 500, 27],
      } });
      map.addLayer({ id: 'points', type: 'circle', source: 'records', filter: ['all', ['!', ['has', 'point_count']], ['!=', ['get','facility'],true]], paint: {
        'circle-color': reviewed ? ['match', ['get','rung'],1,rungColors[1],2,rungColors[2],3,rungColors[3],4,rungColors[4],5,rungColors[5],rungColors[0]] : ['match', ['get', 'mineral'], 'REE', '#945e37', 'Ni', '#3f7565', 'Co', '#526e9b', '#4c6b5c'],
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 1, 4, 8, 6],
        'circle-stroke-color': reviewed ? '#40584b' : '#ffffff', 'circle-stroke-width': 1.25, 'circle-opacity': 0.95,
      } });
      if (reviewed) {
        for (let rung = 0; rung < rungColors.length; rung++) {
          const canvas = document.createElement('canvas'); canvas.width = canvas.height = 28;
          const ctx = canvas.getContext('2d'); ctx.fillStyle = rungColors[rung]; ctx.strokeStyle = '#233f35';
          ctx.lineWidth = 2; ctx.beginPath(); ctx.moveTo(14, 1); ctx.lineTo(27, 14);
          ctx.lineTo(14, 27); ctx.lineTo(1, 14); ctx.closePath(); ctx.fill(); ctx.stroke();
          map.addImage('facility-' + rung, ctx.getImageData(0,0,28,28), {pixelRatio:2});
        }
        map.addLayer({ id:'facilities', type:'symbol', source:'records',
          filter:['all',['!', ['has','point_count']], ['==',['get','facility'],true]],
          layout:{'icon-image':['concat','facility-',['to-string',['get','rung']]],'icon-allow-overlap':true} });
        map.on('click','facilities',event => { const id = event.features?.[0]?.properties?.id; if (byId.has(id)) selectRecord(id,false); });
      }
      map.addSource('selection', { type: 'geojson', data: pointCollection([]) });
      map.addLayer({ id: 'selected-point', type: 'circle', source: 'selection', paint: {
        'circle-radius': 12, 'circle-color': '#ffffff', 'circle-opacity': 0.12,
        'circle-stroke-color': '#1a3429', 'circle-stroke-width': 3,
      } });
      mapReady = true; mapStartPending=false; el('fit-map').textContent='Fit results'; updateCoverage(); el('fit-map').disabled = !filtered.some(canPlot); el('map-loading')?.remove(); updateSelection();
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
      for (const layer of (reviewed ? ['points', 'clusters','facilities'] : ['points','clusters'])) {
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
    mapStartPending=false; overviewMode=true; map?.remove(); map=null; el('map').replaceChildren(); el('map').classList.add('map-unavailable'); renderOverview(); el('fit-map').textContent='World overview';
    const notice = el('map-loading') || element('p');
    notice.textContent = 'World overview. Choose a name in the list to inspect its evidence.';
    notice.id='map-loading'; notice.hidden=false; if (!notice.parentNode) el('map').append(notice);
    el('visible-only').checked = false; el('visible-only').disabled = true;
    el('fit-map').disabled = true; renderList();
  }
}
if (filters.center || filters.zoom !== null || filters.visible || filters.coverage) startMap();
