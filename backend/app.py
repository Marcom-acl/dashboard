"""
ACL Marcom Dashboard — Backend API (Railway)
============================================================
Adapted from the local proxy.py for cloud deployment.
All secrets are read from environment variables.
"""

import os
import json
import re
import time
import datetime
import threading
import requests
import concurrent.futures
from flask import Flask, request, jsonify, redirect, session
from flask_cors import CORS

# ── SSL bypass (optional, for Zscaler corporate proxy) ──────────────────────
if os.environ.get('DISABLE_SSL_VERIFY', 'false').lower() == 'true':
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    _VERIFY = False
else:
    _VERIFY = True

# ── App setup ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET', os.urandom(32))
CORS(app, origins='*')

# ── Constants ────────────────────────────────────────────────────────────────
VEILLE_DATA_URL      = 'https://raw.githubusercontent.com/Marcom-acl/dashboard/main/data/veille-data.json'
VEILLE_IA_DATA_URL   = 'https://raw.githubusercontent.com/Marcom-acl/dashboard/main/data/veille-ia-data.json'
SEO_POSITIONS_URL    = 'https://raw.githubusercontent.com/Marcom-acl/dashboard/main/data/seo-positions-data.json'

GA4_PROPERTY              = '267556854'
GA4_PROPERTY_AUTOTOURING  = '473431929'
GSC_SITE                  = 'sc-domain:acl.lu'
YT_CHANNEL_ID             = 'UC9LK0kbfLqZQCsgNOrz3aqA'
FB_ACCOUNTS               = ['act_1192620784140333']
FB_PAGES                  = {
    'ACL':     '213661677006',
    'Sport':   '900728663131402',
    'Karting': '963910666805565',
}

# ── Generic TTL cache ─────────────────────────────────────────────────────────
_API_CACHE      = {}
_API_CACHE_LOCK = threading.Lock()

def _cache_get(key):
    now = time.time()
    with _API_CACHE_LOCK:
        e = _API_CACHE.get(key)
    if e and now - e['ts'] < e['ttl']:
        return {**e['data'], '_cached': True, '_cacheAge': int(now - e['ts'])}
    return None

def _cache_set(key, data, ttl):
    with _API_CACHE_LOCK:
        _API_CACHE[key] = {'data': data, 'ts': time.time(), 'ttl': ttl}

# ── Env-var secrets ──────────────────────────────────────────────────────────
BREVO_API_KEY     = os.environ.get('BREVO_API_KEY', '')
FB_APP_ID         = os.environ.get('FB_APP_ID', '')
FB_APP_SECRET     = os.environ.get('FB_APP_SECRET', '')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
GOOGLE_CLIENT_ID     = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
GOOGLE_REFRESH_TOKEN = os.environ.get('GOOGLE_REFRESH_TOKEN', '')
SUPERMETRICS_API_KEY = os.environ.get('SUPERMETRICS_API_KEY', '')
BUFFER_API_KEY       = os.environ.get('BUFFER_API_KEY', '')
DATAFORSEO_LOGIN     = os.environ.get('DATAFORSEO_LOGIN', '')
DATAFORSEO_PASSWORD  = os.environ.get('DATAFORSEO_PASSWORD', '')

# ── CTR curve (expected CTR by position, used in /insights/web score_v2) ─────
_CTR_CURVE = {1:0.28, 2:0.15, 3:0.11, 4:0.08, 5:0.07, 6:0.065, 7:0.055, 8:0.045, 9:0.035, 10:0.02}

def _expected_ctr(pos):
    p = max(1, round(float(pos)))
    return _CTR_CURVE.get(p, max(0.005, 0.02 - (p - 10) * 0.0015))

# ── DataForSEO SERP helper ────────────────────────────────────────────────────
def _fetch_dataforseo_serp(keyword, location_code=2442, language_code='fr'):
    """Returns top-10 organic SERP results for keyword on google.lu (LUX = 2442)."""
    if not DATAFORSEO_LOGIN or not DATAFORSEO_PASSWORD:
        return []
    try:
        import base64
        creds = base64.b64encode(f'{DATAFORSEO_LOGIN}:{DATAFORSEO_PASSWORD}'.encode()).decode()
        payload = [{'keyword': keyword, 'location_code': location_code,
                    'language_code': language_code, 'device': 'desktop', 'depth': 10}]
        r = requests.post(
            'https://api.dataforseo.com/v3/serp/google/organic/live/regular',
            json=payload,
            headers={'Authorization': f'Basic {creds}', 'Content-Type': 'application/json'},
            timeout=15, verify=_VERIFY
        )
        if not r.ok:
            return []
        tasks = r.json().get('tasks', [])
        items = tasks[0].get('result', [{}])[0].get('items', []) if tasks else []
        return [
            {'rank': i.get('rank_absolute'), 'url': i.get('url', ''),
             'title': i.get('title', ''), 'description': i.get('description', '')}
            for i in items if i.get('type') == 'organic'
        ][:10]
    except Exception:
        return []

# ── Dashboard users (source: GitHub Gist > DASHBOARD_USERS env var > seed) ───
GITHUB_TOKEN  = os.environ.get('GITHUB_TOKEN', '')
GIST_ID       = os.environ.get('GIST_ID', '')
GIST_FILENAME = 'dashboard_users.json'
_GH_HEADERS   = lambda: {'Authorization': f'token {GITHUB_TOKEN}',
                          'Accept': 'application/vnd.github.v3+json'}

_ADMIN_USER = {'name': 'Vincent Huwer', 'email': 'vhuwer@acl.lu', 'role': 'admin',
               'hash': 'c3ad607d59cebafcfd19ca4da43b4d7ceda52350bae6f733899c49b8c3437b51'}

def _fetch_gist_users():
    if not GITHUB_TOKEN or not GIST_ID:
        return None
    try:
        r = requests.get(f'https://api.github.com/gists/{GIST_ID}',
                         headers=_GH_HEADERS(), timeout=6, verify=_VERIFY)
        if r.ok:
            content = r.json()['files'].get(GIST_FILENAME, {}).get('content', '[]')
            return json.loads(content)
    except Exception:
        pass
    return None

def _init_runtime_users():
    users = {'vhuwer@acl.lu': _ADMIN_USER}
    source = _fetch_gist_users()
    if source is None:
        raw = os.environ.get('DASHBOARD_USERS', '')
        try:
            source = json.loads(raw) if raw else []
        except Exception:
            source = []
    for u in source:
        users[u['email'].lower()] = u
    return users

_RUNTIME_USERS = _init_runtime_users()

def _non_admin_users():
    return [u for u in _RUNTIME_USERS.values() if u['email'] != 'vhuwer@acl.lu']

def _persist_users():
    if not GITHUB_TOKEN or not GIST_ID:
        return False, 'GITHUB_TOKEN ou GIST_ID manquant sur Railway'
    try:
        r = requests.patch(
            f'https://api.github.com/gists/{GIST_ID}',
            headers=_GH_HEADERS(),
            json={'files': {GIST_FILENAME: {'content': json.dumps(_non_admin_users(), ensure_ascii=False)}}},
            timeout=8, verify=_VERIFY,
        )
        if not r.ok:
            return False, f'GitHub API HTTP {r.status_code}'
        return True, None
    except Exception as e:
        return False, str(e)

# ── Storage paths ─────────────────────────────────────────────────────────────
DATA_DIR = os.environ.get('DATA_DIR', os.path.dirname(os.path.abspath(__file__)))
APP_URL  = os.environ.get('APP_URL', 'http://localhost:5050')


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get(url, **kwargs):
    """Wrapper for requests.get that respects DISABLE_SSL_VERIFY."""
    kwargs.setdefault('verify', _VERIFY)
    kwargs.setdefault('timeout', 30)
    return requests.get(url, **kwargs)

def _post(url, **kwargs):
    kwargs.setdefault('verify', _VERIFY)
    kwargs.setdefault('timeout', 30)
    return requests.post(url, **kwargs)

def _token_path(name):
    return os.path.join(DATA_DIR, name)

def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None

def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f)

def _date_range():
    """Parse start/end query params; defaults to last 30 days."""
    end_str   = request.args.get('end')
    start_str = request.args.get('start')
    today = datetime.date.today()
    if end_str:
        try:
            end = datetime.date.fromisoformat(end_str)
        except ValueError:
            end = today
    else:
        end = today
    if start_str:
        try:
            start = datetime.date.fromisoformat(start_str)
        except ValueError:
            start = end - datetime.timedelta(days=30)
    else:
        start = end - datetime.timedelta(days=30)
    return start.isoformat(), end.isoformat()

def _delta(curr, prev):
    """Percentage change from prev to curr."""
    if not prev:
        return None
    return round((curr - prev) / prev * 100, 1)


# ─────────────────────────────────────────────────────────────────────────────
# Google OAuth helpers
# ─────────────────────────────────────────────────────────────────────────────

GOOGLE_TOKEN_PATH   = _token_path('google_token.json')
GOOGLE_SECRETS_PATH = _token_path('google_client_secret.json')

GOOGLE_AUTH_URL  = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'

GOOGLE_SCOPES = [
    'https://www.googleapis.com/auth/analytics.readonly',
    'https://www.googleapis.com/auth/webmasters.readonly',
    'https://www.googleapis.com/auth/youtube.readonly',
]

def _google_secrets():
    if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
        return GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
    s = _load_json(GOOGLE_SECRETS_PATH)
    if not s:
        return None, None
    web = s.get('web') or s.get('installed') or {}
    return web.get('client_id'), web.get('client_secret')

def _google_token():
    stored = _load_json(GOOGLE_TOKEN_PATH)
    if stored:
        return stored
    if GOOGLE_REFRESH_TOKEN:
        return {'refresh_token': GOOGLE_REFRESH_TOKEN}
    return None

def _refresh_google_token(token_data):
    client_id, client_secret = _google_secrets()
    if not client_id:
        return None
    r = _post(GOOGLE_TOKEN_URL, data={
        'client_id':     client_id,
        'client_secret': client_secret,
        'refresh_token': token_data.get('refresh_token'),
        'grant_type':    'refresh_token',
    })
    if r.ok:
        new_token = {**token_data, **r.json()}
        try:
            _save_json(GOOGLE_TOKEN_PATH, new_token)
        except Exception:
            pass
        return new_token
    return None

def _get_google_access_token():
    token_data = _google_token()
    if not token_data:
        return None
    refresh_token = token_data.get('refresh_token')
    if refresh_token:
        refreshed = _refresh_google_token(token_data)
        if refreshed:
            return refreshed.get('access_token')
    return token_data.get('access_token')

def _google_headers():
    token = _get_google_access_token()
    if not token:
        return None
    return {'Authorization': f'Bearer {token}'}


# ─────────────────────────────────────────────────────────────────────────────
# Google OAuth routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/google/auth')
def google_auth():
    client_id, _ = _google_secrets()
    if not client_id:
        return jsonify({'error': 'google_client_secret.json manquant dans DATA_DIR'}), 503
    scope = ' '.join(GOOGLE_SCOPES)
    redirect_uri = f'{APP_URL}/google/callback'
    url = (
        f'{GOOGLE_AUTH_URL}?response_type=code'
        f'&client_id={client_id}'
        f'&redirect_uri={redirect_uri}'
        f'&scope={requests.utils.quote(scope)}'
        f'&access_type=offline&prompt=consent'
    )
    return redirect(url)

@app.route('/google/callback')
def google_callback():
    code = request.args.get('code')
    if not code:
        return jsonify({'error': 'Code manquant'}), 400
    client_id, client_secret = _google_secrets()
    redirect_uri = f'{APP_URL}/google/callback'
    r = _post(GOOGLE_TOKEN_URL, data={
        'code':          code,
        'client_id':     client_id,
        'client_secret': client_secret,
        'redirect_uri':  redirect_uri,
        'grant_type':    'authorization_code',
    })
    if not r.ok:
        return jsonify({'error': r.text}), 400
    token_data = r.json()
    try:
        _save_json(GOOGLE_TOKEN_PATH, token_data)
    except Exception:
        pass
    refresh_token = token_data.get('refresh_token', '')
    return f'''<!DOCTYPE html><html><body style="font-family:sans-serif;padding:2rem;max-width:600px">
<h2 style="color:#22c55e">✅ Google connecté !</h2>
<p>Copie cette valeur et ajoute-la dans Railway → Variables :</p>
<p><strong>Variable :</strong> <code>GOOGLE_REFRESH_TOKEN</code></p>
<p><strong>Valeur :</strong></p>
<textarea style="width:100%;padding:.5rem;font-family:monospace;font-size:.85rem" rows="3" onclick="this.select()">{refresh_token}</textarea>
<p style="color:#666;font-size:.85rem">Une fois sauvegardée dans Railway, tu n'auras plus besoin de reconnecter Google même après un redémarrage.</p>
</body></html>'''


# ─────────────────────────────────────────────────────────────────────────────
# Facebook OAuth helpers
# ─────────────────────────────────────────────────────────────────────────────

FB_TOKEN_PATH = _token_path('fb_token.json')
FB_AUTH_URL   = 'https://www.facebook.com/v22.0/dialog/oauth'
FB_TOKEN_URL  = 'https://graph.facebook.com/v22.0/oauth/access_token'
FB_GRAPH      = 'https://graph.facebook.com/v22.0'

FB_SCOPES = [
    'ads_read', 'read_insights', 'pages_read_engagement',
    'pages_show_list', 'business_management',
]

def _fb_token():
    stored = _load_json(FB_TOKEN_PATH)
    if stored:
        return stored.get('access_token')
    return os.environ.get('FB_TOKEN', '')

@app.route('/fb/auth')
def fb_auth():
    if not FB_APP_ID:
        return jsonify({'error': 'FB_APP_ID manquant'}), 503
    redirect_uri = f'{APP_URL}/fb/callback'
    scope = ','.join(FB_SCOPES)
    url = (
        f'{FB_AUTH_URL}?client_id={FB_APP_ID}'
        f'&redirect_uri={requests.utils.quote(redirect_uri)}'
        f'&scope={scope}&response_type=code'
    )
    return redirect(url)

@app.route('/fb/callback')
def fb_callback():
    code = request.args.get('code')
    if not code:
        return jsonify({'error': 'Code manquant'}), 400
    redirect_uri = f'{APP_URL}/fb/callback'

    # Échange code → token court (1-2h)
    r = _get(FB_TOKEN_URL, params={
        'client_id':     FB_APP_ID,
        'client_secret': FB_APP_SECRET,
        'redirect_uri':  redirect_uri,
        'code':          code,
    })
    if not r.ok:
        return jsonify({'error': r.text}), 400
    short_token = r.json().get('access_token', '')

    # Échange token court → token long (60 jours)
    r2 = _get(FB_TOKEN_URL, params={
        'grant_type':        'fb_exchange_token',
        'client_id':         FB_APP_ID,
        'client_secret':     FB_APP_SECRET,
        'fb_exchange_token': short_token,
    })
    token_data = r2.json() if r2.ok else {'access_token': short_token}
    access_token = token_data.get('access_token', short_token)
    expires_in   = token_data.get('expires_in', '?')

    _save_json(FB_TOKEN_PATH, token_data)
    return f'''<h2>✅ Facebook connecté !</h2>
<p>Token long-durée obtenu (valable ~{expires_in}s ≈ 60 jours).</p>
<hr>
<p><strong>Pour rendre la connexion permanente</strong> (survit aux redéploiements Railway) :</p>
<ol>
  <li>Copiez ce token long-durée :<br>
    <textarea rows="4" style="width:100%;font-family:monospace;font-size:11px">{access_token}</textarea>
  </li>
  <li>Sur Railway → Variables → mettez à jour :<br>
    <code>FB_TOKEN = [valeur ci-dessus]</code>
  </li>
</ol>
<p>À renouveler dans ~60 jours. Vous pouvez fermer cette fenêtre ensuite.</p>'''




# ─────────────────────────────────────────────────────────────────────────────
# Status route
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/status')
def status():
    try:
        vr = _get(VEILLE_DATA_URL, timeout=4)
        veille_ok = vr.ok
    except Exception:
        veille_ok = False
    return jsonify({
        'status':          'ok',
        'google':          bool(_google_token()),
        'facebook':        bool(_fb_token()),
        'linkedin':        bool(SUPERMETRICS_API_KEY),
        'brevo':           bool(BREVO_API_KEY),
        'buffer':          bool(BUFFER_API_KEY),
        'anthropic':       bool(ANTHROPIC_API_KEY),
        'railway_persist': bool(GITHUB_TOKEN and GIST_ID),
        'veille':          veille_ok,
        'user_count':      len(_RUNTIME_USERS),
    })


@app.route('/veille')
def veille():
    cached = _cache_get('veille')
    if cached: return jsonify(cached)
    try:
        r = _get(VEILLE_DATA_URL, timeout=10)
        if not r.ok:
            return jsonify({'error': f'GitHub raw HTTP {r.status_code}'}), 503
        data = r.json()
        _cache_set('veille', data, 3600)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 503


@app.route('/seo-positions')
def seo_positions():
    cached = _cache_get('seo-positions')
    if cached: return jsonify(cached)
    try:
        r = _get(SEO_POSITIONS_URL, timeout=10)
        if not r.ok:
            return jsonify({'error': f'GitHub raw HTTP {r.status_code}'}), 503
        data = r.json()
        _cache_set('seo-positions', data, 3600)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 503


@app.route('/api/users', methods=['GET'])
def api_users_get():
    return jsonify(list(_RUNTIME_USERS.values()))

@app.route('/api/users', methods=['POST'])
def api_users_add():
    data  = request.get_json(silent=True) or {}
    email = (data.get('email') or '').strip().lower()
    name  = (data.get('name')  or '').strip()
    hash_ = (data.get('hash')  or '').strip()
    if not email or not name or not hash_:
        return jsonify({'error': 'name, email, hash requis'}), 400
    _RUNTIME_USERS[email] = {'name': name, 'email': email,
                              'role': data.get('role', 'user'), 'hash': hash_}
    persisted, persist_err = _persist_users()
    export = json.dumps(_non_admin_users(), ensure_ascii=False)
    return jsonify({'ok': True, 'persisted': persisted,
                    'persist_error': persist_err,
                    'export': None if persisted else export})

@app.route('/api/users/<path:email>', methods=['DELETE'])
def api_users_delete(email):
    email = email.lower()
    if email == 'vhuwer@acl.lu':
        return jsonify({'error': 'Admin non supprimable'}), 400
    _RUNTIME_USERS.pop(email, None)
    persisted, persist_err = _persist_users()
    export = json.dumps(_non_admin_users(), ensure_ascii=False)
    return jsonify({'ok': True, 'persisted': persisted,
                    'persist_error': persist_err,
                    'export': None if persisted else export})


@app.route('/google/debug')
def google_debug():
    client_id, client_secret = _google_secrets()
    token_data = _google_token()
    if not token_data:
        return jsonify({'error': 'Pas de token', 'client_id': bool(client_id)})
    refresh_token = token_data.get('refresh_token')
    if not refresh_token:
        return jsonify({'error': 'Pas de refresh_token', 'token_keys': list(token_data.keys())})
    r = _post(GOOGLE_TOKEN_URL, data={
        'client_id':     client_id,
        'client_secret': client_secret,
        'refresh_token': refresh_token,
        'grant_type':    'refresh_token',
    })
    return jsonify({'status': r.status_code, 'response': r.json()})


# ─────────────────────────────────────────────────────────────────────────────
# GA4 route — acl.lu
# ─────────────────────────────────────────────────────────────────────────────

def _ga4_run_report(property_id, start, end, dimensions, metrics):
    headers = _google_headers()
    if not headers:
        return None
    url = f'https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport'
    body = {
        'dateRanges': [{'startDate': start, 'endDate': end}],
        'dimensions': [{'name': d} for d in dimensions],
        'metrics':    [{'name': m} for m in metrics],
    }
    r = _post(url, headers=headers, json=body)
    if r.ok:
        return r.json()
    return None

def _ga4_scalar(property_id, start, end, metric):
    headers = _google_headers()
    if not headers:
        return 0
    url = f'https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport'
    body = {
        'dateRanges': [{'startDate': start, 'endDate': end}],
        'metrics':    [{'name': metric}],
    }
    r = _post(url, headers=headers, json=body)
    if not r.ok:
        return 0
    data = r.json()
    try:
        return float(data['rows'][0]['metricValues'][0]['value'])
    except Exception:
        return 0

def _parse_ga4_main(property_id, start, end):
    """Return a dict with the main GA4 KPIs for a property."""
    headers = _google_headers()
    if not headers:
        return {'error': 'Google non connecté'}

    url = f'https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport'

    # Main KPIs
    main_body = {
        'dateRanges': [
            {'startDate': start, 'endDate': end},
        ],
        'metrics': [
            {'name': 'sessions'},
            {'name': 'totalUsers'},
            {'name': 'bounceRate'},
            {'name': 'averageSessionDuration'},
            {'name': 'engagementRate'},
        ],
    }
    r = _post(url, headers=headers, json=main_body)
    if not r.ok:
        return {'error': r.text[:200]}
    data = r.json()
    try:
        vals = data['rows'][0]['metricValues']
        sessions  = int(float(vals[0]['value']))
        users     = int(float(vals[1]['value']))
        bounce    = round(float(vals[2]['value']) * 100, 1)
        duration  = round(float(vals[3]['value']), 1)
        engage    = round(float(vals[4]['value']) * 100, 1)
    except Exception:
        sessions = users = 0
        bounce = duration = engage = 0.0

    # Previous period for deltas
    days = (datetime.date.fromisoformat(end) - datetime.date.fromisoformat(start)).days
    prev_end   = datetime.date.fromisoformat(start) - datetime.timedelta(days=1)
    prev_start = prev_end - datetime.timedelta(days=days)
    prev_body  = {**main_body, 'dateRanges': [{'startDate': prev_start.isoformat(), 'endDate': prev_end.isoformat()}]}
    rp = _post(url, headers=headers, json=prev_body)
    prev = {}
    if rp.ok:
        try:
            pv = rp.json()['rows'][0]['metricValues']
            prev = {
                'sessions':          int(float(pv[0]['value'])),
                'users':             int(float(pv[1]['value'])),
                'bounceRate':        round(float(pv[2]['value']) * 100, 1),
                'avgSessionDuration': round(float(pv[3]['value']), 1),
                'engagementRate':    round(float(pv[4]['value']) * 100, 1),
            }
        except Exception:
            pass

    deltas = {
        'sessions':           _delta(sessions,  prev.get('sessions')),
        'users':              _delta(users,      prev.get('users')),
        'bounceRate':         _delta(bounce,     prev.get('bounceRate')),
        'avgSessionDuration': _delta(duration,   prev.get('avgSessionDuration')),
        'engagementRate':     _delta(engage,     prev.get('engagementRate')),
    }

    # Top pages
    pages_body = {
        'dateRanges': [{'startDate': start, 'endDate': end}],
        'dimensions': [{'name': 'pagePath'}],
        'metrics':    [{'name': 'screenPageViews'}],
        'orderBys':   [{'metric': {'metricName': 'screenPageViews'}, 'desc': True}],
        'limit':      20,
    }
    rp2 = _post(url, headers=headers, json=pages_body)
    top_pages = []
    if rp2.ok:
        for row in rp2.json().get('rows', []):
            top_pages.append({
                'page':  row['dimensionValues'][0]['value'],
                'views': int(float(row['metricValues'][0]['value'])),
            })

    # Channel breakdown
    chan_body = {
        'dateRanges': [{'startDate': start, 'endDate': end}],
        'dimensions': [{'name': 'sessionDefaultChannelGroup'}],
        'metrics':    [{'name': 'sessions'}],
        'orderBys':   [{'metric': {'metricName': 'sessions'}, 'desc': True}],
    }
    rc = _post(url, headers=headers, json=chan_body)
    channels = []
    if rc.ok:
        for row in rc.json().get('rows', []):
            channels.append({
                'channel':  row['dimensionValues'][0]['value'],
                'sessions': int(float(row['metricValues'][0]['value'])),
            })

    return {
        'sessions':            sessions,
        'users':               users,
        'bounceRate':          bounce,
        'avgSessionDuration':  duration,
        'engagementRate':      engage,
        'channelBreakdown':    channels,
        'topPages':            top_pages,
        'prev':                prev,
        'deltas':              deltas,
    }


@app.route('/ga4')
def ga4():
    start, end = _date_range()
    key = f'ga4:{start}:{end}'
    cached = _cache_get(key)
    if cached: return jsonify(cached)
    data = _parse_ga4_main(GA4_PROPERTY, start, end)
    _cache_set(key, data, 600)
    return jsonify(data)


@app.route('/ga4/autotouring')
def ga4_autotouring():
    start, end = _date_range()
    key = f'ga4-auto:{start}:{end}'
    cached = _cache_get(key)
    if cached: return jsonify(cached)
    data = _parse_ga4_main(GA4_PROPERTY_AUTOTOURING, start, end)
    _cache_set(key, data, 600)
    return jsonify(data)


@app.route('/ga4/extended')
def ga4_extended():
    start, end = _date_range()
    key = f'ga4-ext:{start}:{end}'
    cached = _cache_get(key)
    if cached: return jsonify(cached)
    headers = _google_headers()
    if not headers:
        return jsonify({'error': 'Google non connecté'})

    url = f'https://analyticsdata.googleapis.com/v1beta/properties/{GA4_PROPERTY}:runReport'

    def run(dimensions, metrics, limit=20, order_metric=None):
        body = {
            'dateRanges': [{'startDate': start, 'endDate': end}],
            'dimensions': [{'name': d} for d in dimensions],
            'metrics':    [{'name': m} for m in metrics],
            'limit':      limit,
        }
        if order_metric:
            body['orderBys'] = [{'metric': {'metricName': order_metric}, 'desc': True}]
        r = _post(url, headers=headers, json=body)
        return r.json() if r.ok else {}

    # New vs returning
    nvr_data = run(['newVsReturning'], ['sessions'])
    nvr = [
        {'type': row['dimensionValues'][0]['value'], 'sessions': int(float(row['metricValues'][0]['value']))}
        for row in nvr_data.get('rows', [])
    ]

    # Markets (language)
    mkt_data = run(['language'], ['sessions', 'engagementRate', 'averageSessionDuration'], limit=10, order_metric='sessions')
    markets = []
    for row in mkt_data.get('rows', []):
        markets.append({
            'name':          row['dimensionValues'][0]['value'],
            'sessions':      int(float(row['metricValues'][0]['value'])),
            'engagementRate': round(float(row['metricValues'][1]['value']) * 100, 1),
            'duration':      round(float(row['metricValues'][2]['value']), 1),
        })

    # Entry pages
    ep_data = run(['landingPage'], ['sessions', 'bounceRate', 'engagementRate'], limit=20, order_metric='sessions')
    entry_pages = []
    for row in ep_data.get('rows', []):
        br = round(float(row['metricValues'][1]['value']) * 100, 1)
        entry_pages.append({
            'page':    row['dimensionValues'][0]['value'],
            'sessions': int(float(row['metricValues'][0]['value'])),
            'bounceRate': br,
        })

    # Key events
    evt_data = run(['eventName'], ['eventCount'], limit=20, order_metric='eventCount')
    events = [
        {'name': row['dimensionValues'][0]['value'], 'count': int(float(row['metricValues'][0]['value']))}
        for row in evt_data.get('rows', [])
    ]

    # Devices
    dev_data = run(['deviceCategory'], ['sessions', 'engagementRate'], limit=10, order_metric='sessions')
    total_sessions = sum(int(float(r['metricValues'][0]['value'])) for r in dev_data.get('rows', [])) or 1
    devices = []
    for row in dev_data.get('rows', []):
        s = int(float(row['metricValues'][0]['value']))
        devices.append({
            'name':          row['dimensionValues'][0]['value'],
            'sessions':      s,
            'pct':           round(s / total_sessions * 100, 1),
            'engagementRate': round(float(row['metricValues'][1]['value']) * 100, 1),
        })

    # Conversion by channel
    conv_data = run(['sessionDefaultChannelGroup'], ['sessions', 'conversions'], limit=10, order_metric='sessions')
    conv_by_channel = []
    for row in conv_data.get('rows', []):
        conv_by_channel.append({
            'channel':     row['dimensionValues'][0]['value'],
            'sessions':    int(float(row['metricValues'][0]['value'])),
            'conversions': int(float(row['metricValues'][1]['value'])),
        })

    data = {
        'newVsReturning':    nvr,
        'markets':           markets,
        'entryPages':        entry_pages,
        'keyEvents':         events,
        'devices':           devices,
        'conversionByChannel': conv_by_channel,
    }
    _cache_set(key, data, 600)
    return jsonify(data)


@app.route('/ga4/funnel')
def ga4_funnel():
    start, end = _date_range()
    cache_key = f'ga4-funnel:{start}:{end}'
    cached = _cache_get(cache_key)
    if cached: return jsonify(cached)

    headers = _google_headers()
    if not headers:
        return jsonify({'error': 'Google non connecté'})

    url = f'https://analyticsdata.googleapis.com/v1beta/properties/{GA4_PROPERTY}:runReport'

    def _q(body):
        return _post(url, headers=headers, json=body)

    # Step 0 — Landing adhésion (page_view sur FR/EN/DE)
    landing_paths = ['/fr/adhesion/', '/en/membership/', '/de/mitgliedschaft/']
    landing_filter = {'orGroup': {'expressions': [
        {'filter': {'fieldName': 'pagePath',
                    'stringFilter': {'matchType': 'BEGINS_WITH', 'value': p}}}
        for p in landing_paths
    ]}}
    r0 = _q({'dateRanges': [{'startDate': start, 'endDate': end}],
             'metrics': [{'name': 'screenPageViews'}],
             'dimensionFilter': landing_filter})
    step0_views = 0
    if r0.ok:
        rows = r0.json().get('rows', [])
        if rows:
            step0_views = int(float(rows[0]['metricValues'][0]['value']))

    # Steps 1–5 — Événements funnel_step1 à funnel_step5 (un seul appel)
    funnel_events = {f'funnel_step{i}': 0 for i in range(1, 6)}
    r_ev = _q({'dateRanges': [{'startDate': start, 'endDate': end}],
               'dimensions': [{'name': 'eventName'}],
               'metrics': [{'name': 'eventCount'}],
               'dimensionFilter': {'orGroup': {'expressions': [
                   {'filter': {'fieldName': 'eventName',
                               'stringFilter': {'matchType': 'EXACT', 'value': f'funnel_step{i}'}}}
                   for i in range(1, 6)
               ]}}})
    if r_ev.ok:
        for row in r_ev.json().get('rows', []):
            name = row['dimensionValues'][0]['value']
            if name in funnel_events:
                funnel_events[name] = int(float(row['metricValues'][0]['value']))

    step_defs = [
        ('step0',  'Landing adhésion (FR · EN · DE)', step0_views),
        ('step1',  'Carte choisie',                   funnel_events['funnel_step1']),
        ('step2',  'Infos personnelles remplies',      funnel_events['funnel_step2']),
        ('step3',  'Extras choisis',                   funnel_events['funnel_step3']),
        ('step4',  'Mode de paiement sélectionné',     funnel_events['funnel_step4']),
        ('step5',  'Achat confirmé',                   funnel_events['funnel_step5']),
    ]

    steps = [{'key': k, 'label': l, 'value': v} for k, l, v in step_defs]
    conversion_rate = (
        round(funnel_events['funnel_step5'] / step0_views * 100, 2)
        if step0_views and funnel_events['funnel_step5'] else 0
    )

    data = {'steps': steps, 'conversionRate': conversion_rate}
    _cache_set(cache_key, data, 600)
    return jsonify(data)


@app.route('/ga4/trend')
def ga4_trend():
    """Daily sessions + users for the selected period — powers the line chart."""
    start, end = _date_range()
    key = f'ga4-trend:{start}:{end}'
    cached = _cache_get(key)
    if cached: return jsonify(cached)
    headers = _google_headers()
    if not headers:
        return jsonify({'error': 'Google non connecté'})

    url = f'https://analyticsdata.googleapis.com/v1beta/properties/{GA4_PROPERTY}:runReport'
    body = {
        'dateRanges': [{'startDate': start, 'endDate': end}],
        'dimensions': [{'name': 'date'}],
        'metrics':    [{'name': 'sessions'}, {'name': 'totalUsers'}],
        'orderBys':   [{'dimension': {'dimensionName': 'date'}}],
    }
    r = _post(url, headers=headers, json=body)
    trend = []
    if r.ok:
        for row in r.json().get('rows', []):
            d = row['dimensionValues'][0]['value']  # YYYYMMDD
            trend.append({
                'date':     f"{d[:4]}-{d[4:6]}-{d[6:]}",
                'sessions': int(float(row['metricValues'][0]['value'])),
                'users':    int(float(row['metricValues'][1]['value'])),
            })
    data = {'trend': trend}
    _cache_set(key, data, 600)
    return jsonify(data)


@app.route('/ga4/geographic')
def ga4_geographic():
    """Top cities by engaged sessions + engagement rate + key events."""
    start, end = _date_range()
    key = f'ga4-geo:{start}:{end}'
    cached = _cache_get(key)
    if cached: return jsonify(cached)
    headers = _google_headers()
    if not headers:
        return jsonify({'error': 'Google non connecté'})

    url = f'https://analyticsdata.googleapis.com/v1beta/properties/{GA4_PROPERTY}:runReport'
    body = {
        'dateRanges': [{'startDate': start, 'endDate': end}],
        'dimensions': [{'name': 'city'}],
        'metrics': [
            {'name': 'engagedSessions'},
            {'name': 'engagementRate'},
            {'name': 'keyEvents'},
        ],
        'orderBys': [{'metric': {'metricName': 'engagedSessions'}, 'desc': True}],
        'limit': 20,
    }
    r = _post(url, headers=headers, json=body)
    cities = []
    if r.ok:
        for row in r.json().get('rows', []):
            cities.append({
                'city':            row['dimensionValues'][0]['value'],
                'engagedSessions': int(float(row['metricValues'][0]['value'])),
                'engagementRate':  round(float(row['metricValues'][1]['value']) * 100, 1),
                'keyEvents':       int(float(row['metricValues'][2]['value'])),
            })
    data = {'cities': cities}
    _cache_set(key, data, 900)
    return jsonify(data)


# Section definitions for /ga4/sections
_SITE_SECTIONS = [
    {
        'key':    'carburant',
        'label':  'Carburant',
        'prefixes': [
            '/de/mobilitat/kraftstoffpreise',
            '/fr/mobilite/prix-des-carburants',
            '/en/mobility/fuel-prices',
        ],
    },
    {
        'key':    'mobilite',
        'label':  'Services Mobilité',
        'prefixes': [
            '/fr/mobilite/diagnostic',
            '/fr/mobilite/location',
            '/fr/mobilite/controle-technique',
            '/fr/mobilite/assistance',
            '/fr/mobilite/formation',
            '/fr/mobilite/parking',
            '/de/mobilitat/diagnose',
            '/de/mobilitat/mietwagen',
            '/de/mobilitat/fahrzeugpruefung',
        ],
    },
    {
        'key':    'club',
        'label':  'Club Avantages',
        'prefixes': ['/club/', '/club'],
    },
    {
        'key':    'magazine',
        'label':  'Actualités',
        'prefixes': ['/fr/magazine/', '/de/zeitschrift/'],
    },
    {
        'key':    'karting',
        'label':  'Karting',
        'prefixes': ['/fr/loisirs/karting', '/sport/karting/'],
    },
    {
        'key':    'sport',
        'label':  'Sport Auto',
        'prefixes': [
            '/sport/',
            '/fr/loisirs/sport-automobile/',
        ],
    },
    {
        'key':    'mobilite-location',
        'label':  'Location véhicule',
        'parent': 'mobilite',
        'prefixes': ['/fr/mobilite/location', '/de/mobilitat/mietwagen'],
    },
    {
        'key':    'mobilite-diagnostic',
        'label':  'Diagnostic véhicule',
        'parent': 'mobilite',
        'prefixes': ['/fr/mobilite/diagnostic', '/de/mobilitat/diagnose'],
    },
    {
        'key':    'mobilite-velo',
        'label':  'Location vélo',
        'parent': 'mobilite',
        'prefixes': ['/fr/mobilite/location-velo', '/fr/mobilite/velo', '/fr/loisirs/velo'],
    },
    {
        'key':    'voyages',
        'label':  'Voyages',
        'prefixes': ['/fr/voyages-organises/', '/de/pauschalreisen/'],
    },
    {
        'key':    'b2b',
        'label':  'B2B',
        'prefixes': ['/business/'],
    },
]


@app.route('/ga4/sections')
def ga4_sections():
    """Performance KPIs and top pages per website section."""
    start, end = _date_range()
    key = f'ga4-sections:{start}:{end}'
    cached = _cache_get(key)
    if cached: return jsonify(cached)
    headers = _google_headers()
    if not headers:
        return jsonify({'error': 'Google non connecté'})

    url = f'https://analyticsdata.googleapis.com/v1beta/properties/{GA4_PROPERTY}:runReport'

    def run_section(section):
        # Build OR filter for all prefixes of this section
        filter_exprs = [
            {
                'filter': {
                    'fieldName': 'pagePath',
                    'stringFilter': {'matchType': 'BEGINS_WITH', 'value': pfx},
                }
            }
            for pfx in section['prefixes']
        ]
        dim_filter = (
            {'orGroup': {'expressions': filter_exprs}}
            if len(filter_exprs) > 1
            else filter_exprs[0]
        )

        # Aggregate KPIs
        kpi_body = {
            'dateRanges': [{'startDate': start, 'endDate': end}],
            'metrics': [
                {'name': 'screenPageViews'},
                {'name': 'engagedSessions'},
                {'name': 'engagementRate'},
                {'name': 'averageSessionDuration'},
                {'name': 'keyEvents'},
            ],
            'dimensionFilter': dim_filter,
        }
        rk = _post(url, headers=headers, json=kpi_body)
        views = eng_sessions = eng_rate = duration = key_events = 0
        if rk.ok:
            rows = rk.json().get('rows', [])
            if rows:
                v = rows[0]['metricValues']
                views        = int(float(v[0]['value']))
                eng_sessions = int(float(v[1]['value']))
                eng_rate     = round(float(v[2]['value']) * 100, 1)
                duration     = round(float(v[3]['value']), 1)
                key_events   = int(float(v[4]['value']))

        # Top pages
        pages_body = {
            'dateRanges': [{'startDate': start, 'endDate': end}],
            'dimensions': [{'name': 'pagePath'}],
            'metrics': [
                {'name': 'screenPageViews'},
                {'name': 'averageSessionDuration'},
                {'name': 'engagementRate'},
            ],
            'dimensionFilter': dim_filter,
            'orderBys': [{'metric': {'metricName': 'screenPageViews'}, 'desc': True}],
            'limit': 50,
        }
        rp = _post(url, headers=headers, json=pages_body)
        top_pages = []
        if rp.ok:
            for row in rp.json().get('rows', []):
                top_pages.append({
                    'page':        row['dimensionValues'][0]['value'],
                    'views':       int(float(row['metricValues'][0]['value'])),
                    'duration':    round(float(row['metricValues'][1]['value']), 1),
                    'engageRate':  round(float(row['metricValues'][2]['value']) * 100, 1),
                })

        # Channel breakdown for this section (sessions metric — same scope as sessionDefaultChannelGroup)
        chan_body = {
            'dateRanges': [{'startDate': start, 'endDate': end}],
            'dimensions': [{'name': 'sessionDefaultChannelGroup'}],
            'metrics': [{'name': 'sessions'}],
            'dimensionFilter': dim_filter,
            'orderBys': [{'metric': {'metricName': 'sessions'}, 'desc': True}],
            'limit': 8,
        }
        rc = _post(url, headers=headers, json=chan_body)
        channels = []
        if rc.ok:
            for row in rc.json().get('rows', []):
                channels.append({
                    'channel':  row['dimensionValues'][0]['value'],
                    'sessions': int(float(row['metricValues'][0]['value'])),
                })

        # Monthly trend — last 12 months
        today_dt = datetime.date.today()
        trend_start_dt = today_dt - datetime.timedelta(days=365)
        monthly_body = {
            'dateRanges': [{'startDate': trend_start_dt.isoformat(), 'endDate': today_dt.isoformat()}],
            'dimensions': [{'name': 'yearMonth'}],
            'metrics': [{'name': 'screenPageViews'}, {'name': 'engagedSessions'}],
            'dimensionFilter': dim_filter,
            'orderBys': [{'dimension': {'dimensionName': 'yearMonth'}, 'desc': False}],
            'limit': 14,
        }
        rm = _post(url, headers=headers, json=monthly_body)
        monthly = []
        if rm.ok:
            for row in rm.json().get('rows', []):
                ym = row['dimensionValues'][0]['value']  # e.g. "202406"
                monthly.append({
                    'month':   f"{ym[:4]}-{ym[4:]}",
                    'views':   int(float(row['metricValues'][0]['value'])),
                    'sessions': int(float(row['metricValues'][1]['value'])),
                })

        # Previous period KPIs (same duration, immediately before current period)
        days_n = (datetime.date.fromisoformat(end) - datetime.date.fromisoformat(start)).days
        prev_end_dt   = datetime.date.fromisoformat(start) - datetime.timedelta(days=1)
        prev_start_dt = prev_end_dt - datetime.timedelta(days=days_n)
        prev_kpi_body = {
            'dateRanges': [{'startDate': prev_start_dt.isoformat(), 'endDate': prev_end_dt.isoformat()}],
            'metrics': [
                {'name': 'screenPageViews'},
                {'name': 'engagedSessions'},
                {'name': 'engagementRate'},
                {'name': 'averageSessionDuration'},
                {'name': 'keyEvents'},
            ],
            'dimensionFilter': dim_filter,
        }
        rp2 = _post(url, headers=headers, json=prev_kpi_body)
        prev_kpis = None
        if rp2.ok:
            prev_rows = rp2.json().get('rows', [])
            if prev_rows:
                pv = prev_rows[0]['metricValues']
                prev_kpis = {
                    'views':           int(float(pv[0]['value'])),
                    'engagedSessions': int(float(pv[1]['value'])),
                    'engagementRate':  round(float(pv[2]['value']) * 100, 1),
                    'avgDuration':     round(float(pv[3]['value']), 1),
                    'keyEvents':       int(float(pv[4]['value'])),
                }

        # Geographic breakdown and new-vs-returning — only for parent sections (skip sub-sections)
        geo = []
        nvr = {}
        if not section.get('parent'):
            geo_body = {
                'dateRanges': [{'startDate': start, 'endDate': end}],
                'dimensions': [{'name': 'country'}],
                'metrics': [{'name': 'screenPageViews'}],
                'dimensionFilter': dim_filter,
                'orderBys': [{'metric': {'metricName': 'screenPageViews'}, 'desc': True}],
                'limit': 5,
            }
            rg = _post(url, headers=headers, json=geo_body)
            if rg.ok:
                for row in rg.json().get('rows', []):
                    geo.append({
                        'country': row['dimensionValues'][0]['value'],
                        'views':   int(float(row['metricValues'][0]['value'])),
                    })

            nvr_body = {
                'dateRanges': [{'startDate': start, 'endDate': end}],
                'dimensions': [{'name': 'newVsReturning'}],
                'metrics': [{'name': 'sessions'}],
                'dimensionFilter': dim_filter,
                'limit': 3,
            }
            rnvr = _post(url, headers=headers, json=nvr_body)
            if rnvr.ok:
                for row in rnvr.json().get('rows', []):
                    k = row['dimensionValues'][0]['value']  # 'new' or 'returning'
                    nvr[k] = int(float(row['metricValues'][0]['value']))

        return {
            'key':            section['key'],
            'label':          section['label'],
            'parent':         section.get('parent'),
            'views':          views,
            'engagedSessions': eng_sessions,
            'engagementRate': eng_rate,
            'avgDuration':    duration,
            'keyEvents':      key_events,
            'topPages':       top_pages,
            'channels':       channels,
            'geo':            geo,
            'newVsReturning': nvr,
            'monthly':        monthly,
            'prevKpis':       prev_kpis,
        }

    # Run all sections in parallel
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(run_section, s): s for s in _SITE_SECTIONS}
        for fut in concurrent.futures.as_completed(futures):
            try:
                results.append(fut.result())
            except Exception:
                pass

    # Sort to match original section order
    order = {s['key']: i for i, s in enumerate(_SITE_SECTIONS)}
    results.sort(key=lambda x: order.get(x['key'], 99))

    data = {'sections': results}
    _cache_set(key, data, 1800)
    return jsonify(data)


# ─────────────────────────────────────────────────────────────────────────────
# Google Search Console
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/gsc')
def gsc():
    start, end = _date_range()
    key = f'gsc:{start}:{end}'
    cached = _cache_get(key)
    if cached: return jsonify(cached)
    # GSC requires at least 3 days lag; enforce minimum 28 day window for stable data
    headers = _google_headers()
    if not headers:
        return jsonify({'error': 'Google non connecté'})

    base_url = f'https://www.googleapis.com/webmasters/v3/sites/{requests.utils.quote(GSC_SITE, safe="")}/searchAnalytics/query'

    def query(body):
        r = _post(base_url, headers=headers, json=body)
        return r.json() if r.ok else {}

    # Main KPIs
    main = query({
        'startDate': start, 'endDate': end,
        'rowLimit': 1,
    })
    totals = main.get('rows', [{}])[0]
    clicks      = int(totals.get('clicks', 0))
    impressions = int(totals.get('impressions', 0))
    ctr         = round(totals.get('ctr', 0) * 100, 2)
    position    = round(totals.get('position', 0), 1)

    # Previous period
    days = (datetime.date.fromisoformat(end) - datetime.date.fromisoformat(start)).days
    prev_end   = datetime.date.fromisoformat(start) - datetime.timedelta(days=1)
    prev_start = prev_end - datetime.timedelta(days=days)
    prev_data  = query({'startDate': prev_start.isoformat(), 'endDate': prev_end.isoformat(), 'rowLimit': 1})
    prev_row   = prev_data.get('rows', [{}])[0]
    prev = {
        'clicks':      int(prev_row.get('clicks', 0)),
        'impressions': int(prev_row.get('impressions', 0)),
        'ctr':         round(prev_row.get('ctr', 0) * 100, 2),
        'avgPosition': round(prev_row.get('position', 0), 1),
    }
    deltas = {
        'clicks':      _delta(clicks,      prev['clicks']),
        'impressions': _delta(impressions, prev['impressions']),
        'ctr':         _delta(ctr,         prev['ctr']),
        'avgPosition': _delta(position,    prev['avgPosition']),
    }

    # Top queries
    q_data = query({
        'startDate': start, 'endDate': end,
        'dimensions': ['query'],
        'rowLimit': 25,
        'orderBy': [{'fieldName': 'clicks', 'sortOrder': 'DESCENDING'}],
    })
    top_queries = []
    for row in q_data.get('rows', []):
        top_queries.append({
            'query':       row['keys'][0],
            'clicks':      int(row.get('clicks', 0)),
            'impressions': int(row.get('impressions', 0)),
            'ctr':         round(row.get('ctr', 0) * 100, 2),
            'position':    round(row.get('position', 0), 1),
            'evolution':   None,
        })

    # Top pages
    p_data = query({
        'startDate': start, 'endDate': end,
        'dimensions': ['page'],
        'rowLimit': 25,
        'orderBy': [{'fieldName': 'clicks', 'sortOrder': 'DESCENDING'}],
    })
    top_pages = []
    for row in p_data.get('rows', []):
        top_pages.append({
            'page':        row['keys'][0],
            'clicks':      int(row.get('clicks', 0)),
            'impressions': int(row.get('impressions', 0)),
            'ctr':         round(row.get('ctr', 0) * 100, 2),
            'position':    round(row.get('position', 0), 1),
        })

    # Trend (daily clicks over period)
    trend_data = query({
        'startDate': start, 'endDate': end,
        'dimensions': ['date'],
        'rowLimit': 500,
        'orderBy': [{'fieldName': 'date', 'sortOrder': 'ASCENDING'}],
    })
    trend = [
        {'date': row['keys'][0], 'clicks': int(row.get('clicks', 0)), 'impressions': int(row.get('impressions', 0))}
        for row in trend_data.get('rows', [])
    ]

    # Opportunities: high impressions, low CTR, position 4-20
    opportunities = [
        {
            'page':        p['page'],
            'impressions': p['impressions'],
            'ctr':         p['ctr'],
            'position':    p['position'],
            'potential':   int(p['impressions'] * 0.05),  # est. clicks at 5% CTR
        }
        for p in top_pages
        if p['impressions'] > 500 and p['ctr'] < 3 and 4 <= p['position'] <= 20
    ]

    data = {
        'clicks':      clicks,
        'impressions': impressions,
        'ctr':         ctr,
        'avgPosition': position,
        'prev':        prev,
        'deltas':      deltas,
        'topQueries':  top_queries,
        'topPages':    top_pages,
        'trend':       trend,
        'opportunities': opportunities,
    }
    _cache_set(key, data, 900)
    return jsonify(data)


@app.route('/gsc/compare')
def gsc_compare():
    """Two-period GSC comparison: current period vs N-1 (90 days before)."""
    start, end = _date_range()
    key = f'gsc-cmp:{start}:{end}'
    cached = _cache_get(key)
    if cached: return jsonify(cached)
    headers = _google_headers()
    if not headers:
        return jsonify({'error': 'Google non connecté'})

    base_url = f'https://www.googleapis.com/webmasters/v3/sites/{requests.utils.quote(GSC_SITE, safe="")}/searchAnalytics/query'

    def query(body):
        r = _post(base_url, headers=headers, json=body)
        return r.json() if r.ok else {}

    days = (datetime.date.fromisoformat(end) - datetime.date.fromisoformat(start)).days
    # Previous period: same duration, ending 90 days before current start
    prev_end_dt   = datetime.date.fromisoformat(start) - datetime.timedelta(days=3)
    prev_start_dt = prev_end_dt - datetime.timedelta(days=days)
    p_start = prev_start_dt.isoformat()
    p_end   = prev_end_dt.isoformat()

    def build_period(s, e):
        # KPIs
        main = query({'startDate': s, 'endDate': e, 'rowLimit': 1})
        row = main.get('rows', [{}])[0]
        kpis = {
            'clicks':      int(row.get('clicks', 0)),
            'impressions': int(row.get('impressions', 0)),
            'ctr':         round(row.get('ctr', 0) * 100, 2),
            'avgPosition': round(row.get('position', 0), 1),
            'startDate':   s,
            'endDate':     e,
        }
        # Top queries
        q_data = query({
            'startDate': s, 'endDate': e,
            'dimensions': ['query'],
            'rowLimit': 15,
            'orderBy': [{'fieldName': 'clicks', 'sortOrder': 'DESCENDING'}],
        })
        queries = [{
            'query':       r2['keys'][0],
            'clicks':      int(r2.get('clicks', 0)),
            'impressions': int(r2.get('impressions', 0)),
            'ctr':         round(r2.get('ctr', 0) * 100, 2),
            'position':    round(r2.get('position', 0), 1),
        } for r2 in q_data.get('rows', [])]
        # Trend (daily clicks)
        t_data = query({
            'startDate': s, 'endDate': e,
            'dimensions': ['date'],
            'rowLimit': 500,
            'orderBy': [{'fieldName': 'date', 'sortOrder': 'ASCENDING'}],
        })
        trend = [
            {'date': r2['keys'][0], 'clicks': int(r2.get('clicks', 0)), 'impressions': int(r2.get('impressions', 0))}
            for r2 in t_data.get('rows', [])
        ]
        return {'kpis': kpis, 'topQueries': queries, 'trend': trend}

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        fut_curr = ex.submit(build_period, start, end)
        fut_prev = ex.submit(build_period, p_start, p_end)
        current  = fut_curr.result()
        previous = fut_prev.result()

    data = {'current': current, 'previous': previous}
    _cache_set(key, data, 900)
    return jsonify(data)


# ─────────────────────────────────────────────────────────────────────────────
# Facebook Ads
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/fb')
def fb_ads():
    start, end = _date_range()
    key = f'fb:{start}:{end}'
    cached = _cache_get(key)
    if cached: return jsonify(cached)
    token = _fb_token()
    if not token:
        return jsonify({'error': 'Facebook non connecté — visiter /fb/auth'})

    params = {
        'access_token': token,
        'time_range':   json.dumps({'since': start, 'until': end}),
        'fields':       'spend,impressions,clicks,actions,ctr,cpc,cpm',
        'level':        'account',
    }
    totals = {'spend': 0, 'impressions': 0, 'clicks': 0, 'ctr': 0, 'cpc': 0, 'cpm': 0, 'conversions': 0}
    campaigns = []

    for account_id in FB_ACCOUNTS:
        # Account totals
        r = _get(f'{FB_GRAPH}/{account_id}/insights', params=params)
        if r.ok:
            rows = r.json().get('data', [])
            if rows:
                d = rows[0]
                totals['spend']       += float(d.get('spend', 0))
                totals['impressions'] += int(d.get('impressions', 0))
                totals['clicks']      += int(d.get('clicks', 0))
                totals['ctr']          = float(d.get('ctr', 0))
                totals['cpc']          = float(d.get('cpc', 0))
                totals['cpm']          = float(d.get('cpm', 0))
                for action in d.get('actions', []):
                    if action.get('action_type') == 'purchase':
                        totals['conversions'] += float(action.get('value', 0))

        # Campaign level
        cp = {**params, 'level': 'campaign', 'fields': 'campaign_name,spend,impressions,actions'}
        rc = _get(f'{FB_GRAPH}/{account_id}/insights', params=cp)
        if rc.ok:
            for row in rc.json().get('data', []):
                revenue = sum(float(a.get('value', 0)) for a in row.get('actions', []) if a.get('action_type') == 'purchase')
                spend   = float(row.get('spend', 0))
                campaigns.append({
                    'name':        row.get('campaign_name', ''),
                    'spend':       round(spend, 2),
                    'impressions': int(row.get('impressions', 0)),
                    'roas':        round(revenue / spend, 2) if spend else 0,
                })

    spend = round(totals['spend'], 2)
    revenue = totals['conversions']
    roas = round(revenue / spend, 2) if spend else 0

    some_impr  = sum(c['impressions'] for c in campaigns if 'some' in c['name'].lower())
    other_impr = sum(c['impressions'] for c in campaigns if 'some' not in c['name'].lower())

    data = {
        'spend':            spend,
        'impressions':      totals['impressions'],
        'someImpressions':  some_impr,
        'otherImpressions': other_impr,
        'clicks':      totals['clicks'],
        'conversions': round(revenue, 2),
        'ctr':         round(totals['ctr'], 2),
        'cpc':         round(totals['cpc'], 2),
        'cpm':         round(totals['cpm'], 2),
        'roas':        roas,
        'topCampaigns':     sorted(campaigns, key=lambda x: x['spend'],              reverse=True),
        'topByImpressions': sorted(campaigns, key=lambda x: x.get('impressions', 0), reverse=True)[:5],
        'topByRoas':        sorted(campaigns, key=lambda x: x.get('roas', 0),        reverse=True)[:5],
    }
    _cache_set(key, data, 600)
    return jsonify(data)


@app.route('/fb/page')
def fb_page():
    start, end = _date_range()
    key = f'fb-page:{start}:{end}'
    cached = _cache_get(key)
    if cached: return jsonify(cached)
    token = _fb_token()  # peut être None — la route continue sans token

    pages_data = []
    all_posts  = []
    impressions_total  = 0
    engaged_total      = 0
    sm_errors    = []
    graph_errors = []

    # Supermetrics posts_count — cache séparé 24 h pour rester sous la limite de 100 requêtes/jour
    _SKIP = {'', 'Post ID', 'post_ID'}
    sm_key = f'fb-sm-posts:{start}:{end}'
    sm_posts_by_page = _cache_get(sm_key)
    if sm_posts_by_page is None:
        sm_posts_by_page = {}
        for pname, pid in FB_PAGES.items():
            rows_sm, schema_sm, err_sm = _supermetrics_query(
                'FB', pid,
                ['post_ID', 'post_reactions_total'],
                start, end,
                max_rows=1000,
                settings={'include_all_published_posts': 'true'},
            )
            if err_sm:
                sm_errors.append(f'{pname}: {err_sm}')
                sm_posts_by_page[pid] = 0
            else:
                sm_posts_by_page[pid] = len([r for r in (rows_sm or []) if r and str(r[0]) not in _SKIP])
        _cache_set(sm_key, sm_posts_by_page, 86400)  # 24 h

    for page_name, page_id in FB_PAGES.items():
        fans = 0
        if token:
            # Fan count
            ri = _get(f'{FB_GRAPH}/{page_id}', params={'fields': 'fan_count,followers_count', 'access_token': token})
            if ri.ok:
                d = ri.json()
                fans = d.get('fan_count') or d.get('followers_count', 0)
            else:
                graph_errors.append(f'{page_name} fans: HTTP {ri.status_code} {ri.text[:120]}')

            # Insights — chaque metric séparément pour isoler les échecs
            for metric, target in [
                ('page_impressions',   'impressions'),
                ('page_engaged_users', 'engaged'),
            ]:
                rin = _get(f'{FB_GRAPH}/{page_id}/insights', params={
                    'metric':       metric,
                    'period':       'day',
                    'since':        start,
                    'until':        end,
                    'access_token': token,
                })
                if rin.ok:
                    for md in rin.json().get('data', []):
                        val = sum(v.get('value', 0) for v in md.get('values', []))
                        if target == 'impressions':
                            impressions_total += val
                        elif target == 'engaged':
                            engaged_total += val
                else:
                    graph_errors.append(f'{page_name} insights/{metric}: HTTP {rin.status_code} {rin.text[:100]}')

            # Top posts (détail pour l'affichage)
            rp = _get(f'{FB_GRAPH}/{page_id}/posts', params={
                'fields':       'message,created_time,likes.summary(true),comments.summary(true)',
                'since':        start,
                'until':        end,
                'access_token': token,
                'limit':        25,
            })
            if rp.ok:
                for post in rp.json().get('data', []):
                    likes    = post.get('likes',    {}).get('summary', {}).get('total_count', 0)
                    comments = post.get('comments', {}).get('summary', {}).get('total_count', 0)
                    all_posts.append({
                        'page':       page_name,
                        'message':    (post.get('message') or '')[:80],
                        'likes':      likes,
                        'comments':   comments,
                        'engagement': likes + comments,
                    })
            else:
                graph_errors.append(f'{page_name} posts: HTTP {rp.status_code} {rp.text[:120]}')

        pages_data.append({'name': page_name, 'id': page_id, 'fans': fans,
                           'posts_count': sm_posts_by_page.get(page_id, 0)})

    all_posts.sort(key=lambda x: x['engagement'], reverse=True)

    data = {
        'pages':    pages_data,
        'totals':   {
            'impressions':       impressions_total,
            'engaged_users':     engaged_total,
        },
        'topPosts': all_posts[:10],
        'ytd':      {},
        '_debug': {
            'fb_token':     bool(token),
            'sm_errors':    sm_errors or None,
            'graph_errors': graph_errors or None,
        },
    }
    _cache_set(key, data, 900)
    return jsonify(data)


# ─────────────────────────────────────────────────────────────────────────────
# Brevo
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/brevo')
def brevo():
    if not BREVO_API_KEY:
        return jsonify({'error': 'BREVO_API_KEY manquante'})

    start, end = _date_range()
    key = 'brevo:recent'  # indépendant de la période — les campagnes sont triées par date d'envoi
    cached = _cache_get(key)
    if cached: return jsonify(cached)

    try:
        headers = {'api-key': BREVO_API_KEY, 'Accept': 'application/json'}
        BREVO_BASE = 'https://api.brevo.com/v3'

        # Verify API key is valid before proceeding
        rcheck = _get(f'{BREVO_BASE}/account', headers=headers)
        if not rcheck.ok:
            try:
                detail = rcheck.json().get('message', rcheck.text[:200])
            except Exception:
                detail = rcheck.text[:200]
            return jsonify({'error': f'Brevo API inaccessible (HTTP {rcheck.status_code}): {detail}'})

        # Fetch last 200 sent campaigns — sans filtre de date (Brevo filtre par date de
        # création, pas d'envoi, ce qui cause des résultats vides si les campagnes ont
        # été créées en dehors de la fenêtre sélectionnée)
        MAX_CAMPAIGNS = 200
        campaigns = []
        offset = 0
        while len(campaigns) < MAX_CAMPAIGNS:
            rc = _get(f'{BREVO_BASE}/emailCampaigns', headers=headers, params={
                'status': 'sent', 'limit': 50, 'offset': offset,
                'sort': 'desc', 'statistics': 'globalStats',
            })
            if not rc.ok:
                break
            try:
                batch = rc.json().get('campaigns', [])
            except Exception:
                break
            for c in batch:
                stats = c.get('statistics', {})
                gs = stats.get('globalStats', {})
                # Brevo v3 uses 'uniqueViews' for opens — normalise to 'uniqueOpens'
                if 'uniqueOpens' not in gs and 'uniqueViews' in gs:
                    gs['uniqueOpens'] = gs['uniqueViews']
                campaigns.append({
                    'id':         c.get('id'),
                    'name':       c.get('name'),
                    'sentDate':   c.get('sentDate'),
                    'statistics': stats,
                })
            if len(batch) < 50:
                break
            offset += 50

        # Contact stats — liste #64 (membres ACL)
        LIST_ID = 64
        rl = _get(f'{BREVO_BASE}/contacts/lists/{LIST_ID}', headers=headers)
        try:
            list_data = rl.json() if rl.ok else {}
        except Exception:
            list_data = {}
        subscribed_64  = list_data.get('totalSubscribers', 0)
        blacklisted_64 = list_data.get('totalBlacklisted', 0)
        total_64       = subscribed_64 + blacklisted_64

        # Hard bounces — 6 derniers mois via smtp/statistics/globalStats
        today_str       = datetime.datetime.utcnow().strftime('%Y-%m-%d')
        six_months_ago  = (datetime.datetime.utcnow() - datetime.timedelta(days=180)).strftime('%Y-%m-%d')
        rh = _get(f'{BREVO_BASE}/smtp/statistics/globalStats', headers=headers,
                  params={'startDate': six_months_ago, 'endDate': today_str})
        try:
            hard_bounces = rh.json().get('hardBounces', 0) if rh.ok else 0
        except Exception:
            hard_bounces = 0

        # Désinscriptions totales — cumul depuis 2015
        ru = _get(f'{BREVO_BASE}/smtp/statistics/globalStats', headers=headers,
                  params={'startDate': '2015-01-01', 'endDate': today_str})
        try:
            global_unsubscribed = ru.json().get('unsubscriptions', 0) if ru.ok else 0
        except Exception:
            global_unsubscribed = 0

        contact_stats = {
            'total':        total_64,
            'subscribed':   subscribed_64,
            'blacklisted':  blacklisted_64,
            'hardBounces':  hard_bounces,
            'unsubscribed': global_unsubscribed,
        }

        # Compute avgOpenRate from campaigns
        total_dlvr  = sum(c['statistics'].get('globalStats', {}).get('delivered', 0) for c in campaigns)
        total_opens = sum(
            c['statistics'].get('globalStats', {}).get('uniqueOpens', 0)
            or c['statistics'].get('globalStats', {}).get('uniqueViews', 0)
            for c in campaigns
        )
        avg_open_rate = round(total_opens / total_dlvr * 100, 1) if total_dlvr else 0

        data = {'campaigns': campaigns, 'contactStats': contact_stats, 'avgOpenRate': avg_open_rate}
        _cache_set(key, data, 600)
        return jsonify(data)

    except Exception as e:
        return jsonify({'error': f'Erreur interne Brevo: {str(e)}'})


# ─────────────────────────────────────────────────────────────────────────────
# Brevo — ACL Club  (cache en mémoire, refresh en arrière-plan)
# ─────────────────────────────────────────────────────────────────────────────

_CLUB_CACHE               = {'data': None, 'ts': 0.0}
_CLUB_CACHE_LOCK          = threading.Lock()
_CLUB_REFRESH_IN_PROGRESS = False
_CLUB_CACHE_TTL           = 3600  # 1 heure


def _brevo_club_refresh():
    """Calcule les données Brevo Club et met à jour le cache. Toujours exécuté en thread daemon."""
    global _CLUB_REFRESH_IN_PROGRESS
    try:
        with app.app_context():
            data = _brevo_club_compute()
        with _CLUB_CACHE_LOCK:
            _CLUB_CACHE['data'] = data
            _CLUB_CACHE['ts']   = time.time()
    except Exception as e:
        # Stocker l'erreur en cache avec TTL court (2 min) pour forcer un retry rapide
        with _CLUB_CACHE_LOCK:
            _CLUB_CACHE['data'] = {'error': f'Calcul Brevo Club échoué : {e}', '_computeFailed': True}
            _CLUB_CACHE['ts']   = time.time() - (_CLUB_CACHE_TTL - 120)
    finally:
        _CLUB_REFRESH_IN_PROGRESS = False


@app.route('/brevo/thematic-lists')
def brevo_thematic_lists():
    """Returns active subscriber counts for thematic newsletter lists (FR/DE/EN)."""
    key = 'brevo-thematic-lists'
    cached = _cache_get(key)
    if cached: return jsonify(cached)

    THEMATIC_IDS = {
        'motos':     {'fr': 198, 'de': 199, 'en': 200},
        'camping':   {'fr': 48,  'de': 51,  'en': 52},
        'velo':      {'fr': 12,  'de': 31,  'en': 21},
        'oldtimers': {'fr': 10,  'de': 33,  'en': 18},
        'voyages':   {'fr': 8,   'de': 30,  'en': 19},
        'sport':     {'fr': 75},
    }
    LABELS = {
        'motos': 'Motos', 'camping': 'Camping', 'velo': 'Vélo',
        'oldtimers': 'Oldtimers', 'voyages': 'Voyages', 'sport': 'Sport',
    }

    BREVO_BASE = 'https://api.brevo.com/v3'
    headers = {'api-key': BREVO_API_KEY, 'accept': 'application/json'}

    def fetch_list(list_id):
        try:
            r = requests.get(f'{BREVO_BASE}/contacts/lists/{list_id}',
                             headers=headers, timeout=8, verify=_VERIFY)
            if r.ok:
                data = r.json()
                return data.get('subscriberCount', data.get('totalSubscribers', 0))
        except Exception:
            pass
        return None

    # Collect all (theme, lang, list_id) triples
    tasks = [(theme, lang, lid) for theme, langs in THEMATIC_IDS.items()
             for lang, lid in langs.items()]

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        future_map = {ex.submit(fetch_list, lid): (theme, lang) for theme, lang, lid in tasks}
        for fut, (theme, lang) in future_map.items():
            count = fut.result()
            if theme not in results:
                results[theme] = {'label': LABELS[theme]}
            results[theme][lang] = count if count is not None else 0

    # Compute totals
    for theme in results:
        results[theme]['total'] = sum(
            v for k, v in results[theme].items() if k in ('fr', 'de', 'en') and isinstance(v, int)
        )

    _cache_set(key, results, 3600)
    return jsonify(results)


@app.route('/brevo/club')
def brevo_club():
    global _CLUB_REFRESH_IN_PROGRESS
    if not BREVO_API_KEY:
        return jsonify({'error': 'BREVO_API_KEY manquante'})

    now = time.time()
    with _CLUB_CACHE_LOCK:
        cached_data = _CLUB_CACHE['data']
        cached_ts   = _CLUB_CACHE['ts']

    # Cache frais → réponse immédiate
    if cached_data and (now - cached_ts) < _CLUB_CACHE_TTL:
        return jsonify({**cached_data, '_cached': True, '_cacheAge': int(now - cached_ts)})

    # Pas encore de cache → déclenche le calcul en arrière-plan et retourne 202
    if not cached_data:
        if not _CLUB_REFRESH_IN_PROGRESS:
            _CLUB_REFRESH_IN_PROGRESS = True
            threading.Thread(target=_brevo_club_refresh, daemon=True).start()
        return jsonify({'_loading': True,
                        'message': 'Données en cours de chargement — recharger dans 60 s'}), 202

    # Cache périmé → sert les données existantes et lance un refresh silencieux
    if not _CLUB_REFRESH_IN_PROGRESS:
        _CLUB_REFRESH_IN_PROGRESS = True
        threading.Thread(target=_brevo_club_refresh, daemon=True).start()
    return jsonify({**cached_data, '_cached': True,
                    '_cacheAge': int(now - cached_ts), '_stale': True})


def _brevo_club_compute():
    BREVO_BASE       = 'https://api.brevo.com/v3'
    EXCLUDED_DOMAINS = {'acl.lu', 'epic.net'}
    EXCLUDED_EMAILS  = {'pierreyvesmeert@gmail.com', 'conrardykim@gmail.com'}
    headers          = {'api-key': BREVO_API_KEY, 'Accept': 'application/json'}

    now           = datetime.datetime.utcnow()
    current_month = now.strftime('%Y-%m')
    current_year  = now.strftime('%Y')
    ytd_start     = f'{current_year}-01-01'
    today         = now.strftime('%Y-%m-%d')

    def is_excluded(email):
        e = email.lower().strip()
        return e.split('@')[-1] in EXCLUDED_DOMAINS or e in EXCLUDED_EMAILS

    def offer_from_name(tpl_name):
        n = re.sub(r'^CLUB_', '', tpl_name, flags=re.IGNORECASE)
        n = re.sub(r'_(?:Membre|Partner).*$', '', n, flags=re.IGNORECASE)
        for m in ('janvier','fevrier','mars','avril','mai','juin',
                  'juillet','aout','septembre','octobre','novembre','decembre'):
            n = re.sub(rf'_{m}$', '', n, flags=re.IGNORECASE)
        return n.strip('_ ') or tpl_name

    # 1. Discover CLUB_*_Membre_* templates (active + inactive)
    templates_by_offer = {}
    offset = 0
    try:
        while True:
            rc = _get(f'{BREVO_BASE}/smtp/templates', headers=headers,
                      params={'limit': 50, 'offset': offset})
            if not rc.ok:
                break
            batch = rc.json().get('templates', [])
            if not batch:
                break
            for t in batch:
                n = t.get('name', '')
                if (re.match(r'^CLUB_', n)
                        and re.search(r'_Membre', n, re.IGNORECASE)
                        and 'TEST' not in n.upper()):
                    offer = offer_from_name(n)
                    templates_by_offer.setdefault(offer, []).append(t['id'])
            if len(batch) < 50:
                break
            offset += 50
    except Exception:
        pass

    all_tids     = [tid for tids in templates_by_offer.values() for tid in tids]
    tid_to_offer = {tid: offer
                    for offer, tids in templates_by_offer.items()
                    for tid in tids}

    # 2. Date chunks (Brevo max 30 days per query)
    def date_chunks(start_str, end_str, chunk=28):
        s = datetime.datetime.strptime(start_str, '%Y-%m-%d').date()
        e = datetime.datetime.strptime(end_str,   '%Y-%m-%d').date()
        out = []
        cur = s
        while cur <= e:
            out.append((str(cur), str(min(cur + datetime.timedelta(days=chunk - 1), e))))
            cur += datetime.timedelta(days=chunk)
        return out

    chunks = date_chunks(ytd_start, today)

    # 3a. Reach — /smtp/emails per (templateId, chunk)
    def fetch_reach(args):
        tid, cs, ce = args
        results, off = [], 0
        try:
            while True:
                r = _get(f'{BREVO_BASE}/smtp/emails', headers=headers, params={
                    'templateId': tid, 'startDate': cs, 'endDate': ce,
                    'limit': 500, 'offset': off, 'sort': 'desc',
                })
                if not r.ok:
                    break
                batch = r.json().get('transactionalEmails', [])
                if not batch:
                    break
                results.extend(batch)
                if len(batch) < 500:
                    break
                off += 500
        except Exception:
            pass
        return tid, results

    # 3a-only: fetch reach first (serial per batch of 20)
    reach_tasks     = [(tid, cs, ce) for tid in all_tids for cs, ce in chunks]
    template_reach  = {tid: [] for tid in all_tids}

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        for tid, emails in ex.map(fetch_reach, reach_tasks):
            template_reach[tid].extend(emails)

    # 3b. Open events via /smtp/statistics/events?templateId=X&event=uniqueOpened
    active_tids = [tid for tid in all_tids if template_reach[tid]]

    def fetch_open_events(args):
        tid, cs, ce = args
        results, off = [], 0
        try:
            while True:
                r = _get(f'{BREVO_BASE}/smtp/statistics/events', headers=headers,
                         params={'templateId': tid, 'startDate': cs, 'endDate': ce,
                                 'event': 'uniqueOpened', 'limit': 500, 'offset': off,
                                 'sort': 'desc'})
                if not r.ok:
                    break
                batch = r.json().get('events', [])
                if not batch:
                    break
                results.extend(batch)
                if len(batch) < 500:
                    break
                off += 500
        except Exception:
            pass
        return tid, results

    events_tasks   = [(tid, cs, ce) for tid in active_tids for cs, ce in chunks]
    template_events = {tid: [] for tid in active_tids}

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        for tid, evts in ex.map(fetch_open_events, events_tasks):
            template_events[tid].extend(evts)

    # 4. Aggregate
    offers_data   = {}
    reach_ytd_g   = set()
    reach_month_g = set()
    leads_ytd_g   = set()
    leads_month_g = set()

    for tid in all_tids:
        offer = tid_to_offer[tid]
        if offer not in offers_data:
            offers_data[offer] = {'reach_ytd': set(), 'reach_month': set(),
                                  'leads_ytd': set(), 'leads_month': set()}
        od = offers_data[offer]

        for e in template_reach[tid]:
            email = (e.get('email') or '').lower().strip()
            if not email or is_excluded(email):
                continue
            date = (e.get('date') or '')[:7]
            reach_ytd_g.add(email);  od['reach_ytd'].add(email)
            if date == current_month:
                reach_month_g.add(email); od['reach_month'].add(email)

    for tid in active_tids:
        offer = tid_to_offer.get(tid)
        if not offer:
            continue
        if offer not in offers_data:
            offers_data[offer] = {'reach_ytd': set(), 'reach_month': set(),
                                  'leads_ytd': set(), 'leads_month': set()}
        od = offers_data[offer]
        for ev in template_events[tid]:
            email_addr = (ev.get('email') or '').lower().strip()
            if not email_addr or is_excluded(email_addr):
                continue
            date = (ev.get('date') or '')[:7]
            leads_ytd_g.add(email_addr);  od['leads_ytd'].add(email_addr)
            if date == current_month:
                leads_month_g.add(email_addr); od['leads_month'].add(email_addr)

    offers_list = sorted([
        {
            'name':        offer,
            'reachYtd':    len(d['reach_ytd']),
            'reachMonth':  len(d['reach_month']),
            'leadsYtd':    len(d['leads_ytd']),
            'leadsMonth':  len(d['leads_month']),
            'leadRateYtd': round(len(d['leads_ytd']) / len(d['reach_ytd']) * 100, 1)
                           if d['reach_ytd'] else 0,
        }
        for offer, d in offers_data.items()
    ], key=lambda x: x['leadsYtd'], reverse=True)

    return {
        'offers':       offers_list,
        'leadsMonth':   len(leads_month_g),
        'leadsYtd':     len(leads_ytd_g),
        'reachMonth':   len(reach_month_g),
        'reachYtd':     len(reach_ytd_g),
        'currentMonth': current_month,
        'currentYear':  current_year,
        'note': (
            'Source : templates Brevo CLUB_*_Membre_*. '
            'Adresses @acl.lu, @epic.net et adresses de test exclues. '
            'Déduplication exacte par email.'
        ),
        '_debug': {
            'templateCount':    len(all_tids),
            'offerCount':       len(templates_by_offer),
            'chunksCount':      len(chunks),
            'totalReach':       sum(len(v) for v in template_reach.values()),
            'activeTemplates':  len(active_tids),
            'totalOpenEvents':  sum(len(v) for v in template_events.values()),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# YouTube
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/youtube')
def youtube():
    start, end = _date_range()
    key = f'youtube:{start}:{end}'
    cached = _cache_get(key)
    if cached: return jsonify(cached)
    headers = _google_headers()
    if not headers:
        return jsonify({'error': 'Google non connecté'})

    YT_BASE = 'https://www.googleapis.com/youtube/v3'

    # Channel stats
    rc = _get(f'{YT_BASE}/channels', headers=headers, params={
        'part': 'statistics', 'id': YT_CHANNEL_ID,
    })
    if not rc.ok:
        return jsonify({'error': rc.text[:200]})

    items = rc.json().get('items', [])
    if not items:
        return jsonify({'error': 'Canal YouTube introuvable'})

    stats = items[0].get('statistics', {})
    subscribers  = int(stats.get('subscriberCount', 0))
    total_views  = int(stats.get('viewCount', 0))
    video_count  = int(stats.get('videoCount', 0))

    # Recent videos
    rv = _get(f'{YT_BASE}/search', headers=headers, params={
        'part':       'snippet',
        'channelId':  YT_CHANNEL_ID,
        'order':      'date',
        'publishedAfter': start + 'T00:00:00Z',
        'maxResults': 20,
        'type':       'video',
    })
    videos = []
    period_videos = 0
    if rv.ok:
        video_ids = [i['id']['videoId'] for i in rv.json().get('items', []) if i.get('id', {}).get('videoId')]
        period_videos = len(video_ids)
        if video_ids:
            rs = _get(f'{YT_BASE}/videos', headers=headers, params={
                'part': 'snippet,statistics',
                'id':   ','.join(video_ids),
            })
            if rs.ok:
                for item in rs.json().get('items', []):
                    sn = item.get('snippet', {})
                    st = item.get('statistics', {})
                    videos.append({
                        'title':    sn.get('title', ''),
                        'date':     sn.get('publishedAt', '')[:10],
                        'views':    int(st.get('viewCount', 0)),
                        'likes':    int(st.get('likeCount', 0)),
                        'comments': int(st.get('commentCount', 0)),
                    })

    data = {
        'subscribers':  subscribers,
        'totalViews':   total_views,
        'videoCount':   video_count,
        'periodVideos': period_videos,
        'topVideos':    sorted(videos, key=lambda x: x['views'], reverse=True),
    }
    _cache_set(key, data, 900)
    return jsonify(data)


# Buffer
# ─────────────────────────────────────────────────────────────────────────────

BUFFER_GQL = 'https://api.buffer.com/graphql'

def _buffer_gql(query, variables=None):
    """Run a GraphQL query against Buffer. Returns (data_dict, error_str)."""
    if not BUFFER_API_KEY:
        return None, 'BUFFER_API_KEY manquante'
    try:
        r = requests.post(
            BUFFER_GQL,
            headers={'Authorization': f'Bearer {BUFFER_API_KEY}', 'Content-Type': 'application/json'},
            json={'query': query, 'variables': variables or {}},
            verify=_VERIFY,
            timeout=15,
        )
    except Exception as e:
        return None, f'Réseau : {e}'
    if not r.ok:
        return None, f'HTTP {r.status_code} : {r.text[:300]}'
    body = r.json()
    if 'errors' in body:
        msgs = [e.get('message','?') for e in body['errors']]
        return None, 'GraphQL errors : ' + ' | '.join(msgs)
    return body.get('data'), None


@app.route('/buffer/debug')
def buffer_debug():
    """Diagnostic : récupère l'account + introspecte PostsFiltersInput/PostStatus."""
    if not BUFFER_API_KEY:
        return jsonify({'error': 'BUFFER_API_KEY manquante'})

    queries = {
        'type_Asset': '{ __type(name:"Asset") { fields { name type { name kind ofType { name kind } } } } }',
        'type_ImageAsset': '{ __type(name:"ImageAsset") { fields { name type { name kind ofType { name kind } } } } }',
        'type_VideoAsset': '{ __type(name:"VideoAsset") { fields { name type { name kind ofType { name kind } } } } }',
    }

    results = {}
    for label, q in queries.items():
        data, err = _buffer_gql(q)
        results[label] = {'data': data, 'error': err}

    return jsonify(results)


@app.route('/buffer')
def buffer_planning():
    if not BUFFER_API_KEY:
        return jsonify({'error': 'BUFFER_API_KEY manquante'})

    cached = _cache_get('buffer_main')
    if cached:
        return jsonify(cached)

    # ── Step 1 : récupérer l'organizationId ──────────────────────────────────
    d_account, e_account = _buffer_gql('{ account { currentOrganization { id } } }')
    if e_account or not d_account:
        err = e_account or 'Impossible de récupérer le compte Buffer'
        _cache_set('buffer_main', {'error': err}, 60)
        return jsonify({'error': err})

    org_id = (d_account.get('account') or {}).get('currentOrganization', {}).get('id', '')
    if not org_id:
        err = 'organizationId introuvable dans le compte Buffer'
        _cache_set('buffer_main', {'error': err}, 60)
        return jsonify({'error': err})

    # ── Step 2 : channels + posts en parallèle ────────────────────────────────
    # Inline string (org_id vient de l'API Buffer, pas de l'utilisateur)
    oid = org_id
    Q_CHANNELS  = ('{ channels(input:{organizationId:"%s"})'
                   '{ id name service avatar isDisconnected displayName } }') % oid
    Q_SCHEDULED = ('{ posts(input:{organizationId:"%s",filter:{status:[scheduled]}})'
                   '{ edges { node { id text dueAt status'
                   '  channel{id name displayName service} assets{__typename} } } } }') % oid
    Q_SENT      = ('{ posts(input:{organizationId:"%s",filter:{status:[sent]}})'
                   '{ edges { node { id text dueAt sentAt status'
                   '  channel{id name displayName service} } } } }') % oid

    _api_errors = []

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            fc  = ex.submit(_buffer_gql, Q_CHANNELS)
            fs  = ex.submit(_buffer_gql, Q_SCHEDULED)
            fse = ex.submit(_buffer_gql, Q_SENT)
            d_channels,  e_ch = fc.result()
            d_scheduled, e_sc = fs.result()
            d_sent,      e_se = fse.result()
    except Exception as e:
        return jsonify({'error': str(e)}), 503

    for e in [e_ch, e_sc, e_se]:
        if e:
            _api_errors.append(e)

    if _api_errors and not d_channels and not d_scheduled:
        _cache_set('buffer_main', {'error': _api_errors[0]}, 60)
        return jsonify({'error': _api_errors[0]})

    def _norm_post(node):
        ch = node.get('channel') or {}
        return {
            'id':           node.get('id', ''),
            'text':         node.get('text', ''),
            'due_at':       node.get('dueAt') or node.get('sentAt') or '',
            'status':       node.get('status', ''),
            'channel_id':   ch.get('id', ''),
            'channel_name': ch.get('displayName') or ch.get('name') or '',
            'service':      ch.get('service', '').lower(),
            'has_media':    len(node.get('assets') or []) > 0,
        }

    channels = []
    if d_channels:
        for ch in d_channels.get('channels') or []:
            if not ch.get('isDisconnected'):
                channels.append({
                    'id':      ch.get('id', ''),
                    'name':    ch.get('displayName') or ch.get('name') or '',
                    'service': ch.get('service', '').lower(),
                    'avatar':  ch.get('avatar', ''),
                })

    def _extract_posts(data_node):
        posts = []
        for edge in (data_node.get('posts') or {}).get('edges') or []:
            node = edge.get('node') or {}
            if node:
                posts.append(_norm_post(node))
        return posts

    scheduled = sorted(_extract_posts(d_scheduled or {}), key=lambda x: x['due_at'])
    sent      = sorted(_extract_posts(d_sent      or {}), key=lambda x: x['due_at'], reverse=True)

    # ── Stats ─────────────────────────────────────────────────────────────────
    now        = datetime.datetime.utcnow()
    week_start = now - datetime.timedelta(days=now.weekday())
    week_end   = week_start + datetime.timedelta(days=7)

    def _in_week(iso):
        try:
            dt = datetime.datetime.fromisoformat(iso.replace('Z', '+00:00')).replace(tzinfo=None)
            return week_start <= dt < week_end
        except Exception:
            return False

    this_week = [p for p in scheduled if _in_week(p['due_at'])]
    next_post = scheduled[0] if scheduled else None

    data = {
        'channels':    channels,
        'scheduled':   scheduled,
        'sent':        sent,
        'this_week':   this_week,
        '_api_errors': _api_errors,
        'stats': {
            'scheduled_count':   len(scheduled),
            'active_channels':   len(channels),
            'this_week_count':   len(this_week),
            'next_post_at':      next_post['due_at']       if next_post else None,
            'next_post_channel': next_post['channel_name'] if next_post else None,
            'next_post_service': next_post['service']      if next_post else None,
        },
    }
    _cache_set('buffer_main', data, 300)
    return jsonify(data)


# ─────────────────────────────────────────────────────────────────────────────
# Supermetrics helpers
# ─────────────────────────────────────────────────────────────────────────────

SUPERMETRICS_QUERY_URL  = 'https://api.supermetrics.com/enterprise/v2/query/data/json'
SUPERMETRICS_LI_ACCOUNT = '10097790'  # ACL - Automobile Club du Luxembourg


def _supermetrics_query(ds_id, ds_accounts, fields, start_date, end_date, max_rows=500, report_type=None, settings=None):
    """GET a Supermetrics query (sync_timeout=60 s).

    Returns (rows, schema, error_msg).  rows is a list of lists; schema a list of field IDs.
    On failure rows and schema are None and error_msg is set.
    """
    if not SUPERMETRICS_API_KEY:
        return None, None, 'SUPERMETRICS_API_KEY manquant — ajouter la variable sur Railway'

    accounts = [ds_accounts] if isinstance(ds_accounts, str) else list(ds_accounts)
    query = {
        'api_key':         SUPERMETRICS_API_KEY,
        'ds_id':           ds_id,
        'ds_accounts':     accounts,
        'fields':          fields,
        'date_range_type': 'custom',
        'start_date':      start_date,
        'end_date':        end_date,
        'max_rows':        max_rows,
        'sync_timeout':    60,
    }
    if report_type is not None:
        query['report_type'] = str(report_type)
    if settings:
        query.update(settings)

    try:
        r = _get(SUPERMETRICS_QUERY_URL, params={'json': json.dumps(query)}, timeout=90)
    except Exception as exc:
        return None, None, f'Supermetrics request error: {exc}'

    if not r.ok:
        return None, None, f'Supermetrics HTTP {r.status_code}: {r.text[:300]}'

    try:
        body = r.json()
    except Exception:
        return None, None, f'Supermetrics response non-JSON: {r.text[:200]}'

    if not isinstance(body, dict):
        return None, None, f'Supermetrics unexpected body type: {type(body).__name__}: {str(body)[:200]}'

    if body.get('error') or body.get('message'):
        msg = body.get('error') or body.get('message')
        return None, None, f'Supermetrics API error: {msg}'

    rows   = body.get('data', [])
    raw_sc = body.get('schema', fields)
    schema = [s.get('id', s) if isinstance(s, dict) else s for s in raw_sc]
    return rows, schema, None


def _sm_rows_to_dicts(rows, schema):
    """Convert Supermetrics [row, ...] + schema into list of dicts."""
    return [dict(zip(schema, row)) for row in (rows or [])]


def _n(v, default=0.0):
    """Safely cast a Supermetrics value (often a string) to float."""
    if v is None or v == '':
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default




@app.route('/supermetrics/linkedin')
def supermetrics_linkedin():
    """LinkedIn Pages analytics via Supermetrics (account 10097790 — ACL)."""
    try:
        return _supermetrics_linkedin_impl()
    except Exception as exc:
        import traceback
        return jsonify({'error': str(exc), '_traceback': traceback.format_exc()}), 500


def _supermetrics_linkedin_impl():
    start, end = _date_range()
    if not SUPERMETRICS_API_KEY:
        return jsonify({'error': 'SUPERMETRICS_API_KEY manquant — ajouter la variable sur Railway'}), 503
    key = f'linkedin:{start}:{end}'
    cached = _cache_get(key)
    if cached: return jsonify(cached)

    acc = SUPERMETRICS_LI_ACCOUNT

    sm_errors = []  # capture les erreurs Supermetrics pour diagnostic

    # ── 1. Performance par date (share_statistics, report_type 6) ────────────
    perf_fields = ['date', 'page_impressions', 'page_clicks', 'page_engagements',
                   'page_engagement_rate', 'page_likes', 'page_comments', 'page_shares']
    rows1, sc1, err1 = _supermetrics_query('LIP', acc, perf_fields, start, end)
    if err1: sm_errors.append(f'perf: {err1}')
    # Filter header rows (Supermetrics sometimes includes a row with field names as values)
    items1 = [r for r in _sm_rows_to_dicts(rows1, sc1 or perf_fields)
              if r.get('date', '') not in ('', 'date', 'Date')]

    trend = [{'date': r.get('date', ''),
              'impressions': _n(r.get('page_impressions')),
              'engagements': _n(r.get('page_engagements'))} for r in items1]

    def _sum(key): return sum(_n(r.get(key)) for r in items1)
    avg_er = (sum(_n(r.get('page_engagement_rate')) for r in items1) / len(items1)) if items1 else 0

    # ── 2. Croissance abonnés par date (follower_statistics, report_type 4) ──
    fol_fields = ['date', 'followers_gain_total', 'followers_gain_organic', 'followers_gain_paid']
    rows2, sc2, err2 = _supermetrics_query('LIP', acc, fol_fields, start, end)
    if err2: sm_errors.append(f'followers: {err2}')
    items2 = [r for r in _sm_rows_to_dicts(rows2, sc2 or fol_fields)
              if r.get('date', '') not in ('', 'date', 'Date')]

    new_followers = sum(_n(r.get('followers_gain_total')) for r in items2)
    gain_by_date  = {r.get('date', ''): _n(r.get('followers_gain_total')) for r in items2}
    for t in trend:
        t['newFollowers'] = gain_by_date.get(t['date'], 0)

    # ── 3. Total abonnés (company_statistics, report_type 3) ─────────────────
    rows3, sc3, err3 = _supermetrics_query('LIP', acc, ['follower_count'], start, end, max_rows=1)
    if err3: sm_errors.append(f'follower_count: {err3}')
    items3 = _sm_rows_to_dicts(rows3, sc3 or ['follower_count'])
    follower_count = int(_n(items3[-1].get('follower_count'))) if items3 else 0

    # ── 4. Top posts (update_details, report_type 10) ─────────────────────────
    post_fields = ['update_title', 'update_share_comment', 'update_url',
                   'update_share_media_category', 'page_impressions', 'page_clicks',
                   'page_likes', 'page_comments', 'page_shares', 'page_engagement_rate']
    rows4, sc4, err4 = _supermetrics_query('LIP', acc, post_fields, start, end, max_rows=200)
    if err4: sm_errors.append(f'posts: {err4}')
    _PLACEHOLDER_URLS = {'Update URL', 'update url', ''}
    items4 = [r for r in _sm_rows_to_dicts(rows4, sc4 or post_fields)
              if r.get('update_url', '') not in _PLACEHOLDER_URLS
              and r.get('update_title', '') not in ('Update title', 'update_title', '')]

    posts = [{
        'title':          (r.get('update_title') or r.get('update_share_comment', ''))[:80],
        'text':           (r.get('update_share_comment') or '')[:120],
        'url':            r.get('update_url', ''),
        'mediaCategory':  r.get('update_share_media_category', ''),
        'impressions':    _n(r.get('page_impressions')),
        'clicks':         _n(r.get('page_clicks')),
        'likes':          _n(r.get('page_likes')),
        'comments':       _n(r.get('page_comments')),
        'shares':         _n(r.get('page_shares')),
        'engagementRate': round(_n(r.get('page_engagement_rate')), 2),
    } for r in items4]

    # ── 5. Démographie par pays (follower_statistics, report_type 3) ───────────
    # Note: follower_count retourne le total global pour chaque ligne — seule la
    # liste des segments présents est exploitable.
    rows5, sc5, err5 = _supermetrics_query('LIP', acc, ['follower_country'], start, end, report_type=3, max_rows=50)
    if err5: sm_errors.append(f'countries: {err5}')
    items5 = _sm_rows_to_dicts(rows5, sc5 or ['follower_country'])
    countries = sorted(
        [r.get('follower_country', '') for r in items5
         if r.get('follower_country', '') not in ('', 'Follower country', 'follower_country')],
    )[:30]

    # ── 6. Démographie par secteur (follower_statistics, report_type 3) ─────────
    rows6, sc6, err6 = _supermetrics_query('LIP', acc, ['follower_industry'], start, end, report_type=3, max_rows=50)
    if err6: sm_errors.append(f'industries: {err6}')
    items6 = _sm_rows_to_dicts(rows6, sc6 or ['follower_industry'])
    industries = sorted(
        [r.get('follower_industry', '') for r in items6
         if r.get('follower_industry', '') not in ('', 'Follower industry', 'follower_industry')],
    )[:25]

    data = {
        'source':        'supermetrics',
        '_debug_errors': sm_errors if sm_errors else None,
        # ── compat fields for overview & insights ──
        'followers':      follower_count,
        'totalFollowers': follower_count,
        'impressions':    _sum('page_impressions'),
        'totalImpressions': _sum('page_impressions'),
        'clicks':         _sum('page_clicks'),
        'totalClicks':    _sum('page_clicks'),
        'engagements':    _sum('page_engagements'),
        'totalEngagements': _sum('page_engagements'),
        'engagementRate': round(avg_er, 2),
        # ── enriched structure ──
        'summary': {
            'followerCount':   follower_count,
            'newFollowers':    new_followers,
            'impressions':     _sum('page_impressions'),
            'clicks':          _sum('page_clicks'),
            'engagements':     _sum('page_engagements'),
            'engagementRate':  round(avg_er, 2),
            'likes':           _sum('page_likes'),
            'comments':        _sum('page_comments'),
            'shares':          _sum('page_shares'),
        },
        'trend':  trend,
        'posts':  posts,
        'demographics': {
            'countries':  countries,
            'industries': industries,
        },
    }
    _cache_set(key, data, 900)
    return jsonify(data)


# ─────────────────────────────────────────────────────────────────────────────
# AI Insights (Claude)
# ─────────────────────────────────────────────────────────────────────────────

def build_insights_prompt(data):
    ga4    = data.get('ga4')    or {}
    gsc    = data.get('gsc')    or {}
    fb     = data.get('fb')     or {}
    brevo  = data.get('brevo')  or {}
    li     = data.get('li')     or {}
    yt     = data.get('yt')     or {}
    period = data.get('period', 30)

    lines = [
        "Tu es un analyste marketing senior pour ACL Luxembourg (Automobile Club de Luxembourg, 191 000 membres, secteur automobile/mobilité, Luxembourg).",
        f"Analyse les KPIs marketing ci-dessous sur une période de {period} jours et génère des recommandations actionnables en français.",
        "",
        "## Données KPI",
    ]

    if ga4:
        sess   = ga4.get('sessions', 'N/A')
        deltas = ga4.get('deltas') or {}
        d_sess = deltas.get('sessions')
        lines.append(f"**GA4 acl.lu** : {sess} sessions "
                     f"({'↑' if d_sess and d_sess > 0 else '↓'} {abs(d_sess or 0):.1f}% vs période préc.)")

    if gsc:
        clicks   = gsc.get('clicks', 'N/A')
        ctr      = gsc.get('ctr', 'N/A')
        position = gsc.get('avgPosition', 'N/A')
        lines.append(f"**Search Console** : {clicks} clics, CTR {ctr}%, position moy. {position}")
        lines.append(f"  → Benchmark SEO : CTR moyen pos. 1-3 = 25-35%, pos. 4-10 = 3-10%, au-delà = <3%")

    if fb:
        spend  = fb.get('spend', 'N/A')
        roas   = fb.get('roas', 'N/A')
        ctr_fb = fb.get('ctr', 'N/A')
        lines.append(f"**Meta Ads** : {spend} EUR dépensés, ROAS {roas}x, CTR {ctr_fb}%")
        lines.append(f"  → Benchmark Meta : CTR moyen = 0.9-1.5%, ROAS sain = >2x, coût/clic secteur auto LUX ≈ 1.20€")

    if brevo:
        n_camps  = brevo.get('campaigns', 'N/A')
        contacts = (brevo.get('contactStats') or {}).get('total', 'N/A')
        lines.append(f"**Brevo** : {n_camps} campagnes analysées, {contacts} contacts")
        lines.append(f"  → Benchmark email B2C Europe : taux d'ouverture moyen = 25%, taux de clic = 2-3%, CTOR = 10-15%")

    if li:
        followers = li.get('followers', 'N/A')
        lines.append(f"**LinkedIn** : {followers} abonnés page")
        lines.append(f"  → Benchmark LinkedIn organisations : taux d'engagement = 1-3%, croissance abonnés = +2-5%/mois")

    if yt:
        subs   = yt.get('subscribers', 'N/A')
        videos = yt.get('periodVideos', 'N/A')
        lines.append(f"**YouTube** : {subs} abonnés, {videos} nouvelles vidéos sur {period} jours")
        lines.append(f"  → Benchmark YouTube petites chaînes : 1-4 vidéos/mois pour maintenir l'algorithme")

    has_data = any([ga4, gsc, fb, brevo, li, yt])
    if not has_data:
        lines.append("Aucune donnée disponible pour cette période. Génère des recommandations génériques basées sur les bonnes pratiques marketing pour un club automobile au Luxembourg.")

    lines += [
        "",
        "## Instructions",
        "Réponds UNIQUEMENT avec un tableau JSON brut, sans markdown, sans bloc ```json, sans explication avant ou après.",
        f"Génère exactement 5 recommandations prioritaires pour ACL Luxembourg.",
        "Inclure AU MOINS 1 recommandation de type 'low' (opportunité de croissance identifiée).",
        "Dans le champ 'insight', comparer le KPI au benchmark fourni et quantifier l'écart. Si pas de données, citer la bonne pratique sectorielle.",
        "Dans le champ 'action', proposer une action concrète, mesurable, réalisable sous 2 semaines.",
        "Ta réponse doit commencer par [ et se terminer par ]. Rien d'autre.",
        'Exemple de format : [{"priority":"high","source":"GA4","title":"Titre court","insight":"Observation","action":"Action concrète"}]',
    ]

    return '\n'.join(lines)


def build_market_prompt(data):
    ga4   = data.get('ga4')   or {}
    gsc   = data.get('gsc')   or {}
    fb    = data.get('fb')    or {}
    brevo = data.get('brevo') or {}
    li    = data.get('li')    or {}
    period = data.get('period', 30)

    lines = [
        "Tu es un analyste marketing expert du marché luxembourgeois et des clubs automobiles européens (ADAC, RAC, TCS, ACI).",
        f"Analyse les KPIs d'ACL Luxembourg sur {period} jours et fournis une analyse comparative de marché en français.",
        "",
        "## Données ACL",
    ]
    if gsc:  lines.append(f"SEO : {gsc.get('clicks')} clics, CTR {gsc.get('ctr')}%, position moy. {gsc.get('avgPosition')}")
    if fb:   lines.append(f"Meta Ads : {fb.get('spend')} EUR, ROAS {fb.get('roas')}×, CTR {fb.get('ctr')}%")
    if brevo:lines.append(f"Email : {brevo.get('campaigns')} campagnes, taux ouverture moy. {brevo.get('avgOpenRate')}%")
    if li:   lines.append(f"LinkedIn : {li.get('followers')} abonnés")

    lines += [
        "",
        "## Benchmarks secteur",
        "- SEO Luxembourg : marché petit (650k hab.), concurrence faible, orgs établies atteignent pos. < 10 facilement",
        "- Meta LUX : CPM élevé (marché premium), CTR attendu 0.9-1.5%, ROAS > 2× viable",
        "- Email B2C Europe GDPR : open rate 25-35%, listes qualifiées LUX atteignent 35-50%",
        "- LinkedIn orga. : engagement 1-3%, grands clubs (ADAC) : 2-5%",
        "- Clubs comparables : ADAC (9M membres), RAC UK, TCS Suisse, ACI Italie — présence digitale forte",
        "",
        "## Instructions",
        "Réponds en texte libre (pas de JSON), maximum 180 mots.",
        "Structure : 1 ligne de synthèse globale, puis 3-4 points clés avec chiffres comparatifs.",
        "Sois direct et actionnable.",
    ]
    return '\n'.join(lines)


def build_section_prompt(data):
    section = data.get('section', 'general')
    period  = data.get('period', 30)

    ctx = {
        'ga4':      f"Sessions : {data.get('sessions')}, utilisateurs : {data.get('users')}, rebond : {data.get('bounceRate')}%, deltas (%) : {data.get('deltas')}",
        'gsc':      f"Clics : {data.get('clicks')}, CTR : {data.get('ctr')}%, position moy. : {data.get('avgPosition')}, deltas (%) : {data.get('deltas')}",
        'meta':     f"Budget : {data.get('spend')} EUR, ROAS : {data.get('roas')}×, CTR : {data.get('ctr')}%, impressions : {data.get('impressions')}",
        'brevo':    f"Taux ouverture moy. : {data.get('avgOpenRate')}%, taux clic : {data.get('avgClick')}%, désabonnements : {data.get('totalUnsub')}, campagnes : {data.get('campaigns')}",
        'youtube':  f"Abonnés : {data.get('subscribers')}, vues totales : {data.get('totalViews')}, nouvelles vidéos : {data.get('periodVideos')}",
        'linkedin': f"Abonnés : {data.get('followers')}, engagements : {data.get('engagements')}",
    }
    section_labels = {
        'ga4': 'Google Analytics (acl.lu)', 'gsc': 'Google Search Console (SEO)',
        'meta': 'Meta Ads', 'brevo': 'Email Brevo', 'youtube': 'YouTube', 'linkedin': 'LinkedIn',
    }
    kpi_ctx = ctx.get(section, str(data))
    label   = section_labels.get(section, section)

    return '\n'.join([
        f"Tu es un analyste marketing pour ACL Luxembourg. Analyse les KPIs {label} sur {period} jours :",
        kpi_ctx,
        "",
        "Génère UNE observation concise (20-35 mots max) en français qui explique ce que ces chiffres signifient concrètement.",
        "Commence directement par l'observation. Pas de titre, pas de ponctuation finale.",
        "Exemples : 'Le trafic organique progresse de 12% — l'optimisation des pages de membres porte ses fruits'",
        "Réponds uniquement avec la phrase d'observation.",
    ])


def build_greeting_prompt(data):
    ga4    = data.get('ga4')   or {}
    gsc    = data.get('gsc')   or {}
    fb     = data.get('fb')    or {}
    brevo  = data.get('brevo') or {}
    period = data.get('period', 30)

    kpis = []
    if ga4.get('sessions'):
        delta = ga4.get('deltas', {}).get('sessions')
        trend = f" ({'+' if delta and delta > 0 else ''}{delta:.1f}%)" if delta is not None else ''
        kpis.append(f"Sessions acl.lu : {ga4['sessions']:,}{trend}")
    if gsc.get('clicks'):
        delta = gsc.get('deltas', {}).get('clicks')
        trend = f" ({'+' if delta and delta > 0 else ''}{delta:.1f}%)" if delta is not None else ''
        kpis.append(f"Clics SEO : {gsc['clicks']:,}{trend}")
    if fb.get('roas'):
        kpis.append(f"ROAS Meta : {fb['roas']:.2f}×")
    if brevo.get('avgOpenRate'):
        kpis.append(f"Taux d'ouverture email : {brevo['avgOpenRate']}%")

    lines = [
        f"Tu es un assistant marketing pour ACL Luxembourg. Voici les KPIs principaux sur {period} jours :",
        '\n'.join(f"- {k}" for k in kpis) if kpis else "- Données partiellement disponibles",
        "",
        "Génère UNE phrase courte (15-25 mots max) en français qui résume l'état général de la performance.",
        "Sois direct et positif quand c'est justifié, nuancé sinon.",
        "Commence par un verbe d'action ou un adjectif. Pas de ponctuation finale.",
        "Exemples : 'Bonne semaine côté SEO, les campagnes Meta marquent le pas' | 'Performance solide sur tous les canaux cette période'",
        "Réponds uniquement avec la phrase, sans guillemets ni explication.",
    ]
    return '\n'.join(lines)


@app.route('/insights', methods=['POST'])
def get_insights():
    data = request.json or {}
    mode = data.get('mode', 'insights')

    if not ANTHROPIC_API_KEY:
        return jsonify({'error': 'ANTHROPIC_API_KEY manquante'}), 503

    if mode == 'section':
        prompt = build_section_prompt(data)
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            msg = client.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=100,
                messages=[{'role': 'user', 'content': prompt}],
            )
            text = msg.content[0].text.strip().strip('"').strip("'")
            return jsonify({'insight': text})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    if mode == 'greeting':
        prompt = build_greeting_prompt(data)
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            msg = client.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=80,
                messages=[{'role': 'user', 'content': prompt}],
            )
            text = msg.content[0].text.strip().strip('"').strip("'")
            return jsonify({'greeting': text})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    if mode == 'market':
        prompt = build_market_prompt(data)
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            msg = client.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=512,
                messages=[{'role': 'user', 'content': prompt}],
            )
            return jsonify({'marketAnalysis': msg.content[0].text})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    prompt = build_insights_prompt(data)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=1024,
            messages=[{'role': 'user', 'content': prompt}],
        )
        text = msg.content[0].text
        # Extract JSON array — try direct parse first, then regex
        insights = []
        try:
            parsed = json.loads(text.strip())
            if isinstance(parsed, list):
                insights = parsed
        except Exception:
            # Find the longest JSON array in the response
            for m in re.finditer(r'\[[\s\S]*?\]', text):
                try:
                    candidate = json.loads(m.group())
                    if isinstance(candidate, list) and len(candidate) > len(insights):
                        insights = candidate
                except Exception:
                    pass
        return jsonify({'insights': insights})
    except Exception as e:
        return jsonify({'error': str(e), 'insights': []}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Web Intelligence — cross-source AI recommendations (GA4 + GSC)
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/insights/web')
def insights_web():
    """Fetch GA4 + GSC data, cross-correlate, call Claude for prioritised recommendations."""
    if not ANTHROPIC_API_KEY:
        return jsonify({'error': 'Anthropic API key manquant'}), 503

    start, end = _date_range()
    key = f'insights-web:{start}:{end}'
    cached = _cache_get(key)
    if cached: return jsonify(cached)

    headers_g = _google_headers()
    if not headers_g:
        return jsonify({'error': 'Google non connecté'})

    # Fetch GA4 main + extended + funnel + GSC in parallel
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        fut_ga4     = ex.submit(_parse_ga4_main, GA4_PROPERTY, start, end)
        fut_gsc     = ex.submit(_fetch_gsc_raw, start, end, headers_g)
        fut_funnel  = ex.submit(_fetch_ga4_funnel_raw, start, end, headers_g)
        fut_geo     = ex.submit(_fetch_ga4_geo_raw, start, end, headers_g)

        ga4    = fut_ga4.result()
        gsc    = fut_gsc.result()
        funnel = fut_funnel.result()
        geo    = fut_geo.result()

    # Merge GSC topPages with GA4 entryPages by normalised URL
    def normalise(url):
        return url.rstrip('/').replace('https://www.acl.lu', '').replace('https://acl.lu', '')

    ga4_entry = {normalise(p['page']): p for p in ga4.get('topPages', [])}
    merged_pages = []
    for p in gsc.get('topPages', [])[:20]:
        norm = normalise(p['page'])
        ga4p = ga4_entry.get(norm, {})
        ctr_act  = p['ctr'] / 100
        ctr_exp  = _expected_ctr(p['position'])
        score_v2 = round(p['impressions'] * max(0, ctr_exp - ctr_act), 1)
        # Legacy score kept for backward compat with old frontend
        score = round(p['impressions'] * max(0, 0.05 - ctr_act) / max(p['position'], 1), 1)
        merged_pages.append({
            'url':             norm or p['page'],
            'gscClicks':       p['clicks'],
            'gscImpressions':  p['impressions'],
            'gscCtr':          p['ctr'],
            'gscPosition':     p['position'],
            'gaViews':         ga4p.get('views', 0),
            'opportunityScore': score,
            'ctrAttendu':      round(ctr_exp * 100, 1),
            'scoreV2':         score_v2,
        })
    merged_pages.sort(key=lambda x: x['scoreV2'], reverse=True)

    # ── Step 8c: queries per page (one extra GSC call) ─────────────────────────
    page_queries = {}
    try:
        base_gsc_url = f'https://www.googleapis.com/webmasters/v3/sites/{requests.utils.quote(GSC_SITE, safe="")}/searchAnalytics/query'
        r_pq = _post(base_gsc_url, headers=headers_g, json={
            'startDate': start, 'endDate': end,
            'dimensions': ['page', 'query'], 'rowLimit': 200,
            'orderBy': [{'fieldName': 'impressions', 'sortOrder': 'DESCENDING'}],
        })
        if r_pq.ok:
            for row in r_pq.json().get('rows', []):
                pg = normalise(row['keys'][0])
                kw = row['keys'][1]
                impr = row.get('impressions', 0)
                if pg not in page_queries or impr > page_queries[pg]['impressions']:
                    page_queries[pg] = {'query': kw, 'impressions': int(impr)}
    except Exception:
        pass

    for p in merged_pages:
        p['mainQuery'] = page_queries.get(p['url'], {}).get('query', '')

    # ── Step 8c: DataForSEO SERP enrichment (top 10, concurrent) ───────────────
    top_enrich = [p for p in merged_pages[:10] if p.get('mainQuery') and p['scoreV2'] > 5]
    serp_results = {}
    if DATAFORSEO_LOGIN and top_enrich:
        def _serp_job(pg):
            return pg['url'], _fetch_dataforseo_serp(pg['mainQuery'])
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex2:
            futs = {ex2.submit(_serp_job, pg): pg for pg in top_enrich}
            for fut in concurrent.futures.as_completed(futs, timeout=25):
                try:
                    url_key, items = fut.result()
                    serp_results[url_key] = items
                except Exception:
                    pass

    def _analyze_serp(items):
        if not items:
            return ''
        n = len(items)
        patterns = []
        has_num  = sum(1 for i in items if any(c.isdigit() for c in (i.get('title') or '')))
        has_year = sum(1 for i in items if re.search(r'202\d', i.get('title') or ''))
        has_q    = sum(1 for i in items if '?' in (i.get('title') or ''))
        emo_ws   = ['meilleur', 'guide', 'gratuit', 'essentiel', 'complet', 'rapide', 'facile', 'tout']
        has_emo  = sum(1 for i in items if any(w in (i.get('title') or '').lower() for w in emo_ws))
        if has_num  >= n // 2: patterns.append(f'{has_num}/{n} titres contiennent un chiffre')
        if has_year >= 2:      patterns.append(f"{has_year}/{n} titres mentionnent l'année")
        if has_q    >= 2:      patterns.append(f'{has_q}/{n} titres formulent une question')
        if has_emo  >= 2:      patterns.append(f"{has_emo}/{n} titres utilisent un mot fort ({', '.join(emo_ws[:3])}…)")
        return ' · '.join(patterns)

    for p in merged_pages:
        serp = serp_results.get(p['url'], [])
        p['gapConcurrents'] = _analyze_serp(serp)

    # ── Step 8c: Claude batch for intent + title/meta ──────────────────────────
    pages_for_ai = [p for p in merged_pages[:15] if p.get('mainQuery')]
    if pages_for_ai:
        ai_input = [{'url': p['url'], 'query': p['mainQuery'],
                     'position': p['gscPosition'], 'ctr_actuel': p['gscCtr'],
                     'serp_patterns': p['gapConcurrents']} for p in pages_for_ai]
        enrich_prompt = f"""Analyse ces pages d'ACL Luxembourg (acl.lu) dans les SERP Google Luxembourg.
Pour chaque page génère exactement : intent (informationnel|transactionnel|navigationnel),
recommandation_title (≤60 car, inclure le mot-clé), recommandation_meta (≤155 car, incitative),
effort (faible=seul snippet à modifier · moyen=snippet pauvre contenu OK · fort=décalage intention/contenu).
Réponds UNIQUEMENT avec un JSON array dans le même ordre que l'input.
{json.dumps(ai_input, ensure_ascii=False)}"""
        try:
            import anthropic as _anth
            _c2 = _anth.Anthropic(api_key=ANTHROPIC_API_KEY)
            _msg2 = _c2.messages.create(
                model='claude-haiku-4-5-20251001', max_tokens=2500,
                system='Réponds UNIQUEMENT avec un JSON array valide. Aucun markdown.',
                messages=[{'role': 'user', 'content': enrich_prompt}],
            )
            raw2 = _msg2.content[0].text.strip()
            raw2 = re.sub(r'```(?:json)?\s*', '', raw2).strip().strip('`').strip()
            enrich_data = json.loads(raw2) if raw2.startswith('[') else []
            if isinstance(enrich_data, list):
                for i, p in enumerate(pages_for_ai):
                    if i < len(enrich_data) and isinstance(enrich_data[i], dict):
                        ed = enrich_data[i]
                        p['intent']              = ed.get('intent', '')
                        p['recommandationTitle'] = ed.get('recommandation_title', '')
                        p['recommandationMeta']  = ed.get('recommandation_meta', '')
                        p['effort']              = ed.get('effort', '')
        except Exception:
            pass

    # Build Claude prompt
    organic_sessions = next(
        (c['sessions'] for c in ga4.get('channelBreakdown', []) if 'Organic' in c['channel'] and 'Social' not in c['channel']),
        0
    )
    top_opps = merged_pages[:8]
    top_queries = gsc.get('topQueries', [])[:10]
    funnel_steps = funnel.get('steps', [])

    prompt = f"""Tu es un analyste marketing digital expert pour ACL Luxembourg (automobile club).
Analyse ces données de performance web (GA4 + Google Search Console) et génère des recommandations marketing CONCRÈTES et PRIORISÉES.

## Données GA4 (période : {start} → {end})
- Sessions totales : {ga4.get('sessions', 0):,}
- Utilisateurs : {ga4.get('users', 0):,}
- Sessions organiques : {organic_sessions:,}
- Taux d'engagement : {ga4.get('engagementRate', 0)}%
- Durée moy. session : {round(ga4.get('avgSessionDuration', 0) / 60, 1)} min
- Taux de rebond : {ga4.get('bounceRate', 0)}%
- Top canaux : {json.dumps(ga4.get('channelBreakdown', [])[:5], ensure_ascii=False)}

## Données GSC
- Clics totaux : {gsc.get('clicks', 0):,}
- Impressions : {gsc.get('impressions', 0):,}
- CTR moyen : {gsc.get('ctr', 0)}%
- Position moyenne : {gsc.get('avgPosition', 0)}

## Top requêtes GSC
{json.dumps(top_queries, ensure_ascii=False, indent=2)}

## Pages avec opportunités SEO (scoreV2 = impressions × (CTR_attendu_position − CTR_actuel), trié par scoreV2 décroissant)
{json.dumps(top_opps, ensure_ascii=False, indent=2)}

## Funnel d'adhésion
{json.dumps(funnel_steps, ensure_ascii=False)}

## Top villes (sessions engagées)
{json.dumps(geo[:5], ensure_ascii=False)}

## Instructions
Génère un JSON avec exactement cette structure (réponds UNIQUEMENT avec le JSON, sans markdown) :
{{
  "summary": {{
    "topOpportunity": "phrase courte (max 15 mots)",
    "biggestRisk": "phrase courte (max 15 mots)",
    "quickWin": "phrase courte (max 15 mots)"
  }},
  "recommendations": [
    {{
      "priority": "P1",
      "category": "SEO",
      "title": "titre court (max 8 mots)",
      "rationale": "2 phrases avec chiffres réels des données",
      "action": "action concrète assignable à une personne",
      "metrics": {{"current": "valeur actuelle", "target": "cible réaliste", "estimatedImpact": "gain estimé"}}
    }}
  ],
  "anomalies": [
    {{"type": "warning", "message": "observation courte avec chiffre", "dataPoint": "métrique concernée"}}
  ]
}}

Génère 4-5 recommandations (P1, P1, P2, P2, P3) et 2-3 anomalies.
Priorité P1 = fort impact + réalisable en <30j. P2 = impact moyen ou effort plus long. P3 = backlog.
Catégories possibles : SEO, CRO, Contenu, UX, Acquisition.
Utilise les vrais chiffres des données. Pas de platitudes génériques."""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=1500,
            system='Tu réponds UNIQUEMENT avec du JSON valide. Aucun texte avant ou après. Aucun bloc markdown ni balise ```.',
            messages=[{'role': 'user', 'content': prompt}],
        )
        raw = msg.content[0].text.strip()
        # Strip markdown code fences
        cleaned = re.sub(r'```(?:json)?\s*', '', raw).strip()
        cleaned = re.sub(r'\s*```', '', cleaned).strip()
        result = None
        # Direct parse
        try:
            result = json.loads(cleaned)
        except Exception:
            pass
        # raw_decode: find first valid JSON object
        if not result:
            decoder = json.JSONDecoder()
            for i, ch in enumerate(cleaned):
                if ch == '{':
                    try:
                        obj, _ = decoder.raw_decode(cleaned, i)
                        if isinstance(obj, dict):
                            result = obj
                            break
                    except (json.JSONDecodeError, ValueError):
                        continue
        if not result:
            result = {'error': 'Parsing Claude response failed', 'raw': raw[:800]}
    except Exception as e:
        result = {'error': str(e)}

    data = {
        **result,
        'mergedPages': merged_pages,
        'period': {'start': start, 'end': end},
    }
    _cache_set(key, data, 1800)
    return jsonify(data)


def _fetch_gsc_raw(start, end, headers):
    """Internal: fetch GSC top pages + queries without caching."""
    base_url = f'https://www.googleapis.com/webmasters/v3/sites/{requests.utils.quote(GSC_SITE, safe="")}/searchAnalytics/query'

    def q(body):
        r = _post(base_url, headers=headers, json=body)
        return r.json() if r.ok else {}

    main = q({'startDate': start, 'endDate': end, 'rowLimit': 1})
    row = main.get('rows', [{}])[0]
    top_queries_data = q({
        'startDate': start, 'endDate': end,
        'dimensions': ['query'], 'rowLimit': 15,
        'orderBy': [{'fieldName': 'clicks', 'sortOrder': 'DESCENDING'}],
    })
    top_pages_data = q({
        'startDate': start, 'endDate': end,
        'dimensions': ['page'], 'rowLimit': 25,
        'orderBy': [{'fieldName': 'clicks', 'sortOrder': 'DESCENDING'}],
    })
    return {
        'clicks':      int(row.get('clicks', 0)),
        'impressions': int(row.get('impressions', 0)),
        'ctr':         round(row.get('ctr', 0) * 100, 2),
        'avgPosition': round(row.get('position', 0), 1),
        'topQueries': [{
            'query': r2['keys'][0], 'clicks': int(r2.get('clicks', 0)),
            'impressions': int(r2.get('impressions', 0)),
            'ctr': round(r2.get('ctr', 0) * 100, 2),
            'position': round(r2.get('position', 0), 1),
        } for r2 in top_queries_data.get('rows', [])],
        'topPages': [{
            'page': r2['keys'][0], 'clicks': int(r2.get('clicks', 0)),
            'impressions': int(r2.get('impressions', 0)),
            'ctr': round(r2.get('ctr', 0) * 100, 2),
            'position': round(r2.get('position', 0), 1),
        } for r2 in top_pages_data.get('rows', [])],
    }


def _fetch_ga4_funnel_raw(start, end, headers):
    url = f'https://analyticsdata.googleapis.com/v1beta/properties/{GA4_PROPERTY}:runReport'
    step_names = [
        ('sessions', 'Sessions'),
        ('engagedSessions', 'Sessions engagées'),
        ('conversions', 'Conversions'),
    ]
    body = {
        'dateRanges': [{'startDate': start, 'endDate': end}],
        'metrics': [{'name': m} for m, _ in step_names],
    }
    r = _post(url, headers=headers, json=body)
    steps = []
    if r.ok:
        try:
            vals = r.json()['rows'][0]['metricValues']
            steps = [{'label': lbl, 'value': int(float(vals[i]['value']))} for i, (_, lbl) in enumerate(step_names)]
        except Exception:
            pass
    return {'steps': steps}


def _fetch_ga4_geo_raw(start, end, headers):
    url = f'https://analyticsdata.googleapis.com/v1beta/properties/{GA4_PROPERTY}:runReport'
    body = {
        'dateRanges': [{'startDate': start, 'endDate': end}],
        'dimensions': [{'name': 'city'}],
        'metrics': [{'name': 'engagedSessions'}, {'name': 'engagementRate'}],
        'orderBys': [{'metric': {'metricName': 'engagedSessions'}, 'desc': True}],
        'limit': 10,
    }
    r = _post(url, headers=headers, json=body)
    if r.ok:
        return [{'city': row['dimensionValues'][0]['value'],
                 'engagedSessions': int(float(row['metricValues'][0]['value'])),
                 'engagementRate': round(float(row['metricValues'][1]['value']) * 100, 1)}
                for row in r.json().get('rows', [])]
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Veille IA & Marcom
# Génération : tâche planifiée Claude.ai → POST /veille-ia/ingest
# Lecture    : GET /veille-ia lit depuis GitHub raw URL, cache 1 h
# ─────────────────────────────────────────────────────────────────────────────

VEILLE_INGEST_TOKEN = os.environ.get('VEILLE_INGEST_TOKEN', '')
GITHUB_REPO         = 'Marcom-acl/dashboard'
GITHUB_FILE_PATH    = 'data/veille-ia-data.json'


def _write_veille_ia_to_github(data):
    """Écrit data/veille-ia-data.json sur GitHub via l'API REST."""
    if not GITHUB_TOKEN:
        return False
    import base64
    content_b64 = base64.b64encode(
        json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
    ).decode('ascii')
    api_url = f'https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}'
    headers = {'Authorization': f'token {GITHUB_TOKEN}', 'Accept': 'application/vnd.github.v3+json'}
    sha = None
    try:
        r = requests.get(api_url, headers=headers, timeout=10)
        if r.ok:
            sha = r.json().get('sha')
    except Exception:
        pass
    payload = {'message': f'chore: veille IA — {datetime.date.today().isoformat()}', 'content': content_b64}
    if sha:
        payload['sha'] = sha
    try:
        r = requests.put(api_url, headers=headers, json=payload, timeout=15)
        return r.ok
    except Exception:
        return False


@app.route('/veille-ia')
def veille_ia():
    cached = _cache_get('veille-ia-data')
    if cached:
        return jsonify(cached)
    try:
        r = requests.get(VEILLE_IA_DATA_URL, timeout=10, verify=_VERIFY)
        if not r.ok:
            return jsonify({'error': f'GitHub HTTP {r.status_code}'}), 503
        data = r.json()
    except Exception as e:
        return jsonify({'error': str(e)}), 503
    _cache_set('veille-ia-data', data, 3600)
    return jsonify(data)


@app.route('/veille-ia/ingest', methods=['POST'])
def veille_ia_ingest():
    """Reçoit les données de veille depuis la tâche planifiée Claude.ai.
    Fusionne avec l'historique existant : rétention 6 mois, dédoublonnage
    par (date + titre), les nouveaux items écrasent les doublons."""
    auth = request.headers.get('Authorization', '').replace('Bearer ', '').strip()
    if not VEILLE_INGEST_TOKEN or auth != VEILLE_INGEST_TOKEN:
        return jsonify({'error': 'Non autorisé'}), 401
    incoming = request.get_json(silent=True)
    if not incoming or 'items' not in incoming:
        return jsonify({'error': 'Body invalide — champ items requis'}), 400

    # Charger l'historique existant depuis GitHub ou le cache
    existing_items = []
    cached = _cache_get('veille-ia-data')
    if cached and 'items' in cached:
        existing_items = cached['items']
    else:
        try:
            r = requests.get(VEILLE_IA_DATA_URL, timeout=10, verify=_VERIFY)
            if r.ok:
                existing_items = r.json().get('items', [])
        except Exception:
            pass

    # Fusionner : les nouveaux items écrasent les doublons (date + titre)
    new_items = incoming.get('items', [])
    new_keys = {(i.get('date', ''), i.get('titre', '')) for i in new_items}
    merged = [i for i in existing_items if (i.get('date', ''), i.get('titre', '')) not in new_keys]
    merged.extend(new_items)

    # Rétention 6 mois
    cutoff = (datetime.date.today() - datetime.timedelta(days=183)).isoformat()
    merged = [i for i in merged if i.get('date', '') >= cutoff]

    # Trier du plus récent au plus ancien
    merged.sort(key=lambda i: i.get('date', ''), reverse=True)

    data = {'generated_at': incoming.get('generated_at', datetime.date.today().isoformat()), 'items': merged}
    written = _write_veille_ia_to_github(data)
    _cache_set('veille-ia-data', data, 3600)
    return jsonify({'ok': True, 'items': len(merged), 'added': len(new_items), 'github': written})


# ─────────────────────────────────────────────────────────────────────────────
# Startup — préchauffage des caches (Brevo Club + GA4 + GSC pour 30j)
# ─────────────────────────────────────────────────────────────────────────────

if BREVO_API_KEY:
    _CLUB_REFRESH_IN_PROGRESS = True
    threading.Thread(target=_brevo_club_refresh, daemon=True).start()


def _warmup_cache():
    end   = datetime.date.today().isoformat()
    start = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    jobs = [
        (f'ga4:{start}:{end}',     lambda: _parse_ga4_main(GA4_PROPERTY, start, end),              600),
        (f'ga4-auto:{start}:{end}', lambda: _parse_ga4_main(GA4_PROPERTY_AUTOTOURING, start, end), 600),
    ]
    for cache_key, fn, ttl in jobs:
        try:
            data = fn()
            _cache_set(cache_key, data, ttl)
        except Exception:
            pass

threading.Thread(target=_warmup_cache, daemon=True).start()



# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
