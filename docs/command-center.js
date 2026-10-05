/* ════════════════════════════════════════════════════════════════
   COMMAND CENTER — Vue d'ensemble ACL Marcom
   - Hero : salutation, résumé IA, score de santé expliqué, fraîcheur
   - « Depuis votre dernière visite » : variations des stocks clés
   - Mission Control : objectifs annuels éditables (admin, via backend)
   - Bento : une tuile par section du menu, construite depuis la sidebar
   - Palette ⌘K / Ctrl+K : recherche de sections, indicateurs, objectifs
   Dépend des globales de index.html (API_BASE, api, fmtN, fmtPct,
   fmtEur, fmtDur, globalStart/End/Days, renderCalendar, …) — résolues
   à l'appel uniquement, d'où le stub renderOverview() côté index.html.
   ════════════════════════════════════════════════════════════════ */
(function () {
'use strict';

const CC = window.CC = {};
const RM = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// ── Utilitaires ──────────────────────────────────────────────────
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ok  = x => (x && !x.error) ? x : null;
const num = v => (typeof v === 'number' && isFinite(v)) ? v : null;
const lsGet = (k, d = null) => { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } };
const lsSet = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };
const ssGet = k => { try { return JSON.parse(sessionStorage.getItem(k) || 'null'); } catch (e) { return null; } };
const ssSet = (k, v) => { try { v == null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };
const session = () => lsGet('acl_dash_session');
const isAdmin = () => session()?.role === 'admin';
const fmtDec = (v, d = 1) => (v ?? 0).toLocaleString('fr-BE', {minimumFractionDigits: d, maximumFractionDigits: d});
const compact = n => {
  if (n == null) return '—';
  const a = Math.abs(n);
  if (a >= 1e6) return (n / 1e6).toLocaleString('fr-BE', {maximumFractionDigits: 1}) + ' M';
  if (a >= 1e4) return (n / 1e3).toLocaleString('fr-BE', {maximumFractionDigits: 1}) + ' k';
  return fmtN(n);
};
const pctChange = (a, b) => (num(a) != null && b) ? (a - b) / b * 100 : null;
const inPeriod  = iso => { const s = (iso || '').slice(0, 10); return (!globalStart || s >= globalStart) && (!globalEnd || s <= globalEnd); };
const ageDays   = iso => iso ? (Date.now() - new Date(iso).getTime()) / 864e5 : null;
const dShort    = iso => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleDateString('fr-BE', {day: 'numeric', month: 'short'}); };
const dLong     = iso => { const d = new Date(iso); return isNaN(d) ? '' : d.toLocaleDateString('fr-BE', {day: 'numeric', month: 'long', year: 'numeric'}); };
const rel = ts => {
  const s = (ts - Date.now()) / 1000, f = new Intl.RelativeTimeFormat('fr', {numeric: 'auto'});
  const a = Math.abs(s);
  if (a < 3600)  return f.format(Math.round(s / 60), 'minute');
  if (a < 86400) return f.format(Math.round(s / 3600), 'hour');
  return f.format(Math.round(s / 86400), 'day');
};
function yearProgress() {
  const n = new Date(), y = n.getFullYear();
  const s = new Date(y, 0, 1), e = new Date(y + 1, 0, 1);
  return {year: y, days: Math.max(1, Math.round((n - s) / 864e5)), frac: (n - s) / (e - s)};
}
const ICON = {
  arrow:  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
  search: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
  pencil: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>',
  chev:   '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><polyline points="6 9 12 15 18 9"/></svg>',
  x:      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>',
  bolt:   '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z"/></svg>',
  target: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/></svg>',
};

// Couleurs de tuiles : celles de la sidebar, sauf le navy (hors palette UI)
// et le quasi-noir, remplacés par l'encre courante (lisible en clair/sombre).
const COLOR_OVERRIDE = {'car-market': 'var(--text)', 'poweron': 'var(--text)'};

// ════════════════════════════════════════════════════════════════
// 1. CATALOGUE DE MÉTRIQUES (objectifs)
// kind : 'stock' (valeur courante), 'rate' (moyenne/rythme), 'cumulative'
// (somme depuis le 1er janvier → projection fin d'année).
// ════════════════════════════════════════════════════════════════
function fbPage(d, re) {
  const ytd = (d.dFBPytd?.pages || []).find(p => re.test(p.name));
  const per = (d.dFBP?.pages || []).find(p => re.test(p.name));
  return {fans: ytd?.fans || per?.fans || 0, ytdPosts: ytd?.posts_count || 0, perPosts: per?.posts_count || 0, found: !!(ytd || per)};
}
function postsWeekly(d, re) {
  const p = fbPage(d, re);
  if (!p.found) return null;
  return +(p.ytdPosts / yearProgress().days * 7).toFixed(2);
}
function brevoOpenRates(d) {
  const camps = ok(d.dBrevo)?.campaigns;
  if (!camps) return null;
  const isExcluded = n => /relance|reactivat|desactiv|dormant|enquete|sondage|survey|satisfaction/.test((n || '').toLowerCase());
  const agg = list => {
    let dl = 0, op = 0;
    list.forEach(c => { const g = c.statistics?.globalStats || {}; dl += g.delivered || 0; op += (g.uniqueOpens || g.uniqueViews) || 0; });
    return dl ? op / dl * 100 : null;
  };
  return {mkt: agg(camps.filter(c => !isExcluded(c.name))), all: d.dBrevo.avgOpenRate ?? agg(camps)};
}

const METRICS = {
  fb_fans_sport:    {group: 'Facebook · Abonnés', label: 'ACL Sport — Abonnés page', color: '#1877F2', kind: 'stock', unit: 'abonnés', fmt: v => fmtN(v),
                     get: d => fbPage(d, /sport/i).fans || null},
  fb_fans_karting:  {group: 'Facebook · Abonnés', label: 'ACL Karting — Abonnés page', color: '#1877F2', kind: 'stock', unit: 'abonnés', fmt: v => fmtN(v),
                     get: d => fbPage(d, /karting/i).fans || null},
  fb_fans_acl:      {group: 'Facebook · Abonnés', label: 'ACL — Abonnés page', color: '#1877F2', kind: 'stock', unit: 'abonnés', fmt: v => fmtN(v),
                     get: d => fbPage(d, /^acl$/i).fans || null},
  fb_posts_sport:   {group: 'Facebook · Posts', label: 'ACL Sport — Rythme de publication', color: '#F4B400', kind: 'rate', unit: '/sem', fmt: v => fmtDec(v) + '/sem',
                     get: d => postsWeekly(d, /sport/i),
                     note: d => { const p = fbPage(d, /sport/i); return `<b>${p.ytdPosts}</b> posts depuis janvier · ≈${Math.round(p.ytdPosts / yearProgress().days * 365)}/an`; }},
  fb_posts_karting: {group: 'Facebook · Posts', label: 'ACL Karting — Rythme de publication', color: '#F4B400', kind: 'rate', unit: '/sem', fmt: v => fmtDec(v) + '/sem',
                     get: d => postsWeekly(d, /karting/i),
                     note: d => { const p = fbPage(d, /karting/i); return `<b>${p.ytdPosts}</b> posts depuis janvier · ≈${Math.round(p.ytdPosts / yearProgress().days * 365)}/an`; }},
  meta_impr:        {group: 'Meta Ads', label: 'Impressions publicitaires', color: '#1877F2', kind: 'cumulative', unit: 'impr.', fmt: v => compact(v),
                     get: d => num(d.dFBytd?.impressions || d.dFBytd?.totalImpressions) || null},
  fb_engaged:       {group: 'Facebook · Engagement', label: 'Utilisateurs engagés', color: '#1877F2', kind: 'cumulative', unit: 'utilisateurs', fmt: v => compact(v),
                     get: d => num(d.dFBPytd?.totals?.engaged_users) || null},
  brevo_open:       {group: 'Marketing Emails', label: "Taux d'ouverture moyen", color: '#0B996E', kind: 'rate', unit: '%', fmt: v => fmtPct(v),
                     get: d => brevoOpenRates(d)?.mkt ?? null,
                     note: d => `Hors relances &amp; enquêtes · toutes campagnes : <b>${fmtPct(brevoOpenRates(d)?.all)}</b>`},
  li_followers:     {group: 'LinkedIn', label: 'Abonnés page LinkedIn', color: '#0A66C2', kind: 'stock', unit: 'abonnés', fmt: v => fmtN(v),
                     get: d => num(ok(d.dLI)?.summary?.followerCount ?? ok(d.dLI)?.followers)},
  yt_subscribers:   {group: 'YouTube', label: 'Abonnés chaîne YouTube', color: '#FF0000', kind: 'stock', unit: 'abonnés', fmt: v => fmtN(v),
                     get: d => num(ok(d.dYT)?.subscribers)},
  club_leads:       {group: 'Avantages Club', label: 'Leads partenaires', color: '#F4B400', kind: 'cumulative', unit: 'leads', fmt: v => fmtN(v),
                     get: d => num(ok(d.dPartners)?.leads?.leadsTotalYear)},
};

const DEFAULT_OBJECTIVES = {year: new Date().getFullYear(), items: [
  {id: 'd1', metric: 'fb_fans_sport',    target: 2500},
  {id: 'd2', metric: 'fb_fans_karting',  target: 3000},
  {id: 'd3', metric: 'fb_posts_sport',   target: 1.5},
  {id: 'd4', metric: 'fb_posts_karting', target: 1.5},
  {id: 'd5', metric: 'meta_impr',        target: 2000000},
  {id: 'd6', metric: 'brevo_open',       target: 65},
]};

const STATUS = {
  done:  {label: 'Atteint',        icon: '✓'},
  on:    {label: 'En bonne voie',  icon: '↗'},
  watch: {label: 'À surveiller',   icon: '!'},
  late:  {label: 'En retard',      icon: '↓'},
  na:    {label: 'Pas de donnée',  icon: '–'},
};

function evalObjective(o, d) {
  const m = METRICS[o.metric];
  if (!m) return null;
  const cur = num(m.get(d));
  const yp  = yearProgress();
  if (cur == null) return {m, o, cur: null, pct: 0, status: 'na'};
  const pct = cur / o.target;
  let status, proj = null, expected = null;
  if (m.kind === 'cumulative') {
    expected = yp.frac;
    proj     = cur / yp.frac;
    const r  = proj / o.target;
    status   = pct >= 1 ? 'done' : r >= 1 ? 'on' : r >= .85 ? 'watch' : 'late';
  } else {
    const onT = m.kind === 'rate' ? .95 : .9;
    status = pct >= 1 ? 'done' : pct >= onT ? 'on' : pct >= .75 ? 'watch' : 'late';
  }
  return {m, o, cur, pct, proj, expected, status};
}

// ── Chargement / sauvegarde (backend /api/objectives) ───────────
CC.objCfg = lsGet('cc_objectives_cache');
CC.objMeta = {persist: null, loaded: false};
const objCfg = () => (CC.objCfg && Array.isArray(CC.objCfg.items)) ? CC.objCfg : DEFAULT_OBJECTIVES;

async function loadObjectives() {
  try {
    const r = await fetch(`${API_BASE}/api/objectives`, {signal: AbortSignal.timeout(8000)});
    if (r.ok) {
      const j = await r.json();
      CC.objMeta.persist = j.persist;
      if (j.objectives) { CC.objCfg = j.objectives; lsSet('cc_objectives_cache', j.objectives); }
    }
  } catch (e) { /* cache local ou valeurs par défaut */ }
  CC.objMeta.loaded = true;
}
CC._objPromise = null;
const ensureObjectives = () => (CC._objPromise ||= loadObjectives());

async function saveObjectives(payload, password) {
  let tok = ssGet('cc_admin_token');
  if (!tok || tok.exp * 1000 < Date.now() + 60000) {
    if (!password) return {needPw: true};
    const r = await fetch(`${API_BASE}/api/objectives/unlock`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({password}), signal: AbortSignal.timeout(10000),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) return {needPw: true, error: j.error || `HTTP ${r.status}`};
    tok = {token: j.token, exp: j.expires};
    ssSet('cc_admin_token', tok);
  }
  const r = await fetch(`${API_BASE}/api/objectives`, {
    method: 'PUT', headers: {'Content-Type': 'application/json', 'Authorization': `Bearer ${tok.token}`},
    body: JSON.stringify(payload), signal: AbortSignal.timeout(15000),
  });
  const j = await r.json().catch(() => ({}));
  if (r.status === 401) { ssSet('cc_admin_token', null); return {needPw: true, error: j.error}; }
  if (!r.ok) return {error: j.error || `HTTP ${r.status}`};
  return j;
}

// ════════════════════════════════════════════════════════════════
// 2. TUILES — une par onglet de la sidebar
// get(d) → null (source indisponible) ou {value, fmt, unit, delta,
// spark:{values, labels, fmt}, bars, sub:[[k, v, cls]], foot, desc,
// fresh:'ok'|'warn'|'err'}
// ════════════════════════════════════════════════════════════════
CC.lazy = {};   // sources chargées par la vue d'ensemble elle-même

function freshByAge(iso, maxDays) {
  const a = ageDays(iso);
  return a == null ? 'ok' : a > maxDays ? 'warn' : 'ok';
}

const TILES = {
  ga4: {wide: true, get: d => {
    const g = ok(d.dGA4); if (!g) return null;
    const t = d.dGA4T?.trend || [];
    return {value: g.sessions, fmt: fmtN, unit: 'sessions acl.lu', delta: {v: g.deltas?.sessions},
      spark: {values: t.map(x => x.sessions), labels: t.map(x => dShort(x.date)), fmt: fmtN},
      sub: [['Utilisateurs', fmtN(g.users)], ['Engagement', fmtPct(g.engagementRate)], ['Durée moy.', fmtDur(g.avgSessionDuration)], ['Rebond', fmtPct(g.bounceRate)]]};
  }},
  gsc: {wide: true, get: d => {
    const g = ok(d.dGSC); if (!g) return null;
    const t = g.trend || [];
    const pd = g.deltas?.avgPosition;
    return {value: g.clicks, fmt: fmtN, unit: 'clics Google', delta: {v: g.deltas?.clicks},
      spark: {values: t.map(x => x.clicks), labels: t.map(x => dShort(x.date)), fmt: fmtN},
      sub: [['Impressions', compact(g.impressions)], ['Position moy.', (g.avgPosition || 0).toFixed(1), pd == null ? '' : pd <= 0 ? 'good' : 'bad'], ['CTR', fmtPct(g.ctr)]]};
  }},
  sections: {get: d => {
    const s = (ok(d.dSections)?.sections || []).filter(x => !x.parent);
    if (!s.length) return null;
    const top = [...s].sort((a, b) => (b.views || 0) - (a.views || 0));
    return {value: top[0].views, fmt: fmtN, unit: `vues · ${esc(top[0].label)}`,
      bars: top.slice(0, 4).map(x => ({l: x.label, v: x.views || 0})),
      foot: `${s.length} sections suivies sur acl.lu`};
  }},
  fbads: {get: d => {
    const f = ok(d.dFB); if (!f) return null;
    const fans = (d.dFBP?.pages || []).reduce((a, p) => a + (p.fans || 0), 0);
    return {value: f.impressions, fmt: compact, unit: 'impressions Ads',
      sub: [['Budget', fmtEur(f.spend)], ['Clics', compact(f.clicks)], ['CTR', fmtPct(f.ctr)]],
      foot: fans ? `${fmtN(fans)} abonnés sur ${(d.dFBP.pages || []).length} pages Facebook` : ''};
  }},
  brevo: {get: d => {
    const b = ok(d.dBrevo); if (!b) return null;
    const camps = (b.campaigns || []).filter(c => c.sentDate).sort((a, c) => a.sentDate.localeCompare(c.sentDate));
    const rate = c => { const g = c.statistics?.globalStats || {}; return g.delivered ? ((g.uniqueOpens || g.uniqueViews) || 0) / g.delivered * 100 : null; };
    const period = camps.filter(c => inPeriod(c.sentDate));
    const last = camps.slice(-14).filter(c => rate(c) != null);
    const dl = period.reduce((a, c) => a + (c.statistics?.globalStats?.delivered || 0), 0);
    const op = period.reduce((a, c) => a + ((c.statistics?.globalStats?.uniqueOpens || c.statistics?.globalStats?.uniqueViews) || 0), 0);
    return {value: dl ? op / dl * 100 : b.avgOpenRate, fmt: fmtPct, unit: dl ? "taux d'ouverture (période)" : "taux d'ouverture moyen",
      spark: {values: last.map(rate), labels: last.map(c => `${dShort(c.sentDate)} · ${c.name}`), fmt: fmtPct},
      sub: [['Campagnes', fmtN(period.length)], ['Délivrés', compact(dl)], ['Contacts', compact(b.contactStats?.subscribed)]]};
  }},
  partners: {get: d => {
    const p = ok(d.dPartners); if (!p) return null;
    const cs = p.meta?.clicksSeries || [];
    const L = p.leads || {};
    return {value: p.ga4?.sessions, fmt: fmtN, unit: 'sessions pages partenaires',
      spark: {values: cs.map(x => x.value), labels: cs.map(x => dShort(x.date)), fmt: v => `${fmtN(v)} clics Meta`},
      sub: [['Reach Meta', compact(p.meta?.reach)], ['Clics liens', fmtN(p.meta?.linkClicks)], ['Leads ' + new Date().getFullYear(), fmtN(L.leadsTotalYear ?? L.total)]]};
  }},
  linkedin: {get: d => {
    const l = ok(d.dLI); if (!l) return null;
    const s = l.summary || l, t = l.trend || [];
    return {value: s.followerCount ?? l.followers, fmt: fmtN, unit: 'abonnés',
      delta: s.newFollowers ? {v: s.newFollowers, kind: 'abs', label: 'sur la période'} : null,
      spark: {values: t.map(x => x.impressions || 0), labels: t.map(x => dShort(x.date)), fmt: v => `${fmtN(v)} impressions`},
      sub: [['Impressions', compact(s.impressions)], ['Engagement', fmtPct(s.engagementRate)], ['Clics', fmtN(s.clicks)]]};
  }},
  youtube: {get: d => {
    const y = ok(d.dYT); if (!y) return null;
    const v = [...(y.topVideos || [])].filter(x => x.date).sort((a, b) => a.date.localeCompare(b.date));
    return {value: y.subscribers, fmt: fmtN, unit: 'abonnés',
      spark: v.length > 1 ? {values: v.map(x => x.views), labels: v.map(x => `${dShort(x.date)} · ${x.title}`), fmt: x => `${fmtN(x)} vues`} : null,
      sub: [['Vidéos période', fmtN(y.periodVideos)], ['Vues cumulées', compact(y.totalViews)], ['Catalogue', fmtN(y.videoCount)]]};
  }},
  buffer: {get: d => {
    const b = ok(d.dBuffer); if (!b) return null;
    const s = b.stats || {};
    const next = s.next_post_at ? new Date(s.next_post_at).toLocaleString('fr-BE', {weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'}) : null;
    return {value: s.scheduled_count ?? (b.scheduled || []).length, fmt: fmtN, unit: 'posts planifiés',
      sub: [['Cette semaine', fmtN(s.this_week_count)], ['Publiés', fmtN((b.sent || []).length)], ['Canaux', fmtN(s.active_channels)]],
      foot: next ? `Prochain : <b>${esc(next)}</b> · ${esc(s.next_post_service || '')}` : 'Aucune publication planifiée',
      fresh: (b._api_errors || []).length ? 'warn' : 'ok'};
  }},
  wrike: {wide: true, get: d => {
    const w = ok(d.dWrike); if (!w) return null;
    const s = w.summary || {}, t = w.task_stats || {};
    // La semaine en cours (dernier point, partielle) fausserait la courbe.
    const weeks = (t.weeks || []).slice(0, -1), done = (t.completed_per_week || []).slice(0, -1);
    return {value: s.active, fmt: fmtN, unit: 'projets actifs',
      spark: done.length > 1 ? {values: done, labels: weeks.map(x => `${x} · tâches clôturées`), fmt: fmtN} : null,
      sub: [['Projets en retard', fmtN(s.overdue_projects), s.overdue_projects ? 'bad' : 'good'],
            ['Tâches en retard', fmtN(t.overdue_active), t.overdue_active ? 'bad' : 'good'],
            ['Clôturées S-1', fmtN(done[done.length - 1])],
            ['Nouvelles S-1', fmtN((t.new_per_week || [])[(t.new_per_week || []).length - 2])]]};
  }},
  seopos: {get: d => {
    const s = ok(d.dSEO); if (!s?.markets) return null;
    const mk = ['fr', 'de', 'en'].filter(k => s.markets[k]);
    if (!mk.length) return null;
    const avg = mk.reduce((a, k) => a + (s.markets[k].visibility_pct || 0), 0) / mk.length;
    const dv  = mk.reduce((a, k) => a + (s.markets[k].visibility_delta || 0), 0) / mk.length;
    return {value: avg, fmt: v => fmtDec(v, 0) + ' %', unit: 'visibilité moyenne', delta: {v: dv, kind: 'pts'},
      sub: mk.map(k => [k.toUpperCase(), fmtDec(s.markets[k].visibility_pct, 0) + ' %']),
      foot: esc(s.period_label || ''), fresh: freshByAge(s.generated_at, 9)};
  }},
  veille: {get: d => {
    const v = ok(d.dVeille); if (!v?.items) return null;
    const it = v.items.filter(i => i.category !== 'Veille presse LU' && ageDays(i.date) <= 30);
    const clubs = new Set(it.map(i => i.club).filter(Boolean));
    return {value: it.filter(i => i.badge === 'NOTABLE').length, fmt: fmtN, unit: 'signaux notables · 30 j',
      sub: [['À surveiller', fmtN(it.filter(i => i.badge === 'SURVEILLER').length)], ['Clubs actifs', fmtN(clubs.size)], ['Édition', dShort(v.generated_at)]],
      fresh: freshByAge(v.generated_at, 3)};
  }},
  'veille-presse': {get: d => {
    const v = ok(d.dVeille); if (!v?.items) return null;
    const it = v.items.filter(i => i.category === 'Veille presse LU').sort((a, b) => (b.date || '').localeCompare(a.date || ''));
    const recent = it.filter(i => ageDays(i.date) <= 30);
    return {value: recent.length, fmt: fmtN, unit: 'articles · 30 j',
      foot: it[0] ? `<b>${esc(dShort(it[0].date))}</b> · ${esc((it[0].content || '').replace(/<[^>]+>/g, '').slice(0, 120))}` : 'Aucun article récent',
      fresh: freshByAge(v.generated_at, 3)};
  }},
  'car-market': {wide: true, get: d => {
    const c = ok(d.dCarReg); if (!c?.months?.length || typeof carSumByMonth !== 'function') return null;
    const sums = carSumByMonth(c, c.cube);
    const n = c.months.length, last = c.months[n - 1];
    const [y, m] = last.split('-');
    const yoyIdx = c.months.indexOf(`${+y - 1}-${m}`);
    const evIdx = c.fuels.findIndex(f => /lectri/i.test(f));
    let ev = 0;
    if (evIdx >= 0) c.cube.forEach(r => { if (r[0] === n - 1 && r[CAR_DIM_POS.fuel] === evIdx) ev += r[CAR_COUNT_POS]; });
    const s12 = sums.slice(-12), m12 = c.months.slice(-12);
    return {value: sums[n - 1], fmt: fmtN, unit: `immatriculations · ${carMonthLabel(last)}`,
      delta: yoyIdx >= 0 ? {v: pctChange(sums[n - 1], sums[yoyIdx]), label: 'vs N-1'} : null,
      spark: {values: s12, labels: m12.map(carMonthLabel), fmt: fmtN},
      sub: [['Part électrique', sums[n - 1] ? fmtDec(ev / sums[n - 1] * 100, 1) + ' %' : '—'],
            ['CO₂ moyen', c.co2Avg?.length ? fmtDec(c.co2Avg[c.co2Avg.length - 1], 0) + ' g/km' : '—'],
            ['Autonomie VE', c.autoElecAvg?.length ? fmtN(c.autoElecAvg[c.autoElecAvg.length - 1]) + ' km' : '—']],
      fresh: freshByAge(c.generated_at, 45)};
  }},
  'energy-costs': {lazy: 'energy', get: () => {
    const e = CC.lazy.energy;
    if (e === undefined) return 'loading';
    if (!e?.values) return null;
    const ser = k => e.values[k] || [];
    const last = a => a[a.length - 1];
    const dsl = ser('Diesel_B7'), e10 = ser('Essence_E10');
    if (!dsl.length) return null;
    return {value: last(dsl), fmt: v => fmtDec(v, 3) + ' €', unit: 'Diesel B7 / litre',
      delta: {v: pctChange(last(dsl), dsl[dsl.length - 2]), invert: true, label: 'vs M-1'},
      spark: {values: dsl, labels: e.months.map(carMonthLabel), fmt: v => fmtDec(v, 3) + ' €/l'},
      sub: [['Essence E10', e10.length ? fmtDec(last(e10), 3) + ' €' : '—'], ['GPL', ser('LPG').length ? fmtDec(last(ser('LPG')), 3) + ' €' : '—'], ['Mois', carMonthLabel(last(e.months))]],
      fresh: freshByAge(e.generated_at, 45)};
  }},
  'veille-ia': {lazy: 'veilleIA', get: () => {
    const v = CC.lazy.veilleIA;
    if (v === undefined) return 'loading';
    if (!v?.items) return null;
    const it = v.items.filter(i => ageDays(i.date) <= 30);
    const themes = {};
    it.forEach(i => { if (i.theme) themes[i.theme] = (themes[i.theme] || 0) + 1; });
    const top = Object.entries(themes).sort((a, b) => b[1] - a[1])[0];
    const latest = [...v.items].sort((a, b) => (b.date || '').localeCompare(a.date || ''))[0];
    return {value: it.length, fmt: fmtN, unit: 'signaux · 30 j',
      sub: [['Notables', fmtN(it.filter(i => i.badge === 'NOTABLE').length)], ['Thème n°1', top ? esc(top[0]) : '—']],
      foot: latest ? `<b>${esc(dShort(latest.date))}</b> · ${esc(latest.titre || '')}` : '',
      fresh: freshByAge(v.generated_at, 9)};
  }},
  intelligence: {get: () => ({link: true, desc: 'Analyse IA du web à la demande : concurrence, opportunités SEO et recommandations.'})},
  poweron:      {get: () => ({link: true, desc: 'Composer, traduire et tester la newsletter interne POWER ON!'})},
};

const LAZY_LOADERS = {
  energy: () => (typeof _energyData !== 'undefined' && _energyData)
    ? Promise.resolve(_energyData)
    : fetch(ENERGY_DATA_URL, {cache: 'no-store', signal: AbortSignal.timeout(10000)}).then(r => r.ok ? r.json() : null),
  veilleIA: () => api('/veille-ia'),
};
function ensureLazy(key, onDone) {
  if (key in CC.lazy) return;
  CC.lazy[key] = undefined;
  CC['_lz_' + key] ||= LAZY_LOADERS[key]().catch(() => null).then(v => {
    CC.lazy[key] = v || null;
    if (key === 'energy' && v?.values) { try { if (!_energyData) _energyData = v; } catch (e) {} }
    onDone();
  });
}

// Sidebar → groupes de tuiles (toute nouvelle section du menu apparaît
// automatiquement dans la vue d'ensemble, au minimum comme tuile-lien).
function sidebarGroups() {
  return [...document.querySelectorAll('.sb .sb-section')].map(sec => ({
    title: sec.querySelector('.sb-section-title')?.textContent.trim() || '',
    tabs: [...sec.querySelectorAll('.tab-btn[data-tab]')].map(b => b.dataset.tab).filter(t => t !== 'overview'),
  })).filter(g => g.tabs.length);
}
function tabMeta(tab) {
  const btn  = document.querySelector(`.tab-btn[data-tab="${tab}"]`);
  const wrap = btn?.querySelector('.sb-ic-wrap');
  const raw  = wrap?.style.getPropertyValue('--ic-color').trim();
  return {
    label: btn?.querySelector('.sb-item-label')?.textContent.trim() || btn?.title || tab,
    title: btn?.title || '',
    icon:  wrap ? wrap.outerHTML : '',
    color: COLOR_OVERRIDE[tab] || raw || 'var(--y)',
  };
}

function computeTile(tab, d) {
  const def = TILES[tab];
  if (!def) return {link: true, desc: tabMeta(tab).title};
  try { return def.get(d); } catch (e) { console.warn('[CC] tuile', tab, e); return null; }
}

// ── Rendu d'une tuile ────────────────────────────────────────────
CC._fmts = {};
CC._sparks = {};
function deltaHtml(dl) {
  if (!dl || num(dl.v) == null) return '';
  const v = dl.v, kind = dl.kind || 'pct';
  const up = v > 0, flat = Math.abs(v) < (kind === 'abs' ? 1 : .05);
  const good = dl.invert ? !up : up;
  const cls = flat ? 'flat' : good ? 'good' : 'bad';
  const txt = kind === 'abs' ? `${up ? '+' : ''}${fmtN(v)}` : `${up ? '+' : ''}${fmtDec(v, 1)}${kind === 'pts' ? ' pts' : ' %'}`;
  return `<span class="cc-delta ${cls}">${flat ? '→' : up ? '↑' : '↓'} ${txt}${dl.label ? ` <small>${esc(dl.label)}</small>` : ''}</span>`;
}
function sparkHtml(id, sp) {
  const vals = (sp?.values || []).map(v => num(v) ?? 0);
  if (vals.length < 2) return '';
  CC._sparks[id] = {...sp, values: vals};
  const H = 40, min = Math.min(...vals), max = Math.max(...vals), rg = (max - min) || 1;
  const pts = vals.map((v, i) => [i / (vals.length - 1) * 100, H - 3 - (v - min) / rg * (H - 8)]);
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(2)},${p[1].toFixed(2)}`).join('');
  return `<div class="cc-spark" data-spark="${id}">
    <svg viewBox="0 0 100 ${H}" preserveAspectRatio="none" aria-hidden="true">
      <path class="cc-spark-area" d="${line}L100,${H}L0,${H}Z"/><path class="cc-spark-line" d="${line}"/>
    </svg>
    <div class="cc-spark-cursor"></div><div class="cc-spark-dot"></div><div class="cc-spark-tip"></div>
  </div>`;
}
function tileInner(tab, t) {
  const meta = tabMeta(tab);
  const head = `<div class="cc-tile-head">${meta.icon}<span class="cc-tile-title">${esc(meta.label)}</span>
    ${t && t !== 'loading' && !t.link ? `<span class="cc-tile-fresh ${t.fresh || 'ok'}" title="${t.fresh === 'warn' ? 'Données à rafraîchir' : 'Source à jour'}"></span>` : ''}
    <span class="cc-tile-go">${ICON.arrow}</span></div>`;
  if (t === 'loading') return head + `<div class="cc-skel" style="width:55%;height:28px"></div><div class="cc-skel" style="height:40px"></div><div class="cc-skel" style="width:80%"></div>`;
  if (!t) return head + `<div class="cc-tile-kpi"><span class="cc-tile-val f-num">—</span></div><div class="cc-tile-desc">Source indisponible pour le moment. Ouvrir la section pour le détail.</div>`;
  if (t.link) return head + `<div class="cc-tile-desc">${esc(t.desc || '')}</div><div class="cc-sub"><div><span>Accès</span><b>Ouvrir l'outil →</b></div></div>`;
  CC._fmts[tab] = t.fmt || fmtN;
  const maxBar = Math.max(1, ...(t.bars || []).map(b => b.v));
  return head + `
    <div class="cc-tile-kpi">
      <span class="cc-tile-val f-num" data-cc-count="${num(t.value) ?? ''}" data-cc-fmt="${tab}">${num(t.value) == null ? '—' : (t.fmt || fmtN)(t.value)}</span>
      ${deltaHtml(t.delta)}
    </div>
    <div class="cc-tile-unit">${t.unit || ''}</div>
    ${t.spark ? sparkHtml(tab, t.spark) : ''}
    ${t.bars ? `<div class="cc-bars">${t.bars.map(b => `<div class="cc-bar-row"><span title="${esc(b.l)}">${esc(b.l)}</span><i style="width:${(b.v / maxBar * 100).toFixed(1)}%"></i><b>${compact(b.v)}</b></div>`).join('')}</div>` : ''}
    ${t.sub?.length ? `<div class="cc-sub">${t.sub.map(([k, v, c]) => `<div><span>${esc(k)}</span><b class="${c || ''}">${v ?? '—'}</b></div>`).join('')}</div>` : ''}
    ${t.foot ? `<div class="cc-foot">${t.foot}</div>` : ''}`;
}

// ── Compteurs animés ─────────────────────────────────────────────
function animateCounts(root) {
  root.querySelectorAll('[data-cc-count]').forEach(el => {
    const target = parseFloat(el.dataset.ccCount);
    if (!isFinite(target) || RM || el.dataset.ccDone) return;
    el.dataset.ccDone = '1';
    const fmt = CC._fmts[el.dataset.ccFmt] || fmtN;
    const dec = Math.abs(target) < 10 && target % 1 !== 0;
    const t0 = performance.now(), dur = 900;
    const step = now => {
      const p = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - p, 3);
      const v = target * e;
      el.textContent = fmt(p < 1 && !dec && Math.abs(target) >= 10 ? Math.round(v) : v);
      if (p < 1) requestAnimationFrame(step); else el.textContent = fmt(target);
    };
    requestAnimationFrame(step);
  });
}

// ── Navigation vers un onglet (avec View Transition si dispo) ───
function openTab(tab) {
  const go = () => {
    document.querySelector(`.tab-btn[data-tab="${tab}"]`)?.click();
    document.querySelector('.main, main')?.scrollTo?.({top: 0});
    window.scrollTo({top: 0, behavior: RM ? 'auto' : 'smooth'});
  };
  if (document.startViewTransition && !RM) document.startViewTransition(go); else go();
}
CC.openTab = openTab;

// ════════════════════════════════════════════════════════════════
// 3. RENDU DE LA VUE D'ENSEMBLE
// ════════════════════════════════════════════════════════════════
CC._args = null;
CC.renderOverview = function (args) {
  const el = document.getElementById('panel-overview');
  if (!el) return;
  CC._args = args;
  const {dGA4, dGSC, dFB, dBrevo, dLI, dYT} = args;
  const detailsOpen = lsGet('cc_details_open', true);

  el.innerHTML = `
    <section class="cc-hero" aria-label="Synthèse">
      <div class="cc-hero-main">
        <div class="hero-eyebrow"><span class="live-dot"></span>Semaine ${getWeekNum()} · ${periodLabel()}</div>
        <h1 class="hero-title f-display">Bonjour <span class="hl-y">${esc(getCurrentUserFirstName())}</span></h1>
        <p class="cc-hero-sub" id="heroGreeting">${heroSubText()}</p>
        <div class="ai-summary-row" id="ov-ai-summary" style="display:none">
          <span class="ai-summary-icon">✦</span>
          <p class="ai-summary-text" id="ov-ai-summary-text"></p>
          <a class="ai-summary-link" href="#ov-insights-anchor">Voir les recommandations ↓</a>
        </div>
        <div class="cc-hero-actions">
          <button class="cc-chip-btn is-primary" data-cc-action="palette">${ICON.search} Rechercher une donnée <span class="cc-kbd">${isMac() ? '⌘' : 'Ctrl'} K</span></button>
          <button class="cc-chip-btn" id="ccFreshBtn" aria-haspopup="true"><span class="cc-dot" id="ccFreshDot"></span><span id="ccFreshTxt">Sources…</span></button>
        </div>
      </div>
      <div class="cc-health" id="ccHealth"></div>
    </section>

    <div class="cc-since" id="ccSince"></div>

    <div id="ov-objectives"></div>

    <div id="ccGroups" style="display:flex;flex-direction:column;gap:26px"></div>

    <div class="cc-duo">
      <div class="ap-wrap" id="ov-insights-anchor">
        <div class="ap-head">
          <div class="ap-title"><span class="ap-title-star">✦</span> Actions prioritaires</div>
          <button class="ap-link" id="apToutVoir" style="display:none">Tout voir →</button>
        </div>
        <div class="ap-subtitle" id="apSubtitle">Générées depuis vos données — analyse en cours…</div>
        <div class="ap-body" id="insights-body">
          <div class="ap-loading"><div class="spin-star">✦</div><br>Génération des recommandations IA…</div>
        </div>
      </div>
      <div class="cal-module" id="calModule">
        <div class="cal-head">
          <div class="cal-title-wrap"><span class="cal-icon">📅</span><span class="cal-title">Calendrier marketing ${new Date().getFullYear()}</span></div>
          <div class="cal-count" id="calCount"></div>
        </div>
        <div class="cal-list" id="calList"></div>
        <button class="cal-more" id="calMore" style="display:none">Voir plus ↓</button>
      </div>
    </div>

    <details class="cc-details" id="ccDetails"${detailsOpen ? ' open' : ''}>
      <summary><div class="cc-group-head"><span class="cc-group-title">Analyse détaillée</span><span class="cc-group-count">Trafic · SEO · Social · Benchmarks</span><span class="cc-group-line"></span></div></summary>
      <div class="cc-details-body">
        <div id="ov-buffer-posts"></div>
        <div class="overview-bars-2">
          <div class="card"><div class="card-head"><div class="card-title">Top pages acl.lu</div></div>
            <div class="card-body">${barListHtml(dGA4?.topPages?.slice(0, 5) || [], 'var(--c-ga4)', null, 'page', 'views', fmtN)}</div></div>
          <div class="card"><div class="card-head"><div class="card-title">Top requêtes GSC</div></div>
            <div class="card-body">${barListHtml(dGSC?.topQueries?.slice(0, 5) || [], 'var(--c-gsc)', null, 'query', 'clicks', fmtN)}</div></div>
        </div>
        <div class="ov-mid-grid">
          <div class="channel-donut-wrap">
            <div class="channel-donut-title">Mix canaux GA4</div>
            <div class="channel-donut-inner">
              <div class="channel-donut-canvas-wrap"><canvas id="chartChannelDonut" width="200" height="200"></canvas></div>
              <div class="channel-donut-legend" id="channelDonutLegend"></div>
            </div>
          </div>
          <div class="gsc-pos-wrap" id="gscPosModule"></div>
        </div>
        <div id="ov-benchmark"></div>
      </div>
    </details>`;

  wireOverview(el);
  renderGroups();
  renderSince();
  renderObjectivesPanel();
  ensureObjectives().then(() => { renderObjectivesPanel(); renderHealth(); });
  renderHealth();
  renderFreshness();

  renderCalendar();
  renderBufferWidget(args.dBuffer);
  if (detailsOpen) renderChannelDonut(dGA4?.channelBreakdown || []);
  renderGSCPositions(dGSC?.topQueries || []);
  renderBenchmarks({dGA4, dGSC, dFB, dBrevo, dLI, dYT});
};

function isMac() { return /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent); }
function periodLabel() {
  if (globalStart && globalEnd && new Date().getFullYear() + '-01-01' === globalStart) return 'depuis le 1er janvier';
  return `${globalDays} derniers jours`;
}
function heroSubText() {
  const n = sidebarGroups().reduce((a, g) => a + g.tabs.length, 0);
  return `Toutes les données marketing d'ACL en un seul endroit : <b>${n} sections</b> suivies en continu, des canaux digitaux au marché automobile.`;
}

function wireOverview(el) {
  if (el.dataset.ccWired) return;
  el.dataset.ccWired = '1';
  el.addEventListener('click', e => {
    const tile = e.target.closest('[data-cc-tab]');
    if (tile) { openTab(tile.dataset.ccTab); return; }
    const act = e.target.closest('[data-cc-action]');
    if (act) {
      const a = act.dataset.ccAction;
      if (a === 'palette') openPalette();
      else if (a === 'edit-obj') openObjectivesEditor();
      else if (a === 'collapse-obj') {
        const p = act.closest('.cc-mission'); const c = p.classList.toggle('collapsed');
        lsSet('cc_mission_collapsed', c); act.setAttribute('aria-expanded', String(!c));
      }
      return;
    }
    const sg = e.target.closest('[data-cc-sig]');
    if (sg) {
      const t = sg.dataset.ccSig;
      if (t === '#mission') document.getElementById('ccMission')?.scrollIntoView({behavior: RM ? 'auto' : 'smooth', block: 'start'});
      else if (t === '#fresh') toggleFreshPop(document.getElementById('ccFreshBtn'));
      else openTab(t);
      return;
    }
    if (e.target.closest('#ccFreshBtn')) { toggleFreshPop(e.target.closest('#ccFreshBtn')); return; }
    const btn = e.target.closest('#apToutVoir');
    if (btn) {
      const body = document.getElementById('insights-body'); if (!body) return;
      const hidden = body.querySelectorAll('.rec.rec-hidden'), total = body.querySelectorAll('.rec').length;
      if (hidden.length) { hidden.forEach(r => r.classList.remove('rec-hidden')); btn.textContent = 'Voir moins ↑'; }
      else { body.querySelectorAll('.rec').forEach((r, i) => { if (i >= 3) r.classList.add('rec-hidden'); }); btn.textContent = `Tout voir (${total}) →`; }
    }
  });
  el.addEventListener('keydown', e => {
    const tile = e.target.closest('[data-cc-tab]');
    if (tile && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); openTab(tile.dataset.ccTab); }
  });
  // Halo qui suit la souris + survol des sparklines
  el.addEventListener('pointermove', e => {
    const tile = e.target.closest('.cc-tile');
    if (tile) {
      const r = tile.getBoundingClientRect();
      tile.style.setProperty('--mx', `${e.clientX - r.left}px`);
      tile.style.setProperty('--my', `${e.clientY - r.top}px`);
    }
    const sp = e.target.closest('.cc-spark');
    if (sp) sparkHover(sp, e);
  });
  el.addEventListener('pointerout', e => {
    const sp = e.target.closest('.cc-spark');
    if (sp && !sp.contains(e.relatedTarget)) sp.classList.remove('hover');
  });
  el.addEventListener('toggle', e => {
    if (e.target.id !== 'ccDetails') return;
    lsSet('cc_details_open', e.target.open);
    if (e.target.open) renderChannelDonut(window._dashData?.dGA4?.channelBreakdown || []);
  }, true);
}

function sparkHover(sp, e) {
  const data = CC._sparks[sp.dataset.spark]; if (!data) return;
  const r = sp.getBoundingClientRect();
  const n = data.values.length;
  const i = Math.max(0, Math.min(n - 1, Math.round((e.clientX - r.left) / r.width * (n - 1))));
  const min = Math.min(...data.values), max = Math.max(...data.values), rg = (max - min) || 1;
  const x = i / (n - 1) * 100, y = (40 - 3 - (data.values[i] - min) / rg * 32) / 40 * 100;
  sp.querySelector('.cc-spark-cursor').style.left = x + '%';
  const dot = sp.querySelector('.cc-spark-dot'); dot.style.left = x + '%'; dot.style.top = y + '%';
  const tip = sp.querySelector('.cc-spark-tip');
  const lbl = data.labels?.[i] || '';
  tip.textContent = `${lbl.length > 48 ? lbl.slice(0, 47) + '…' : lbl}${lbl ? ' · ' : ''}${(data.fmt || fmtN)(data.values[i])}`;
  tip.style.left = Math.min(82, Math.max(18, x)) + '%';
  sp.classList.add('hover');
}

// ── Groupes et tuiles ────────────────────────────────────────────
function renderGroups() {
  const host = document.getElementById('ccGroups'); if (!host) return;
  const d = window._dashData || {};
  CC._tileData = {};
  let delay = 0;
  host.innerHTML = sidebarGroups().map(g => `
    <section class="cc-group" aria-label="${esc(g.title)}">
      <div class="cc-group-head"><span class="cc-group-title">${esc(g.title)}</span><span class="cc-group-count">${g.tabs.length} section${g.tabs.length > 1 ? 's' : ''}</span><span class="cc-group-line"></span></div>
      <div class="cc-grid">${g.tabs.map(tab => {
        const def = TILES[tab];
        if (def?.lazy) ensureLazy(def.lazy, () => refreshTile(tab));
        const t = computeTile(tab, d);
        CC._tileData[tab] = t;
        const wide = def?.wide && t && t !== 'loading' && !t.link;
        delay += 35;
        return `<div class="cc-tile${wide ? ' wide' : ''}${!t ? ' is-empty' : ''}" role="link" tabindex="0" data-cc-tab="${tab}"
          style="--c:${tabMeta(tab).color};animation-delay:${RM ? 0 : delay}ms" aria-label="Ouvrir ${esc(tabMeta(tab).label)}">${tileInner(tab, t)}</div>`;
      }).join('')}</div>
    </section>`).join('');
  animateCounts(host);
}
function refreshTile(tab) {
  const el = document.querySelector(`.cc-tile[data-cc-tab="${tab}"]`); if (!el) return;
  const t = computeTile(tab, window._dashData || {});
  CC._tileData[tab] = t;
  el.classList.toggle('is-empty', !t);
  el.classList.toggle('wide', !!(TILES[tab]?.wide && t && t !== 'loading' && !t.link));
  el.innerHTML = tileInner(tab, t);
  animateCounts(el);
  renderFreshness();
  renderHealth();
  renderSince();
}

// ── Fraîcheur des sources ────────────────────────────────────────
function sourceStates() {
  return sidebarGroups().flatMap(g => g.tabs).filter(t => !CC._tileData?.[t]?.link).map(tab => {
    const t = CC._tileData?.[tab];
    return {tab, label: tabMeta(tab).label, state: t === 'loading' ? 'loading' : !t ? 'err' : (t.fresh || 'ok')};
  });
}
function renderFreshness() {
  const s = sourceStates(); if (!s.length) return;
  const okN = s.filter(x => x.state === 'ok').length, err = s.filter(x => x.state === 'err').length;
  const dot = document.getElementById('ccFreshDot'), txt = document.getElementById('ccFreshTxt');
  if (!dot || !txt) return;
  dot.className = 'cc-dot ' + (err ? 'err' : okN === s.length ? 'ok' : 'warn');
  txt.textContent = `${okN}/${s.length} sources à jour`;
}
function toggleFreshPop(btn) {
  const old = document.getElementById('ccFreshPop');
  if (old) { old.remove(); return; }
  const lbl = {ok: 'À jour', warn: 'À rafraîchir', err: 'Indisponible', loading: 'Chargement…'};
  const pop = document.createElement('div');
  pop.id = 'ccFreshPop'; pop.className = 'cc-pop'; pop.setAttribute('role', 'dialog');
  pop.innerHTML = sourceStates().map(s => `<div class="cc-pop-row" data-cc-tab="${s.tab}" style="cursor:pointer">
    <span class="cc-dot ${s.state === 'loading' ? '' : s.state}"></span><span>${esc(s.label)}</span><em>${lbl[s.state]}</em></div>`).join('');
  const r = btn.getBoundingClientRect();
  pop.style.top  = `${r.bottom + window.scrollY + 8}px`;
  pop.style.left = `${Math.min(r.left + window.scrollX, window.innerWidth - 380)}px`;
  document.body.appendChild(pop);
  pop.addEventListener('click', e => { const row = e.target.closest('[data-cc-tab]'); if (row) { pop.remove(); openTab(row.dataset.ccTab); } });
  setTimeout(() => document.addEventListener('click', function close(ev) {
    if (!pop.contains(ev.target)) { pop.remove(); document.removeEventListener('click', close); }
  }), 0);
}

// ── Score de santé (expliqué signal par signal) ─────────────────
// Chaque signal : la mesure réelle, la règle qui le met au vert, et
// l'onglet où creuser. Le score = part des signaux au vert (sur ceux dont
// la donnée est disponible), pour la période sélectionnée.
function healthSignals() {
  const d = window._dashData || {};
  const sig = [];
  const sgn = v => (v > 0 ? '+' : '') + fmtDec(v, 1) + ' %';
  const add = (o) => { if (num(o.raw) != null) sig.push({...o, ok: o.test(o.raw)}); };
  const ga = ok(d.dGA4), gs = ok(d.dGSC);
  add({label: 'Trafic web', tab: 'ga4', raw: ga?.deltas?.sessions, test: v => v >= 0,
       value: v => `${sgn(v)} de sessions`, rule: 'Sessions acl.lu stables ou en hausse vs période précédente'});
  add({label: 'Clics SEO', tab: 'gsc', raw: gs?.deltas?.clicks, test: v => v >= 0,
       value: v => `${sgn(v)} de clics`, rule: 'Clics Google stables ou en hausse vs période précédente'});
  add({label: 'Position SEO', tab: 'gsc', raw: gs?.deltas?.avgPosition, test: v => v <= 0,
       value: v => `${fmtDec(gs.avgPosition, 1)} (${v <= 0 ? 'meilleure' : 'moins bonne'})`, rule: 'Position moyenne Google stable ou meilleure'});
  const brevoObj = objCfg().items.find(o => o.metric === 'brevo_open');
  const brevoTarget = brevoObj?.target || 50;
  add({label: 'Emails', tab: 'brevo', raw: brevoOpenRates(d)?.mkt, test: v => v >= brevoTarget,
       value: v => `${fmtDec(v, 1)} % d'ouverture`, rule: `Taux d'ouverture ≥ ${fmtDec(brevoTarget, 0)} %${brevoObj ? ' (objectif Mission Control)' : ''}`});
  add({label: 'LinkedIn', tab: 'linkedin', raw: ok(d.dLI)?.summary?.engagementRate, test: v => v >= 2,
       value: v => `${fmtDec(v, 1)} % d'engagement`, rule: 'Engagement ≥ 2 % (moyenne secteur : 1 à 3 %)'});
  const w = ok(d.dWrike)?.summary;
  if (w?.active) add({label: 'Projets', tab: 'wrike', raw: w.overdue_projects / w.active, test: v => v <= .25,
       value: () => `${w.overdue_projects} en retard sur ${w.active}`, rule: 'Au plus 25 % des projets Wrike en retard'});
  const ev = objCfg().items.map(o => evalObjective(o, d)).filter(x => x && x.status !== 'na');
  if (ev.length) { const g = ev.filter(x => x.status === 'done' || x.status === 'on').length;
    add({label: 'Objectifs', tab: '#mission', raw: g / ev.length, test: v => v >= .5,
       value: () => `${g} sur ${ev.length} en bonne voie`, rule: 'Au moins la moitié des objectifs Mission Control en bonne voie'}); }
  const s = sourceStates().filter(x => x.state !== 'loading');
  if (s.length) { const g = s.filter(x => x.state === 'ok').length;
    add({label: 'Sources', tab: '#fresh', raw: g / s.length, test: v => v >= .85,
       value: () => `${g} sur ${s.length} à jour`, rule: 'Au moins 85 % des sources de données à jour'}); }
  return sig.map(x => ({...x, value: x.value(x.raw)}));
}
function renderHealth() {
  const el = document.getElementById('ccHealth'); if (!el) return;
  const sig = healthSignals();
  const good = sig.filter(s => s.ok).length;
  const score = sig.length ? Math.round(good / sig.length * 100) : 0;
  const color = score >= 70 ? 'var(--good)' : score >= 40 ? 'var(--warn)' : 'var(--bad)';
  const lbl = score >= 70 ? 'Bonne dynamique' : score >= 40 ? 'Dynamique mitigée' : 'Vigilance';
  const R = 54, C = 2 * Math.PI * R;
  el.innerHTML = `
    <div class="cc-health-side">
      <div class="cc-health-title">Santé marketing</div>
      <div class="cc-ring" role="img" aria-label="Score de santé ${score} sur 100 : ${good} signaux sur ${sig.length} au vert">
        <svg viewBox="0 0 132 132"><circle class="cc-ring-track" cx="66" cy="66" r="${R}" fill="none" stroke-width="10"/>
          <circle class="cc-ring-val" cx="66" cy="66" r="${R}" fill="none" stroke="${color}" stroke-width="10" stroke-linecap="round"
            stroke-dasharray="${C}" stroke-dashoffset="${C}"/></svg>
        <div class="cc-ring-num"><span class="f-num" data-cc-count="${score}" data-cc-fmt="_score">${score}</span><small>/ 100</small></div>
      </div>
      <div class="cc-health-verdict" style="color:${color}">${lbl}</div>
      <p class="cc-health-how"><b>${good} signaux sur ${sig.length}</b> sont au vert. Le score est la part des signaux au vert, sur la période sélectionnée.</p>
    </div>
    <ul class="cc-signals">${sig.map(s => `
      <li><button class="cc-signal ${s.ok ? 'ok' : 'ko'}" data-cc-sig="${s.tab}" title="Ouvrir le détail">
        <i aria-hidden="true">${s.ok ? '✓' : '✗'}</i>
        <span class="cc-signal-txt"><span class="cc-signal-top"><b>${esc(s.label)}</b><em>${esc(s.value)}</em></span>
        <small>${esc(s.rule)}</small></span>
        <span class="sr-only">${s.ok ? 'au vert' : 'au rouge'}</span>
      </button></li>`).join('')}</ul>`;
  CC._fmts._score = v => String(Math.round(v));
  requestAnimationFrame(() => requestAnimationFrame(() => {
    const c = el.querySelector('.cc-ring-val'); if (c) c.style.strokeDashoffset = String(C * (1 - score / 100));
  }));
  animateCounts(el);
}

// ── Depuis votre dernière visite ────────────────────────────────
// Stocke localement (par navigateur) un instantané des indicateurs de
// stock, indépendants de la période sélectionnée. Commodité personnelle :
// rien de critique ne dépend de ce stockage.
function snapshotValues() {
  const d = window._dashData || {};
  const v = {};
  const put = (k, x) => { if (num(x) != null) v[k] = x; };
  put('li', ok(d.dLI)?.summary?.followerCount ?? ok(d.dLI)?.followers);
  put('yt', ok(d.dYT)?.subscribers);
  const pages = d.dFBP?.pages || [];
  if (pages.length) put('fb', pages.reduce((a, p) => a + (p.fans || 0), 0));
  put('brevo', ok(d.dBrevo)?.contactStats?.subscribed);
  put('wrike_active', ok(d.dWrike)?.summary?.active);
  put('wrike_late', ok(d.dWrike)?.summary?.overdue_projects);
  const seo = CC._tileData?.seopos; if (seo && seo !== 'loading') put('seo', seo.value);
  const car = CC._tileData?.['car-market']; if (car && car !== 'loading') put('car', car.value);
  return v;
}
const SNAP_DEF = {
  li:           {label: 'Abonnés LinkedIn',          tab: 'linkedin', dir: 1},
  yt:           {label: 'Abonnés YouTube',           tab: 'youtube',  dir: 1},
  fb:           {label: 'Abonnés pages Facebook',    tab: 'fbads',    dir: 1},
  brevo:        {label: 'Contacts emailing',         tab: 'brevo',    dir: 1},
  wrike_active: {label: 'Projets actifs',            tab: 'wrike',    dir: 0},
  wrike_late:   {label: 'Projets en retard',         tab: 'wrike',    dir: -1},
  seo:          {label: 'Visibilité SEO',            tab: 'seopos',   dir: 1, unit: ' pts'},
  car:          {label: 'Immatriculations du mois',  tab: 'car-market', dir: 0},
};
function renderSince() {
  const el = document.getElementById('ccSince'); if (!el) return;
  const cur = snapshotValues();
  if (!Object.keys(cur).length) { el.innerHTML = ''; return; }
  const S = lsGet('cc_visit_v1', {});
  const now = Date.now();
  if (!S.last) S.last = {at: now, v: cur};
  else if (now - S.last.at > 4 * 3600e3) { S.base = S.last; S.last = {at: now, v: cur}; }
  else S.last.v = {...S.last.v, ...cur};
  lsSet('cc_visit_v1', S);
  if (!S.base) {
    el.innerHTML = `<span class="cc-since-label">Depuis votre dernière visite</span><span class="cc-since-empty">Première visite enregistrée sur ce navigateur : les évolutions s'afficheront ici lors de votre prochain passage.</span>`;
    return;
  }
  const chips = Object.entries(SNAP_DEF).map(([k, def]) => {
    const a = S.base.v[k], b = cur[k];
    if (num(a) == null || num(b) == null || a === b) return null;
    const dv = b - a, up = dv > 0;
    const cls = def.dir === 0 ? '' : (up === (def.dir > 0) ? 'up' : 'down');
    const txt = def.unit ? `${up ? '+' : ''}${fmtDec(dv, 1)}${def.unit}` : `${up ? '+' : ''}${fmtN(dv)}`;
    return `<button class="cc-since-chip" data-cc-tab="${def.tab}">${esc(def.label)} <b class="${cls}">${up ? '↑' : '↓'} ${txt}</b></button>`;
  }).filter(Boolean);
  el.innerHTML = `<span class="cc-since-label">Depuis votre dernière visite · ${rel(S.base.at)}</span>` +
    (chips.length ? chips.join('') : `<span class="cc-since-empty">Aucune variation sur les indicateurs suivis.</span>`);
}

// ════════════════════════════════════════════════════════════════
// 4. MISSION CONTROL
// ════════════════════════════════════════════════════════════════
function objCard(ev, i) {
  const {m, o, cur, pct, proj, expected, status} = ev;
  const R = 23, C = 2 * Math.PI * R, p = Math.min(1, pct || 0);
  const st = STATUS[status];
  let note = '';
  if (status === 'na') note = 'Donnée non disponible pour le moment.';
  else if (m.kind === 'cumulative') note = `Projection fin ${yearProgress().year} : <b>${m.fmt(proj)}</b> (${Math.round(proj / o.target * 100)} % de l'objectif)`;
  else if (m.kind === 'stock') note = pct >= 1 ? `Objectif dépassé de <b>${m.fmt(cur - o.target)}</b>` : `Encore <b>${m.fmt(o.target - cur)}</b> ${m.unit} à gagner`;
  if (m.note && status !== 'na') note = m.note(window._dashData || {}) + (note ? '<br>' + note : '');
  return `<article class="cc-obj" style="--c:${m.color};animation-delay:${RM ? 0 : i * 50}ms">
    <div class="cc-obj-top"><span class="cc-obj-group">${esc(m.group)}</span><span class="cc-st ${status}"><span aria-hidden="true">${st.icon}</span> ${st.label}</span></div>
    <div class="cc-obj-label">${esc(o.label || m.label)}</div>
    <div class="cc-obj-main">
      <div class="cc-mini-ring"><svg viewBox="0 0 54 54"><circle cx="27" cy="27" r="${R}" fill="none" stroke="var(--border)" stroke-width="5"/>
        <circle cx="27" cy="27" r="${R}" fill="none" stroke="${m.color}" stroke-width="5" stroke-linecap="round" stroke-dasharray="${C * p} ${C}"/></svg>
        <span>${status === 'na' ? '–' : Math.round(pct * 100) + '%'}</span></div>
      <div><div class="cc-obj-val f-num">${cur == null ? '—' : m.fmt(cur)}</div><div class="cc-obj-tgt">objectif ${m.fmt(o.target)}</div></div>
    </div>
    <div class="cc-obj-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(p * 100)}">
      <div class="fill" data-w="${(p * 100).toFixed(1)}"></div>
      ${expected != null ? `<div class="mark" style="left:${(expected * 100).toFixed(1)}%" title="Rythme attendu à date : ${Math.round(expected * 100)} %"></div>` : ''}
    </div>
    <div class="cc-obj-note">${note}</div>
  </article>`;
}
const ORDER = {done: 0, on: 1, watch: 2, late: 3, na: 4};
function renderObjectivesPanel() {
  const el = document.getElementById('ov-objectives'); if (!el) return;
  const d = window._dashData || {};
  const cfg = objCfg();
  const evs = cfg.items.map(o => evalObjective(o, d)).filter(Boolean);
  const valid = evs.filter(e => e.status !== 'na');
  const good = valid.filter(e => e.status === 'done' || e.status === 'on').length;
  const collapsed = lsGet('cc_mission_collapsed', false);
  const last = (cfg.history || []).slice(-1)[0];
  el.innerHTML = `<section class="cc-mission${collapsed ? ' collapsed' : ''}" id="ccMission" aria-label="Mission Control">
    <div class="cc-mission-head">
      <div class="cc-mission-titles">
        <div class="cc-mission-title">Mission Control — Objectifs ${cfg.year}</div>
        <div class="cc-mission-sub">Cumul depuis le 1er janvier au ${dLong(new Date())} · le trait vertical indique le rythme attendu à date</div>
      </div>
      ${valid.length ? `<div class="cc-mission-sum"><div class="cc-segbar">${[...valid].sort((a, b) => ORDER[a.status] - ORDER[b.status]).map(e => `<span class="${e.status}"></span>`).join('')}</div><span><b style="color:var(--text)">${good}/${valid.length}</b> en bonne voie</span></div>` : ''}
      ${isAdmin() ? `<button class="cc-icon-btn is-y" data-cc-action="edit-obj">${ICON.pencil} Modifier</button>` : ''}
      <button class="cc-icon-btn cc-collapse" data-cc-action="collapse-obj" aria-expanded="${!collapsed}" title="Réduire / développer">${ICON.chev}</button>
    </div>
    <div class="cc-obj-grid">${evs.length ? evs.map(objCard).join('') : `<div class="cc-obj-note">Aucun objectif défini.${isAdmin() ? ' Cliquez sur « Modifier » pour en ajouter.' : ''}</div>`}</div>
    ${last ? `<div class="cc-mission-foot">Dernière mise à jour des objectifs ${rel(new Date(last.at).getTime())} par ${esc(last.by)}</div>` : ''}
  </section>`;
  requestAnimationFrame(() => requestAnimationFrame(() => el.querySelectorAll('.cc-obj-bar .fill').forEach(f => { f.style.width = f.dataset.w + '%'; })));
}
CC.renderObjectives = renderObjectivesPanel;

// ── Éditeur (admin) ──────────────────────────────────────────────
function metricOptions(sel) {
  const groups = {};
  Object.entries(METRICS).forEach(([k, m]) => (groups[m.group] ||= []).push([k, m]));
  return Object.entries(groups).map(([g, list]) => `<optgroup label="${esc(g)}">${list.map(([k, m]) =>
    `<option value="${k}"${k === sel ? ' selected' : ''}>${esc(m.label)}</option>`).join('')}</optgroup>`).join('');
}
function editRow(o) {
  const m = METRICS[o.metric] || Object.values(METRICS)[0];
  const cur = num(m.get(window._dashData || {}));
  return `<div class="cc-edit-row" data-id="${esc(o.id || Math.random().toString(36).slice(2, 9))}">
    <select class="cc-field" data-k="metric" aria-label="Indicateur">${metricOptions(o.metric)}</select>
    <input class="cc-field" data-k="label" maxlength="80" placeholder="${esc(m.label)}" value="${esc(o.label || '')}" aria-label="Libellé (optionnel)">
    <div class="cc-target"><input class="cc-field" data-k="target" type="number" min="0" step="any" value="${o.target ?? ''}" aria-label="Cible"><em>${esc(m.unit)}</em></div>
    <button class="cc-icon-btn" data-k="remove" title="Retirer cet objectif" aria-label="Retirer">${ICON.x}</button>
    <div class="cc-hint">${m.kind === 'cumulative' ? 'Cumul annuel, projeté en fin d’année' : m.kind === 'rate' ? 'Moyenne depuis janvier' : 'Comparé à la valeur du jour'} · aujourd’hui <b>${cur == null ? '—' : m.fmt(cur)}</b></div>
  </div>`;
}
function openObjectivesEditor() {
  if (!isAdmin()) return;
  document.getElementById('ccObjModal')?.remove();
  const cfg = objCfg();
  const back = document.createElement('div');
  back.className = 'cc-modal-back'; back.id = 'ccObjModal';
  const hist = (cfg.history || []).slice(-3).reverse().map(h => `${dLong(h.at)} (${h.count} obj.)`).join(' · ');
  back.innerHTML = `<div class="cc-modal" role="dialog" aria-modal="true" aria-labelledby="ccObjTitle">
    <div class="cc-modal-head">
      <h3 id="ccObjTitle">Modifier les objectifs</h3>
      <label style="font-size:12px;color:var(--muted);display:flex;align-items:center;gap:6px">Année <input class="cc-field" id="ccObjYear" type="number" min="2020" max="2100" value="${cfg.year}" style="width:86px"></label>
      <button class="cc-icon-btn" data-k="close" aria-label="Fermer">${ICON.x}</button>
    </div>
    <div class="cc-modal-body">
      <div class="cc-edit-head"><span>Indicateur</span><span>Libellé affiché</span><span>Cible</span><span></span></div>
      <div id="ccObjRows" style="display:flex;flex-direction:column;gap:8px">${cfg.items.map(editRow).join('')}</div>
      <button class="cc-icon-btn cc-add" data-k="add">+ Ajouter un objectif</button>
    </div>
    <div class="cc-modal-foot">
      <div class="cc-pw" id="ccObjPw" style="display:none">
        <span style="font-size:12.5px;font-weight:600">Confirmez votre mot de passe admin</span>
        <input class="cc-field" type="password" id="ccObjPwInput" autocomplete="current-password" placeholder="Mot de passe">
      </div>
      <span class="cc-history">${hist ? 'Historique : ' + esc(hist) : CC.objMeta.persist === false ? '⚠ Sauvegarde serveur non configurée (GIST_ID)' : 'Valeurs par défaut — jamais modifiées'}</span>
      <span class="cc-err" id="ccObjErr" role="alert"></span>
      <button class="cc-icon-btn" data-k="reset" title="Revenir aux objectifs par défaut">Réinitialiser</button>
      <button class="cc-icon-btn" data-k="close">Annuler</button>
      <button class="cc-icon-btn is-y" data-k="save" style="height:34px;padding:0 16px">Enregistrer</button>
    </div>
  </div>`;
  document.body.appendChild(back);
  const rows = back.querySelector('#ccObjRows');
  const err  = back.querySelector('#ccObjErr');
  const close = () => { back.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = e => { if (e.key === 'Escape') close(); };
  document.addEventListener('keydown', onKey);
  back.addEventListener('mousedown', e => { if (e.target === back) close(); });
  rows.addEventListener('change', e => {
    if (e.target.dataset.k !== 'metric') return;
    const row = e.target.closest('.cc-edit-row');
    const fresh = document.createElement('div');
    fresh.innerHTML = editRow({id: row.dataset.id, metric: e.target.value, label: '', target: row.querySelector('[data-k=target]').value});
    row.replaceWith(fresh.firstElementChild);
  });
  back.addEventListener('click', async e => {
    const k = e.target.closest('[data-k]')?.dataset.k;
    if (k === 'close') close();
    else if (k === 'remove') e.target.closest('.cc-edit-row').remove();
    else if (k === 'add') { rows.insertAdjacentHTML('beforeend', editRow({metric: Object.keys(METRICS)[0], target: ''})); rows.lastElementChild.querySelector('select').focus(); }
    else if (k === 'reset') { rows.innerHTML = DEFAULT_OBJECTIVES.items.map(editRow).join(''); }
    else if (k === 'save') {
      err.textContent = '';
      const items = [...rows.querySelectorAll('.cc-edit-row')].map(r => ({
        id: r.dataset.id, metric: r.querySelector('[data-k=metric]').value,
        label: r.querySelector('[data-k=label]').value.trim(),
        target: parseFloat(r.querySelector('[data-k=target]').value),
      }));
      const bad = items.find(i => !(i.target > 0));
      if (bad) { err.textContent = `Cible manquante pour « ${bad.label || METRICS[bad.metric].label} »`; return; }
      const year = parseInt(back.querySelector('#ccObjYear').value, 10);
      const pwBox = back.querySelector('#ccObjPw'), pwIn = back.querySelector('#ccObjPwInput');
      const btn = e.target.closest('[data-k]');
      btn.disabled = true; btn.textContent = 'Enregistrement…';
      let res;
      try { res = await saveObjectives({year, items}, pwIn.value); }
      catch (ex) { res = {error: 'Backend injoignable'}; }
      btn.disabled = false; btn.textContent = 'Enregistrer';
      if (res.needPw) {
        pwBox.style.display = 'flex'; pwIn.value = ''; pwIn.focus();
        if (res.error) err.textContent = res.error;
        return;
      }
      if (res.error) { err.textContent = res.error; return; }
      CC.objCfg = res.objectives; lsSet('cc_objectives_cache', res.objectives);
      close();
      renderObjectivesPanel(); renderHealth();
      showToast(res.persisted ? 'Objectifs enregistrés.' : `Objectifs appliqués, mais non sauvegardés durablement (${res.persist_error}).`, res.persisted ? 'success' : 'error');
    }
  });
  back.querySelector('#ccObjPwInput').addEventListener('keydown', e => { if (e.key === 'Enter') back.querySelector('[data-k=save]').click(); });
  setTimeout(() => rows.querySelector('input,select')?.focus(), 50);
}
CC.openObjectivesEditor = openObjectivesEditor;

// ════════════════════════════════════════════════════════════════
// 5. PALETTE ⌘K — disponible sur tous les onglets
// ════════════════════════════════════════════════════════════════
const norm = s => String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
function paletteIndex() {
  const items = [];
  const groups = sidebarGroups();
  items.push({sec: 'Sections', label: "Vue d'ensemble", hint: 'Synthèse', icon: tabMeta('overview').icon, run: () => openTab('overview')});
  groups.forEach(g => g.tabs.forEach(tab => {
    const m = tabMeta(tab);
    items.push({sec: 'Sections', label: m.label, hint: g.title, icon: m.icon, kw: m.title, run: () => openTab(tab)});
  }));
  const d = window._dashData || {};
  groups.flatMap(g => g.tabs).forEach(tab => {
    const t = CC._tileData?.[tab] ?? computeTile(tab, d);
    if (!t || t === 'loading' || t.link) return;
    const m = tabMeta(tab);
    if (num(t.value) != null) items.push({sec: 'Indicateurs', label: `${m.label} — ${String(t.unit || '').replace(/<[^>]+>/g, '')}`, val: (t.fmt || fmtN)(t.value), icon: m.icon, run: () => openTab(tab)});
    (t.sub || []).forEach(([k, v]) => items.push({sec: 'Indicateurs', label: `${k}`, hint: m.label, val: String(v ?? '—').replace(/<[^>]+>/g, ''), icon: m.icon, run: () => openTab(tab)}));
  });
  objCfg().items.forEach(o => {
    const ev = evalObjective(o, d); if (!ev) return;
    items.push({sec: 'Objectifs', label: o.label || ev.m.label, hint: STATUS[ev.status].label,
      val: ev.cur == null ? '—' : `${ev.m.fmt(ev.cur)} / ${ev.m.fmt(o.target)}`, ico: ICON.target,
      run: () => { openTab('overview'); setTimeout(() => document.getElementById('ccMission')?.scrollIntoView({behavior: RM ? 'auto' : 'smooth', block: 'start'}), 120); }});
  });
  items.push({sec: 'Actions', label: 'Actualiser toutes les données', ico: ICON.bolt, kw: 'refresh recharger', run: () => typeof loadAll === 'function' && loadAll()});
  [['7', '7 derniers jours'], ['30', '30 derniers jours'], ['90', '90 derniers jours'], ['365', '12 derniers mois'], ['ytd', 'Depuis le 1er janvier']].forEach(([p, l]) =>
    items.push({sec: 'Actions', label: `Période : ${l}`, ico: ICON.bolt, kw: 'periode filtre date', run: () => document.querySelector(`.filter-btn[data-gperiod="${p}"]`)?.click()}));
  items.push({sec: 'Actions', label: 'Basculer mode sombre / clair', ico: ICON.bolt, kw: 'theme dark nuit', run: () => document.getElementById('btnTheme')?.click()});
  items.push({sec: 'Actions', label: "Exporter l'onglet courant en Excel", ico: ICON.bolt, kw: 'xlsx export', run: () => document.getElementById('btnExport')?.click()});
  if (isAdmin()) items.push({sec: 'Actions', label: 'Modifier les objectifs Mission Control', ico: ICON.pencil, kw: 'objectif cible admin', run: () => { openTab('overview'); setTimeout(openObjectivesEditor, 150); }});
  items.forEach(i => { i._n = norm(`${i.label} ${i.hint || ''} ${i.kw || ''} ${i.sec}`); });
  return items;
}
function searchPalette(items, q) {
  const terms = norm(q).split(/\s+/).filter(Boolean);
  if (!terms.length) return items.filter(i => i.sec !== 'Indicateurs' || /—/.test(i.label)).slice(0, 60);
  return items.map(i => {
    if (!terms.every(t => i._n.includes(t))) return null;
    const l = norm(i.label);
    const score = terms.reduce((s, t) => s + (l.startsWith(t) ? 6 : l.includes(t) ? 3 : 1), 0) + (i.sec === 'Sections' ? 1 : 0);
    return {i, score};
  }).filter(Boolean).sort((a, b) => b.score - a.score).slice(0, 50).map(x => x.i);
}
function openPalette() {
  if (document.getElementById('ccPal')) return;
  if (document.querySelector('.pw-overlay.active')) return; // pas avant connexion
  const items = paletteIndex();
  const back = document.createElement('div');
  back.className = 'cc-pal-back'; back.id = 'ccPal';
  back.innerHTML = `<div class="cc-pal" role="dialog" aria-modal="true" aria-label="Recherche">
    <div class="cc-pal-input">${ICON.search}<input id="ccPalIn" placeholder="Rechercher une section, un indicateur, un objectif, une action…" autocomplete="off" role="combobox" aria-expanded="true" aria-controls="ccPalList"><span class="cc-kbd" style="background:var(--bg);border:1px solid var(--border)">Esc</span></div>
    <div class="cc-pal-list" id="ccPalList" role="listbox"></div>
    <div class="cc-pal-foot"><span><span class="cc-kbd">↑</span> <span class="cc-kbd">↓</span> naviguer</span><span><span class="cc-kbd">↵</span> ouvrir</span><span style="margin-left:auto">${items.length} éléments indexés</span></div>
  </div>`;
  document.body.appendChild(back);
  const input = back.querySelector('#ccPalIn'), list = back.querySelector('#ccPalList');
  let res = [], act = 0;
  const draw = () => {
    res = searchPalette(items, input.value);
    act = Math.min(act, Math.max(0, res.length - 1));
    if (!res.length) { list.innerHTML = `<div class="cc-pal-empty">Aucun résultat pour « ${esc(input.value)} »</div>`; return; }
    let sec = '';
    list.innerHTML = res.map((i, k) => {
      const head = i.sec !== sec ? `<div class="cc-pal-sec">${esc(sec = i.sec)}</div>` : '';
      return head + `<div class="cc-pal-item${k === act ? ' active' : ''}" role="option" aria-selected="${k === act}" data-k="${k}">
        ${i.icon || `<span class="cc-pal-ico">${i.ico || ''}</span>`}
        <span class="lbl">${esc(i.label)}${i.hint ? `<small>${esc(i.hint)}</small>` : ''}</span>${i.val ? `<span class="val">${esc(i.val)}</span>` : ''}</div>`;
    }).join('');
    list.querySelector('.cc-pal-item.active')?.scrollIntoView({block: 'nearest'});
  };
  const close = () => { back.remove(); document.removeEventListener('keydown', onKey, true); };
  const run = k => { const it = res[k]; if (!it) return; close(); it.run(); };
  const onKey = e => {
    if (e.key === 'Escape') { e.preventDefault(); close(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); act = Math.min(res.length - 1, act + 1); draw(); }
    else if (e.key === 'ArrowUp')   { e.preventDefault(); act = Math.max(0, act - 1); draw(); }
    else if (e.key === 'Enter')     { e.preventDefault(); run(act); }
  };
  document.addEventListener('keydown', onKey, true);
  input.addEventListener('input', () => { act = 0; draw(); });
  list.addEventListener('mousemove', e => { const it = e.target.closest('.cc-pal-item'); if (it && +it.dataset.k !== act) { act = +it.dataset.k; list.querySelectorAll('.cc-pal-item').forEach(n => n.classList.toggle('active', +n.dataset.k === act)); } });
  list.addEventListener('click', e => { const it = e.target.closest('.cc-pal-item'); if (it) run(+it.dataset.k); });
  back.addEventListener('mousedown', e => { if (e.target === back) close(); });
  draw();
  input.focus();
}
CC.openPalette = openPalette;

document.addEventListener('keydown', e => {
  const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable;
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); }
  else if (e.key === '/' && !typing && !e.metaKey && !e.ctrlKey) { e.preventDefault(); openPalette(); }
});

// Bouton de recherche dans la topbar (visible sur tous les onglets)
(function mountTopbarSearch() {
  const host = document.querySelector('.tb-actions');
  if (!host || document.getElementById('ccTbSearch') || document.body.classList.contains('public-mode')) return;
  const b = document.createElement('button');
  b.id = 'ccTbSearch'; b.className = 'cc-tb-search'; b.title = 'Rechercher (⌘K / Ctrl+K)';
  b.innerHTML = `${ICON.search}<span>Rechercher</span><span class="cc-kbd">${isMac() ? '⌘' : 'Ctrl'} K</span>`;
  b.addEventListener('click', openPalette);
  host.insertBefore(b, host.firstChild);
})();

// Rendu en attente (si les données sont arrivées avant ce script)
if (window._ccPending) { const a = window._ccPending; window._ccPending = null; CC.renderOverview(a); }
ensureObjectives();
})();
