// A loopback static server needs byte ranges for PMTiles archive reads.
export function byteRange(header, size) {
  if (header === undefined) return null;
  const match = /^bytes=(\d*)-(\d*)$/.exec(header);
  if (!match || (!match[1] && !match[2]) || !Number.isSafeInteger(size) || size <= 0) return false;
  if (!match[1]) {
    const tail=Number(match[2]);
    return Number.isSafeInteger(tail) && tail>0 ? [Math.max(0,size-tail),size-1] : false;
  }
  const start=Number(match[1]), end=match[2] ? Number(match[2]) : size-1;
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start>=size || end<start) return false;
  return [start,Math.min(size-1,end)];
}
