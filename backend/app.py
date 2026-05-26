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

# ── Env-var secrets ──────────────────────────────────────────────────────────
BREVO_API_KEY     = os.environ.get('BREVO_API_KEY', '')
FB_APP_ID         = os.environ.get('FB_APP_ID', '')
FB_APP_SECRET     = os.environ.get('FB_APP_SECRET', '')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
GOOGLE_CLIENT_ID     = os.environ.get('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '')
GOOGLE_REFRESH_TOKEN = os.environ.get('GOOGLE_REFRESH_TOKEN', '')
SUPERMETRICS_API_KEY = os.environ.get('SUPERMETRICS_API_KEY', '')

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
FB_AUTH_URL   = 'https://www.facebook.com/v19.0/dialog/oauth'
FB_TOKEN_URL  = 'https://graph.facebook.com/v19.0/oauth/access_token'
FB_GRAPH      = 'https://graph.facebook.com/v19.0'

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
    r = _get(FB_TOKEN_URL, params={
        'client_id':     FB_APP_ID,
        'client_secret': FB_APP_SECRET,
        'redirect_uri':  redirect_uri,
        'code':          code,
    })
    if not r.ok:
        return jsonify({'error': r.text}), 400
    token_data = r.json()
    _save_json(FB_TOKEN_PATH, token_data)
    access_token = token_data.get('access_token', '')
    return f'''<h2>✅ Facebook connecté !</h2>
<p>Token enregistré sur le serveur.</p>
<hr>
<p><strong>Pour rendre la connexion permanente</strong> (survit aux redéploiements Railway) :</p>
<ol>
  <li>Copiez ce token :<br>
    <textarea rows="4" style="width:100%;font-family:monospace;font-size:11px">{access_token}</textarea>
  </li>
  <li>Sur Railway → Variables → ajoutez :<br>
    <code>FB_TOKEN = [valeur ci-dessus]</code>
  </li>
</ol>
<p>Vous pouvez fermer cette fenêtre ensuite.</p>'''


# ─────────────────────────────────────────────────────────────────────────────
# LinkedIn OAuth helpers
# ─────────────────────────────────────────────────────────────────────────────

LI_TOKEN_PATH    = _token_path('linkedin_token.json')
LI_CONFIG_PATH   = _token_path('linkedin_config.json')
LI_AUTH_URL      = 'https://www.linkedin.com/oauth/v2/authorization'
LI_TOKEN_URL     = 'https://www.linkedin.com/oauth/v2/accessToken'
LI_API           = 'https://api.linkedin.com/v2'
LI_SCOPES        = ['r_organization_social', 'r_basicprofile', 'rw_organization_admin']

def _li_config():
    cfg = _load_json(LI_CONFIG_PATH) or {}
    return {
        'client_id':       cfg.get('client_id')       or os.environ.get('LI_CLIENT_ID', ''),
        'client_secret':   cfg.get('client_secret')   or os.environ.get('LI_CLIENT_SECRET', ''),
        'organization_id': cfg.get('organization_id') or os.environ.get('LI_ORGANIZATION_ID', ''),
    }

def _li_token():
    stored = _load_json(LI_TOKEN_PATH)
    if stored:
        return stored.get('access_token')
    return None

@app.route('/linkedin/auth')
def linkedin_auth():
    cfg = _li_config()
    if not cfg['client_id']:
        return jsonify({'error': 'LI_CLIENT_ID manquant'}), 503
    redirect_uri = f'{APP_URL}/linkedin/callback'
    scope = '%20'.join(LI_SCOPES)
    url = (
        f'{LI_AUTH_URL}?response_type=code'
        f'&client_id={cfg["client_id"]}'
        f'&redirect_uri={requests.utils.quote(redirect_uri)}'
        f'&scope={scope}&state=acl_marcom'
    )
    return redirect(url)

@app.route('/linkedin/callback')
def linkedin_callback():
    code = request.args.get('code')
    if not code:
        return jsonify({'error': 'Code manquant'}), 400
    cfg = _li_config()
    redirect_uri = f'{APP_URL}/linkedin/callback'
    r = _post(LI_TOKEN_URL, data={
        'grant_type':    'authorization_code',
        'code':          code,
        'redirect_uri':  redirect_uri,
        'client_id':     cfg['client_id'],
        'client_secret': cfg['client_secret'],
    })
    if not r.ok:
        return jsonify({'error': r.text}), 400
    _save_json(LI_TOKEN_PATH, r.json())
    return '<h2>LinkedIn connecté !</h2><p>Vous pouvez fermer cette fenêtre.</p>'


# ─────────────────────────────────────────────────────────────────────────────
# Status route
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/status')
def status():
    return jsonify({
        'status':    'ok',
        'google':    bool(_google_token()),
        'facebook':  bool(_fb_token()),
        'linkedin':  bool(_li_token()),
        'brevo':     bool(BREVO_API_KEY),
        'anthropic': bool(ANTHROPIC_API_KEY),
    })


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
    return jsonify(_parse_ga4_main(GA4_PROPERTY, start, end))


@app.route('/ga4/autotouring')
def ga4_autotouring():
    start, end = _date_range()
    return jsonify(_parse_ga4_main(GA4_PROPERTY_AUTOTOURING, start, end))


@app.route('/ga4/extended')
def ga4_extended():
    start, end = _date_range()
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

    return jsonify({
        'newVsReturning':    nvr,
        'markets':           markets,
        'entryPages':        entry_pages,
        'keyEvents':         events,
        'devices':           devices,
        'conversionByChannel': conv_by_channel,
    })


@app.route('/ga4/funnel')
def ga4_funnel():
    start, end = _date_range()
    headers = _google_headers()
    if not headers:
        return jsonify({'error': 'Google non connecté'})

    url = f'https://analyticsdata.googleapis.com/v1beta/properties/{GA4_PROPERTY}:runReport'

    steps_metrics = [
        ('sessions',    'Sessions'),
        ('engagedSessions', 'Sessions engagées'),
        ('conversions', 'Conversions'),
    ]

    body = {
        'dateRanges': [{'startDate': start, 'endDate': end}],
        'metrics': [{'name': m} for m, _ in steps_metrics],
    }
    r = _post(url, headers=headers, json=body)
    steps = []
    conversion_rate = 0
    if r.ok:
        try:
            vals = r.json()['rows'][0]['metricValues']
            values = [int(float(v['value'])) for v in vals]
            for i, (_, label) in enumerate(steps_metrics):
                steps.append({'label': label, 'value': values[i]})
            if values[0]:
                conversion_rate = round(values[-1] / values[0] * 100, 2)
        except Exception:
            pass

    return jsonify({'steps': steps, 'conversionRate': conversion_rate})


@app.route('/ga4/trend')
def ga4_trend():
    """Daily sessions + users for the selected period — powers the line chart."""
    start, end = _date_range()
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
    return jsonify({'trend': trend})


# ─────────────────────────────────────────────────────────────────────────────
# Google Search Console
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/gsc')
def gsc():
    start, end = _date_range()
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
        {'date': row['keys'][0], 'clicks': int(row.get('clicks', 0))}
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

    return jsonify({
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
    })


# ─────────────────────────────────────────────────────────────────────────────
# Facebook Ads
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/fb')
def fb_ads():
    start, end = _date_range()
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

    return jsonify({
        'spend':       spend,
        'impressions': totals['impressions'],
        'clicks':      totals['clicks'],
        'conversions': round(revenue, 2),
        'ctr':         round(totals['ctr'], 2),
        'cpc':         round(totals['cpc'], 2),
        'cpm':         round(totals['cpm'], 2),
        'roas':        roas,
        'topCampaigns': sorted(campaigns, key=lambda x: x['spend'], reverse=True)[:10],
    })


@app.route('/fb/page')
def fb_page():
    start, end = _date_range()
    token = _fb_token()
    if not token:
        return jsonify({'error': 'Facebook non connecté — visiter /fb/auth'})

    pages_data = []
    all_posts  = []
    impressions_total  = 0
    engaged_total      = 0
    engagements_total  = 0

    for page_name, page_id in FB_PAGES.items():
        # Fan count
        ri = _get(f'{FB_GRAPH}/{page_id}', params={'fields': 'fan_count,followers_count', 'access_token': token})
        fans = 0
        if ri.ok:
            d = ri.json()
            fans = d.get('fan_count') or d.get('followers_count', 0)

        # Insights
        rin = _get(f'{FB_GRAPH}/{page_id}/insights', params={
            'metric':       'page_impressions,page_engaged_users,page_post_engagements',
            'period':       'day',
            'since':        start,
            'until':        end,
            'access_token': token,
        })
        if rin.ok:
            for metric_data in rin.json().get('data', []):
                total_val = sum(v.get('value', 0) for v in metric_data.get('values', []))
                mn = metric_data.get('name', '')
                if mn == 'page_impressions':
                    impressions_total += total_val
                elif mn == 'page_engaged_users':
                    engaged_total += total_val
                elif mn == 'page_post_engagements':
                    engagements_total += total_val

        pages_data.append({'name': page_name, 'id': page_id, 'fans': fans})

        # Posts
        rp = _get(f'{FB_GRAPH}/{page_id}/posts', params={
            'fields':       'message,created_time,likes.summary(true),comments.summary(true)',
            'since':        start,
            'until':        end,
            'access_token': token,
            'limit':        25,
        })
        if rp.ok:
            for post in rp.json().get('data', []):
                likes   = post.get('likes',    {}).get('summary', {}).get('total_count', 0)
                comments = post.get('comments', {}).get('summary', {}).get('total_count', 0)
                all_posts.append({
                    'page':       page_name,
                    'message':    (post.get('message') or '')[:80],
                    'likes':      likes,
                    'comments':   comments,
                    'engagement': likes + comments,
                })

    all_posts.sort(key=lambda x: x['engagement'], reverse=True)

    return jsonify({
        'pages':    pages_data,
        'totals':   {
            'impressions':       impressions_total,
            'engaged_users':     engaged_total,
            'post_engagements':  engagements_total,
        },
        'topPosts': all_posts[:10],
        'ytd':      {},
    })


# ─────────────────────────────────────────────────────────────────────────────
# Brevo
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/brevo')
def brevo():
    if not BREVO_API_KEY:
        return jsonify({'error': 'BREVO_API_KEY manquante'})

    start, end = _date_range()
    headers = {'api-key': BREVO_API_KEY, 'Accept': 'application/json'}
    BREVO_BASE = 'https://api.brevo.com/v3'

    # Fetch last 50 sent campaigns — no date filter to avoid empty results
    rc = _get(f'{BREVO_BASE}/emailCampaigns', headers=headers, params={
        'status': 'sent', 'limit': 50, 'offset': 0,
        'sort': 'desc', 'statistics': 'globalStats',
    })
    campaigns = []
    if rc.ok:
        for c in rc.json().get('campaigns', []):
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

    # Contact stats — liste #64 (membres ACL)
    LIST_ID = 64
    rl = _get(f'{BREVO_BASE}/contacts/lists/{LIST_ID}', headers=headers)
    list_data = rl.json() if rl.ok else {}
    subscribed_64  = list_data.get('totalSubscribers', 0)
    blacklisted_64 = list_data.get('totalBlacklisted', 0)
    total_64       = subscribed_64 + blacklisted_64

    # Hard bounces — 6 derniers mois via smtp/statistics/globalStats
    today_str       = datetime.datetime.utcnow().strftime('%Y-%m-%d')
    six_months_ago  = (datetime.datetime.utcnow() - datetime.timedelta(days=180)).strftime('%Y-%m-%d')
    rh = _get(f'{BREVO_BASE}/smtp/statistics/globalStats', headers=headers,
              params={'startDate': six_months_ago, 'endDate': today_str})
    hard_bounces = rh.json().get('hardBounces', 0) if rh.ok else 0

    # Désinscriptions totales — cumul depuis 2015
    ru = _get(f'{BREVO_BASE}/smtp/statistics/globalStats', headers=headers,
              params={'startDate': '2015-01-01', 'endDate': today_str})
    global_unsubscribed = ru.json().get('unsubscriptions', 0) if ru.ok else 0

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

    return jsonify({'campaigns': campaigns, 'contactStats': contact_stats, 'avgOpenRate': avg_open_rate})


# ─────────────────────────────────────────────────────────────────────────────
# Brevo — ACL Club
# ─────────────────────────────────────────────────────────────────────────────


@app.route('/brevo/club')
def brevo_club():
    if not BREVO_API_KEY:
        return jsonify({'error': 'BREVO_API_KEY manquante'})

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
        return tid, results

    # 3a-only: fetch reach first (serial per batch of 20)
    reach_tasks     = [(tid, cs, ce) for tid in all_tids for cs, ce in chunks]
    template_reach  = {tid: [] for tid in all_tids}

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
        for tid, emails in ex.map(fetch_reach, reach_tasks):
            template_reach[tid].extend(emails)

    # 3b. Open events via /smtp/statistics/events?templateId=X&event=uniqueOpened
    active_tids = [tid for tid in all_tids if template_reach[tid]]

    def fetch_open_events(args):
        tid, cs, ce = args
        results, off = [], 0
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
        return tid, results

    events_tasks   = [(tid, cs, ce) for tid in active_tids for cs, ce in chunks]
    template_events = {tid: [] for tid in active_tids}

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
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

    return jsonify({
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
    })


# ─────────────────────────────────────────────────────────────────────────────
# YouTube
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/youtube')
def youtube():
    start, end = _date_range()
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

    return jsonify({
        'subscribers':  subscribers,
        'totalViews':   total_views,
        'videoCount':   video_count,
        'periodVideos': period_videos,
        'topVideos':    sorted(videos, key=lambda x: x['views'], reverse=True),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Supermetrics helpers
# ─────────────────────────────────────────────────────────────────────────────

SUPERMETRICS_QUERY_URL  = 'https://api.supermetrics.com/enterprise/v2/query/data/json'
SUPERMETRICS_LI_ACCOUNT = '10097790'  # ACL - Automobile Club du Luxembourg


def _supermetrics_query(ds_id, ds_accounts, fields, start_date, end_date, max_rows=500):
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

    r = _get(SUPERMETRICS_QUERY_URL, params={'json': json.dumps(query)}, timeout=90)
    if not r.ok:
        return None, None, f'Supermetrics HTTP {r.status_code}: {r.text[:200]}'

    body = r.json()

    rows   = body.get('data', [])
    raw_sc = body.get('schema', fields)
    schema = [s.get('id', s) if isinstance(s, dict) else s for s in raw_sc]
    return rows, schema, None


def _sm_rows_to_dicts(rows, schema):
    """Convert Supermetrics [row, ...] + schema into list of dicts."""
    return [dict(zip(schema, row)) for row in (rows or [])]


# ─────────────────────────────────────────────────────────────────────────────
# LinkedIn
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/linkedin')
def linkedin():
    start, end = _date_range()
    token = _li_token()
    cfg   = _li_config()
    if not token:
        return jsonify({'error': 'LinkedIn non connecté — visiter /linkedin/auth'})

    headers    = {'Authorization': f'Bearer {token}', 'X-Restli-Protocol-Version': '2.0.0'}
    org_id     = cfg.get('organization_id', '')
    LI_API_URL = 'https://api.linkedin.com/v2'

    if not org_id:
        return jsonify({'error': 'LI_ORGANIZATION_ID manquant'})

    # Follower count
    rf = _get(f'{LI_API_URL}/organizationalEntityFollowerStatistics', headers=headers, params={
        'q':                      'organizationalEntity',
        'organizationalEntity':   f'urn:li:organization:{org_id}',
    })
    followers = 0
    if rf.ok:
        for el in rf.json().get('elements', []):
            followers += el.get('totalFollowerCount', 0)
            break

    # Share statistics
    start_dt = int(datetime.datetime.fromisoformat(start).timestamp() * 1000)
    end_dt   = int(datetime.datetime.fromisoformat(end).timestamp() * 1000)

    rs = _get(f'{LI_API_URL}/organizationalEntityShareStatistics', headers=headers, params={
        'q':                      'organizationalEntity',
        'organizationalEntity':   f'urn:li:organization:{org_id}',
        'timeIntervals.timeGranularityType': 'DAY',
        'timeIntervals.start':    start_dt,
        'timeIntervals.end':      end_dt,
    })
    impressions  = 0
    clicks       = 0
    engagements  = 0
    if rs.ok:
        for el in rs.json().get('elements', []):
            ts = el.get('totalShareStatistics', {})
            impressions += ts.get('impressionCount', 0)
            clicks      += ts.get('clickCount', 0)
            engagements += ts.get('engagement', 0)

    # Recent posts
    rp = _get(f'{LI_API_URL}/shares', headers=headers, params={
        'q':    'owners',
        'owners': f'urn:li:organization:{org_id}',
        'count': 20,
    })
    posts = []
    if rp.ok:
        for item in rp.json().get('elements', []):
            text = ''
            try:
                text = item['text']['text'][:80]
            except Exception:
                pass
            activity = item.get('activity', '')
            # Get share stats for each post (simplified)
            posts.append({
                'text':        text,
                'activity':    activity,
                'impressions': 0,
                'clicks':      0,
                'reactions':   0,
            })

    return jsonify({
        'followers':        followers,
        'totalFollowers':   followers,
        'impressions':      impressions,
        'totalImpressions': impressions,
        'clicks':           clicks,
        'totalClicks':      clicks,
        'engagements':      engagements,
        'totalEngagements': engagements,
        'topPosts':         posts[:10],
        'recentPosts':      posts[:10],
    })


@app.route('/supermetrics/linkedin')
def supermetrics_linkedin():
    """LinkedIn Pages analytics via Supermetrics (account 10097790 — ACL)."""
    start, end = _date_range()
    if not SUPERMETRICS_API_KEY:
        return jsonify({'error': 'SUPERMETRICS_API_KEY manquant — ajouter la variable sur Railway'}), 503

    acc = SUPERMETRICS_LI_ACCOUNT

    sm_errors = []  # capture les erreurs Supermetrics pour diagnostic

    # ── 1. Performance par date (share_statistics, report_type 6) ────────────
    perf_fields = ['date', 'page_impressions', 'page_clicks', 'page_engagements',
                   'page_engagement_rate', 'page_likes', 'page_comments', 'page_shares']
    rows1, sc1, err1 = _supermetrics_query('LIP', acc, perf_fields, start, end)
    if err1: sm_errors.append(f'perf: {err1}')
    items1 = _sm_rows_to_dicts(rows1, sc1 or perf_fields)

    trend = [{'date': r.get('date', ''),
              'impressions': r.get('page_impressions', 0) or 0,
              'engagements': r.get('page_engagements', 0) or 0} for r in items1]

    def _sum(key): return sum(r.get(key, 0) or 0 for r in items1)
    avg_er = (sum(r.get('page_engagement_rate', 0) or 0 for r in items1) / len(items1)) if items1 else 0

    # ── 2. Croissance abonnés par date (follower_statistics, report_type 4) ──
    fol_fields = ['date', 'followers_gain_total', 'followers_gain_organic', 'followers_gain_paid']
    rows2, sc2, err2 = _supermetrics_query('LIP', acc, fol_fields, start, end)
    if err2: sm_errors.append(f'followers: {err2}')
    items2 = _sm_rows_to_dicts(rows2, sc2 or fol_fields)

    new_followers = sum(r.get('followers_gain_total', 0) or 0 for r in items2)
    gain_by_date  = {r.get('date', ''): r.get('followers_gain_total', 0) or 0 for r in items2}
    for t in trend:
        t['newFollowers'] = gain_by_date.get(t['date'], 0)

    # ── 3. Total abonnés (company_statistics, report_type 3) ─────────────────
    rows3, sc3, err3 = _supermetrics_query('LIP', acc, ['follower_count'], start, end, max_rows=1)
    if err3: sm_errors.append(f'follower_count: {err3}')
    items3 = _sm_rows_to_dicts(rows3, sc3 or ['follower_count'])
    follower_count = int(items3[-1].get('follower_count', 0) or 0) if items3 else 0

    # ── 4. Top posts (update_details, report_type 10) ─────────────────────────
    post_fields = ['update_title', 'update_share_comment', 'update_url',
                   'update_share_media_category', 'page_impressions', 'page_clicks',
                   'page_likes', 'page_comments', 'page_shares', 'page_engagement_rate']
    rows4, sc4, err4 = _supermetrics_query('LIP', acc, post_fields, start, end, max_rows=50)
    if err4: sm_errors.append(f'posts: {err4}')
    items4 = _sm_rows_to_dicts(rows4, sc4 or post_fields)

    posts = [{
        'title':          (r.get('update_title') or r.get('update_share_comment', ''))[:80],
        'text':           (r.get('update_share_comment') or '')[:120],
        'url':            r.get('update_url', ''),
        'mediaCategory':  r.get('update_share_media_category', ''),
        'impressions':    r.get('page_impressions', 0) or 0,
        'clicks':         r.get('page_clicks', 0) or 0,
        'likes':          r.get('page_likes', 0) or 0,
        'comments':       r.get('page_comments', 0) or 0,
        'shares':         r.get('page_shares', 0) or 0,
        'engagementRate': round(float(r.get('page_engagement_rate', 0) or 0), 2),
    } for r in items4[:20]]

    # ── 5. Démographie par pays (company_statistics, report_type 3) ──────────
    rows5, sc5, err5 = _supermetrics_query('LIP', acc, ['follower_country', 'follower_count'], start, end)
    if err5: sm_errors.append(f'countries: {err5}')
    items5 = _sm_rows_to_dicts(rows5, sc5 or ['follower_country', 'follower_count'])
    countries = sorted(
        [{'name': r.get('follower_country', ''), 'count': r.get('follower_count', 0) or 0} for r in items5],
        key=lambda x: x['count'], reverse=True
    )[:10]

    # ── 6. Démographie par secteur ────────────────────────────────────────────
    rows6, sc6, err6 = _supermetrics_query('LIP', acc, ['follower_industry', 'follower_count'], start, end)
    if err6: sm_errors.append(f'industries: {err6}')
    items6 = _sm_rows_to_dicts(rows6, sc6 or ['follower_industry', 'follower_count'])
    industries = sorted(
        [{'name': r.get('follower_industry', ''), 'count': r.get('follower_count', 0) or 0} for r in items6],
        key=lambda x: x['count'], reverse=True
    )[:8]

    return jsonify({
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
    })


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

    lines += [
        "",
        "## Instructions",
        "Réponds UNIQUEMENT avec un tableau JSON valide (pas de markdown, pas d'explication).",
        f"Génère exactement 5 à 6 recommandations prioritaires pour ACL Luxembourg.",
        "Inclure AU MOINS 1 recommandation de type 'low' (opportunité de croissance identifiée).",
        "Dans le champ 'insight', comparer le KPI au benchmark fourni et quantifier l'écart.",
        "Dans le champ 'action', proposer une action concrète, mesurable, réalisable sous 2 semaines.",
        "Format exact (JSON array, rien d'autre) :",
        '[{"priority":"high|med|low","source":"GA4|Meta|GSC|Brevo|LinkedIn|YouTube","title":"titre court max 8 mots","insight":"observation avec écart au benchmark","action":"action concrète et mesurable"}]',
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
        # Extract JSON array from response
        match = re.search(r'\[.*\]', text, re.DOTALL)
        insights = json.loads(match.group()) if match else []
        return jsonify({'insights': insights})
    except Exception as e:
        return jsonify({'error': str(e), 'insights': []}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
