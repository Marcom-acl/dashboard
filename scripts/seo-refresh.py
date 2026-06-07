#!/usr/bin/env python3
"""
SEO Positions Tracker — ACL Luxembourg
Soumet 83 requêtes SERP à l'API DataForSEO (google.lu, mobile),
calcule les positions et parts de voix par segment et par marché,
et écrit data/seo-positions-data.json.

Requiert : DATAFORSEO_AUTH (chaîne Base64 login:password) dans l'environnement.
"""

import json
import os
import time
import datetime
import requests
from pathlib import Path

# ── Chemins ───────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).parent.parent
DATA_FILE = REPO_ROOT / "data" / "seo-positions-data.json"
TODAY     = datetime.date.today().isoformat()
NOW       = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

# ── DataForSEO ────────────────────────────────────────────────────────────────
API_BASE = "https://api.dataforseo.com/v3/serp/google/organic"
HEADERS  = {
    "Authorization": f"Basic {os.environ.get('DATAFORSEO_AUTH', '')}",
    "Content-Type": "application/json",
}

LANG_MAP = {"fr": "French", "en": "English", "de": "German"}
ACL_DOMAIN = "acl.lu"

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
    """Normalise un domaine : retire www., met en minuscules."""
    d = raw.lower().strip()
    if d.startswith("www."):
        d = d[4:]
    return d


def _matches(result_domain, tracked):
    """Vérifie si result_domain correspond à tracked (sous-domaines inclus)."""
    rd = _clean_domain(result_domain)
    td = _clean_domain(tracked)
    return rd == td or rd.endswith("." + td)


def build_tasks():
    """Construit la liste de toutes les tâches SERP (keyword × marché)."""
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
                    # Métadonnées pour post-traitement
                    "_segment": segment,
                    "_lang":    lang,
                })
    return tasks


def post_tasks(tasks):
    """Soumet les tâches en batch (max 100 par requête). Retourne {tag → task_id}."""
    tag_to_id = {}
    batch_size = 100
    for i in range(0, len(tasks), batch_size):
        batch = tasks[i:i + batch_size]
        payload = []
        for t in batch:
            payload.append({
                "keyword":       t["keyword"],
                "language_name": t["language_name"],
                "location_name": t["location_name"],
                "se_domain":     t["se_domain"],
                "device":        t["device"],
                "depth":         t["depth"],
                "tag":           f"{t['_segment']}|{t['_lang']}|{t['keyword']}",
            })

        r = requests.post(f"{API_BASE}/task_post", headers=HEADERS, json=payload, timeout=60)
        r.raise_for_status()
        resp = r.json()

        for item in resp.get("tasks", []):
            tag = item.get("data", {}).get("tag", "")
            tid = item.get("id")
            if tag and tid:
                tag_to_id[tag] = tid

    print(f"[INFO] {len(tag_to_id)} tâches soumises.")
    return tag_to_id


def wait_and_collect(tag_to_id, timeout=300):
    """Attend que les tâches soient prêtes et récupère les résultats. Retourne {tag → items}."""
    pending = set(tag_to_id.values())
    id_to_tag = {v: k for k, v in tag_to_id.items()}
    results = {}
    deadline = time.time() + timeout
    sleep_secs = 30

    print(f"[INFO] Attente des résultats (timeout {timeout}s)…")
    time.sleep(sleep_secs)

    while pending and time.time() < deadline:
        r = requests.get(f"{API_BASE}/tasks_ready", headers=HEADERS, timeout=30)
        r.raise_for_status()
        ready_ids = [t["id"] for t in r.json().get("tasks", [])]

        for tid in ready_ids:
            if tid not in pending:
                continue
            try:
                gr = requests.get(f"{API_BASE}/task_get/{tid}", headers=HEADERS, timeout=30)
                gr.raise_for_status()
                task_data = gr.json().get("tasks", [{}])[0]
                items = (task_data.get("result") or [{}])[0].get("items") or []
                tag = id_to_tag.get(tid, "")
                if tag:
                    results[tag] = items
                pending.discard(tid)
            except Exception as e:
                print(f"[WARN] Erreur récupération tâche {tid}: {e}")

        if pending:
            remaining = int(deadline - time.time())
            print(f"[INFO] {len(pending)} tâche(s) en attente. Retry dans {sleep_secs}s (reste {remaining}s)…")
            time.sleep(sleep_secs)
            sleep_secs = min(sleep_secs + 15, 60)

    if pending:
        print(f"[WARN] {len(pending)} tâche(s) non résolues après timeout.")

    return results


def parse_positions(results):
    """
    Parse les résultats SERP.
    Retourne : {(segment, lang, keyword) → {domain → position}}
    """
    positions = {}
    for tag, items in results.items():
        parts = tag.split("|", 2)
        if len(parts) != 3:
            continue
        segment, lang, kw = parts
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
    """Charge le JSON précédent pour calculer les deltas."""
    if not DATA_FILE.exists():
        return None
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def build_output(positions, previous):
    """Construit le JSON final."""

    # ── Positions ACL précédentes pour les deltas ──
    prev_acl = {}  # (lang, kw) → pos
    if previous:
        for lang in ("fr", "en", "de"):
            mkt = previous.get("markets", {}).get(lang, {})
            for kw_data in mkt.get("acl_keywords", []):
                prev_acl[(lang, kw_data["kw"])] = kw_data["pos"]

    # ── Calcul par marché ──
    markets_out = {}
    for lang in ("fr", "en", "de"):
        # Tous les mots-clés de ce marché et leurs positions ACL
        acl_kws = []
        kw_gains = []
        kw_losses = []
        top3 = 0
        page1 = 0
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
                    page1 += 1  # top 10 = page 1
                    prev_pos = prev_acl.get((lang, kw))
                    delta = (prev_pos - acl_pos) if prev_pos else 0
                    kw_entry = {"kw": kw, "pos": acl_pos, "delta": delta, "segment": segment}
                    acl_kws.append(kw_entry)
                    if delta > 0:
                        kw_gains.append(kw_entry)
                    elif delta < 0:
                        kw_losses.append(kw_entry)

        # Visibilité = % de mots-clés où ACL est en top 10
        visibility = round(page1 / total_kws * 100, 2) if total_kws else 0.0

        # Deltas vs précédent
        prev_mkt = (previous or {}).get("markets", {}).get(lang, {})
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

    # ── Calcul des parts de voix par segment et par marché ──
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
                    if dom in dom_pos:  # top 10 par construction (depth=10)
                        domain_counts[dom] += 1

            total = len(kws)
            competitors_list = []
            for dom in TRACKED_DOMAINS:
                cnt = domain_counts[dom]
                if cnt > 0 or dom == ACL_DOMAIN:
                    competitors_list.append({
                        "domain":     dom,
                        "voice_pct":  round(cnt / total * 100, 1),
                        "top10_count": cnt,
                        "is_acl":     dom == ACL_DOMAIN,
                    })

            competitors_list.sort(key=lambda x: -x["voice_pct"])
            seg_out[segment] = {
                "label":        SEGMENT_LABELS.get(segment, segment),
                "total_keywords": total,
                "competitors":  competitors_list,
            }

        competitors_out[lang] = {"segments": seg_out}

    # ── Assemblage final ──
    week_num = datetime.date.today().isocalendar()[1]
    return {
        "generated_at":   NOW,
        "period_label":   f"Semaine {week_num} — {TODAY}",
        "markets":        markets_out,
        "competitors":    competitors_out,
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
    print(f"[INFO] {len(tasks)} tâches à soumettre (83 mots-clés × marchés).")

    tag_to_id = post_tasks(tasks)
    results   = wait_and_collect(tag_to_id)
    positions = parse_positions(results)

    print(f"[INFO] {len(positions)} résultats SERP parsés.")

    data = build_output(positions, previous)
    save(data)

    # Résumé rapide
    for lang in ("fr", "en", "de"):
        m = data["markets"].get(lang, {})
        print(f"  [{lang.upper()}] Visibilité ACL : {m.get('visibility_pct', 0)}% | "
              f"Top 3 : {m.get('top3', {}).get('count', 0)} | "
              f"Page 1 : {m.get('page1', {}).get('count', 0)}")


if __name__ == "__main__":
    main()
