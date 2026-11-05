import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { cp, mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createInterface } from 'node:readline';

const original=resolve(dirname(fileURLToPath(import.meta.url)), '..');
const sandbox=await mkdtemp(join(tmpdir(),'atlas-site-modes-'));
const site=join(sandbox,'site'), data=join(sandbox,'data'), product=join(sandbox,'fixture-products');
const source={id:'fixture-government', title:'Synthetic source for testing', url:'https://example.invalid/source', license:'Synthetic fixture'};
const record={id:'fixture-record-a',record_id:'fixture:1',source_id:source.id,name:'Fixture input observation',country:'USA',country_name:'United States',minerals:['REE'],lat:34,lon:-117,entity_level:'site',source_title:source.title};
const archive={schema_version:'atlas-private-preview-v1',release_tag:'2999.01.01-test',data_as_of:null,
  records:[record],sources:[source],summary:{record_count:1,source_count:1}};
const siteRow={id:'fixture-site-a',name:'Fixture outside-China mine',country_iso3s:['USA'],minerals:['REE'],lat:34,lon:-117,
  location_precision:null,entity_level:'site',kind:'deposit',maturity_rung:5,maturity_reason:'Dated synthetic source',status:'Operating',
  reporting_system:null,source_ids:[source.id],aliases:[],review_state:'provisional_single',operator:'Fixture operator',production_families:['REE'],
  evidence:[{source_id:source.id,record_id:'fixture:1',grade:'B',date:'2999-01-01',url:source.url,note:'Synthetic evidence.'}],
  operator_evidence:{source_id:source.id,record_id:'fixture:1',grade:'B',date:'2999-01-01',url:source.url,note:'Synthetic operator source.'}};
const csv='site_id,name\nfixture-site-a,Fixture outside-China mine\n';
const sha256=createHash('sha256').update(csv).digest('hex');
const release={schema_version:'atlas-private-release-v1',release_tag:'2999.01.01-test',data_as_of:'2999-01-01',sites:[siteRow],sources:[source],
  countries:[{code:'USA',name:'United States',context:[{label:'Fixture production',value:'12,000',unit:'tonnes',year:2998,source_id:source.id,url:source.url,grade:'B'}]}],
  minerals:[{code:'REE',name:'Rare earth elements',context:[]}],downloads:[{path:'downloads/sites.csv',format:'CSV',label:'Fixture site table',table:'sites',rows:1,sha256}]};
function build(command, path) {
  const env={...process.env}; delete env.ATLAS_RECORDS_PATH; delete env.ATLAS_RELEASE_PATH;
  if (path) env.ATLAS_RELEASE_PATH=path;
  const run=spawnSync(process.platform==='win32'?'npm.cmd':'npm',['run',command],{cwd:site,env,encoding:'utf8'});
  if (run.status!==0) throw new Error(run.stdout+'\n'+run.stderr);
}
async function html(name, privateBuild=true) {
  return readFile(join(site,privateBuild?'.preview-dist':'dist',name,'index.html'),'utf8');
}
async function startLauncher(args=[]) {
  const child=spawn(process.execPath,['scripts/open-preview.mjs','--no-open',...args],{
    cwd:site,stdio:['ignore','pipe','pipe'],
  });
  const exited=new Promise(resolve=>child.once('exit',resolve));
  let errors='';
  child.stderr.on('data',chunk=>{errors+=chunk;});
  const reader=createInterface({input:child.stdout});
  const timeout=setTimeout(()=>child.kill('SIGTERM'),10000);
  try {
    for await (const line of reader) {
      const match=line.match(/http:\/\/127\.0\.0\.1:\d+\//);
      if (match) return {url:match[0],async stop(){
        if (child.exitCode===null && child.signalCode===null) child.kill('SIGTERM');
        await exited;
      }};
    }
    throw new Error('Loopback launcher failed: '+errors);
  } finally { clearTimeout(timeout); reader.close(); }
}
async function checkBrowsers(manifest, mode) {
  // Invoke the same Astro build entry point directly so Windows needs no POSIX env syntax.
  const env={...process.env, ATLAS_PRIVATE_PREVIEW:'1', ATLAS_RELEASE_PATH:manifest,
    ASTRO_TELEMETRY_DISABLED:'1'};
  delete env.ATLAS_RECORDS_PATH;
  const build=spawnSync(process.execPath,[join(site,'node_modules','astro','bin','astro.mjs'),'build'],
    {cwd:site,env,stdio:'inherit',timeout:120000});
  if (build.status!==0) throw build.error || new Error('Synthetic Astro build failed');
  assert.match(await html('sites/fixture-site-a'),/Fixture operator/);
  assert.equal(await readFile(join(site,'.preview-dist','downloads','sites.csv'),'utf8'),csv);
  const host=await startLauncher();
  try {
    const smoke=join(original,'../tools/browser-qa/windows-smoke.mjs');
    const run=spawnSync(process.execPath,[smoke,host.url,mode],
      {cwd:original,env,stdio:'inherit',timeout:180000});
    if (run.status!==0) throw run.error || new Error('Synthetic browser acceptance failed');
  } finally { await host.stop(); }
}
async function checkTransport() {
  // Probe archive transport with synthetic bytes in this throwaway build only.
  const bytes=Buffer.from('fixture-archive-byte-range');
  await writeFile(join(site,'.preview-dist','probe.pmtiles'),bytes);
  let host=await startLauncher();
  try {
    const port=new URL(host.url).port;
    const busy=spawnSync(process.execPath,['scripts/open-preview.mjs','--no-open','--port',port],
      {cwd:site,encoding:'utf8',timeout:5000});
    assert.equal(busy.status,1); assert.match(busy.stderr,/Could not start the Atlas.*EADDRINUSE/);
    await host.stop();
    host=await startLauncher(['--port',port]);
    assert.equal(new URL(host.url).port,port);
    const resource=host.url+'probe.pmtiles';
    const response=await fetch(resource,{headers:{Range:'bytes=2-8'}});
    assert.equal(response.status,206);
    assert.equal(response.headers.get('content-type'),'application/vnd.pmtiles');
    assert.equal(response.headers.get('content-range'),'bytes 2-8/'+bytes.length);
    assert.deepEqual(Buffer.from(await response.arrayBuffer()),bytes.subarray(2,9));
    const head=await fetch(resource,{method:'HEAD',headers:{Range:'bytes=2-8'}});
    assert.equal(head.status,206); assert.equal(head.headers.get('content-length'),'7');
    assert.equal((await head.arrayBuffer()).byteLength,0);
    const full=await fetch(resource,{method:'HEAD'});
    assert.equal(full.status,200); assert.equal(full.headers.get('content-length'),String(bytes.length));
    assert.equal((await fetch(resource,{headers:{Range:'bytes=9999-'}})).status,416);
    assert.equal((await fetch(host.url+'sites/fixture-site-a/')).status,200);
    for(const invalid of ['0','65536','bad']) {
      const attempt=spawnSync(process.execPath,['scripts/open-preview.mjs','--no-open','--port',invalid],{cwd:site,encoding:'utf8',timeout:5000});
      assert.equal(attempt.status,1); assert.match(attempt.stderr,/1 through 65535/);
    }
  } finally { await host.stop(); }
}
try {
  await mkdir(site); await mkdir(join(data,'preview','site-v1'),{recursive:true});
  await mkdir(join(product,'downloads'),{recursive:true});
  for (const name of ['src','public','scripts','package.json','astro.config.mjs','tsconfig.json']) {
    await cp(join(original,name),join(site,name),{recursive:true});
  }
  await symlink(join(original,'node_modules'),join(site,'node_modules'),process.platform==='win32'?'junction':'dir');
  await writeFile(join(data,'preview','site-v1','dataset.json'),JSON.stringify(archive));
  await writeFile(join(product,'downloads','sites.csv'),csv);
  const manifest=join(product,'atlas.json'); await writeFile(manifest,JSON.stringify(release));
  const browserMode=process.argv.slice(2).find(a=>a==='--windows-browsers' || a==='--local-browser');
  if (browserMode) {
    await checkBrowsers(manifest,browserMode);
  } else {
    build('build',manifest);
    assert.doesNotMatch(await html('',false),/Fixture input observation|Fixture outside-China mine/);
    await assert.rejects(html('sites/fixture-site-a',false),{code:'ENOENT'});
    build('build:private');
    assert.match(await html(''),/Fixture input observation/);
    assert.doesNotMatch(await html(''),/Fixture outside-China mine/);
    build('build:private',manifest);
    assert.match(await html('sites/fixture-site-a'),/Fixture operator/);
    assert.match(await html('countries/usa'),/12,000/);
    assert.match(await html('records'),/\/sites\/fixture-site-a\//);
    assert.equal(await readFile(join(site,'.preview-dist','downloads','sites.csv'),'utf8'),csv);
    if (process.argv.includes('--transport')) await checkTransport();
    console.log('Verified empty standard build, source-only private build, reviewed private routes, and declared download.'+
      (process.argv.includes('--transport')?' Verified free/fixed ports, visible bind errors, PMTiles 206, HEAD, MIME and 416.':''));
  }
} finally { await rm(sandbox,{recursive:true,force:true}); }
