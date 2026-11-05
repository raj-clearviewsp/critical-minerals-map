import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { copyFile, mkdir, realpath, stat, unlink } from 'node:fs/promises';
import { dirname, relative, resolve, sep } from 'node:path';
import { safeDownloadPath } from '../src/lib/release-data.mjs';

export async function copyDownloads(release, manifestPath, outDir) {
  if (!release?.downloads.length) return;
  const products = await realpath(dirname(manifestPath));
  const permitted = resolve(products, 'downloads');
  for (const download of release.downloads) {
    if (!safeDownloadPath(download.path)) throw new Error('Unsafe download path');
    const input = await realpath(resolve(products, download.path));
    const rel = relative(permitted, input);
    if (!rel || rel.startsWith('..' + sep) || rel === '..' || !(await stat(input)).isFile()) throw new Error('Download outside approved directory');
    const output = resolve(outDir, download.path);
    await mkdir(dirname(output), { recursive: true });
    await copyFile(input, output);
    const hash = createHash('sha256');
    for await (const chunk of createReadStream(output)) hash.update(chunk);
    if (hash.digest('hex') !== download.sha256 || (download.bytes != null && (await stat(output)).size !== download.bytes)) {
      await unlink(output); throw new Error('Download differs from release receipt: ' + download.path);
    }
  }
}
