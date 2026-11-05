// The caller builds only the synthetic catalog in a disposable directory.
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { appendFile } from 'node:fs/promises';
import { join } from 'node:path';
import puppeteer from 'puppeteer-core';

const [origin, mode]=process.argv.slice(2);
assert.equal(new URL(origin).hostname,'127.0.0.1','Fixture server must be local');
function browserList() {
  if (mode==='--local-browser') {
    const path=process.env.ATLAS_TEST_BROWSER;
    assert.ok(path && existsSync(path),'ATLAS_TEST_BROWSER must identify an installed browser');
    return [['Local Chromium browser',path]];
  }
  assert.equal(mode,'--windows-browsers');
  assert.equal(process.platform,'win32','Windows acceptance needs actual Windows');
  const bases=[process.env.ProgramW6432,process.env.ProgramFiles,
    process.env['ProgramFiles(x86)'],process.env.LOCALAPPDATA].filter(Boolean);
  return [['Google Chrome','Google/Chrome/Application/chrome.exe'],
    ['Microsoft Edge','Microsoft/Edge/Application/msedge.exe']].map(([label,suffix])=>{
    const path=bases.map(base=>join(base,...suffix.split('/'))).find(p=>existsSync(p));
    assert.ok(path,`${label} executable was missing on this Windows runner`);
    return [label,path];
  });
}

async function assertPageWidth(page, expected) {
  const styles=await page.evaluate(()=>({bodyMargin:getComputedStyle(document.body).marginLeft,
    masthead:getComputedStyle(document.querySelector('.masthead')).display,
    sheets:[...document.querySelectorAll('link[rel~="stylesheet"]')].map(l=>({href:l.href,loaded:!!l.sheet})),
  }));
  assert.ok(styles.bodyMargin==='0px' && styles.masthead==='flex' && styles.sheets.length>0 && styles.sheets.every(s=>s.loaded),
    `Atlas stylesheet wasn't applied at ${page.url()}: ${JSON.stringify(styles)}`);
  const dimensions=await page.evaluate(async()=>{
    await new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done)));
    const viewport=innerWidth, width=document.documentElement.scrollWidth;
    if (width<=viewport) return {viewport,width,client:document.documentElement.clientWidth};
    const path=el=>[el.localName,el.id?`#${el.id}`:'',el.classList.length?'.'+[...el.classList].join('.'):''].join('');
    const box=el=>{
      const rect=el.getBoundingClientRect(),style=getComputedStyle(el);
      return {element:path(el),left:Math.round(rect.left),right:Math.round(rect.right),
        width:Math.round(rect.width),display:style.display,position:style.position,
        overflowX:style.overflowX,whiteSpace:style.whiteSpace};
    };
    const beyond=[...document.body.querySelectorAll('*')].filter(el=>el.getBoundingClientRect().right>viewport);
    const offenders=beyond.slice(0,12).map(el=>{
      const ancestors=[];
      for (let x=el.parentElement; x && x!==document.body && ancestors.length<6; x=x.parentElement) ancestors.push(box(x));
      return {...box(el),text:el.textContent.trim().slice(0,100),href:el.getAttribute('href'),ancestors};
    });
    const probes={};
    for (const [name,rule] of Object.entries({
      closedNavigation:'.mobile-navigation:not([open]) nav {display:none!important}',
      footerLinks:'footer a {display:none!important}',
      sourceLinks:'.print-sources a,.brief-source a {display:none!important}',
      cardLinks:'.profile-table a {display:none!important}',
      wrappedLinks:'main a,footer a {overflow-wrap:anywhere!important;white-space:normal!important}',
    })) {
      const style=document.createElement('style');style.textContent=rule;document.head.append(style);
      probes[name]=document.documentElement.scrollWidth;style.remove();
    }
    return {viewport,width,client:document.documentElement.clientWidth,offenders,probeWidths:probes};
  });
  assert.equal(dimensions.viewport,expected);
  assert.ok(dimensions.width<=expected,`Horizontal page overflow at ${page.url()}: ${JSON.stringify(dimensions)}`);
}
async function follow(page,selector) {
  await page.waitForSelector(selector,{visible:true});
  await Promise.all([page.waitForNavigation({waitUntil:'load'}),page.click(selector)]);
}
async function run(label, path) {
  const browser=await puppeteer.launch({executablePath:path,headless:true,
    args:['--no-first-run','--disable-default-apps','--disable-background-networking']});
  try {
    const version=await browser.version();
    const page=await browser.newPage();await page.setViewport({width:1280,height:800});
    page.setDefaultTimeout(25000);
    const errors=[],external=[];
    page.on('pageerror',e=>errors.push(String(e.message || e)));
    page.on('request',req=>{
      const url=new URL(req.url());
      if (['http:','https:'].includes(url.protocol) && url.origin!==new URL(origin).origin) external.push(url.origin);
    });
    await page.goto(origin,{waitUntil:'load'});
    assert.equal(await page.$eval('h1',x=>x.textContent.trim()),'Critical mineral sites');
    const profile='/sites/fixture-site-a/';
    const result=`#record-rows a[href="${profile}"]`;
    await page.waitForSelector(result);await assertPageWidth(page,1280);
    await page.select('#mineral','Ni');
    await page.waitForFunction(()=>new URLSearchParams(location.search).get('mineral')==='Ni'
      && !document.querySelector('#record-rows a'));
    await page.select('#mineral','REE');
    await page.waitForFunction(()=>new URLSearchParams(location.search).get('mineral')==='REE');
    await follow(page,result);
    assert.equal(new URL(page.url()).pathname,profile);
    assert.equal(await page.$eval('h1',x=>x.textContent.trim()),'Fixture outside-China mine');
    assert.match(await page.$eval('main',x=>x.innerText),/Fixture operator/);
    await follow(page,'.profile-locations a[href="/countries/usa/"]');
    assert.equal(await page.$eval('h1',x=>x.textContent.trim()),'United States');
    assert.match(await page.$eval('main',x=>x.innerText),/12,000/);
    await page.setViewport({width:390,height:844});await assertPageWidth(page,390);
    await page.goto(new URL('search/',origin).href,{waitUntil:'load'});
    await page.type('#atlas-query','Fixture outside-China mine');
    await follow(page,`#search-results a[href="${profile}"]`);
    await assertPageWidth(page,390);
    await page.goto(origin,{waitUntil:'load'});
    await page.waitForSelector(result);await assertPageWidth(page,390);
    assert.deepEqual(errors,[],'Uncaught app errors');
    assert.deepEqual(external,[],'Unexpected external runtime requests');
    const line=`${label} (${version}) passed the home, mineral filter, profile, search and 390-pixel layout checks.`;
    console.log(line);
    if (process.env.GITHUB_STEP_SUMMARY) await appendFile(process.env.GITHUB_STEP_SUMMARY,`- ${line}\n`);
  } finally { await browser.close(); }
}
for (const [label,path] of browserList()) await run(label,path);
