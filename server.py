#!/usr/bin/env python3
"""
COINDIVA MACRO TERMINAL — Local Server
Serves static files + proxies Stooq market data + Reuters Connect API
"""
import os, json, ssl, urllib.request, urllib.parse, re
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from concurrent.futures import ThreadPoolExecutor, as_completed

PORT     = 8765
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── SECRETS ───────────────────────────────────────────────────────────────
# Keys were previously hardcoded here and in MacroTerminal.html. They now live
# in secrets.json, which is gitignored, so this repo can be published without
# leaking credentials. Environment variables win if set (useful for hosting).
def _load_secrets():
    path = os.path.join(BASE_DIR, 'secrets.json')
    data = {}
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        print('!! secrets.json not found — copy secrets.example.json to secrets.json and add your keys')
    except json.JSONDecodeError as e:
        print(f'!! secrets.json is not valid JSON: {e}')
    return {
        'FRED_KEY':    os.environ.get('FRED_KEY')    or data.get('FRED_KEY', ''),
        'REUTERS_KEY': os.environ.get('REUTERS_KEY') or data.get('REUTERS_KEY', ''),
    }

SECRETS     = _load_secrets()
REUTERS_KEY = SECRETS['REUTERS_KEY']

for _k, _v in SECRETS.items():
    print(f'  {_k:12} {"loaded" if _v else "MISSING — that panel will not populate"}')

class Handler(SimpleHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path.startswith('/config.js'):
            self._config_js()
        elif self.path.startswith('/stooq'):
            self._stooq()
        elif self.path.startswith('/yahoo'):
            self._yahoo()
        elif self.path.startswith('/reuters'):
            self._reuters()
        elif self.path.startswith('/news'):
            self._news()
        elif self.path.startswith('/coindesk'):
            self._coindesk()
        elif self.path.startswith('/binance'):
            self._binance()
        elif self.path.startswith('/bybit'):
            self._bybit()
        elif self.path.startswith('/okx'):
            self._okx()
        elif self.path.startswith('/coingecko'):
            self._coingecko()
        elif self.path.startswith('/relay'):
            self._relay()
        else:
            super().do_GET()

    def end_headers(self):
        # Kill browser caching for HTML files so edits show immediately
        if self.path.endswith('.html') or '?' in self.path:
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
        super().end_headers()

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')

    def _config_js(self):
        """Serve API keys to the browser at runtime from secrets.json.

        This is what lets MacroTerminal.html stay free of hardcoded credentials.
        Served only over localhost, so the keys never enter the git repo.
        """
        body = f'window.CONFIG = {json.dumps(SECRETS)};'
        self.send_response(200)
        self.send_header('Content-Type', 'application/javascript')
        self.send_header('Cache-Control', 'no-store')
        self._cors()
        self.end_headers()
        self.wfile.write(body.encode())

    def _stooq(self):
        """Proxy Stooq market data — solves CORS + rate limiting in browser"""
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        sym = params.get('sym', [''])[0]
        if not sym:
            self.send_response(400)
            self._cors()
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'Missing sym parameter'}).encode())
            return

        # Stooq URL — sym is already URL-encoded by client
        url = f'https://stooq.com/q/l/?s={urllib.parse.quote(sym)}&f=sd2t2ohlcv&h&e=json'
        ctx = ssl.create_default_context()
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': 'https://stooq.com/',
            })
            with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
                data = resp.read()
            # Fix Stooq malformed JSON (empty field values)
            text = data.decode('utf-8', errors='replace')
            text = re.sub(r'"([^"]+)":\s*([,}\]])', r'"\1":null\2', text)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self._cors()
            self.end_headers()
            self.wfile.write(text.encode('utf-8'))
        except Exception as e:
            self.send_response(502)
            self.send_header('Content-Type', 'application/json')
            self._cors()
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e), 'sym': sym}).encode())

    def _yahoo(self):
        """Proxy Yahoo Finance for indices/VIX — no API key, server-side no CORS"""
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        syms_str = params.get('sym', [''])[0]
        if not syms_str:
            self.send_response(400)
            self._cors()
            self.end_headers()
            self.wfile.write(json.dumps({'error': 'Missing sym'}).encode())
            return

        syms = [s.strip() for s in syms_str.split(',') if s.strip()]
        result = {}
        ctx = ssl.create_default_context()

        def _fetch_one(sym):
            url = f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range=5d&interval=1d&includePrePost=false'
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Accept': 'application/json',
                'Accept-Language': 'en-US,en;q=0.9',
            })
            with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
                d = json.loads(resp.read())
            meta = d['chart']['result'][0]['meta']
            price = meta.get('regularMarketPrice') or meta.get('previousClose')
            prev  = meta.get('chartPreviousClose') or meta.get('previousClose') or price
            if price:
                chg = ((price - prev) / prev * 100) if prev else 0
                return sym, {'price': round(price, 4), 'change_pct': round(chg, 2)}
            return sym, None

        # NOTE: the client now batches ~22 symbols here (was 7) after Stooq was
        # removed. A single as_completed() timeout used to raise uncaught and 500
        # the entire batch, losing every symbol including the ones that succeeded.
        # Partial results are strictly better than none, so the timeout is caught
        # and whatever arrived in time is returned.
        with ThreadPoolExecutor(max_workers=min(len(syms), 24)) as pool:
            futures = {pool.submit(_fetch_one, sym): sym for sym in syms}
            try:
                for future in as_completed(futures, timeout=20):
                    sym = futures[future]
                    try:
                        s, val = future.result()
                        if val:
                            result[s] = val
                    except Exception as e:
                        print(f'Yahoo {sym} → {e}')
            except Exception as e:
                print(f'Yahoo batch timeout — returning {len(result)}/{len(syms)} partial: {e}')

        print(f'Yahoo → {len(result)}/{len(syms)} symbols OK')
        missing = [s for s in syms if s not in result]
        if missing:
            print(f'Yahoo → MISSING: {", ".join(missing)}')

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def _parse_rss(self, url, source_name, category='crypto'):
        """Generic RSS parser — returns list of news items as dicts"""
        import xml.etree.ElementTree as ET
        from email.utils import parsedate_to_datetime
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'application/rss+xml,application/xml,*/*'
        })
        with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
        root = ET.fromstring(raw)
        items = []
        for item in root.findall('.//item')[:25]:
            title = item.findtext('title', '').strip()
            link  = (item.findtext('link') or item.findtext('{http://www.w3.org/2005/Atom}link', '#')).strip()
            pub   = item.findtext('pubDate', '').strip()
            if not title: continue
            try:
                dt = parsedate_to_datetime(pub)
                ts       = dt.timestamp() * 1000
                time_str = dt.astimezone().strftime('%H:%M')
                date_str = dt.astimezone().strftime('%d %b')
            except:
                import time as _t
                ts = _t.time()*1000; time_str = '--:--'; date_str = ''
            items.append({
                'headline': title, 'source': source_name, 'url': link,
                'category': category, 'time_awst': time_str, 'date_awst': date_str, 'ts': ts
            })
        return items

    def _news(self):
        """Aggregate news from multiple RSS feeds — CoinTelegraph, Decrypt, Bitcoin Mag, AP Business, NYT Business, MarketWatch"""
        import xml.etree.ElementTree as ET
        feeds = [
            ('https://cointelegraph.com/rss',                                        'CoinTelegraph', 'crypto'),
            ('https://decrypt.co/feed',                                              'Decrypt',       'crypto'),
            ('https://bitcoinmagazine.com/.rss/full/',                               'Bitcoin Mag',   'crypto'),
            ('https://feeds.apnews.com/rss/apf-business',                            'AP Business',   'macro'),
            ('https://rss.nytimes.com/services/xml/rss/nyt/Business.xml',            'NYT Business',  'macro'),
            ('https://www.marketwatch.com/rss/topstories',                           'MarketWatch',   'macro'),
        ]
        all_items = []
        seen = set()
        # Fetch all feeds in parallel. Serially, 6 feeds x up to 10s each ran past
        # the browser's 12s limit and the whole news panel timed out (Sep 2026).
        def _one(feed):
            url, name, cat = feed
            try:
                items = self._parse_rss(url, name, cat)
                print(f'  News OK {name}: {len(items)} articles')
                return items
            except Exception as e:
                print(f'  News FAILED {name}: {e}')
                return []
        # Hard 7s budget for the whole call. MarketWatch alone can take 11s+,
        # which pushed /news past the browser's 12s limit. Slow feeds are skipped
        # for this round and logged, not waited on.
        from concurrent.futures import wait as _wait
        pool = ThreadPoolExecutor(max_workers=len(feeds))
        futs = {pool.submit(_one, f): f[1] for f in feeds}
        done, late = _wait(futs, timeout=7)
        pool.shutdown(wait=False)
        for f in late:
            print(f'  News SKIPPED {futs[f]}: slower than 7s')
        results = [f.result() for f in done]
        for items in results:
            for it in items:
                if it['headline'] not in seen:
                    seen.add(it['headline'])
                    all_items.append(it)
        # Sort by timestamp descending
        all_items.sort(key=lambda x: x.get('ts',0), reverse=True)
        data = json.dumps(all_items[:50]).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _coindesk(self):
        """Legacy endpoint — now calls _news for backwards compat"""
        self._news()

    def _binance(self):
        """Proxy Binance Futures API — solves CORS for fapi.binance.com"""
        tail = self.path[len('/binance'):]
        url  = f'https://fapi.binance.com{tail}'
        ctx  = ssl.create_default_context()
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Accept': 'application/json',
            })
            with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
                data = resp.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self._cors()
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_response(502)
            self.send_header('Content-Type', 'application/json')
            self._cors()
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def _bybit(self):
        """Proxy Bybit API — accessible from AU where Binance is geo-blocked"""
        tail = self.path[len('/bybit'):]
        url  = f'https://api.bybit.com{tail}'
        ctx  = ssl.create_default_context()
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Accept': 'application/json',
            })
            with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
                data = resp.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self._cors()
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_response(502)
            self.send_header('Content-Type', 'application/json')
            self._cors()
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def _okx(self):
        """Proxy OKX API — accessible from AU, good L/S ratio data"""
        tail = self.path[len('/okx'):]
        url  = f'https://www.okx.com{tail}'
        ctx  = ssl.create_default_context()
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Accept': 'application/json',
            })
            with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
                data = resp.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self._cors()
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_response(502)
            self.send_header('Content-Type', 'application/json')
            self._cors()
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def _coingecko(self):
        """Proxy CoinGecko API — prevents browser rate limiting (429 errors)"""
        tail = self.path[len('/coingecko'):]
        url  = f'https://api.coingecko.com{tail}'
        ctx  = ssl.create_default_context()
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Accept': 'application/json',
            })
            with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                data = resp.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self._cors()
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_response(502)
            self.send_header('Content-Type', 'application/json')
            self._cors()
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e)}).encode())

    def _reuters(self):
        """Proxy Reuters — tries every auth format, returns detailed errors for debugging"""
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        q     = params.get('q', ['bitcoin crypto markets economy federal reserve'])[0]
        limit = params.get('limit', ['40'])[0]
        qenc  = urllib.parse.quote(q)

        # ERROR DIAGNOSIS: api.reutersconnect.com returns HTTP 403:
        #   "Invalid key=value pair (missing equal-sign) in Authorization header"
        # This means Bearer token must be in key=value format e.g. "Bearer token=UUID"
        # api.reuters.com DNS does not resolve — dead domain, skip entirely.
        endpoints = [
            # Reuters Connect — key=value Bearer formats (fixing the 403)
            (f'https://api.reutersconnect.com/content/news?q={qenc}&limit={limit}',  'token'),
            (f'https://api.reutersconnect.com/content/news?q={qenc}&limit={limit}',  'key'),
            (f'https://api.reutersconnect.com/content/news?q={qenc}&limit={limit}',  'apikey_kv'),
            (f'https://api.reutersconnect.com/content/news?limit={limit}',           'token'),
            # Also try x-api-key header alone (no Bearer)
            (f'https://api.reutersconnect.com/content/news?q={qenc}&limit={limit}',  'xapikey'),
            # Token as query param
            (f'https://api.reutersconnect.com/content/news?token={REUTERS_KEY}&q={qenc}&limit={limit}', 'none'),
        ]

        ctx = ssl.create_default_context()
        errors = []
        for url, auth_style in endpoints:
            try:
                headers = {
                    'Accept':     'application/json',
                    'User-Agent': 'CoinDivaMacroTerminal/1.0',
                }
                if auth_style == 'token':
                    headers['Authorization'] = f'Bearer token={REUTERS_KEY}'
                elif auth_style == 'key':
                    headers['Authorization'] = f'Bearer key={REUTERS_KEY}'
                elif auth_style == 'apikey_kv':
                    headers['Authorization'] = f'Bearer apikey={REUTERS_KEY}'
                elif auth_style == 'xapikey':
                    headers['X-Api-Key'] = REUTERS_KEY
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
                    data = resp.read()
                print(f'  ✅ Reuters OK [{auth_style}] → {url[:70]}')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self._cors()
                self.end_headers()
                self.wfile.write(data)
                return
            except urllib.error.HTTPError as e:
                body = e.read().decode('utf-8', errors='replace')[:300]
                msg  = f'HTTP {e.code} [{auth_style}] {url[:55]}: {body}'
                print(f'  ❌ {msg}')
                errors.append(msg)
            except Exception as e:
                msg = f'ERR [{auth_style}] {url[:55]}: {e}'
                print(f'  ❌ {msg}')
                errors.append(msg)

        self.send_response(502)
        self.send_header('Content-Type', 'application/json')
        self._cors()
        self.end_headers()
        # Return detailed errors so we can debug from browser
        self.wfile.write(json.dumps({'error': 'Reuters API unavailable', 'debug': errors}).encode())

    # Hosts the browser is allowed to reach through /relay. Replaces the public
    # corsproxy.io fallback, which stopped accepting anonymous requests (Sep 2026)
    # and took down every FRED field and the economic calendar.
    RELAY_HOSTS = ('api.stlouisfed.org', 'nfs.faireconomy.media', 'bitcoinmagazine.com',
                   'cointelegraph.com', 'decrypt.co', 'marketwatch.com')

    # Short server-side cache. ForexFactory rate-limits hard (HTTP 429), and every
    # browser refresh used to hit it again. Stale copies are served if upstream fails.
    _RELAY_CACHE = {}
    RELAY_TTL = {'nfs.faireconomy.media': 900}   # seconds; everything else uses 120

    def _relay(self):
        """Generic CORS relay: /relay?url=<full https url>. Allowlisted hosts only."""
        target = self.path.split('url=', 1)[1] if 'url=' in self.path else ''
        target = urllib.parse.unquote(target) if target.startswith('https%3A') else target
        host = urllib.parse.urlparse(target).hostname or ''
        if not target.startswith('https://') or not any(host == h or host.endswith('.' + h) for h in self.RELAY_HOSTS):
            print(f'Relay → REFUSED host: {host or "(none)"}')
            self.send_response(403)
            self.send_header('Content-Type', 'application/json')
            self._cors()
            self.end_headers()
            self.wfile.write(json.dumps({'error': f'host not allowed: {host}'}).encode())
            return
        import time
        cached = self._RELAY_CACHE.get(target)
        if cached and time.time() - cached[0] < self.RELAY_TTL.get(host, 120):
            self.send_response(200)
            self.send_header('Content-Type', cached[2])
            self._cors()
            self.end_headers()
            self.wfile.write(cached[1])
            return
        try:
            req = urllib.request.Request(target, headers={
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                'Accept': 'application/json, application/xml, text/xml, */*',
            })
            with urllib.request.urlopen(req, context=ssl.create_default_context(), timeout=15) as resp:
                data  = resp.read()
                ctype = resp.headers.get('Content-Type', 'application/octet-stream')
            self._RELAY_CACHE[target] = (time.time(), data, ctype)
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self._cors()
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            code = getattr(e, 'code', 502)
            print(f'Relay → FAILED {host}: {e}')
            if cached:
                print(f'Relay → serving STALE copy for {host} ({int(time.time() - cached[0])}s old)')
                self.send_response(200)
                self.send_header('Content-Type', cached[2])
                self.send_header('X-Relay-Stale', '1')
                self._cors()
                self.end_headers()
                self.wfile.write(cached[1])
                return
            self.send_response(code if isinstance(code, int) else 502)
            self.send_header('Content-Type', 'application/json')
            self._cors()
            self.end_headers()
            self.wfile.write(json.dumps({'error': str(e), 'host': host}).encode())

    def log_message(self, fmt, *args):
        pass  # suppress access logs

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded server so concurrent page requests don't block each other."""
    daemon_threads = True

if __name__ == '__main__':
    os.chdir(BASE_DIR)
    httpd = ThreadedHTTPServer(('', PORT), Handler)
    print(f'\n  COINDIVA server running → http://localhost:{PORT}/MacroTerminal.html')
    print(f'  Stooq proxy             → http://localhost:{PORT}/stooq?sym=^SPX')
    print(f'  Reuters proxy           → http://localhost:{PORT}/reuters?q=bitcoin')
    print(f'  Multi-threaded: YES')
    print(f'  Press Ctrl+C to stop.\n')
    httpd.serve_forever()
