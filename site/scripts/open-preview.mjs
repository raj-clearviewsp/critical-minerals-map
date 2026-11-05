// Open the already-built private site on loopback. Nothing is uploaded.
import { createServer } from 'node:http';
import { createReadStream } from 'node:fs';
import { byteRange } from './http-range.mjs';
import { readFile, stat } from 'node:fs/promises';
import { resolve, sep, extname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { gzipSync } from 'node:zlib';

const args = process.argv.slice(2);
const flag = args.indexOf('--port');
let listenPort = 0;
if (flag !== -1) {
  const value = args[flag + 1] || '';
  if (flag !== args.lastIndexOf('--port') || !/^\d{1,5}$/.test(value)
      || Number(value) < 1 || Number(value) > 65535) {
    console.error('Use --port followed by a port number from 1 through 65535.');
    process.exit(1);
  }
  listenPort = Number(value);
}

const root = fileURLToPath(new URL('../.preview-dist/', import.meta.url));
try { await stat(resolve(root, 'index.html')); }
catch { console.error('Build the private Atlas first: make site-private'); process.exit(1); }
const mime = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8', '.json': 'application/json',
  '.csv': 'text/csv; charset=utf-8', '.wasm': 'application/wasm', '.pmtiles': 'application/vnd.pmtiles',
  '.geojson': 'application/geo+json', '.svg': 'image/svg+xml', '.png': 'image/png' };
const server = createServer(async (request, response) => {
  try {
    if (!['GET', 'HEAD'].includes(request.method)) {
      response.writeHead(405, { Allow: 'GET, HEAD' }).end(); return;
    }
    const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
    let path = resolve(root, `.${pathname}`);
    if (path !== resolve(root) && !path.startsWith(`${resolve(root)}${sep}`)) {
      response.writeHead(403).end(); return;
    }
    if ((await stat(path)).isDirectory()) path = resolve(path, 'index.html');
    const size = (await stat(path)).size;
    const range = byteRange(request.headers.range, size);
    const headers = { 'Content-Type': mime[extname(path)] || 'application/octet-stream',
      'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', Vary: 'Accept-Encoding' };
    headers['Accept-Ranges'] = 'bytes';
    if (range === false) {
      response.writeHead(416, { ...headers, 'Content-Range': 'bytes */' + size }).end(); return;
    }
    if (range) {
      const [start,end] = range;
      response.writeHead(206, {...headers, 'Content-Range': 'bytes ' + start + '-' + end + '/' + size,
        'Content-Length': end-start+1 });
      if (request.method === 'HEAD') response.end();
      else createReadStream(path,{start,end}).on('error',()=>response.destroy()).pipe(response);
      return;
    }
    if (request.method === 'HEAD' || !/^(?:text\/|application\/(?:json|geo\+json))/.test(headers['Content-Type'])) {
      response.writeHead(200, { ...headers, 'Content-Length': size });
      if (request.method === 'HEAD' || size===0) response.end();
      else createReadStream(path).on('error',()=>response.destroy()).pipe(response);
      return;
    }
    let body = await readFile(path);
    if (/\bgzip\b/.test(request.headers['accept-encoding'] || '')) {
      body = gzipSync(body); headers['Content-Encoding'] = 'gzip';
    }
    response.writeHead(200, { ...headers, 'Content-Length': body.length });
    response.end(request.method === 'HEAD' ? undefined : body);
  } catch { response.writeHead(404).end('Not found'); }
});
server.on('error', error => {
  const address = listenPort ? '127.0.0.1:'+listenPort : 'a loopback port';
  console.error('Could not start the Atlas on '+address+'. '+error.message);
  process.exitCode = 1;
});
server.listen(listenPort, '127.0.0.1', () => {
  const url = `http://127.0.0.1:${server.address().port}/`;
  console.log(`Atlas is ready: ${url}\nKeep this window open while using the site. Press Control-C to stop.`);
  if (!process.argv.includes('--no-open')) {
    const command = process.platform === 'darwin' ? 'open' : 'xdg-open';
    spawn(command, [url], { stdio: 'ignore' }).on('error', () => console.log(`Open ${url} in your browser.`));
  }
});
process.on('SIGINT', () => server.close(() => process.exit(0)));
process.on('SIGTERM', () => server.close(() => process.exit(0)));
