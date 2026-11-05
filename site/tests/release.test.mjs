import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, writeFile, readFile, symlink, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { loadRelease, validateRelease, releaseExplorer, recordExplorer, safeDownloadPath } from '../src/lib/release-data.mjs';
import { filterRecords, canPlot, recordsCsv, parseView, serializeView, selectedProductionLabel } from '../src/lib/preview-model.mjs';
import { copyDownloads } from '../scripts/copy-downloads.mjs';

function fixture() {
  return {schema_version:'atlas-private-release-v1',release_tag:'2026.11.04-fixture',data_as_of:'2026-11-04',
    sources:[{id:'source-a',title:'Fixture source'},{id:'source-b',title:'Second source'}],
    countries:[{code:'CAN',name:'Canada'},{code:'USA',name:'United States'}],
    minerals:[{code:'Ni',name:'Nickel'}],downloads:[],
    sites:[{id:'test-site-a',name:'Fixture ridge',country_iso3s:['CAN','USA'],minerals:['Ni'],lat:45,lon:-75,
      source_ids:['source-a','source-b'],kind:'deposit',maturity_rung:2,maturity_reason:'Historic statement',status:'Idle',
      evidence:[{source_id:'source-b',record_id:'original:1'}]}]};
}

test('standard build never opens an explicitly supplied private release',()=>{
  assert.equal(loadRelease({enabled:false,path:'/private/unreadable/atlas.json'}),null);
});
test('malformed known relationships fail before private pages are generated',()=>{
  for (const changes of [{id:'../escape'},{maturity_rung:6},{lat:null},{country_iso3s:['XXX']},{source_ids:['missing']},{evidence:[{source_id:'missing'}]}]) {
    const value=fixture(); Object.assign(value.sites[0],changes);
    assert.throws(()=>validateRelease(value));
  }
  const value=fixture(); value.sites.push({...value.sites[0]});
  assert.throws(()=>validateRelease(value),/duplicate/);
});
test('multiple source and country associations, maturity, status and kind retain URL state',()=>{
  const value=validateRelease(fixture());
  const {records}=releaseExplorer(value);
  const filters={country:'USA',source:'source-b',rung:'2',status:'Idle',kind:'deposit',mineral:'Ni'};
  assert.equal(filterRecords(records,parseView(serializeView(filters))).length,1);
  assert.equal(filterRecords(records,{...filters,rung:'5'}).length,0);
  assert.match(recordsCsv(records,true),/maturity_reason/);
  const archive={records:[{record_id:'original:1'},{record_id:'original:2'}]};
  assert.deepEqual(recordExplorer(archive,value).records.map(r=>r.href),['/sites/test-site-a/',null]);
});
test('unlocated and country-centroid sites stay in lists and out of map bounds',()=>{
  for (const r of [{lat:null,lon:null},{lat:40,lon:3,location_precision:'country centroid'}]) {
    assert.equal(canPlot(r),false);
    assert.equal(filterRecords([r],{}).length,1);
    assert.equal(filterRecords([r],{},[-180,-90,180,90]).length,0);
  }
});
test('only declared, checksum-matching downloads under the product directory can enter a build',async()=>{
  const dir=await mkdtemp(join(tmpdir(),'atlas-download-test-'));
  try {
    const inside=join(dir,'products','downloads'), out=join(dir,'built');
    await mkdir(inside,{recursive:true});
    const bytes='id,name\n1,test\n', sha256=createHash('sha256').update(bytes).digest('hex');
    await writeFile(join(inside,'sites.csv'),bytes);
    const manifest=join(dir,'products','atlas.json');
    const release=fixture(); release.downloads=[{path:'downloads/sites.csv',label:'Sites',table:'site',format:'CSV',sha256}];
    await copyDownloads(validateRelease(release),manifest,out);
    assert.equal(await readFile(join(out,'downloads','sites.csv'),'utf8'),bytes);
    for (const path of ['../secrets.csv','downloads/../raw.csv','downloads/%2e%2e/raw.csv','/downloads/site.csv']) assert.equal(safeDownloadPath(path),false);
    await writeFile(join(dir,'private.csv'),bytes);
    await symlink(join(dir,'private.csv'),join(inside,'leak.csv'));
    release.downloads[0].path='downloads/leak.csv';
    await assert.rejects(copyDownloads(release,manifest,out),/outside/);
    release.downloads[0].path='downloads/sites.csv'; release.downloads[0].sha256='0'.repeat(64);
    await assert.rejects(copyDownloads(release,manifest,out),/receipt/);
  } finally { await rm(dir,{recursive:true,force:true}); }
});
test('range requests cover PMTiles headers, archive tails and invalid offsets',async()=>{
  const {byteRange}=await import('../scripts/http-range.mjs');
  assert.deepEqual(byteRange('bytes=0-16383',90000),[0,16383]);
  assert.deepEqual(byteRange('bytes=900-',1024),[900,1023]);
  assert.deepEqual(byteRange('bytes=-12',1024),[1012,1023]);
  assert.equal(byteRange('bytes=1024-2000',1024),false);
  assert.equal(byteRange('bytes=0-1,4-5',100),false);
});
test('outside-China resource-or-better query excludes uncertain geography, low stages and non-REE entries',()=>{
  const base={minerals:['REE'],countries:['USA'],maturity_rung:3,entity_level:'site'};
  const records=[{...base,id:'keep'},{...base,id:'also-keep',maturity_rung:5},
    {...base,id:'low',maturity_rung:2},{...base,id:'unassigned',maturity_rung:null},
    {...base,id:'china',countries:['CHN','USA']},{...base,id:'unknown',countries:[]},
    {...base,id:'nickel',minerals:['Ni']}];
  const view=parseView('?mineral=REE&exclude=CHN&minimum=3');
  assert.deepEqual(filterRecords(records,view).map(r=>r.id),['keep','also-keep']);
  assert.equal(parseView(serializeView(view)).minimum,'3');
});
test('source view favors explicit membership and does not choose between shared citations',()=>{
  const archive={records:[{record_id:'member'},{record_id:'citation'}]};
  const two={sites:[{id:'first',record_ids:['member'],evidence:[{record_id:'citation'}]},
    {id:'second',record_ids:['different'],evidence:[{record_id:'citation'}]}]};
  assert.deepEqual(recordExplorer(archive,two).records.map(r=>r.href),['/sites/first/',null]);
  for(const site of two.sites) delete site.record_ids;
  assert.equal(recordExplorer(archive,two).records[1].href,null);
});

test('operating rows distinguish selected-mineral production from association', () => {
  const site = { maturity_rung: 5, minerals: ['REE','Ni','Co'], production_families: ['Ni'] };
  assert.equal(selectedProductionLabel(site, 'Ni'), 'Documented production of nickel');
  assert.equal(selectedProductionLabel(site, 'REE'), 'Production of rare earths unconfirmed');
  assert.equal(selectedProductionLabel({...site, production_families: ['Co']}, 'Co'), 'Documented production of cobalt');
  assert.equal(selectedProductionLabel({...site, production_families: undefined}, 'Co'), 'Production of cobalt unconfirmed');
  assert.equal(selectedProductionLabel(site, ''), '');
  assert.equal(selectedProductionLabel({...site, maturity_rung: 4}, 'Ni'), '');
});
