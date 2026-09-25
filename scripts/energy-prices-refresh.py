"""
ACL Marcom Dashboard — Coût de l'énergie par motorisation (mensuel)
===================================================================
Source : "Comparaison des prix de carburants par motorisation"
(data.public.lu, slug comparaison-des-prix-de-carburants-par-motorisation).
Prix moyens mensuels TVAC, chacun dans l'unité de vente propre au vecteur
énergétique (aucune conversion n'est faite ici — les unités sont conservées
telles quelles dans le JSON de sortie) :
- Essence_E10, Diesel_B7, LPG : €/litre (STATEC)
- Hydrogene                   : €/kg (H2.live)
- Electricie_Fournisseur      : €/kWh, prix estimatif client résidentiel
                                (Ministère de l'Économie, conso type STATEC)
- Recharge_AC, Recharge_DC    : €/kWh, bornes accessibles au public (Chargeprice)

Règles de fraîcheur :
- Aucune URL de fichier codée en dur : la ressource CSV la plus récente est
  résolue à chaque run via l'API (check_freshness.latest_resource, tri par
  last_modified).
- Les métadonnées de fraîcheur (fréquence déclarée, date du dernier dépôt)
  sont écrites dans le JSON pour l'affichage « Données au … » du dashboard.

Historique : le producteur écrase un fichier unique à chaque dépôt (8 mois
seulement, jan.-août 2026, au premier run). Pour ne pas perdre d'historique
si le fichier devient glissant, les mois déjà persistés et absents du
nouveau CSV sont conservés ; un mois présent dans le CSV prend toujours la
valeur de la source (le producteur fait autorité) et toute révision d'une
valeur déjà publiée est journalisée.

Échec propre : si le schéma du CSV change (colonnes, format de mois, valeur
non numérique ou hors plage plausible, doublon), le script s'arrête avec un
code non nul et un message explicite SANS toucher au JSON existant — le
dashboard continue d'afficher la dernière version valide (et l'alerte de
fraîcheur prendra le relais). Le workflow GitHub Actions ouvre alors une
issue de notification.

Sortie : data/energy-prices-data.json, consommé par l'onglet « Coût de
l'énergie » du dashboard.
"""

import os
import re
import sys
import csv
import io
import json
import datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_freshness import fetch_dataset, latest_resource, freshness, STALE_FACTOR  # noqa: E402

SLUG = "comparaison-des-prix-de-carburants-par-motorisation"
FORMAT = "csv"
DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "energy-prices-data.json")

# Schéma attendu, colonne par colonne (ordre compris). Le nom
# "Electricie_Fournisseur" (sic) est celui de la source.
# plausible = bornes larges de vraisemblance, en unité source : un prix hors
# de ces bornes signale plus probablement un changement d'unité (ex. cents
# au lieu d'euros) qu'un vrai mouvement de marché.
SERIES = [
    {"key": "Essence_E10",            "label": "Essence E10",             "unit": "€/l",   "vector": "liquide",     "plausible": (0.5, 4.0),  "provider": "STATEC"},
    {"key": "Diesel_B7",              "label": "Diesel B7",               "unit": "€/l",   "vector": "liquide",     "plausible": (0.5, 4.0),  "provider": "STATEC"},
    {"key": "LPG",                    "label": "GPL",                     "unit": "€/l",   "vector": "liquide",     "plausible": (0.2, 3.0),  "provider": "STATEC"},
    {"key": "Hydrogene",              "label": "Hydrogène",               "unit": "€/kg",  "vector": "hydrogene",   "plausible": (2.0, 50.0), "provider": "H2.live"},
    {"key": "Electricie_Fournisseur", "label": "Électricité — domicile",  "unit": "€/kWh", "vector": "electricite", "plausible": (0.03, 1.5), "provider": "Ministère de l'Économie (estimation client résidentiel)"},
    {"key": "Recharge_AC",            "label": "Recharge publique AC",    "unit": "€/kWh", "vector": "electricite", "plausible": (0.05, 2.0), "provider": "Chargeprice"},
    {"key": "Recharge_DC",            "label": "Recharge publique DC",    "unit": "€/kWh", "vector": "electricite", "plausible": (0.05, 2.5), "provider": "Chargeprice"},
]
EXPECTED_HEADER = ["Intervalle"] + [s["key"] for s in SERIES]

# Format "Jan-26" (abréviations anglaises, année sur 2 chiffres).
MONTH_RE = re.compile(r"^([A-Z][a-z]{2})-(\d{2})$")
MONTHS_EN = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


class SchemaError(Exception):
    pass


def parse_month(raw, line_no):
    m = MONTH_RE.match(raw.strip())
    if not m or m.group(1) not in MONTHS_EN:
        raise SchemaError(f"ligne {line_no} : format de mois inattendu « {raw} » (attendu « Mmm-AA », ex. Jan-26)")
    return f"20{m.group(2)}-{MONTHS_EN[m.group(1)]:02d}"


def parse_csv(text):
    """Valide strictement le CSV et renvoie {mois 'AAAA-MM': {clé: valeur|None}}."""
    text = text.lstrip("﻿")
    reader = csv.reader(io.StringIO(text))
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        raise SchemaError("fichier vide")
    # Colonnes lues par nom : un simple réordonnancement est toléré, mais
    # toute colonne ajoutée, retirée ou renommée fait échouer le pipeline.
    if sorted(header) != sorted(EXPECTED_HEADER) or len(set(header)) != len(header):
        missing = [h for h in EXPECTED_HEADER if h not in header]
        extra = [h for h in header if h not in EXPECTED_HEADER]
        raise SchemaError(f"en-tête modifié.\n  attendu : {EXPECTED_HEADER}\n  reçu    : {header}\n  manquantes : {missing} · nouvelles : {extra}")

    this_month = datetime.date.today().strftime("%Y-%m")
    rows = {}
    for line_no, row in enumerate(reader, start=2):
        if not any(c.strip() for c in row):
            continue
        if len(row) != len(EXPECTED_HEADER):
            raise SchemaError(f"ligne {line_no} : {len(row)} colonnes au lieu de {len(EXPECTED_HEADER)}")
        cells = dict(zip(header, row))
        ym = parse_month(cells["Intervalle"], line_no)
        if ym in rows:
            raise SchemaError(f"ligne {line_no} : mois {ym} en double")
        if ym > this_month:
            raise SchemaError(f"ligne {line_no} : mois {ym} dans le futur")
        values = {}
        for s in SERIES:
            cell = cells[s["key"]].strip()
            if cell == "":
                values[s["key"]] = None  # valeur manquante tolérée, affichée « n.d. »
                continue
            try:
                v = float(cell.replace(",", "."))
            except ValueError:
                raise SchemaError(f"ligne {line_no}, {s['key']} : valeur non numérique « {cell} »")
            lo, hi = s["plausible"]
            if not lo <= v <= hi:
                raise SchemaError(f"ligne {line_no}, {s['key']} : {v} {s['unit']} hors plage plausible [{lo} ; {hi}] — changement d'unité probable")
            values[s["key"]] = v
        rows[ym] = values

    if not rows:
        raise SchemaError("aucune ligne de données")
    return rows


def load_existing():
    if not os.path.exists(DATA_PATH):
        return {}
    with open(DATA_PATH, encoding="utf-8") as f:
        old = json.load(f)
    return {ym: {k: old["values"][k][i] for k in old["values"]} for i, ym in enumerate(old["months"])}


def merge(new_rows, old_rows):
    merged = dict(old_rows)
    for ym, vals in new_rows.items():
        for k, v in vals.items():
            prev = old_rows.get(ym, {}).get(k)
            if prev is not None and v is not None and abs(prev - v) > 1e-9:
                print(f"  Révision source {ym} {k} : {prev} -> {v}", file=sys.stderr)
        merged[ym] = vals
    kept = sorted(set(old_rows) - set(new_rows))
    if kept:
        print(f"  Mois conservés depuis l'historique (absents du nouveau CSV) : {', '.join(kept)}", file=sys.stderr)
    return merged


def main():
    dataset = fetch_dataset(SLUG)
    res = latest_resource(dataset, FORMAT)
    fresh = freshness(dataset, res)
    print(f"Ressource la plus récente : {res['title']} (déposée le {fresh['last_deposit']})\n  {res['url']}", file=sys.stderr)

    r = requests.get(res["url"], timeout=60)
    r.raise_for_status()
    r.encoding = "utf-8-sig"
    new_rows = parse_csv(r.text)

    rows = merge(new_rows, load_existing())
    months = sorted(rows)

    output = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": {
            "slug": SLUG,
            "dataset_url": f"https://data.public.lu/fr/datasets/{SLUG}/",
            "resource_title": res.get("title"),
            "resource_url": res.get("url"),
            "last_deposit": fresh["last_deposit"],
            "frequency": fresh["frequency"],
            "frequency_days": fresh["frequency_days"],
            "stale_factor": STALE_FACTOR,
        },
        "series": [{k: s[k] for k in ("key", "label", "unit", "vector", "provider")} for s in SERIES],
        "months": months,
        "values": {s["key"]: [rows[ym].get(s["key"]) for ym in months] for s in SERIES},
    }

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=1)
    print(f"Écrit {DATA_PATH} ({len(months)} mois : {months[0]} → {months[-1]})", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except SchemaError as e:
        print(f"::error title=Schéma source modifié ({SLUG})::{e}", file=sys.stderr)
        print(f"\nÉCHEC — schéma du CSV modifié, data/energy-prices-data.json NON modifié.\n{e}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"::error title=Échec pipeline énergie::{e}", file=sys.stderr)
        print(f"\nÉCHEC — {type(e).__name__}: {e}\ndata/energy-prices-data.json NON modifié.", file=sys.stderr)
        sys.exit(2)
