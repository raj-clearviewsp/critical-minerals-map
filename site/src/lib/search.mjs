const input = document.getElementById('atlas-query');
const status = document.getElementById('search-status');
const list = document.getElementById('search-results');
const more = document.getElementById('search-more');
let generation = 0, timer, found = [], shown = 0;
let index;
async function appendResults(version) {
  more.disabled = true;
  const entries = await Promise.all(found.slice(shown, shown + 12).map(item=>item.data()));
  if (generation !== version) return;
  const nodes = [];
  for (const entry of entries) {
    const url = new URL(entry.url, location.origin);
    if (url.origin !== location.origin) continue;
    const item = document.createElement('li'), link = document.createElement('a');
    link.href = url.pathname + url.hash;
    link.textContent = entry.meta.title || url.pathname;
    const excerpt = document.createElement('p');
    excerpt.textContent = (entry.excerpt || '').replace(/<\/?mark>/gi, '').replaceAll('&amp;','&');
    item.append(link, excerpt); nodes.push(item);
  }
  list.append(...nodes); shown += entries.length;
  more.hidden = shown >= found.length; more.disabled = false;
}
async function search(query) {
  const version = ++generation;
  clearTimeout(timer); input.value=query;
  const url = new URL(location.href);
  if (query) url.searchParams.set('q',query); else url.searchParams.delete('q');
  history.replaceState(null,'',url.pathname+url.search);
  list.replaceChildren(); more.hidden=true; found=[]; shown=0;
  if (!query.trim()) { status.textContent=''; return; }
  status.textContent='Searching…';
  try {
    const indexUrl='/pagefind/pagefind.js';
    index ??= await import(/* @vite-ignore */ indexUrl);
    const result=await index.search(query);
    if (version !== generation) return;
    found=result.results;
    status.textContent = found.length ? found.length.toLocaleString('en-US')+' matching pages' : 'No matching pages. Try another name or mineral.';
    await appendResults(version);
  } catch {
    if (version===generation) status.textContent='Search is available in the built release. Use the site or country directory to browse.';
  }
}
input.form.addEventListener('submit',event=>{event.preventDefault();search(input.value.trim());});
input.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(()=>search(input.value.trim()),220);});
more.addEventListener('click',()=>appendResults(generation));
window.addEventListener('popstate',()=>search((new URLSearchParams(location.search).get('q') || '').slice(0,200)));
search((new URLSearchParams(location.search).get('q') || '').slice(0,200));
