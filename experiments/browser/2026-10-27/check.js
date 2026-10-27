// Browser check for FusionEdges runs: node check.js <label>=<build dir> ...  -> results.json + screenshots/
const { chromium } = require('playwright');
const http = require('http');
const fs = require('fs');
const path = require('path');

const TYPES = { '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.svg': 'image/svg+xml', '.gif': 'image/gif',
  '.webp': 'image/webp', '.ico': 'image/x-icon', '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf', '.pdf': 'application/pdf' };

function serve(dir, port) {
  return new Promise(res => {
    const srv = http.createServer((req, rsp) => {
      let p = decodeURIComponent(req.url.split('?')[0]);
      let f = path.join(dir, p);
      if (!f.startsWith(dir)) { rsp.writeHead(403); return rsp.end(); }
      if (!fs.existsSync(f) || fs.statSync(f).isDirectory()) f = fs.existsSync(path.join(f, 'index.html')) ? path.join(f, 'index.html') : path.join(dir, 'index.html');
      rsp.writeHead(200, { 'content-type': TYPES[path.extname(f)] || 'application/octet-stream' });
      fs.createReadStream(f).pipe(rsp);
    }).listen(port, () => res(srv));
  });
}

const VIEWPORTS = { desktop: { width: 1280, height: 800 }, mobile: { width: 390, height: 844 } };

async function inspect(page) {
  return page.evaluate(() => {
    const vis = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity !== 0; };
    // the navbar: the topmost visible header/nav/.navbar that starts near the top of the page
    const cands = [...document.querySelectorAll('header, nav, [class*="navbar"], [class*="Navbar"], [class*="menubar"]')]
      .filter(vis).filter(el => el.getBoundingClientRect().top < 140);
    const bar = cands.sort((a, b) => b.getBoundingClientRect().width * 1 - a.getBoundingClientRect().width)[0];
    const r = bar ? bar.getBoundingClientRect() : null;
    const links = bar ? [...bar.querySelectorAll('a, button')].filter(vis)
      .map(a => (a.innerText || a.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ')).filter(Boolean) : [];
    // elements hidden by an inline display:none inside the navbar (the pilot's suspected bug)
    const inlineHidden = bar ? [...bar.querySelectorAll('[style*="display: none"], [style*="display:none"]')]
      .map(e => `${e.tagName.toLowerCase()}.${(e.className || '').toString().split(' ').slice(0, 3).join('.')} (${e.querySelectorAll('a').length} links)`) : [];
    return {
      navbar: bar ? { tag: bar.tagName.toLowerCase(), classes: (bar.className || '').toString().slice(0, 120),
        top: Math.round(r.top), height: Math.round(r.height), width: Math.round(r.width) } : null,
      visibleLinks: [...new Set(links)].slice(0, 20),
      inlineHidden,
      hasNavbarClass: !!document.querySelector('.navbar'),
      hasNavbarDefault: !!document.querySelector('.navbar-default'),
      bodyText: (document.body.innerText || '').trim().length,
    };
  });
}

(async () => {
  const targets = process.argv.slice(2).map(a => a.split('='));
  const out = {};
  fs.mkdirSync('screenshots', { recursive: true });
  const browser = await chromium.launch();
  let port = 4300;
  for (const [label, dir] of targets) {
    const srv = await serve(path.resolve(dir), ++port);
    out[label] = {};
    for (const [vp, size] of Object.entries(VIEWPORTS)) {
      const ctx = await browser.newContext({ viewport: size });
      const page = await ctx.newPage();
      const errors = { load: [], scroll: [] };
      let phase = 'load';
      page.on('pageerror', e => errors[phase].push('pageerror: ' + e.message.split('\n')[0]));
      page.on('console', m => { if (m.type() === 'error') errors[phase].push('console: ' + m.text().split('\n')[0].slice(0, 200)); });
      try { await page.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'networkidle', timeout: 30000 }); }
      catch (e) { errors.load.push('navigation: ' + e.message.split('\n')[0]); }
      await page.waitForTimeout(3500);            // preloaders, fonts, animations
      const atLoad = await inspect(page);
      await page.screenshot({ path: `screenshots/${label}-${vp}.png` });
      phase = 'scroll';                            // the site's own scripts often run on scroll
      for (const y of [300, 900, 0]) { await page.mouse.wheel(0, y === 0 ? -5000 : y); await page.waitForTimeout(500); }
      const afterScroll = await inspect(page);
      out[label][vp] = { atLoad, afterScroll, errors };
      await ctx.close();
    }
    srv.close();
    console.log(label, JSON.stringify({ d: out[label].desktop.atLoad.visibleLinks.length, m: out[label].mobile.atLoad.visibleLinks.length,
      err: out[label].desktop.errors.load.length + out[label].desktop.errors.scroll.length }));
  }
  await browser.close();
  fs.writeFileSync('results.json', JSON.stringify(out, null, 1));
})();
