// ═════════════════════════════════════════════════════════════════════════════
// Générateur de newsletter interne POWER ON!
// Formulaire → aperçu en direct → HTML email compatible Outlook (tables + styles
// inline, 640px), basé sur _templates/Emails/newsletter poweron - cap2032/.
// Dépend du script principal : API_BASE, showToast.
// ═════════════════════════════════════════════════════════════════════════════
(function () {
  const LS_KEY   = 'poweron_builder_draft_v1';
  const RICH     = new Set(['body']);
  const FREQ_RE  = /\b(mensuel(le)?s?|chaque mois|tous les mois|hebdomadaire|trimestriel(le)?s?|monatlich(e[nrs]?)?|jeden monat|wöchentlich)\b/i;
  const GMAIL_CLIP = 100 * 1024;

  // ── Modèle ──────────────────────────────────────────────────────────────────
  // Champs traduisibles : item.t.fr / item.t.de. Le reste (noms, photos,
  // structure) est commun aux deux langues.
  const person = () => ({ name: '', photo: { url: '' } });
  const newFocus = () => ({ ...person(), twoCols: true,
    t: { fr: { label: 'Focus projet', title: '', role: '', body: '' }, de: { label: 'Projekt im Fokus' } },
    contrib: { on: false, ...person(), t: { fr: { title: '', role: '', quote: '' }, de: {} } },
    // Blocs affichés juste après ce projet (en plus de ceux de fin d'email)
    extras: { gallery: newGallery(), encadre: newEncadre(), kpis: newKpis() } });
  const newMetier = () => ({ ...person(),
    t: { fr: { label: 'Focus métier', title: '', role: '', quote: '' }, de: { label: 'Berufsfokus' } } });
  const newKpi = () => ({ t: { fr: { value: '', label: '' }, de: {} } });
  const newCol = () => ({ t: { fr: { subtitle: '', items: '' }, de: {} } });
  const newGallery = () => ({ on: false, photos: [{ url: '', alt: '' }] });
  const newEncadre = () => ({ on: false, cols: [newCol(), newCol()], t: { fr: { title: '' }, de: {} } });
  const newKpis = () => ({ on: false, items: [newKpi(), newKpi(), newKpi()] });
  // sig : signature manuscrite (image PNG), w/h = taille d'affichage en px
  const newSigner = () => ({ ...person(), sig: { url: '', w: 0, h: 0 }, t: { fr: { title: '' }, de: {} } });

  function blank() {
    return {
      v: 1,
      meta: { edition: '', t: { fr: { periode: '', headline: '', preheader: '' }, de: { headline: '' } } },
      edito:     { on: true, ...person(), sign: { on: false, people: [newSigner()] }, t: { fr: { label: 'Édito', title: '', body: '' }, de: { label: 'Editorial' } } },
      focus:     [newFocus()],
      gallery:   newGallery(),
      metier:    [],
      encadre:   newEncadre(),
      kpis:      newKpis(),
      signature: { on: true, people: [newSigner()], t: { fr: { body: '' }, de: {} } },
      de:        { on: false, src: '' },
    };
  }

  let S = load();
  let lang = 'fr';
  let previewMode = 'desktop';
  const pending = new Set();
  let built = false;

  function load() {
    try { const s = JSON.parse(localStorage.getItem(LS_KEY) || 'null'); if (s && s.v === 1) return migrate(s); } catch (e) {}
    return blank();
  }
  // Brouillons créés avant l'ajout des blocs par projet et des 2 signataires
  function migrate(s) {
    if (!s.edito.sign) s.edito.sign = { on: false, people: [newSigner()] };
    s.focus.forEach(f => { if (!f.extras) f.extras = { gallery: newGallery(), encadre: newEncadre(), kpis: newKpis() }; });
    const g = s.signature;
    if (!g.people) {
      g.people = [{ name: g.name || '', photo: g.photo || { url: '' }, sig: { url: '', w: 0, h: 0 },
        t: { fr: { title: g.t.fr.title || '' }, de: { title: (g.t.de || {}).title || '' } } }];
      delete g.name; delete g.photo; delete g.t.fr.title; if (g.t.de) delete g.t.de.title;
    }
    return s;
  }
  function save() { try { localStorage.setItem(LS_KEY, JSON.stringify(S)); } catch (e) {} }

  const getP = (o, p) => p.split('.').reduce((a, k) => (a == null ? a : a[k]), o);
  function setP(o, p, v) {
    const ks = p.split('.'); let a = o;
    ks.slice(0, -1).forEach(k => { if (a[k] == null) a[k] = {}; a = a[k]; });
    a[ks[ks.length - 1]] = v;
  }

  // ── Utilitaires texte ───────────────────────────────────────────────────────
  const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  const isUrl = u => /^https:\/\/\S+$/i.test(u || '');

  // Texte riche (contenteditable) → liste de paragraphes ne contenant que
  // <strong>, <em>, <a href> et <br>, le reste du HTML étant échappé.
  const BLOCK = /^(P|DIV|LI|UL|OL|H[1-6]|BLOCKQUOTE|SECTION|ARTICLE|TABLE|TR)$/;
  function richToParas(html) {
    const root = document.createElement('div');
    root.innerHTML = html || '';
    const paras = []; let cur = '';
    const flush = () => {
      const s = cur.replace(/^(\s|&nbsp;|<br>)+|(\s|&nbsp;|<br>)+$/g, '');
      if (s) paras.push(s);
      cur = '';
    };
    function inline(n) {
      if (n.nodeType === 3) return esc(n.nodeValue.replace(/\s+/g, ' '));
      if (n.nodeType !== 1) return '';
      if (/^(SCRIPT|STYLE|TEMPLATE|NOSCRIPT)$/.test(n.tagName)) return '';
      if (n.tagName === 'BR') return '<br>';
      let s = [...n.childNodes].map(inline).join('');
      if (!s) return '';
      const fw = n.style && n.style.fontWeight;
      if (n.tagName === 'B' || n.tagName === 'STRONG' || fw === 'bold' || +fw >= 600) s = `<strong>${s}</strong>`;
      if (n.tagName === 'I' || n.tagName === 'EM' || (n.style && n.style.fontStyle === 'italic')) s = `<em>${s}</em>`;
      if (n.tagName === 'A') {
        const href = n.getAttribute('href') || '';
        if (/^(https?:|mailto:)/i.test(href)) s = `<a href="${esc(href)}">${s}</a>`;
      }
      return s;
    }
    function walk(n) {
      if (n.nodeType === 1 && BLOCK.test(n.tagName)) { flush(); [...n.childNodes].forEach(walk); flush(); }
      else cur += inline(n);
    }
    [...root.childNodes].forEach(walk);
    flush();
    return paras;
  }
  const plain = html => { const d = document.createElement('div'); d.innerHTML = richToParas(html).join(' '); return d.textContent; };
  const normRich = html => richToParas(html).map(p => `<p>${p}</p>`).join('');
  const linkStyle = h => h.replace(/<a href=/g, '<a style="color:#1a1a1a; text-decoration:underline;" href=');
  const stripQuotes = s => String(s || '').trim().replace(/^[«„"“\s]+|[»“"”\s]+$/g, '');

  // Texte d'un champ dans la langue demandée, avec repli sur le FR.
  const tx = (item, k, l) => {
    const v = l === 'de' ? (item.t.de || {})[k] : null;
    return (v != null && String(v).trim() !== '') ? v : (item.t.fr[k] || '');
  };

  // ── Génération HTML email ───────────────────────────────────────────────────
  const F = "font-family:'Albert Sans',Arial,Helvetica,sans-serif;";
  const row = (pad, inner) => `
          <tr>
            <td style="padding:${pad}px 44px 0 44px;" class="px">
${inner}
            </td>
          </tr>
`;
  const divider = pad => row(pad, `              <div style="height:1px; background-color:#ededed; font-size:0; line-height:0;">&nbsp;</div>`);
  const yellowBar = pad => row(pad, `              <div style="height:3px; background-color:#FCD900; font-size:0; line-height:0;">&nbsp;</div>`);
  const secLabel = txt => `              <p style="margin:0 0 14px 0; ${F} font-size:12px; font-weight:bold; letter-spacing:1.5px; text-transform:uppercase; color:#9a9a9a;"><span style="border-bottom:2px solid #FCD900; padding-bottom:4px;">${esc(txt)}</span></p>`;

  // Bloc avatar + nom + sous-titre. sz : {img, name, sub, gap, mb}
  function personHead(p, sub, sz) {
    const url = p.photo && p.photo.url;
    const img = isUrl(url) ? `
                  <td valign="middle" style="padding-right:${sz.gap}px;">
                    <img src="${esc(url)}" alt="${esc(p.name)}" width="${sz.img}" height="${sz.img}" style="display:block; width:${sz.img}px; height:${sz.img}px; border-radius:50%; object-fit:cover;" />
                  </td>` : '';
    return `              <table role="presentation" cellpadding="0" cellspacing="0"${sz.mb ? ` style="margin-bottom:${sz.mb}px;"` : ''}>
                <tr>${img}
                  <td valign="middle">
                    <p style="margin:0; ${F} font-size:${sz.name}px; line-height:${sz.lh}; color:#1a1a1a;"><strong>${esc(p.name)}</strong>${sub ? `<br><span style="font-size:${sz.sub}px; color:#777777;">${esc(sub)}</span>` : ''}</p>
                  </td>
                </tr>
              </table>`;
  }
  const SZ_HEAD = { img: 44, name: 13.5, sub: 12, gap: 12, mb: 14, lh: 1.4 };
  const SZ_CARD = { img: 40, name: 13, sub: 11.5, gap: 12, mb: 12, lh: 1.4 };
  const SZ_SIGN = { img: 52, name: 14, sub: 13, gap: 14, mb: 0, lh: 1.5 };
  const subOf = (item, l) => [tx(item, 'title', l), tx(item, 'role', l)].filter(s => s && s.trim()).join(' · ');

  function paras(html, style, lastMargin = '0') {
    const ps = richToParas(html);
    return ps.map((p, i) => `              <p style="margin:${i === ps.length - 1 ? lastMargin : '0 0 14px 0'};${style}">${linkStyle(p)}</p>`).join('\n');
  }
  const BODY = ` ${F} font-size:13.5px; line-height:1.7; color:#333333;`;

  function quoteCard(p, item, l, extraStyle) {
    const q = stripQuotes(tx(item, 'quote', l));
    const [o, c] = l === 'de' ? ['„', '“'] : ['« ', ' »'];
    return `              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #ededed; border-left:4px solid #FCD900; border-radius:10px;${extraStyle || ''}">
                <tr>
                  <td style="padding:20px 24px;">
${personHead(p, subOf(item, l), SZ_CARD).replace(/^ {14}/gm, '                    ')}
                    <p style="margin:0; ${F} font-size:13px; line-height:1.7; color:#333333;">${q ? esc(o + q + c) : ''}</p>
                  </td>
                </tr>
              </table>`;
  }

  function blkEdito(l) {
    const e = S.edito;
    const sg = e.sign.on ? signers(e.sign.people, l) : '';
    return `
          <!-- EDITO -->` + row(26, [secLabel(tx(e, 'label', l)), e.name ? personHead(e, tx(e, 'title', l), SZ_HEAD) : '',
      paras(tx(e, 'body', l), BODY, sg ? '0 0 16px 0' : '0'), sg].filter(Boolean).join('\n'));
  }

  function blkFocus(f, l, first) {
    const ps = richToParas(tx(f, 'body', l));
    const pp = (list, style) => list.map((p, i) => `                    <p style="margin:${i === list.length - 1 ? '0' : '0 0 14px 0'};">${linkStyle(p)}</p>`).join('\n');
    let body = '';
    if (ps.length) {
      if (f.twoCols && ps.length > 1) {
        const mid = Math.ceil(ps.length / 2);
        body = `              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td class="stack" valign="top" width="50%" style="padding-right:20px;${BODY}">
${pp(ps.slice(0, mid))}
                  </td>
                  <td class="stack stack-pad" valign="top" width="50%" style="${BODY.trim()}">
${pp(ps.slice(mid))}
                  </td>
                </tr>
              </table>`;
      } else {
        body = paras(tx(f, 'body', l), BODY);
      }
    }
    const contrib = f.contrib.on && f.contrib.name ? quoteCard(f.contrib, f.contrib, l, ' margin-top:22px;') : '';
    return (first ? '' : divider(28)) + `
          <!-- FOCUS PROJET -->` + row(22, [secLabel(tx(f, 'label', l)), f.name ? personHead(f, subOf(f, l), SZ_HEAD) : '', body, contrib].filter(Boolean).join('\n'));
  }

  function blkGallery(g) {
    const ph = g.photos.filter(p => isUrl(p.url));
    if (!ph.length) return '';
    const n = ph.length, w = n === 1 ? 552 : n === 2 ? 268 : 173, h = Math.round(w * 0.75);
    const pct = n === 1 ? '100%' : n === 2 ? '50%' : '33.33%';
    const pad = i => n === 1 ? '' : i === 0 ? ' style="padding-right:8px;"' : i === n - 1 ? ' style="padding-left:8px;"' : ' style="padding:0 8px;"';
    const cells = ph.map((p, i) => `                  <td class="stack gal-cell${i ? ' stack-pad' : ''}" width="${pct}" valign="top"${pad(i)}>
                    <img src="${esc(p.url)}" alt="${esc(p.alt)}" width="${w}" height="${h}" class="gal-img" style="display:block; width:${w}px; height:${h}px; border-radius:8px;" />
                  </td>`).join('\n');
    return `
          <!-- GALERIE PHOTOS -->` + row(22, `              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                <tr>
${cells}
                </tr>
              </table>`);
  }

  function blkMetier(m, l) {
    return `
          <!-- FOCUS MÉTIER -->` + row(28, [secLabel(tx(m, 'label', l)), quoteCard(m, m, l)].join('\n'));
  }

  function blkEncadre(E, l) {
    const cols = E.cols.filter(c => tx(c, 'subtitle', l).trim() || tx(c, 'items', l).trim());
    if (!cols.length && !tx(E, 'title', l).trim()) return '';
    const col = (c, i, n) => {
      const items = tx(c, 'items', l).split('\n').map(s => s.replace(/^[\s•●\-–*]+/, '').trim()).filter(Boolean);
      const sub = tx(c, 'subtitle', l).trim();
      const attrs = n === 2 ? (i === 0 ? ' class="stack" valign="top" width="50%" style="padding-right:20px;"' : ' class="stack stack-pad" valign="top" width="50%"') : ' valign="top" width="100%"';
      return `                        <td${attrs}>
${sub ? `                          <p style="margin:0 0 8px 0; ${F} font-size:11px; font-weight:bold; letter-spacing:0.4px; text-transform:uppercase; color:#9a9a9a;">${esc(sub)}</p>\n` : ''}${items.map((it, j) => `                          <p style="margin:${j === items.length - 1 ? '0' : '0 0 6px 0'}; ${F} font-size:13px; line-height:1.6; color:#333333;"><span style="color:#FCD900;">●</span>&nbsp;&nbsp;${esc(it)}</p>`).join('\n')}
                        </td>`;
    };
    const title = tx(E, 'title', l).trim();
    return `
          <!-- ENCADRÉ -->` + row(28, `              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #ededed; border-radius:10px;">
                <tr>
                  <td style="padding:22px 26px;">
${title ? `                    <p style="margin:0 0 16px 0; ${F} font-size:13px; font-weight:bold; letter-spacing:0.6px; text-transform:uppercase; color:#1a1a1a;">${esc(title)}</p>\n` : ''}                    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                      <tr>
${cols.map((c, i) => col(c, i, cols.length)).join('\n')}
                      </tr>
                    </table>
                  </td>
                </tr>
              </table>`);
  }

  function blkKpis(K, l) {
    const items = K.items.filter(k => tx(k, 'value', l).trim());
    if (!items.length) return '';
    const w = Math.floor(100 / items.length) + '%';
    const cells = items.map((k, i) => `                  <td class="kpi-stack" width="${w}" align="center" style="padding:20px 8px;${i < items.length - 1 ? ' border-right:1px solid #ededed;' : ''}">
                    <div style="${F} font-size:26px; font-weight:800; color:#1a1a1a; line-height:1;">${esc(tx(k, 'value', l))}</div>
                    <div style="${F} font-size:10px; font-weight:bold; letter-spacing:0.4px; text-transform:uppercase; color:#9a9a9a; margin-top:6px;">${esc(tx(k, 'label', l))}</div>
                  </td>`).join('\n');
    return `
          <!-- CHIFFRES CLÉS -->` + row(28, `              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#ffffff; border:1px solid #ededed; border-top:3px solid #FCD900; border-radius:10px;">
                <tr>
${cells}
                </tr>
              </table>`);
  }

  // Signature manuscrite (optionnelle) au-dessus du nom de chaque signataire
  function signer(p, l, ind) {
    const sig = isUrl(p.sig && p.sig.url) ? `${ind}<img src="${esc(p.sig.url)}" alt="Signature de ${esc(p.name)}" width="${p.sig.w || 180}" height="${p.sig.h || 60}" style="display:block; width:${p.sig.w || 180}px; height:${p.sig.h || 60}px; margin:0 0 10px 0;" />\n` : '';
    return sig + personHead(p, tx(p, 'title', l), SZ_SIGN).replace(/^ {14}/gm, ind);
  }
  function blkSignature(l) {
    const s = S.signature;
    const parts = [`              <div style="height:1px; background-color:#ededed; font-size:0; line-height:0; margin-bottom:22px;">&nbsp;</div>`];
    const b = paras(tx(s, 'body', l), BODY, '0 0 16px 0');
    if (b) parts.push(b);
    const sg = signers(s.people, l);
    if (sg) parts.push(sg);
    return `
          <!-- SIGNATURE -->` + row(30, parts.join('\n'));
  }

  // 1 signataire : bloc simple ; 2 : côte à côte (empilés sur mobile)
  function signers(people, l) {
    const ppl = people.filter(p => p.name.trim());
    if (ppl.length === 1) return signer(ppl[0], l, '              ');
    if (ppl.length === 2) return `              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td class="stack" valign="bottom" width="50%" style="padding-right:20px;">
${signer(ppl[0], l, '                    ')}
                  </td>
                  <td class="stack stack-pad" valign="bottom" width="50%">
${signer(ppl[1], l, '                    ')}
                  </td>
                </tr>
              </table>`;
    return '';
  }

  const extras = (x, l) => (x.gallery.on ? blkGallery(x.gallery) : '') + (x.encadre.on ? blkEncadre(x.encadre, l) : '') + (x.kpis.on ? blkKpis(x.kpis, l) : '');

  function blocks(l) {
    let h = '';
    if (S.edito.on) h += blkEdito(l);
    S.focus.forEach((f, i) => {
      if (i === 0 && S.edito.on) h += divider(22);
      h += blkFocus(f, l, i === 0) + extras(f.extras, l);
    });
    if (S.gallery.on) h += blkGallery(S.gallery);
    S.metier.forEach(m => { h += blkMetier(m, l); });
    if (S.encadre.on) h += blkEncadre(S.encadre, l);
    if (S.kpis.on) h += blkKpis(S.kpis, l);
    if (S.signature.on) h += blkSignature(l);
    return h;
  }

  function buildEmail() {
    const m = S.meta, fr = m.t.fr;
    const ed = String(m.edition || '').trim();
    const title = 'POWER ON!' + (ed ? ` — Édition n°${ed}` : '');
    const edLine = [ed ? `Édition n°${ed}` : '', (fr.periode || '').trim()].filter(Boolean).join(' — ');
    const year = new Date().getFullYear();
    let h = `<!DOCTYPE html>
<html lang="fr" xmlns="http://www.w3.org/1999/xhtml" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <meta name="x-apple-disable-message-reformatting">
  <title>${esc(title)}</title>
  <!--[if mso]>
  <noscript><xml><o:OfficeDocumentSettings><o:PixelsPerInch>96</o:PixelsPerInch></o:OfficeDocumentSettings></xml></noscript>
  <![endif]-->
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Albert+Sans:wght@200;400;600;700;800&display=swap');
    body, table, td, a { -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }
    table, td { mso-table-lspace: 0pt; mso-table-rspace: 0pt; }
    img { -ms-interpolation-mode: bicubic; border: 0; outline: none; text-decoration: none; display: block; }
    body { margin: 0 !important; padding: 0 !important; width: 100% !important; }
    a { text-decoration: none; }
    @media only screen and (max-width: 640px) {
      .container { width: 100% !important; max-width: 100% !important; }
      .px { padding-left: 22px !important; padding-right: 22px !important; }
      .stack { display: block !important; width: 100% !important; box-sizing: border-box !important; padding-right: 0 !important; }
      .stack-pad { padding-top: 16px !important; }
      .gal-cell { padding-left: 0 !important; padding-right: 0 !important; }
      .gal-img { width: 100% !important; }
      .kpi-stack { display: block !important; width: 100% !important; border-right: none !important; border-bottom: 1px solid #ededed; }
      img { max-width: 100% !important; height: auto !important; }
    }
  </style>
</head>
<body style="margin:0; padding:0; background-color:#ececed; font-family:'Albert Sans',Arial,Helvetica,sans-serif; color:#1f1f1f;">

  <!-- Préheader caché -->
  <div style="display:none; max-height:0; overflow:hidden; mso-hide:all; font-size:1px; line-height:1px; color:#ececed; opacity:0;">
    ${esc(fr.preheader)}&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;
  </div>

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#ececed;">
    <tr>
      <td align="center" style="padding:24px 12px;">

        <!-- Carte email -->
        <table role="presentation" class="container" width="640" cellpadding="0" cellspacing="0" style="width:640px; max-width:640px; background-color:#ffffff; border-radius:14px; overflow:hidden; box-shadow:0 6px 24px rgba(0,0,0,0.07);">

          <!-- MASTHEAD -->
          <tr>
            <td style="padding:34px 44px 0 44px;" class="px">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td valign="top" width="68%">
                    <div style="${F} font-size:34px; font-weight:800; letter-spacing:-0.5px; line-height:1.05; color:#1a1a1a; text-transform:uppercase;">POWER ON!</div>
                    <div style="${F} font-size:13px; font-weight:600; color:#666666; margin-top:6px;">Le rendez-vous qui met vos projets en lumière</div>
                  </td>
                  <td valign="top" align="right" width="32%">
                    <img src="https://img.mailinblue.com/4363334/images/content_library/original/6a8c4a6ba57749ebdeda8f00.png" alt="ACL – Automobile Club du Luxembourg" width="86" style="display:block; width:86px; max-width:86px; height:auto; margin-left:auto;" />
                    <div style="${F} font-size:11px; color:#9a9a9a; margin-top:10px; text-align:right;">${esc(edLine)}</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>
` + yellowBar(18);
    if ((fr.headline || '').trim()) h += row(14, `              <p style="margin:0; ${F} font-size:15px; font-weight:bold; color:#1a1a1a;">${esc(fr.headline)}</p>`);
    if (S.de.on) h += row(8, `              <p style="margin:0; ${F} font-size:11.5px; font-style:italic; color:#9a9a9a;">Eine deutsche Version dieser Ausgabe finden Sie am Ende dieser E-Mail.</p>`);
    h += blocks('fr');
    if (S.de.on) {
      h += `
          <!-- SÉPARATEUR VERSION ALLEMANDE -->` + yellowBar(34)
        + row(18, `              <p style="margin:0; ${F} font-size:12px; font-weight:bold; letter-spacing:1.5px; text-transform:uppercase; color:#9a9a9a;"><span style="border-bottom:2px solid #FCD900; padding-bottom:4px;">Deutsche Version</span></p>`);
      const hd = tx(m, 'headline', 'de');
      if (hd.trim()) h += row(10, `              <p style="margin:0; ${F} font-size:15px; font-weight:bold; color:#1a1a1a;">${esc(hd)}</p>`);
      h += blocks('de').replace(/<!-- ([^>]+?) -->/g, '<!-- $1 DE -->');
    }
    h += divider(32) + `
          <!-- FOOTER -->
          <tr>
            <td bgcolor="#FCD900" style="background-color:#FCD900; padding:26px 44px 18px 44px;" class="px">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="${F} margin:0 0 16px 0;">
                <tr>
                  <td width="33%" style="vertical-align:top; padding-right:14px;">
                    <p style="margin:0 0 4px 0; font-size:13px; font-weight:bold; color:#1a1a1a;">ACL Bertrange</p>
                    <p style="margin:0; font-size:12px; line-height:1.6; color:#1a1a1a;">54, route de Longwy<br>L-8080 Bertrange</p>
                  </td>
                  <td width="1" style="border-left:1px solid rgba(26,26,26,0.25); font-size:0;">&nbsp;</td>
                  <td width="33%" style="vertical-align:top; padding:0 14px;">
                    <p style="margin:0 0 4px 0; font-size:13px; font-weight:bold; color:#1a1a1a;">ACL Ingeldorf</p>
                    <p style="margin:0; font-size:12px; line-height:1.6; color:#1a1a1a;">34, route d'Ettelbruck<br>L-9160 Ingeldorf</p>
                  </td>
                  <td width="1" style="border-left:1px solid rgba(26,26,26,0.25); font-size:0;">&nbsp;</td>
                  <td width="33%" style="vertical-align:top; padding-left:14px;">
                    <p style="margin:0 0 4px 0; font-size:13px; font-weight:bold; color:#1a1a1a;">ACL Karting</p>
                    <p style="margin:0; font-size:12px; line-height:1.6; color:#1a1a1a;">152, rue de Limpach<br>L-3932 Mondercange</p>
                  </td>
                </tr>
              </table>
              <p style="margin:0 0 8px 0; ${F} font-size:11px; line-height:1.6; color:#1a1a1a; text-align:center;">Cet email vous a été envoyé en tant que collaborateur·rice ACL.</p>
              <p style="margin:0; ${F} font-size:10px; color:#1a1a1a; text-align:center;">© ${year} Automobile Club du Luxembourg — Communication interne</p>
            </td>
          </tr>

        </table>

      </td>
    </tr>
  </table>

</body>
</html>
`;
    return h;
  }

  // ── Traduction ──────────────────────────────────────────────────────────────
  // Collecte tous les champs t.fr non vides : { "focus.0.t.de.body": "<p>…</p>" }
  function collectFr() {
    const out = {};
    (function walk(o, base) {
      if (!o || typeof o !== 'object') return;
      if (o.t && o.t.fr) {
        for (const [k, v] of Object.entries(o.t.fr)) {
          const val = RICH.has(k) ? normRich(v) : (k === 'quote' ? stripQuotes(v) : String(v || '').trim());
          if (val) out[`${base}t.de.${k}`] = val;
        }
      }
      for (const [k, v] of Object.entries(o)) if (k !== 't' && v && typeof v === 'object') walk(v, `${base}${k}.`);
    })(S, '');
    delete out['meta.t.de.preheader'];
    delete out['meta.t.de.periode'];
    return out;
  }
  const frSignature = () => JSON.stringify(collectFr());

  async function translate() {
    const texts = collectFr();
    if (!Object.keys(texts).length) { showToast('Rien à traduire : remplissez d\'abord la version française', 'error'); return; }
    const hasDe = Object.keys(texts).some(k => { const v = getP(S, k); return v && String(v).trim(); });
    if (hasDe && !confirm('Remplacer la version allemande actuelle par une nouvelle traduction ?\nLes modifications faites à la main en DE seront perdues.')) return;
    const btn = document.querySelector('#pob [data-act="translate"]');
    if (btn) { btn.disabled = true; btn.textContent = 'Traduction en cours…'; }
    try {
      const r = await fetch(`${API_BASE}/poweron/translate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ texts }), signal: AbortSignal.timeout(120000),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.error) throw new Error(d.error || `HTTP ${r.status}`);
      for (const [k, v] of Object.entries(d.texts || {})) setP(S, k, RICH.has(k.split('.').pop()) ? normRich(v) : v);
      S.de.src = JSON.stringify(texts);
      save();
      lang = 'de';
      renderForm(); schedule();
      showToast('Version allemande générée — relisez-la avant envoi');
    } catch (e) {
      showToast('Traduction impossible : ' + e.message, 'error');
      if (btn) { btn.disabled = false; btn.textContent = 'Traduire en allemand'; }
    }
  }

  // ── Images ──────────────────────────────────────────────────────────────────
  // Recadrage centré + export JPEG côté navigateur : Outlook ne lit pas le
  // WebP, et des proportions fixes évitent les images déformées (Outlook
  // ignore object-fit). Portrait 1:1, galerie 4:3.
  async function prepareImage(file, kind) {
    const bmp = await createImageBitmap(file);
    if (kind === 'signature') {
      // Signature : pas de recadrage, PNG pour garder la transparence,
      // 360×120 max (affichée en 180×60 max, nette sur écrans haute densité)
      const k = Math.min(360 / bmp.width, 120 / bmp.height, 1);
      const cw = Math.max(1, Math.round(bmp.width * k)), ch = Math.max(1, Math.round(bmp.height * k));
      const c = document.createElement('canvas'); c.width = cw; c.height = ch;
      c.getContext('2d').drawImage(bmp, 0, 0, cw, ch);
      const blob = await new Promise(res => c.toBlob(res, 'image/png'));
      return { blob, ext: 'png', w: Math.round(cw / 2), h: Math.round(ch / 2) };
    }
    const [tw, th] = kind === 'portrait' ? [240, 240] : [1104, 828];
    const s = Math.max(tw / bmp.width, th / bmp.height);
    const w = bmp.width * s, h = bmp.height * s;
    const c = document.createElement('canvas'); c.width = tw; c.height = th;
    const ctx = c.getContext('2d');
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, tw, th);
    ctx.drawImage(bmp, (tw - w) / 2, (th - h) / 2, w, h);
    const blob = await new Promise(res => c.toBlob(res, 'image/jpeg', 0.85));
    return { blob, ext: 'jpg' };
  }

  async function uploadPhoto(path, kind, file) {
    if (!file || !/^image\//.test(file.type)) { showToast('Choisissez un fichier image (JPG, PNG…)', 'error'); return; }
    pending.add(path); renderForm(); schedule();
    try {
      const img = await prepareImage(file, kind);
      const fd = new FormData();
      fd.append('image', img.blob, 'image.' + img.ext);
      fd.append('name', `${kind}-${file.name.replace(/\.[^.]+$/, '')}`);
      const r = await fetch(`${API_BASE}/poweron/upload-image`, { method: 'POST', body: fd, signal: AbortSignal.timeout(60000) });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d.url) throw new Error(d.error || `HTTP ${r.status}`);
      setP(S, path + '.url', d.url);
      if (img.w) { setP(S, path + '.w', img.w); setP(S, path + '.h', img.h); }
      save();
      showToast('Image enregistrée dans la galerie Brevo');
    } catch (e) {
      showToast('Envoi de l\'image impossible : ' + e.message, 'error');
    }
    pending.delete(path); renderForm(); schedule();
  }

  // ── Contrôles avant export ──────────────────────────────────────────────────
  function checks(html) {
    const w = [];
    const fr = S.meta.t.fr;
    if (!String(S.meta.edition || '').trim()) w.push('Numéro d\'édition manquant');
    if (!fr.periode.trim()) w.push('Période manquante (ex : Octobre 2026)');
    if (!fr.headline.trim()) w.push('Titre de l\'édition manquant');
    if (!fr.preheader.trim()) w.push('Préheader vide (texte d\'aperçu affiché dans la boîte de réception)');
    if (pending.size) w.push('Image en cours d\'envoi, attendez la fin avant de copier');
    const ppl = [];
    if (S.edito.on) {
      ppl.push(['Édito', S.edito]);
      if (S.edito.sign.on) S.edito.sign.people.forEach((p, i) => ppl.push([`Édito — signataire ${i + 1}`, p]));
    }
    S.focus.forEach((f, i) => {
      ppl.push([`Focus projet ${i + 1}`, f]);
      if (!f.t.fr.role.trim()) w.push(`Focus projet ${i + 1} : rôle projet manquant (sponsor, chef de projet, contributeur…)`);
      if (!richToParas(f.t.fr.body).length) w.push(`Focus projet ${i + 1} : texte vide`);
      if (f.contrib.on) {
        ppl.push([`Focus projet ${i + 1} — citation`, f.contrib]);
        if (!f.contrib.t.fr.role.trim()) w.push(`Focus projet ${i + 1} — citation : rôle projet manquant`);
      }
    });
    S.metier.forEach((m, i) => { ppl.push([`Focus métier ${i + 1}`, m]); if (!stripQuotes(m.t.fr.quote)) w.push(`Focus métier ${i + 1} : citation vide`); });
    if (S.signature.on) S.signature.people.forEach((p, i) => ppl.push([`Signataire ${i + 1}`, p]));
    ppl.forEach(([lbl, p]) => {
      if (!p.name.trim()) w.push(`${lbl} : nom manquant`);
      else if (!isUrl(p.photo.url) && !(p.sig && isUrl(p.sig.url))) w.push(`${lbl} : pas de photo (le bloc s'affichera sans portrait)`);
      if (p.photo.url && !isUrl(p.photo.url)) w.push(`${lbl} : l'URL de la photo doit commencer par https://`);
    });
    const chkGallery = (g, lbl) => {
      if (!g.on) return;
      if (!g.photos.some(p => isUrl(p.url))) w.push(`${lbl}Galerie activée mais aucune photo chargée`);
      g.photos.forEach((p, i) => {
        if (!isUrl(p.url)) w.push(`${lbl}Galerie : emplacement ${i + 1} vide (ignoré)`);
        else if (!p.alt.trim()) w.push(`${lbl}Galerie : photo ${i + 1} sans texte alternatif`);
      });
    };
    const chkKpis = (K, lbl) => { if (K.on && K.items.some(k => !k.t.fr.value.trim())) w.push(`${lbl}Chiffres clés : valeur manquante (KPI ignoré)`); };
    S.focus.forEach((f, i) => { chkGallery(f.extras.gallery, `Focus projet ${i + 1} — `); chkKpis(f.extras.kpis, `Focus projet ${i + 1} — `); });
    chkGallery(S.gallery, '');
    chkKpis(S.kpis, '');
    const allText = JSON.stringify(S);
    const fm = allText.match(FREQ_RE);
    if (fm) w.push(`« ${fm[0]} » : PowerON n'a pas de cadence fixe, évitez toute mention de fréquence`);
    if (S.de.on) {
      if (!S.de.src) w.push('Version allemande activée mais pas encore traduite');
      else if (S.de.src !== frSignature()) w.push('Le texte français a changé depuis la traduction : relancez-la ou ajustez la version DE');
      const missing = Object.keys(collectFr()).filter(k => !String(getP(S, k) || '').trim()).length;
      if (S.de.src && missing) w.push(`${missing} champ(s) DE vide(s) : le texte français est repris à la place`);
    }
    const kb = new Blob([html]).size;
    if (kb > GMAIL_CLIP) w.push(`HTML de ${Math.round(kb / 1024)} Ko : Gmail tronque les emails au-delà d'environ 100 Ko`);
    return w;
  }

  // ── Formulaire ──────────────────────────────────────────────────────────────
  const DE = () => lang === 'de';
  function fT(label, item, base, key, o = {}) {
    const p = `${base}.t.${lang}.${key}`;
    const v = getP(S, p) || '';
    const frHint = DE() && item.t.fr[key] ? `<div class="pob-fr">FR : ${esc(RICH.has(key) ? plain(item.t.fr[key]) : item.t.fr[key])}</div>` : '';
    let input;
    if (RICH.has(key)) {
      input = `<div class="pob-rich-wrap">
        <div class="pob-rich-tb">
          <button type="button" data-cmd="bold" title="Gras"><b>G</b></button>
          <button type="button" data-cmd="italic" title="Italique"><i>I</i></button>
          <button type="button" data-cmd="link" title="Lien">Lien</button>
          <button type="button" data-cmd="unlink" title="Retirer le lien">Retirer lien</button>
        </div>
        <div class="pob-rich" contenteditable="true" data-p="${p}" data-rich="1" data-ph="${esc(o.ph || '')}">${normRich(v)}</div>
      </div>`;
    } else if (o.area) {
      input = `<textarea class="pob-in" rows="${o.rows || 3}" data-p="${p}" placeholder="${esc(o.ph || '')}">${esc(v)}</textarea>`;
    } else {
      input = `<input class="pob-in" type="text" data-p="${p}" value="${esc(v)}" placeholder="${esc(o.ph || '')}">`;
    }
    return `<label class="pob-f${o.half ? ' half' : ''}"><span>${label}</span>${input}${o.hint ? `<small>${o.hint}</small>` : ''}${frHint}</label>`;
  }
  function fN(label, p, o = {}) {
    if (DE()) return '';
    return `<label class="pob-f${o.half ? ' half' : ''}"><span>${label}</span><input class="pob-in" type="text" data-p="${p}" value="${esc(getP(S, p) || '')}" placeholder="${esc(o.ph || '')}">${o.hint ? `<small>${o.hint}</small>` : ''}</label>`;
  }
  function fPhoto(label, p, kind, o = {}) {
    if (DE()) return '';
    const ph = getP(S, p) || {};
    const busy = pending.has(p);
    const thumb = busy ? '<span class="pob-spin"></span>' : isUrl(ph.url) ? `<img src="${esc(ph.url)}" alt="">` : '<span>＋</span>';
    const help = kind === 'portrait' ? 'Recadrage automatique carré.' : kind === 'gallery' ? 'Recadrage automatique 4:3.' : 'PNG sur fond transparent ou blanc idéalement. Redimensionnée automatiquement (180×60 px max).';
    return `<div class="pob-f pob-photo${kind === 'portrait' ? ' round' : kind === 'signature' ? ' sig' : ''}" data-photo="${p}" data-kind="${kind}">
      <span>${label}</span>
      <div class="pob-photo-row">
        <button type="button" class="pob-thumb" data-act="pick" title="Choisir ou déposer une image">${thumb}</button>
        <div class="pob-photo-side">
          <div class="pob-btns">
            <button type="button" class="pob-btn sm" data-act="pick">${isUrl(ph.url) ? 'Remplacer' : 'Choisir une image'}</button>
            ${ph.url ? '<button type="button" class="pob-btn sm ghost" data-act="photo-del">Retirer</button>' : ''}
          </div>
          <small>${busy ? 'Envoi vers Brevo…' : 'Glisser-déposer ou cliquer. ' + help}</small>
          <input class="pob-in sm" type="url" data-p="${p}.url" value="${esc(ph.url || '')}" placeholder="…ou coller l'URL https d'une image en ligne">
          ${o.alt ? `<input class="pob-in sm" type="text" data-p="${p}.alt" value="${esc(ph.alt || '')}" placeholder="Texte alternatif (description de la photo)">` : ''}
        </div>
      </div>
      <input type="file" accept="image/*" hidden>
    </div>`;
  }
  function card(title, body, o = {}) {
    const sw = o.toggle ? `<label class="pob-sw" title="Afficher / masquer la section"><input type="checkbox" data-act="toggle" data-p="${o.toggle}" ${getP(S, o.toggle) ? 'checked' : ''}><i></i></label>` : '';
    const on = !o.toggle || getP(S, o.toggle);
    const del = o.del && !DE() ? `<button type="button" class="pob-x" data-act="${o.del}" data-i="${o.i}" title="Supprimer ce bloc">✕</button>` : '';
    return `<section class="pob-card${on ? '' : ' off'}">
      <header>${DE() ? '' : sw}<h4>${title}</h4>${o.tag ? `<em>${o.tag}</em>` : ''}${del}</header>
      ${on ? `<div class="pob-card-b">${body}</div>` : ''}
    </section>`;
  }

  function personFields(item, base, o = {}) {
    return [
      fN('Prénom Nom', `${base}.name`, { half: true }),
      fT(o.titleLabel || 'Poste', item, base, 'title', { half: true, ph: 'Titre officiel' }),
      o.role ? fT('Rôle dans le projet', item, base, 'role', { ph: 'Sponsor du projet, Chef de projet, Contributeur…', hint: 'Affiché après le poste : « Poste · Rôle projet ».' }) : '',
      fPhoto('Photo', `${base}.photo`, 'portrait'),
    ].join('');
  }

  // Galerie / encadré / KPIs : mêmes champs en fin d'email et sous un projet.
  // base = chemin de l'objet dans S (ex : "gallery" ou "focus.0.extras.gallery")
  function galleryFields(g, base) {
    if (DE()) return '<p class="pob-muted">Les photos sont communes aux deux versions.</p>';
    return g.photos.map((p, i) => `<div class="pob-gal-item">${fPhoto(`Photo ${i + 1}`, `${base}.photos.${i}`, 'gallery', { alt: true })}${g.photos.length > 1 ? `<button type="button" class="pob-x" data-act="del-photo" data-base="${base}" data-i="${i}" title="Retirer cet emplacement">✕</button>` : ''}</div>`).join('')
      + (g.photos.length < 3 ? `<button type="button" class="pob-add" data-act="add-photo" data-base="${base}">＋ Ajouter une photo (3 max)</button>` : '');
  }
  function encadreFields(E, base) {
    return [
      fT('Titre de l\'encadré', E, base, 'title', { ph: 'Ce que cela représente, concrètement' }),
      DE() ? '' : `<div class="pob-seg mini">
        <button type="button" data-act="cols" data-base="${base}" data-n="1" class="${E.cols.length === 1 ? 'on' : ''}">1 colonne</button>
        <button type="button" data-act="cols" data-base="${base}" data-n="2" class="${E.cols.length === 2 ? 'on' : ''}">2 colonnes</button></div>`,
      `<div class="pob-cols">${E.cols.map((c, i) => `<div>${fT('Sous-titre', c, `${base}.cols.${i}`, 'subtitle', { ph: i ? 'En cours de préparation' : 'Ce qui change' })}${fT('Points (un par ligne)', c, `${base}.cols.${i}`, 'items', { area: true, rows: 5 })}</div>`).join('')}</div>`,
    ].join('');
  }
  function kpiFields(K, base) {
    return `<div class="pob-kpis">${K.items.map((k, i) => `<div class="pob-kpi">
        ${fT('Valeur', k, `${base}.items.${i}`, 'value', { ph: '150.000+' })}
        ${fT('Libellé', k, `${base}.items.${i}`, 'label', { ph: 'véhicules couverts' })}
        ${!DE() && K.items.length > 2 ? `<button type="button" class="pob-x" data-act="del-kpi" data-base="${base}" data-i="${i}" title="Retirer">✕</button>` : ''}
      </div>`).join('')}</div>
      ${!DE() && K.items.length < 4 ? `<button type="button" class="pob-add" data-act="add-kpi" data-base="${base}">＋ Ajouter un chiffre (4 max)</button>` : ''}`;
  }
  // Signataires (signature de fin et signature d'édito). base = chemin du tableau people
  function signerFields(people, base) {
    return people.map((p, i) => `<div class="pob-sub">
        <h5>Signataire ${i + 1}${i && !DE() ? `<button type="button" class="pob-x" data-act="del-signer" data-base="${base}" data-i="${i}" title="Retirer ce signataire">✕</button>` : ''}</h5>
        ${personFields(p, `${base}.${i}`)}
        ${fPhoto('Signature manuscrite (optionnelle)', `${base}.${i}.sig`, 'signature')}
      </div>`).join('')
      + (!DE() && people.length < 2 ? `<button type="button" class="pob-add" data-act="add-signer" data-base="${base}">＋ Ajouter un 2e signataire</button>` : '');
  }

  // Sous-bloc activable à l'intérieur d'une carte
  function subBlock(title, togglePath, body) {
    const on = getP(S, togglePath);
    if (DE() && !on) return '';
    return `<div class="pob-sub">
      ${DE() ? `<h5>${title}</h5>` : `<label class="pob-sw-inline"><input type="checkbox" data-act="toggle" data-p="${togglePath}" ${on ? 'checked' : ''}> ${title}</label>`}
      ${on ? body : ''}
    </div>`;
  }

  function renderForm() {
    const root = document.getElementById('pob-form');
    if (!root) return;
    const scrollY = root.scrollTop;
    let h = '';

    h += `<div class="pob-langbar">
      <label class="pob-sw-inline"><input type="checkbox" data-act="toggle" data-p="de.on" ${S.de.on ? 'checked' : ''}> Version bilingue (ajoute une version allemande en fin d'email)</label>
      ${S.de.on ? `<div class="pob-seg">
        <button type="button" data-act="lang" data-l="fr" class="${lang === 'fr' ? 'on' : ''}">Français</button>
        <button type="button" data-act="lang" data-l="de" class="${lang === 'de' ? 'on' : ''}">Deutsch</button>
      </div>
      <button type="button" class="pob-btn" data-act="translate">Traduire en allemand</button>` : ''}
    </div>`;
    if (DE()) h += `<div class="pob-note">Vous modifiez la <b>version allemande</b>. Les noms, photos et la structure sont communs aux deux langues : modifiez-les depuis l'onglet Français. Un champ DE laissé vide reprend le texte français.</div>`;

    // En-tête
    const m = S.meta;
    h += card('En-tête', [
      fN('N° d\'édition', 'meta.edition', { half: true, ph: '3' }),
      DE() ? '' : fT('Période', m, 'meta', 'periode', { half: true, ph: 'Octobre 2026' }),
      fT('Titre de l\'édition', m, 'meta', 'headline', { ph: 'Projet Foyer — un contrat structurant pour notre développement B2B' }),
      DE() ? '' : fT('Préheader', m, 'meta', 'preheader', { area: true, rows: 2, ph: 'Phrase d\'accroche visible dans la boîte de réception, à côté de l\'objet', hint: 'Non affiché dans l\'email lui-même.' }),
    ].join(''), { tag: 'Obligatoire' });

    // Édito
    const e = S.edito;
    h += card('Édito', [fT('Titre de section', e, 'edito', 'label', { half: true }), personFields(e, 'edito'), fT('Texte', e, 'edito', 'body', { ph: 'Le mot d\'introduction…' }),
      subBlock('Signature en fin d\'édito', 'edito.sign.on', signerFields(e.sign.people, 'edito.sign.people'))].join(''), { toggle: 'edito.on' });

    // Focus projet
    S.focus.forEach((f, i) => {
      const b = `focus.${i}`;
      const contrib = subBlock('Citation d\'un contributeur (encadré jaune)', `${b}.contrib.on`,
        personFields(f.contrib, `${b}.contrib`, { role: true }) + fT('Citation', f.contrib, `${b}.contrib`, 'quote', { area: true, rows: 4, hint: 'Les guillemets sont ajoutés automatiquement.' }));
      const x = f.extras, xb = `${b}.extras`;
      const after = (DE() && !x.gallery.on && !x.encadre.on && !x.kpis.on) ? '' : `<div class="pob-group"><h5>Juste après ce projet</h5>`
        + subBlock('Galerie photos', `${xb}.gallery.on`, galleryFields(x.gallery, `${xb}.gallery`))
        + subBlock('Encadré « Ce qui change »', `${xb}.encadre.on`, encadreFields(x.encadre, `${xb}.encadre`))
        + subBlock('Chiffres clés', `${xb}.kpis.on`, kpiFields(x.kpis, `${xb}.kpis`))
        + '</div>';
      h += card(`Focus projet${S.focus.length > 1 ? ' ' + (i + 1) : ''}`, [
        fT('Titre de section', f, b, 'label', { half: true }),
        personFields(f, b, { role: true }),
        fT('Texte', f, b, 'body', { ph: 'Le témoignage, en plusieurs paragraphes…' }),
        DE() ? '' : `<label class="pob-sw-inline"><input type="checkbox" data-act="toggle" data-p="${b}.twoCols" ${f.twoCols ? 'checked' : ''}> Répartir le texte sur 2 colonnes (desktop)</label>`,
        contrib,
        after,
      ].join(''), { del: 'del-focus', i });
    });
    if (!DE()) h += `<button type="button" class="pob-add" data-act="add-focus">＋ Ajouter un focus projet</button>`;

    // Galerie
    h += `<div class="pob-divider">Fin d'email, après tous les projets</div>`;
    h += card('Galerie photos', galleryFields(S.gallery, 'gallery'), { toggle: 'gallery.on' });

    // Focus métier
    S.metier.forEach((mt, i) => {
      const b = `metier.${i}`;
      h += card(`Focus métier${S.metier.length > 1 ? ' ' + (i + 1) : ''}`, [
        fT('Titre de section', mt, b, 'label', { ph: 'Focus métier — Assistance' }),
        personFields(mt, b, { role: true }),
        fT('Citation', mt, b, 'quote', { area: true, rows: 4, hint: 'Les guillemets sont ajoutés automatiquement.' }),
      ].join(''), { del: 'del-metier', i });
    });
    if (!DE()) h += `<button type="button" class="pob-add" data-act="add-metier">＋ Ajouter un focus métier</button>`;

    // Encadré
    h += card('Encadré « Ce qui change »', encadreFields(S.encadre, 'encadre'), { toggle: 'encadre.on' });

    // KPIs
    h += card('Chiffres clés', kpiFields(S.kpis, 'kpis'), { toggle: 'kpis.on' });

    // Signature
    const s = S.signature;
    h += card('Signature', [
      fT('Mot de fin', s, 'signature', 'body', { ph: 'Merci à toutes les équipes…', hint: 'Texte commun, affiché au-dessus du ou des signataires.' }),
      signerFields(s.people, 'signature.people'),
    ].join(''), { toggle: 'signature.on' });

    h += `<div class="pob-foot-actions">
      <button type="button" class="pob-btn ghost sm" data-act="export-json">Exporter le brouillon (.json)</button>
      <button type="button" class="pob-btn ghost sm" data-act="import-json">Importer un brouillon</button>
      <button type="button" class="pob-btn ghost sm danger" data-act="reset">Tout effacer</button>
      <input type="file" accept=".json,application/json" id="pob-import" hidden>
    </div>`;

    root.innerHTML = h;
    root.scrollTop = scrollY;
  }

  // ── Aperçu + contrôles ──────────────────────────────────────────────────────
  let tmr = null, lastHtml = '';
  function schedule() { clearTimeout(tmr); tmr = setTimeout(refresh, 250); }
  function refresh() {
    lastHtml = buildEmail();
    const fr = document.getElementById('pob-frame');
    if (fr) {
      const y = fr.contentWindow ? fr.contentWindow.scrollY : 0;
      fr.onload = () => { try { fr.contentWindow.scrollTo(0, y); } catch (e) {} };
      fr.srcdoc = lastHtml;
    }
    const w = checks(lastHtml);
    const box = document.getElementById('pob-checks');
    if (box) box.innerHTML = w.length
      ? `<details ${w.length <= 4 ? 'open' : ''}><summary>${w.length} point${w.length > 1 ? 's' : ''} à vérifier avant envoi</summary><ul>${w.map(x => `<li>${esc(x)}</li>`).join('')}</ul></details>`
      : '<div class="pob-ok">Tout est prêt. Faites un envoi test pour vérifier le rendu dans Outlook.</div>';
    const sz = document.getElementById('pob-size');
    if (sz) sz.textContent = `${Math.round(new Blob([lastHtml]).size / 1024)} Ko`;
  }

  async function copyHtml() {
    const html = buildEmail();
    try { await navigator.clipboard.writeText(html); }
    catch (e) {
      const ta = document.createElement('textarea'); ta.value = html; document.body.appendChild(ta);
      ta.select(); document.execCommand('copy'); ta.remove();
    }
    showToast('Code HTML copié dans le presse-papiers');
  }
  function download(name, content, type) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([content], { type }));
    a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }
  const fileBase = () => `PowerON_edition-${String(S.meta.edition || 'X').trim().replace(/[^\w-]+/g, '')}`;

  async function sendTest() {
    const inp = document.getElementById('pob-test-to');
    const to = (inp.value || '').split(/[,;\s]+/).filter(Boolean);
    if (!to.length) { showToast('Indiquez une adresse @acl.lu', 'error'); return; }
    if (pending.size) { showToast('Attendez la fin de l\'envoi des images', 'error'); return; }
    try { localStorage.setItem('poweron_test_to', inp.value); } catch (e) {}
    const btn = document.getElementById('pob-send');
    btn.disabled = true; btn.textContent = 'Envoi…';
    try {
      const ed = String(S.meta.edition || '').trim();
      const subject = `POWER ON!${ed ? ' — Édition n°' + ed : ''}${S.meta.t.fr.headline ? ' — ' + S.meta.t.fr.headline : ''}`;
      const r = await fetch(`${API_BASE}/poweron/send-test`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ html: buildEmail(), subject, to }), signal: AbortSignal.timeout(30000),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.error) throw new Error(d.error || `HTTP ${r.status}`);
      showToast(`Email test envoyé à ${to.join(', ')}`);
    } catch (e) {
      showToast('Envoi test impossible : ' + e.message, 'error');
    }
    btn.disabled = false; btn.textContent = 'Envoyer un test';
  }

  // ── Événements ──────────────────────────────────────────────────────────────
  function onAction(btn) {
    const act = btn.dataset.act, i = +btn.dataset.i, base = btn.dataset.base;
    const restructure = () => { save(); renderForm(); schedule(); };
    switch (act) {
      case 'lang': lang = btn.dataset.l; renderForm(); break;
      case 'translate': translate(); break;
      case 'add-focus': S.focus.push(newFocus()); restructure(); break;
      case 'del-focus': if (confirm('Supprimer ce focus projet ?')) { S.focus.splice(i, 1); restructure(); } break;
      case 'add-metier': S.metier.push(newMetier()); restructure(); break;
      case 'del-metier': if (confirm('Supprimer ce focus métier ?')) { S.metier.splice(i, 1); restructure(); } break;
      case 'add-photo': getP(S, base).photos.push({ url: '', alt: '' }); restructure(); break;
      case 'del-photo': getP(S, base).photos.splice(i, 1); restructure(); break;
      case 'add-kpi': getP(S, base).items.push(newKpi()); restructure(); break;
      case 'del-kpi': getP(S, base).items.splice(i, 1); restructure(); break;
      case 'cols': {
        const E = getP(S, base), n = +btn.dataset.n;
        if (n === 1) E.cols = E.cols.slice(0, 1);
        else while (E.cols.length < 2) E.cols.push(newCol());
        restructure(); break;
      }
      case 'pick': btn.closest('.pob-photo').querySelector('input[type=file]').click(); break;
      case 'photo-del': { const p = btn.closest('.pob-photo').dataset.photo; setP(S, p + '.url', ''); restructure(); break; }
      case 'add-signer': getP(S, base).push(newSigner()); restructure(); break;
      case 'del-signer': getP(S, base).splice(i, 1); restructure(); break;
      case 'export-json': download(`${fileBase()}_brouillon.json`, JSON.stringify(S, null, 2), 'application/json'); break;
      case 'import-json': document.getElementById('pob-import').click(); break;
      case 'reset':
        if (confirm('Effacer tout le contenu de cette newsletter ?\nExportez d\'abord le brouillon si vous voulez le garder.')) { S = blank(); lang = 'fr'; restructure(); }
        break;
      case 'copy': copyHtml(); break;
      case 'download': download(`${fileBase()}.html`, buildEmail(), 'text/html'); break;
      case 'send': sendTest(); break;
      case 'view':
        previewMode = btn.dataset.v;
        document.querySelectorAll('#pob [data-act="view"]').forEach(b => b.classList.toggle('on', b === btn));
        document.getElementById('pob-frame-wrap').classList.toggle('mobile', previewMode === 'mobile');
        break;
    }
  }

  function bind(root) {
    root.addEventListener('click', e => {
      const cmd = e.target.closest('[data-cmd]');
      if (cmd) {
        e.preventDefault();
        const c = cmd.dataset.cmd;
        if (c === 'link') {
          const url = prompt('Adresse du lien (https://…)', 'https://');
          if (url && /^(https?:\/\/|mailto:)\S+$/i.test(url)) document.execCommand('createLink', false, url);
          else if (url) showToast('Le lien doit commencer par https:// ou mailto:', 'error');
        } else document.execCommand(c);
        const ed = cmd.closest('.pob-rich-wrap').querySelector('.pob-rich');
        ed.dispatchEvent(new Event('input', { bubbles: true }));
        return;
      }
      const btn = e.target.closest('[data-act]');
      if (btn && btn.tagName !== 'INPUT') onAction(btn);
    });
    // Garder la sélection du texte quand on clique dans la barre d'outils
    root.addEventListener('mousedown', e => { if (e.target.closest('[data-cmd]')) e.preventDefault(); });

    root.addEventListener('input', e => {
      const el = e.target.closest('[data-p]');
      if (!el || el.dataset.act === 'toggle') return;
      setP(S, el.dataset.p, el.dataset.rich ? el.innerHTML : el.value);
      save(); schedule();
    });
    root.addEventListener('focusout', e => {
      const el = e.target.closest('[data-rich]');
      if (!el) return;
      const n = normRich(el.innerHTML);
      setP(S, el.dataset.p, n); save();
    });
    root.addEventListener('change', e => {
      const el = e.target;
      if (el.dataset.act === 'toggle') {
        setP(S, el.dataset.p, el.checked);
        if (el.dataset.p === 'de.on' && !el.checked) lang = 'fr';
        // Signature d'édito : pré-remplie avec l'auteur de l'édito
        const p0 = S.edito.sign.people[0];
        if (el.dataset.p === 'edito.sign.on' && el.checked && !p0.name.trim()) {
          p0.name = S.edito.name; p0.photo = { ...S.edito.photo };
          p0.t.fr.title = S.edito.t.fr.title; p0.t.de.title = (S.edito.t.de || {}).title || '';
        }
        save(); renderForm(); schedule(); return;
      }
      if (el.type === 'file' && el.id === 'pob-import') {
        const f = el.files[0]; if (!f) return;
        f.text().then(t => {
          const d = JSON.parse(t);
          if (!d || d.v !== 1) throw new Error('format');
          S = migrate(d); lang = 'fr'; save(); renderForm(); schedule();
          showToast('Brouillon importé');
        }).catch(() => showToast('Fichier de brouillon invalide', 'error'));
        el.value = '';
        return;
      }
      if (el.type === 'file') {
        const box = el.closest('.pob-photo');
        uploadPhoto(box.dataset.photo, box.dataset.kind, el.files[0]);
        el.value = '';
      }
      if (el.type === 'url' && el.value && !isUrl(el.value)) showToast('L\'URL de l\'image doit commencer par https://', 'error');
      if (el.type === 'url') renderForm();
    });
    // Collage dans un texte riche : texte brut uniquement (évite le HTML Word)
    root.addEventListener('paste', e => {
      const el = e.target.closest('[data-rich]');
      if (!el) return;
      e.preventDefault();
      const txt = (e.clipboardData || window.clipboardData).getData('text/plain');
      const html = txt.split(/\r?\n/).filter(l => l.trim()).map(l => `<p>${esc(l)}</p>`).join('');
      document.execCommand('insertHTML', false, html);
    });
    // Glisser-déposer d'images
    root.addEventListener('dragover', e => { const b = e.target.closest('.pob-photo'); if (b) { e.preventDefault(); b.classList.add('drop'); } });
    root.addEventListener('dragleave', e => { const b = e.target.closest('.pob-photo'); if (b) b.classList.remove('drop'); });
    root.addEventListener('drop', e => {
      const b = e.target.closest('.pob-photo'); if (!b) return;
      e.preventDefault(); b.classList.remove('drop');
      uploadPhoto(b.dataset.photo, b.dataset.kind, e.dataTransfer.files[0]);
    });
  }

  // ── Mise en page de l'outil ─────────────────────────────────────────────────
  const CSS = `
  #pob{display:grid;grid-template-columns:minmax(380px,1fr) minmax(400px,1.1fr);gap:20px;align-items:start;}
  #pob *{box-sizing:border-box;}
  #pob .pob-intro{grid-column:1/-1;display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;align-items:flex-end;}
  #pob .pob-intro h2{font-family:var(--font-display);font-size:22px;margin:0 0 4px;color:var(--text);}
  #pob .pob-intro p{color:var(--muted);font-size:13px;margin:0;max-width:640px;line-height:1.5;}
  #pob-form{display:flex;flex-direction:column;gap:12px;max-height:calc(100vh - 150px);overflow:auto;padding-right:6px;}
  #pob .pob-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--r-lg);box-shadow:var(--shadow-1);}
  #pob .pob-card>header{display:flex;align-items:center;gap:10px;padding:12px 16px;}
  #pob .pob-card.off>header{opacity:.6;}
  #pob .pob-card h4{font-size:14px;font-weight:700;margin:0;color:var(--text);}
  #pob .pob-card>header em{font-style:normal;font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);background:var(--bg);padding:2px 7px;border-radius:99px;}
  #pob .pob-card-b{padding:4px 16px 16px;display:flex;flex-wrap:wrap;gap:12px;border-top:1px solid var(--border);padding-top:14px;}
  #pob .pob-f{display:flex;flex-direction:column;gap:5px;width:100%;}
  #pob .pob-f.half{width:calc(50% - 6px);}
  #pob .pob-f>span{font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);}
  #pob .pob-f small,#pob .pob-muted{font-size:11.5px;color:var(--muted);line-height:1.4;}
  #pob .pob-fr{font-size:11.5px;color:var(--muted);background:var(--bg);border-radius:var(--r-sm);padding:6px 8px;line-height:1.45;max-height:80px;overflow:auto;}
  #pob .pob-in,#pob .pob-rich{width:100%;font:inherit;font-size:13.5px;color:var(--text);background:var(--surface);border:1px solid var(--border-strong);border-radius:var(--r-sm);padding:8px 10px;}
  #pob .pob-in.sm{font-size:12.5px;padding:6px 8px;}
  #pob textarea.pob-in{resize:vertical;line-height:1.5;}
  #pob .pob-in:focus,#pob .pob-rich:focus{outline:2px solid var(--y);outline-offset:-1px;border-color:transparent;}
  #pob .pob-rich-wrap{border:1px solid var(--border-strong);border-radius:var(--r-sm);overflow:hidden;}
  #pob .pob-rich-wrap .pob-rich{border:0;border-radius:0;min-height:120px;line-height:1.6;}
  #pob .pob-rich p{margin:0 0 10px;}
  #pob .pob-rich:empty::before{content:attr(data-ph);color:var(--muted-2);}
  #pob .pob-rich a{text-decoration:underline;}
  #pob .pob-rich-tb{display:flex;gap:2px;padding:4px;background:var(--bg);border-bottom:1px solid var(--border);}
  #pob .pob-rich-tb button{font:inherit;font-size:12px;border:0;background:transparent;color:var(--text);padding:4px 9px;border-radius:4px;cursor:pointer;}
  #pob .pob-rich-tb button:hover{background:var(--surface);}
  #pob .pob-sub{width:100%;border:1px dashed var(--border-strong);border-radius:var(--r-md);padding:12px;display:flex;flex-wrap:wrap;gap:12px;}
  #pob .pob-sub h5{margin:0;font-size:12px;width:100%;display:flex;align-items:center;}
  #pob .pob-group{width:100%;display:flex;flex-direction:column;gap:10px;margin-top:4px;padding-top:12px;border-top:1px solid var(--border);}
  #pob .pob-group>h5{margin:0;font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);}
  #pob .pob-divider{font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);display:flex;align-items:center;gap:10px;margin-top:8px;}
  #pob .pob-divider::before,#pob .pob-divider::after{content:"";flex:1;height:1px;background:var(--border-strong);}
  #pob .pob-photo.sig .pob-thumb{width:120px;height:48px;background:#fff;}
  #pob .pob-photo.sig .pob-thumb img{object-fit:contain;}
  #pob .pob-sw{position:relative;display:inline-block;width:34px;height:20px;flex-shrink:0;cursor:pointer;}
  #pob .pob-sw input{opacity:0;width:0;height:0;position:absolute;}
  #pob .pob-sw i{position:absolute;inset:0;background:var(--border-strong);border-radius:99px;transition:.15s;}
  #pob .pob-sw i::after{content:"";position:absolute;width:14px;height:14px;left:3px;top:3px;background:#fff;border-radius:50%;transition:.15s;}
  #pob .pob-sw input:checked+i{background:#231f20;}
  #pob .pob-sw input:checked+i::after{transform:translateX(14px);background:var(--y);}
  #pob .pob-sw input:focus-visible+i{outline:2px solid var(--y);outline-offset:2px;}
  #pob .pob-sw-inline{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--text);cursor:pointer;width:100%;}
  #pob .pob-sw-inline input{accent-color:#231f20;width:15px;height:15px;}
  #pob .pob-x{margin-left:auto;border:0;background:transparent;color:var(--muted);font-size:13px;cursor:pointer;padding:4px 6px;border-radius:4px;}
  #pob .pob-x:hover{background:var(--bg);color:var(--bad);}
  #pob .pob-add{border:1px dashed var(--border-strong);background:transparent;color:var(--text);font:inherit;font-size:13px;font-weight:600;padding:10px;border-radius:var(--r-md);cursor:pointer;width:100%;}
  #pob .pob-add:hover{border-color:#231f20;background:var(--surface);}
  #pob .pob-btn{font:inherit;font-size:13px;font-weight:700;border:1px solid #231f20;background:#231f20;color:#fff;padding:9px 14px;border-radius:var(--r-sm);cursor:pointer;white-space:nowrap;}
  #pob .pob-btn.primary{background:var(--y);border-color:var(--y);color:#231f20;}
  #pob .pob-btn.ghost{background:transparent;color:var(--text);border-color:var(--border-strong);}
  #pob .pob-btn.danger{color:var(--bad);}
  #pob .pob-btn.sm{font-size:12px;padding:6px 10px;}
  #pob .pob-btn:disabled{opacity:.5;cursor:wait;}
  #pob .pob-btns{display:flex;gap:6px;flex-wrap:wrap;}
  #pob .pob-photo-row{display:flex;gap:12px;align-items:flex-start;}
  #pob .pob-photo-side{display:flex;flex-direction:column;gap:6px;flex:1;min-width:0;}
  #pob .pob-thumb{width:96px;height:72px;flex-shrink:0;border:1px dashed var(--border-strong);border-radius:8px;background:var(--bg);display:flex;align-items:center;justify-content:center;overflow:hidden;cursor:pointer;color:var(--muted);font-size:20px;padding:0;}
  #pob .pob-photo.round .pob-thumb{width:64px;height:64px;border-radius:50%;}
  #pob .pob-thumb img{width:100%;height:100%;object-fit:cover;}
  #pob .pob-photo.drop .pob-thumb{border-color:#231f20;background:var(--y-soft);}
  #pob .pob-spin{width:20px;height:20px;border:2px solid var(--border-strong);border-top-color:#231f20;border-radius:50%;animation:pobspin .8s linear infinite;}
  @keyframes pobspin{to{transform:rotate(360deg);}}
  #pob .pob-gal-item{display:flex;gap:8px;width:100%;align-items:flex-start;}
  #pob .pob-cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;width:100%;}
  #pob .pob-cols>div{display:flex;flex-direction:column;gap:10px;}
  #pob .pob-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;width:100%;}
  #pob .pob-kpi{position:relative;display:flex;flex-direction:column;gap:8px;background:var(--bg);border-radius:var(--r-md);padding:10px;}
  #pob .pob-kpi .pob-x{position:absolute;top:4px;right:4px;}
  #pob .pob-seg{display:inline-flex;border:1px solid var(--border-strong);border-radius:var(--r-sm);overflow:hidden;}
  #pob .pob-seg button{font:inherit;font-size:12.5px;font-weight:600;border:0;background:var(--surface);color:var(--text);padding:7px 12px;cursor:pointer;}
  #pob .pob-seg button.on{background:#231f20;color:#fff;}
  #pob .pob-langbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;background:var(--surface);border:1px solid var(--border);border-radius:var(--r-lg);padding:12px 16px;}
  #pob .pob-langbar .pob-sw-inline{width:auto;flex:1 1 260px;}
  #pob .pob-note{font-size:12.5px;line-height:1.5;background:var(--y-soft);color:var(--text);border-radius:var(--r-md);padding:10px 14px;}
  #pob .pob-foot-actions{display:flex;gap:8px;flex-wrap:wrap;padding:4px 0 20px;}
  #pob-right{position:sticky;top:16px;display:flex;flex-direction:column;gap:12px;}
  #pob .pob-out{background:var(--surface);border:1px solid var(--border);border-radius:var(--r-lg);padding:14px 16px;display:flex;flex-direction:column;gap:12px;}
  #pob .pob-out-row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;}
  #pob .pob-out-row .pob-in{flex:1;min-width:180px;}
  #pob-checks details{font-size:12.5px;color:var(--text);}
  #pob-checks summary{cursor:pointer;font-weight:700;color:var(--warn);}
  #pob-checks ul{margin:8px 0 0 18px;line-height:1.55;color:var(--muted);}
  #pob .pob-ok{font-size:12.5px;font-weight:600;color:var(--good);}
  #pob .pob-prev-bar{display:flex;align-items:center;gap:10px;justify-content:space-between;}
  #pob .pob-prev-bar small{color:var(--muted);font-size:11.5px;}
  #pob-frame-wrap{background:#ececed;border:1px solid var(--border);border-radius:var(--r-lg);overflow:hidden;height:calc(100vh - 330px);min-height:420px;display:flex;justify-content:center;}
  #pob-frame{width:100%;height:100%;border:0;background:#ececed;transition:width .2s;}
  #pob-frame-wrap.mobile #pob-frame{width:375px;border-left:1px solid #d6d6d6;border-right:1px solid #d6d6d6;}
  @media (max-width:1100px){#pob{grid-template-columns:1fr;}#pob-form{max-height:none;overflow:visible;}#pob-right{position:static;}}
  @media (max-width:560px){#pob .pob-f.half{width:100%;}}
  `;

  function build() {
    const panel = document.getElementById('panel-poweron');
    if (!panel || built) return;
    built = true;
    const st = document.createElement('style'); st.textContent = CSS; document.head.appendChild(st);
    let testTo = '';
    try { testTo = localStorage.getItem('poweron_test_to') || JSON.parse(localStorage.getItem('acl_dash_session') || '{}').email || ''; } catch (e) {}
    panel.innerHTML = `<div id="pob">
      <div class="pob-intro">
        <div>
          <h2>Newsletter POWER ON!</h2>
          <p>Remplissez les sections, activez seulement celles dont vous avez besoin, puis copiez le code HTML. Le brouillon est enregistré automatiquement dans ce navigateur.</p>
        </div>
      </div>
      <div id="pob-form"></div>
      <div id="pob-right">
        <div class="pob-out">
          <div class="pob-out-row">
            <button type="button" class="pob-btn primary" data-act="copy">Copier le code HTML</button>
            <button type="button" class="pob-btn ghost" data-act="download">Télécharger le .html</button>
          </div>
          <div class="pob-out-row">
            <input class="pob-in sm" id="pob-test-to" type="email" value="${esc(testTo)}" placeholder="prenom.nom@acl.lu" aria-label="Adresse pour l'envoi test">
            <button type="button" class="pob-btn ghost sm" id="pob-send" data-act="send">Envoyer un test</button>
          </div>
          <div id="pob-checks"></div>
        </div>
        <div class="pob-prev-bar">
          <div class="pob-seg">
            <button type="button" data-act="view" data-v="desktop" class="on">Ordinateur</button>
            <button type="button" data-act="view" data-v="mobile">Mobile</button>
          </div>
          <small>Aperçu navigateur · <span id="pob-size"></span> · le rendu Outlook peut différer légèrement (coins carrés)</small>
        </div>
        <div id="pob-frame-wrap"><iframe id="pob-frame" title="Aperçu de la newsletter" sandbox="allow-same-origin allow-popups"></iframe></div>
      </div>
    </div>`;
    bind(panel);
    renderForm();
    refresh();
  }

  window.renderPowerOn = build;
  document.querySelectorAll('.tab-btn[data-tab="poweron"]').forEach(b => b.addEventListener('click', build));
  if (document.getElementById('panel-poweron')?.classList.contains('active')) build();
})();
