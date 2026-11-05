// Run against the built Atlas on loopback. Reports and print proofs belong outside the checkout.
// node tools/browser-qa/audit.mjs http://127.0.0.1:4387 ../../work/release-browser-audit <atlas.json>
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdir, readFile, realpath, rename, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gzipSync } from 'node:zlib';
import lighthouse from 'lighthouse';
import puppeteer from 'puppeteer-core';

const require = createRequire(import.meta.url);
const VIEWPORTS = [390, 549, 1280];
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];
const JS_LIMIT = 300_000;
const PERFORMANCE_MINIMUM = 90;
const LIGHTHOUSE_RUNS = ['home', 'home', 'home', 'site', 'mineral', 'country'];
const TIMEOUT = 60_000;

function outsideCheckout(path, root) {
  const rel = relative(root, path);
  return rel === '..' || rel.startsWith('..' + sep);
}

async function options() {
  const [base, directory, catalog] = process.argv.slice(2);
  if (!base || !directory || !catalog) {
    throw new Error('Supply a loopback base URL, private results directory, and release catalog.');
  }
  const url = new URL(base);
  if (url.hostname !== '127.0.0.1' || !['http:', 'https:'].includes(url.protocol)
      || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('Use the origin of the loopback Atlas, such as http://127.0.0.1:4387.');
  }
  const root = await realpath(fileURLToPath(new URL('../../', import.meta.url)));
  const output = resolve(directory);
  if (!outsideCheckout(output, root)) throw new Error('Choose a private output directory outside the repository.');
  await mkdir(output, { recursive: true });
  if (!outsideCheckout(await realpath(output), root)) throw new Error('Output resolves inside the repository.');
  const bytes = await readFile(catalog);
  const release = JSON.parse(bytes);
  if (release.schema_version !== 'atlas-private-release-v1' || !release.sites?.length
      || !release.minerals?.length || !release.countries?.length) throw new Error('A populated private release is required.');
  return { origin: url.origin, output, release, catalogHash: createHash('sha256').update(bytes).digest('hex') };
}

function routes(release) {
  const site = release.sites.find(s => s.name.toLowerCase() === 'mountain pass' && s.operator) || release.sites[0];
  const mineral = release.minerals.find(p => p.code === 'REE') || release.minerals[0];
  const country = release.countries.find(p => p.code === 'AUS') || release.countries[0];
  return {
    home: '/',
    site: '/sites/' + encodeURIComponent(site.id) + '/',
    mineral: '/minerals/' + encodeURIComponent(mineral.code.toLowerCase()) + '/',
    country: '/countries/' + encodeURIComponent(country.code.toLowerCase()) + '/',
    sites_directory: '/sites/',
    minerals_directory: '/minerals/',
    countries_directory: '/countries/',
    sources: '/sources/',
    methods: '/methods/',
    downloads: '/downloads/',
    search: '/search/',
    records: '/records/',
    about: '/about/',
  };
}

function externalUrl(value, origin) {
  if (!/^(?:https?|wss?):\/\//i.test(value)) return false;
  const url = new URL(value);
  url.protocol = url.protocol === 'wss:' ? 'https:' : url.protocol === 'ws:' ? 'http:' : url.protocol;
  return url.origin !== origin;
}

async function navigate(page, origin, route, waitUntil = 'networkidle0') {
  const response = await page.goto(origin + route, { waitUntil, timeout: TIMEOUT });
  if (response?.status() !== 200 || new URL(page.url()).origin !== origin) {
    throw new Error('Expected local HTTP 200 at ' + route + ', received ' + response?.status());
  }
}

async function viewportWidth(page, width) {
  await page.setViewport({ width, height: 844, deviceScaleFactor: 1 });
  return page.evaluate(async () => {
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    return {
      viewport: document.documentElement.clientWidth,
      content: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
    };
  });
}

function observeRequests(page, origin) {
  const external = new Set(), pendingScripts = [];
  page.on('request', request => {
    if (externalUrl(request.url(), origin)) external.add(request.url());
  });
  // Start reading bodies as responses arrive. Await every read before recording a byte budget.
  page.on('response', response => {
    if (response.request().resourceType() !== 'script') return;
    const url = response.url();
    const task = response.ok()
      ? response.buffer().then(bytes => ({ url, bytes: gzipSync(bytes).length }))
      : Promise.reject(new Error('Script response ' + response.status() + ': ' + url));
    pendingScripts.push(task.catch(error => ({ url, error: error.message })));
  });
  return { external, pendingScripts };
}

async function initialScriptBytes(page, pendingScripts) {
  const responses = await Promise.all(pendingScripts);
  const broken = responses.find(item => item.error);
  if (broken) throw new Error('Could not measure initial script ' + broken.url + ': ' + broken.error);
  const unique = new Map(responses.map(item => [item.url, item.bytes]));
  const inline = await page.evaluate(() => [...document.scripts]
    .filter(script => !script.src && ['', 'module', 'text/javascript', 'application/javascript',
      'text/ecmascript', 'application/ecmascript'].includes(script.type.toLowerCase()))
    .map(script => script.textContent).filter(text => text?.trim()));
  return [...unique.values()].reduce((sum, bytes) => sum + bytes, 0)
    + inline.reduce((sum, text) => sum + gzipSync(text).length, 0);
}

async function checkHomeRelease(page, release) {
  const identity = await page.evaluate(() => {
    const data = JSON.parse(document.getElementById('atlas-preview-data').textContent);
    return { mode: data.mode, tag: data.release_tag, ids: data.records.map(record => record.id).sort() };
  });
  const expected = release.sites.map(site => site.id).sort();
  if (identity.mode !== 'sites' || identity.tag !== release.release_tag
      || JSON.stringify(identity.ids) !== JSON.stringify(expected)) {
    throw new Error('Home does not serve the selected reviewed inventory and release.');
  }
}

async function accessibility(page) {
  await page.addScriptTag({ path: require.resolve('axe-core/axe.min.js') });
  return page.evaluate(async tags => {
    const result = await window.axe.run(document, { runOnly: { type: 'tag', values: tags } });
    return {
      axe_version: result.testEngine.version,
      issues: result.violations.map(v => ({ id: v.id, impact: v.impact, help: v.help,
        nodes: v.nodes.map(n => ({ target: n.target, summary: n.failureSummary })) })),
      incomplete_rules: result.incomplete.map(rule => rule.id),
    };
  }, WCAG_TAGS);
}

async function printPage(page) {
  await page.emulateMediaType('print');
  await page.evaluate(() => document.fonts.ready.then(() => true));
  const pdf = Buffer.from(await page.pdf({ format: 'A4', preferCSSPageSize: true, printBackground: true }));
  const info = execFileSync('pdfinfo', ['-'], { input: pdf, encoding: 'utf8' });
  const pages = Number(info.match(/^Pages:\s+(\d+)/m)?.[1]);
  if (!Number.isInteger(pages) || pages < 1) throw new Error('Could not count printed pages.');
  return { pdf, pages };
}

async function auditTemplate(browser, config, name, route) {
  const page = await browser.newPage();
  try {
    await page.setCacheEnabled(false);
    await page.setViewport({ width: VIEWPORTS[0], height: 844, deviceScaleFactor: 1 });
    const requests = observeRequests(page, config.origin);
    const cdp = await page.createCDPSession();
    await cdp.send('Network.enable');
    cdp.on('Network.webSocketCreated', ({ url }) => {
      if (externalUrl(url, config.origin)) requests.external.add(url);
    });
    await navigate(page, config.origin, route);
    if (name === 'home') await checkHomeRelease(page, config.release);
    const bytes = await initialScriptBytes(page, requests.pendingScripts);
    // Inject axe only after measuring application scripts.
    const axe = await accessibility(page);
    const widths = [];
    for (const width of VIEWPORTS) widths.push(await viewportWidth(page, width));
    const { pdf, pages } = await printPage(page);
    await writeFile(join(config.output, name + '.pdf'), pdf);
    return { name, route, width: widths[0], widths, script_gzip_bytes: bytes,
      print_pages: pages, ...axe, external_runtime_requests: [...requests.external].sort() };
  } finally { await page.close(); }
}

async function allProfilePrints(browser, config, report) {
  const page = await browser.newPage();
  try {
    await page.setViewport({ width: 1280, height: 844, deviceScaleFactor: 1 });
    await page.emulateMediaType('print');
    for (const [kind, entries] of [['countries', config.release.countries], ['minerals', config.release.minerals]]) {
      for (const entry of entries) {
        const code = entry.code.toLowerCase();
        if (!/^[a-z]{2,3}$/.test(code)) throw new Error('Invalid profile code');
        const route = '/' + kind + '/' + code + '/';
        await navigate(page, config.origin, route, 'load');
        const { pdf, pages } = await printPage(page);
        const content = execFileSync('pdftotext', ['-', '-'], { input: pdf, encoding: 'utf8' })
          .normalize('NFKC').replace(/\s/g, '');
        const title = entry.name.normalize('NFKC').replace(/\s/g, '');
        const result = { kind, code: entry.code, route, pages,
          has_profile_name: content.includes(title), has_release_tag: content.includes(config.release.release_tag) };
        report.profile_prints.push(result);
        if (pages !== 1 || !result.has_profile_name || !result.has_release_tag) {
          report.failures.push(route + ' print brief must be one page with its profile name and release.');
          await writeFile(join(config.output, 'failed-print-' + kind + '-' + code + '.pdf'), pdf);
        }
      }
      console.log('Checked ' + entries.length + ' printable ' + kind + ' profiles.');
    }
  } finally { await page.close(); }
}

async function mobilePerformance(browser, origin, name, route) {
  const batch = await lighthouse(origin + route, {
    port: Number(new URL(browser.wsEndpoint()).port), logLevel: 'error', output: 'json',
    onlyCategories: ['performance'], formFactor: 'mobile',
  });
  const lhr = batch?.lhr;
  if (!lhr || lhr.runtimeError || !Number.isFinite(lhr.categories.performance.score)
      || lhr.configSettings.formFactor !== 'mobile') {
    throw new Error('Mobile Lighthouse failed for ' + name + ': ' + (lhr?.runtimeError?.message || 'missing mobile score'));
  }
  const metric = key => lhr.audits[key].numericValue;
  const requests = lhr.audits['network-requests']?.details?.items || [];
  return {
    name, route, score: Math.round(lhr.categories.performance.score * 10000) / 100,
    version: lhr.lighthouseVersion, fcp_ms: metric('first-contentful-paint'),
    lcp_ms: metric('largest-contentful-paint'), tbt_ms: metric('total-blocking-time'),
    cls: metric('cumulative-layout-shift'),
    external_runtime_requests: [...new Set(requests.map(item => item.url).filter(url => externalUrl(url, origin)))].sort(),
  };
}

const config = await options();
const endpoints = routes(config.release);
const report = {
  release: config.release.release_tag, catalog_sha256: config.catalogHash, origin: config.origin,
  started_at: new Date().toISOString(), passed: false,
  thresholds: { template_count: 13, viewport_widths: VIEWPORTS, initial_js_gzip_bytes_below: JS_LIMIT,
    mobile_lighthouse_minimum: PERFORMANCE_MINIMUM, mobile_lighthouse_runs: LIGHTHOUSE_RUNS,
    maximum_serious_or_critical_issues: 0, maximum_external_runtime_requests: 0,
    country_and_mineral_print_pages: 1 },
  templates: [], profile_prints: [], mobile_lighthouse: [], serious_issues: 0,
  external_runtime_requests: [], failures: [],
};
let browser;
try {
  browser = await puppeteer.launch({
    headless: true,
    ...(process.env.ATLAS_CHROME ? { executablePath: process.env.ATLAS_CHROME } : { channel: 'chrome' }),
    args: ['--disable-background-networking', '--disable-dev-shm-usage', '--enable-unsafe-swiftshader'],
  });
  report.browser = await browser.version();
  const external = new Set();
  for (const [name, route] of Object.entries(endpoints)) {
    const result = await auditTemplate(browser, config, name, route);
    report.templates.push(result);
    const serious = result.issues.filter(issue => ['serious', 'critical'].includes(issue.impact)).length;
    report.serious_issues += serious;
    if (serious) report.failures.push(name + ' has ' + serious + ' serious/critical axe violations.');
    if (result.widths.some(w => w.content > w.viewport)) report.failures.push(name + ' overflows the viewport.');
    if (result.script_gzip_bytes >= JS_LIMIT) report.failures.push(name + ' exceeds the initial JavaScript budget.');
    if (['country', 'mineral'].includes(name) && result.print_pages !== 1) report.failures.push(name + ' print exceeds one page.');
    for (const url of result.external_runtime_requests) external.add(url);
    console.log('Audited ' + name + ' (' + result.script_gzip_bytes + ' bytes of initial gzip JavaScript).');
  }
  await allProfilePrints(browser, config, report);
  for (const name of LIGHTHOUSE_RUNS) {
    const result = await mobilePerformance(browser, config.origin, name, endpoints[name]);
    report.mobile_lighthouse.push(result);
    if (result.score < PERFORMANCE_MINIMUM) report.failures.push(name + ' Lighthouse score is ' + result.score + '.');
    for (const url of result.external_runtime_requests) external.add(url);
    console.log('Mobile Lighthouse ' + name + ': ' + result.score);
  }
  report.external_runtime_requests = [...external].sort();
  if (external.size) report.failures.push('Observed external application runtime requests.');
  if (report.templates.length !== 13 || report.mobile_lighthouse.length !== 6
      || report.profile_prints.length !== config.release.countries.length + config.release.minerals.length) {
    report.failures.push('The audit did not cover every required template, profile or Lighthouse run.');
  }
} catch (error) {
  report.failures.push(error.message);
} finally {
  if (browser) await browser.close();
  report.finished_at = new Date().toISOString();
  report.passed = report.failures.length === 0;
  const path = join(config.output, 'audit.json');
  await writeFile(path + '.tmp', JSON.stringify(report, null, 2) + '\n');
  await rename(path + '.tmp', path);
}
if (!report.passed) {
  console.error(report.failures.join('\n'));
  process.exitCode = 1;
} else {
  console.log('All browser, print, accessibility and performance gates passed.');
}
