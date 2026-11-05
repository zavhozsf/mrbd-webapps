#!/usr/bin/env python3
"""tw_bridge.py — локальный мост X/Twitter для web-app очков (MRBD /x/).

X рендерится локальным Playwright (chromium) с ПЕРСИСТЕНТНЫМ профилем —
логин остаётся на этой машине, куки никуда не уходят.
Команды:
  login  — открыть окно браузера, залогинься в x.com руками, закрой окно (сессия сохранится)
  serve  — headless-сервер :8789 с JSON-API для очков
"""
import asyncio, json, re, sys, time, pathlib
from aiohttp import web
from playwright.async_api import async_playwright

PORT = 8789
ROOT = pathlib.Path(__file__).parent
PROFILE = ROOT / "twprofile"
CACHE_TTL = 90
cache, _S = {}, {}
RU_MONTH = ['янв','фев','мар','апр','мая','июн','июл','авг','сен','окт','ноя','дек']

def fmt_date(iso):
    if not iso: return ''
    try:
        t = time.strptime(iso[:19], '%Y-%m-%dT%H:%M:%S')
        lt = time.localtime(time.mktime(t)); now = time.time()
        dt = now - time.mktime(t)
        if dt < 3600: return f"{max(1,int(dt//60))} мин"
        if dt < 86400: return f"{int(dt//3600)} ч"
        if time.strftime('%Y', lt) == time.strftime('%Y'): return f"{lt.tm_mday} {RU_MONTH[lt.tm_mon-1]}"
        return f"{lt.tm_mday} {RU_MONTH[lt.tm_mon-1]} {lt.tm_year}"
    except Exception:
        return iso[:10]

EXTRACT = r"""
() => {
  const out = [];
  for (const t of document.querySelectorAll('article[data-testid="tweet"]')) {
    const txt = t.querySelector('[data-testid="tweetText"]');
    if (!txt) continue;
    const tm = t.querySelector('time[datetime]');
    const ln = [...t.querySelectorAll('a[href*="/status/"]')].find(a=>/\d{8,}/.test(a.href));
    const un = t.querySelector('[data-testid="User-Name"]');
    let user = '', name = '';
    if (un) { const a = un.querySelector('a[href^="/"]'); user = a ? a.getAttribute('href').slice(1) : '';
              const sp = un.querySelector('div[dir="ltr"] span'); name = sp ? sp.textContent.trim() : ''; }
    let stats = {};
    const g = t.querySelector('div[role="group"][aria-label]');
    if (g) { const m = g.getAttribute('aria-label').replace(/,/g,'');
      stats = { replies:(m.match(/(\d+) Repl/i)||[0,''])[1]||'', rts:(m.match(/(\d+) Repost/i)||[0,''])[1]||'',
                favs:(m.match(/(\d+) Lik/i)||[0,''])[1]||'', views:'' }; }
    const bq = t.querySelector('blockquote');
    const qt = bq ? bq.innerText.trim() : '';
    let qh = ''; const q = t.querySelector('div[quoted="true"] a[href^="/"], blockquote a[href^="/"]');
    if (q) qh = q.getAttribute('href').slice(1).split('/')[0];
    const media = t.querySelectorAll('[data-testid="tweetPhoto"], [data-testid="videoPlayer"]').length;
    const ph = t.querySelector('[data-testid="tweetPhoto"] img');
    out.push({ text: txt.innerText, date: tm ? tm.getAttribute('datetime') : '',
      id: ln ? (ln.href.match(/status\/(\d+)/)||[])[1] : '', user, name, stats,
      quote: qt ? { handle: qh, text: qt } : null,
      media: media ? 1 : 0, img: (media && ph) ? ph.src : '' });
    if (out.length >= 25) break;
  }
  return out;
}
"""

async def get_context(p, headless=True):
    if _S.get('ctx'): return _S['ctx']
    ctx = await p.chromium.launch_persistent_context(
        str(PROFILE), headless=headless, viewport={'width':900,'height':1600},
        args=['--disable-blink-features=AutomationControlled'], locale='en-US')
    ctx.set_default_navigation_timeout(30000)
    _S['ctx'] = ctx
    return ctx

async def scrape(url):
    key = url
    c = cache.get(key)
    if c and time.time()-c[0] < CACHE_TTL: return c[1]
    async with _S.setdefault('lock', asyncio.Lock()):
        c = cache.get(key)
        if c and time.time()-c[0] < CACHE_TTL: return c[1]
        pg = await _S['ctx'].new_page()
        try:
            await pg.goto(url, wait_until='domcontentloaded')
            try:
                await pg.wait_for_selector('article[data-testid="tweet"]', timeout=25000)
            except Exception:
                return {'error':'no-tweets (не залогинен / недоступно)','posts':[]}
            for _ in range(2):
                await pg.mouse.wheel(0, 2200); await pg.wait_for_timeout(1200)
            posts = await pg.evaluate(EXTRACT)
            for p_ in posts: p_['date'] = fmt_date(p_['date'])
            data = {'posts': posts}
            cache[key] = (time.time(), data)
            return data
        finally:
            await pg.close()

def logged_in(ctx_pages):
    return PROFILE.exists() and any(True for _ in PROFILE.rglob('*')) and _S.get('li') is not None

async def check_login():
    pg = await _S['ctx'].new_page()
    try:
        await pg.goto('https://x.com/home', wait_until='domcontentloaded')
        await pg.wait_for_timeout(4000)
        ok = await pg.evaluate("()=>!!document.querySelector('article[data-testid=\"tweet\"]') && !location.href.includes('login')")
        _S['li'] = ok
        if ok and not _S.get('handle'):
            _S['handle'] = await pg.evaluate(
                "()=>{const e=document.querySelector('[data-testid=\"sideNavAccountSwitcher\"]');const m=e&&e.innerText&&e.innerText.match(/@(\\w{2,15})/);return m?m[1]:'';}")
        return ok
    finally:
        await pg.close()

async def main_login():
    async with async_playwright() as p:
        ctx = await get_context(p, headless=False)
        pg = await ctx.new_page()
        await pg.goto('https://x.com/x/i/flow/login')
        print('Окно открыто. Залогинься в X и ДОЖДИСЬ ленты (или закрой окно сам).')
        while ctx.pages:
            try:
                u = pg.url if not pg.is_closed() else ''
            except Exception:
                break
            if 'x.com/home' in u:
                await pg.wait_for_timeout(6000); break
            await asyncio.sleep(1)
        print('Сессия сохранена в', PROFILE, '— можно закрывать окно и запускать serve.')
        while ctx.pages: await asyncio.sleep(1)
        await ctx.close(); _S['ctx'] = None

async def serve():
    async with async_playwright() as p:
        await get_context(p, headless=True)
        await check_login()
        async def cors(r, h): h['Access-Control-Allow-Origin'] = '*'
        async def state(r):
            await check_login()
            return web.json_response({'logged_in': bool(_S.get('li'))})
        async def home(r):
            tab = r.query.get('tab') or ''
            url = 'https://x.com/home' + ('?showFollowing=true' if tab == 'following' else '')
            return web.json_response(await scrape(url))
        async def user(r):
            h = r.match_info['h'].lstrip('@')
            return web.json_response(await scrape(f'https://x.com/{h}'))
        async def me(r):
            if not _S.get('handle'): return web.json_response({'error':'нет хэндла (перелогин)','posts':[]})
            return web.json_response(await scrape(f"https://x.com/{_S['handle']}"))
        async def tweet(r):
            return web.json_response(await scrape(f"https://x.com/i/status/{r.match_info['id']}"))
        app = web.Application(middlewares=[cors])
        app.router.add_get('/api/state', state)
        app.router.add_get('/api/home', home)
        app.router.add_get('/api/me', me)
        app.router.add_get('/api/user/{h}', user)
        app.router.add_get('/api/tweet/{id}', tweet)
        runner = web.AppRunner(app); await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', PORT); await site.start()
        print(f'tw_bridge на :{PORT}, logged_in={_S.get("li")}')
        while True: await asyncio.sleep(3600)

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'serve'
    asyncio.run(main_login() if cmd == 'login' else serve())
