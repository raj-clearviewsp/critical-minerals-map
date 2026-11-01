// Open the already-built private site on loopback. Nothing is uploaded.
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { resolve, sep, extname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { gzipSync } from 'node:zlib';

const root = fileURLToPath(new URL('../.preview-dist/', import.meta.url));
try { await stat(resolve(root, 'index.html')); }
catch { console.error('Build the private Atlas first: make site-private'); process.exit(1); }
const mime = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8', '.json': 'application/json',
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
    let body = await readFile(path);
    const headers = { 'Content-Type': mime[extname(path)] || 'application/octet-stream',
      'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', Vary: 'Accept-Encoding' };
    if (/\bgzip\b/.test(request.headers['accept-encoding'] || '')) {
      body = gzipSync(body); headers['Content-Encoding'] = 'gzip';
    }
    response.writeHead(200, { ...headers, 'Content-Length': body.length });
    response.end(request.method === 'HEAD' ? undefined : body);
  } catch { response.writeHead(404).end('Not found'); }
});
server.listen(0, '127.0.0.1', () => {
  const url = `http://127.0.0.1:${server.address().port}/`;
  console.log(`Atlas is ready: ${url}\nKeep this window open while using the site. Press Control-C to stop.`);
  if (!process.argv.includes('--no-open')) {
    const command = process.platform === 'darwin' ? 'open' : 'xdg-open';
    spawn(command, [url], { stdio: 'ignore' }).on('error', () => console.log(`Open ${url} in your browser.`));
  }
});
process.on('SIGINT', () => server.close(() => process.exit(0)));
process.on('SIGTERM', () => server.close(() => process.exit(0)));
