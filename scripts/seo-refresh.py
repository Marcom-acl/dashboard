#!/usr/bin/env python3
"""
SEO Positions Tracker — ACL Luxembourg
Soumet 83 requêtes SERP à l'API DataForSEO (google.lu, mobile, Live Advanced),
calcule les positions et parts de voix par segment et par marché,
et écrit data/seo-positions-data.json.

Requiert : DATAFORSEO_AUTH (chaîne Base64 login:password) dans l'environnement.
"""

import json
import os
import time
import datetime
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ── Chemins ───────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parent.parent
DATA_FILE = REPO_ROOT / "data" / "seo-positions-data.json"
TODAY     = datetime.date.today().isoformat()
NOW       = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

# ── DataForSEO ────────────────────────────────────────────────────────────────
API_URL = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"
HEADERS = {
    "Authorization": f"Basic {os.environ.get('DATAFORSEO_AUTH', '')}",
    "Content-Type": "application/json",
}

LANG_MAP   = {"fr": "French", "en": "English", "de": "German"}
ACL_DOMAIN = "acl.lu"
MAX_WORKERS = 5  # requêtes Live parallèles

# ── Domaines suivis (30) ──────────────────────────────────────────────────────
TRACKED_DOMAINS = [
    "acl.lu",
    # Tier 1 — concurrents directs
    "spritpreise.lu", "carbu.com", "assistance.lu", "petrol.lu",
    "rentalcars.com", "kayak.com", "bikestation.lu", "rentabike-mellerdall.lu",
    "europcar.lu", "europ-assistance.lu", "visitluxembourg.com",
    "lalux.lu", "foyer.lu", "axa.lu",
    # Tier 2 — présence confirmée dans les SERPs
    "globalpetrolprices.com", "fuel-prices.eu", "rtl.lu", "lesfrontaliers.lu",
    "cita.lu", "drive-rent.lu", "sixt.com", "vdl.lu", "baloise.lu",
    "dekra.lu", "lokki.rent",
    # Tier 3 — à valider
    "prix-carburant.eu", "charlie24.com", "depalux.lu", "flex.lu", "dkv.lu",
]

# ── Mots-clés par segment et marché ──────────────────────────────────────────
KEYWORDS = {
    "mobilite_carburant": {
        "fr": [
            "prix carburant luxembourg",
            "prix essence luxembourg",
            "prix diesel luxembourg",
            "prix gasoil luxembourg",
            "carburant luxembourg",
            "essence luxembourg",
            "prix du gasoil au luxembourg",
            "carburant",
        ],
        "en": [
            "diesel price luxembourg",
            "luxembourg diesel price",
            "fuel price luxembourg",
            "petrol price luxembourg",
            "fuel prices luxembourg",
            "gas prices luxembourg",
            "luxembourg fuel prices",
            "diesel luxembourg",
        ],
        "de": [
            "dieselpreis luxemburg",
            "spritpreise luxemburg",
            "benzinpreise luxemburg",
            "spritpreise luxemburg heute",
            "luxemburg spritpreise",
            "dieselpreis luxemburg heute",
            "benzinpreis luxemburg",
            "diesel preis luxemburg",
            "spritpreise europa",
        ],
    },
    "mobilite_trafic": {
        "fr": ["trafic luxembourg", "info trafic luxembourg", "trafic autoroute luxembourg"],
        "en": ["traffic luxembourg", "luxembourg traffic"],
        "de": ["stau luxemburg", "verkehr luxemburg"],
    },
    "assistance": {
        "fr": [
            "assistance routière luxembourg",
            "dépannage voiture luxembourg",
            "assistance auto luxembourg",
            "dépannage luxembourg",
        ],
        "en": ["roadside assistance luxembourg", "breakdown assistance luxembourg"],
        "de": [
            "pannenhilfe luxemburg",
            "luxemburg pannenhilfe",
            "abschleppdienst luxemburg",
            "pannendienst luxemburg",
        ],
    },
    "location_voiture": {
        "fr": ["location voiture luxembourg", "louer voiture luxembourg", "location voiture luxembourg pas cher"],
        "en": ["car rental luxembourg", "cheap car rental luxembourg", "rent a car luxembourg"],
        "de": ["autovermietung luxemburg", "luxemburg autovermietung", "mietwagen luxemburg"],
    },
    "location_velo": {
        "fr": [
            "location vélo luxembourg",
            "location vélo cargo luxembourg",
            "location vélo électrique luxembourg",
            "location vélo cargo",
        ],
        "en": ["bicycle rental luxembourg", "bike rental luxembourg", "electric bike rental luxembourg"],
        "de": ["fahrrad mieten luxemburg", "e-bike mieten luxemburg"],
    },
    "voyage": {
        "fr": ["assurance voyage luxembourg", "assurance annulation luxembourg", "contrat de vente voiture"],
        "en": ["travel insurance luxembourg", "holiday insurance luxembourg"],
        "de": ["reiseversicherung luxemburg", "auslandsreisekrankenversicherung luxemburg"],
    },
    "controle_technique": {
        "fr": ["contrôle technique voiture luxembourg", "sport automobile luxembourg"],
        "en": ["car diagnostics luxembourg", "car inspection luxembourg"],
        "de": ["auto kontrolle luxemburg", "hauptuntersuchung luxemburg"],
    },
    "peages": {
        "fr": ["vignette autoroute suisse", "péage france"],
        "en": ["france toll", "france highway toll", "spain toll", "swiss road tax"],
        "de": ["vignette luxemburg", "maut luxemburg"],
    },
    "parking": {
        "fr": ["parking kirchberg luxembourg"],
        "en": ["parking luxembourg kirchberg"],
    },
}

SEGMENT_LABELS = {
    "mobilite_carburant": "Mobilité — Carburant",
    "mobilite_trafic":    "Mobilité — Trafic",
    "assistance":         "Assistance routière",
    "location_voiture":   "Location voiture",
    "location_velo":      "Location vélo",
    "voyage":             "Voyage & Assurance",
    "controle_technique": "Contrôle technique",
    "peages":             "Péages & Vignettes",
    "parking":            "Parking",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clean_domain(raw):
    d = raw.lower().strip()
    if d.startswith("www."):
        d = d[4:]
    return d


def _matches(result_domain, tracked):
    rd = _clean_domain(result_domain)
    td = _clean_domain(tracked)
    return rd == td or rd.endswith("." + td)


def build_tasks():
    tasks = []
    for segment, markets in KEYWORDS.items():
        for lang, kws in markets.items():
            for kw in kws:
                tasks.append({
                    "keyword":       kw,
                    "language_name": LANG_MAP[lang],
                    "location_name": "Luxembourg",
                    "se_domain":     "google.lu",
                    "device":        "mobile",
                    "depth":         10,
                    "_segment":      segment,
                    "_lang":         lang,
                })
    return tasks


def fetch_serp(task, retries=2):
    """Appelle l'endpoint Live pour une tâche. Retourne (task, items) ou (task, []) en cas d'erreur."""
    payload = [{
        "keyword":       task["keyword"],
        "language_name": task["language_name"],
        "location_name": task["location_name"],
        "se_domain":     task["se_domain"],
        "device":        task["device"],
        "depth":         task["depth"],
    }]
    for attempt in range(retries + 1):
        try:
            r = requests.post(API_URL, headers=HEADERS, json=payload, timeout=60)
            r.raise_for_status()
            data = r.json()
            tasks_resp = data.get("tasks", [])
            if not tasks_resp:
                return task, []
            t = tasks_resp[0]
            if t.get("status_code") != 20000:
                print(f"  [WARN] {task['keyword']}: status {t.get('status_code')} — {t.get('status_message')}")
                return task, []
            result = t.get("result") or []
            if not result:
                return task, []
            items = result[0].get("items") or []
            return task, items
        except Exception as e:
            if attempt < retries:
                time.sleep(5 * (attempt + 1))
            else:
                print(f"  [WARN] Erreur pour '{task['keyword']}': {e}")
                return task, []


def fetch_all(tasks):
    """Soumet toutes les tâches en parallèle (MAX_WORKERS workers simultanés)."""
    results = {}
    done = 0
    total = len(tasks)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch_serp, t): t for t in tasks}
        for fut in as_completed(futures):
            task, items = fut.result()
            key = (task["_segment"], task["_lang"], task["keyword"])
            results[key] = items
            done += 1
            if done % 10 == 0 or done == total:
                print(f"  [{done}/{total}] requêtes terminées")
    return results


def parse_positions(results):
    """
    Parse les résultats SERP.
    Retourne : {(segment, lang, keyword) → {domain → position}}
    """
    positions = {}
    for (segment, lang, kw), items in results.items():
        domain_pos = {}
        for item in items:
            if item.get("type") != "organic":
                continue
            raw_domain = item.get("domain", "")
            pos = item.get("rank_absolute", 99)
            for tracked in TRACKED_DOMAINS:
                if _matches(raw_domain, tracked) and tracked not in domain_pos:
                    domain_pos[tracked] = pos
        positions[(segment, lang, kw)] = domain_pos
    return positions


def load_previous():
    if not DATA_FILE.exists():
        return None
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def build_output(positions, previous):
    # ── Positions ACL précédentes pour les deltas ──
    prev_acl = {}
    if previous:
        for lang in ("fr", "en", "de"):
            mkt = previous.get("markets", {}).get(lang, {})
            for kw_data in mkt.get("acl_keywords", []):
                prev_acl[(lang, kw_data["kw"])] = kw_data["pos"]

    # ── Calcul par marché ──
    markets_out = {}
    for lang in ("fr", "en", "de"):
        acl_kws  = []
        kw_gains = []
        kw_losses = []
        top3     = 0
        page1    = 0
        total_kws = 0

        for segment, markets in KEYWORDS.items():
            if lang not in markets:
                continue
            for kw in markets[lang]:
                total_kws += 1
                dom_pos = positions.get((segment, lang, kw), {})
                acl_pos = dom_pos.get(ACL_DOMAIN)

                if acl_pos:
                    if acl_pos <= 3:
                        top3 += 1
                    page1 += 1
                    prev_pos = prev_acl.get((lang, kw))
                    delta = (prev_pos - acl_pos) if prev_pos else 0
                    kw_entry = {"kw": kw, "pos": acl_pos, "delta": delta, "segment": segment}
                    acl_kws.append(kw_entry)
                    if delta > 0:
                        kw_gains.append(kw_entry)
                    elif delta < 0:
                        kw_losses.append(kw_entry)

        visibility = round(page1 / total_kws * 100, 2) if total_kws else 0.0
        prev_mkt   = (previous or {}).get("markets", {}).get(lang, {})
        vis_delta  = round(visibility - prev_mkt.get("visibility_pct", visibility), 2)
        top3_delta = top3  - prev_mkt.get("top3",  {}).get("count", top3)
        p1_delta   = page1 - prev_mkt.get("page1", {}).get("count", page1)

        markets_out[lang] = {
            "visibility_pct":   visibility,
            "visibility_delta": vis_delta,
            "top3":  {"count": top3,  "delta": top3_delta},
            "page1": {"count": page1, "delta": p1_delta},
            "keyword_gains":  sorted(kw_gains,  key=lambda x: -x["delta"])[:10],
            "keyword_losses": sorted(kw_losses, key=lambda x:  x["delta"])[:10],
            "acl_keywords":   sorted(acl_kws,  key=lambda x:  x["pos"]),
        }

    # ── Parts de voix par segment et par marché ──
    competitors_out = {}
    for lang in ("fr", "en", "de"):
        seg_out = {}
        for segment, markets in KEYWORDS.items():
            if lang not in markets:
                continue
            kws = markets[lang]
            if not kws:
                continue

            domain_counts = {d: 0 for d in TRACKED_DOMAINS}
            for kw in kws:
                dom_pos = positions.get((segment, lang, kw), {})
                for dom in TRACKED_DOMAINS:
                    if dom in dom_pos:
                        domain_counts[dom] += 1

            total = len(kws)
            competitors_list = []
            for dom in TRACKED_DOMAINS:
                cnt = domain_counts[dom]
                if cnt > 0 or dom == ACL_DOMAIN:
                    competitors_list.append({
                        "domain":      dom,
                        "voice_pct":   round(cnt / total * 100, 1),
                        "top10_count": cnt,
                        "is_acl":      dom == ACL_DOMAIN,
                    })

            competitors_list.sort(key=lambda x: -x["voice_pct"])
            seg_out[segment] = {
                "label":          SEGMENT_LABELS.get(segment, segment),
                "total_keywords": total,
                "competitors":    competitors_list,
            }

        competitors_out[lang] = {"segments": seg_out}

    week_num = datetime.date.today().isocalendar()[1]
    return {
        "generated_at": NOW,
        "period_label": f"Semaine {week_num} — {TODAY}",
        "markets":      markets_out,
        "competitors":  competitors_out,
    }


def save(data):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[OK] {DATA_FILE} écrit ({DATA_FILE.stat().st_size // 1024} Ko).")


# ── Entrypoint ────────────────────────────────────────────────────────────────
def main():
    if not os.environ.get("DATAFORSEO_AUTH"):
        raise ValueError("DATAFORSEO_AUTH manquant dans l'environnement.")

    print(f"[START] SEO Refresh — {TODAY}")
    previous = load_previous()

    tasks = build_tasks()
    print(f"[INFO] {len(tasks)} requêtes SERP à effectuer (Live Advanced, {MAX_WORKERS} workers).")

    results   = fetch_all(tasks)
    positions = parse_positions(results)
    print(f"[INFO] {len(positions)} résultats SERP parsés.")

    # Résumé des positions ACL trouvées
    acl_found = sum(1 for dom_pos in positions.values() if ACL_DOMAIN in dom_pos)
    print(f"[INFO] ACL trouvé dans {acl_found}/{len(positions)} SERPs.")

    data = build_output(positions, previous)
    save(data)

    for lang in ("fr", "en", "de"):
        m = data["markets"].get(lang, {})
        print(f"  [{lang.upper()}] Visibilité ACL : {m.get('visibility_pct', 0)}% | "
              f"Top 3 : {m.get('top3', {}).get('count', 0)} | "
              f"Page 1 : {m.get('page1', {}).get('count', 0)}")


if __name__ == "__main__":
    main()
