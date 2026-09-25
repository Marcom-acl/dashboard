"""
ACL Marcom Dashboard — Contrôle de fraîcheur des sources data.public.lu
=======================================================================
Pour chaque source déclarée dans SOURCES, interroge l'API udata
(https://data.public.lu/api/1/datasets/{slug}/) et affiche :
- la fréquence de mise à jour déclarée par le producteur,
- la date du dernier dépôt (ressource la plus récente au format attendu,
  triée par last_modified — jamais d'URL de fichier codée en dur),
- le délai écoulé depuis ce dépôt, et une alerte si ce délai dépasse
  STALE_FACTOR x la fréquence déclarée.

Ce module sert aussi de bibliothèque aux scripts de rafraîchissement
(latest_resource(), freshness()) pour que la règle "dernière ressource via
l'API" et le calcul d'alerte soient définis à un seul endroit.

Usage : python scripts/check_freshness.py [--json]
Code de sortie 0 = tout est frais, 1 = au moins une source en retard,
2 = erreur API / aucune ressource au format attendu.
"""

import sys
import json
import datetime

import requests

API_URL = "https://data.public.lu/api/1/datasets/{slug}/"
STALE_FACTOR = 1.5

# Sources consommées par le dashboard : slug data.public.lu + format de la
# ressource réellement lue par le pipeline.
SOURCES = [
    {"name": "Marché automobile (parc automobile SNCA)", "slug": "parc-automobile-du-luxembourg", "format": "xml"},
    {"name": "Coût de l'énergie par motorisation", "slug": "comparaison-des-prix-de-carburants-par-motorisation", "format": "csv"},
]

# Fréquences udata -> durée nominale en jours.
FREQUENCY_DAYS = {
    "continuous": 1, "hourly": 1 / 24, "fourTimesADay": 0.25, "threeTimesADay": 1 / 3,
    "semidaily": 0.5, "daily": 1, "fourTimesAWeek": 7 / 4, "threeTimesAWeek": 7 / 3,
    "semiweekly": 3.5, "weekly": 7, "biweekly": 14, "threeTimesAMonth": 10,
    "semimonthly": 15, "monthly": 30.44, "bimonthly": 61, "quarterly": 91.3,
    "threeTimesAYear": 121.7, "semiannual": 182.6, "annual": 365.25,
    "biennial": 730.5, "triennial": 1095.75, "quinquennial": 1826.25,
}


def _parse_dt(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def fetch_dataset(slug):
    r = requests.get(API_URL.format(slug=slug), timeout=30)
    r.raise_for_status()
    return r.json()


def latest_resource(dataset, fmt):
    """Ressource la plus récente au format fmt, triée par last_modified."""
    candidates = [r for r in dataset.get("resources", []) if (r.get("format") or "").lower() == fmt.lower()]
    if not candidates:
        raise RuntimeError(f"Aucune ressource au format '{fmt}' dans le jeu '{dataset.get('slug')}'")
    return max(candidates, key=lambda r: r.get("last_modified") or r.get("created_at") or "")


def freshness(dataset, resource, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    frequency = dataset.get("frequency") or "unknown"
    freq_days = FREQUENCY_DAYS.get(frequency)
    last = _parse_dt(resource.get("last_modified") or resource["created_at"])
    age_days = (now - last).total_seconds() / 86400
    return {
        "frequency": frequency,
        "frequency_days": freq_days,
        "last_deposit": last.isoformat(),
        "age_days": round(age_days, 1),
        "stale_after_days": round(freq_days * STALE_FACTOR, 1) if freq_days else None,
        "stale": bool(freq_days and age_days > freq_days * STALE_FACTOR),
        "resource_title": resource.get("title"),
        "resource_url": resource.get("url"),
    }


def main():
    as_json = "--json" in sys.argv
    results, exit_code = [], 0
    for src in SOURCES:
        try:
            ds = fetch_dataset(src["slug"])
            res = latest_resource(ds, src["format"])
            info = {**src, **freshness(ds, res)}
            if info["stale"]:
                exit_code = max(exit_code, 1)
        except Exception as e:
            info = {**src, "error": str(e)}
            exit_code = 2
        results.append(info)

    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return exit_code

    for i in results:
        print(f"\n■ {i['name']}  [{i['slug']} · {i['format']}]")
        if "error" in i:
            print(f"  ERREUR : {i['error']}")
            continue
        last = _parse_dt(i["last_deposit"])
        print(f"  Fréquence déclarée : {i['frequency']}" + (f" (~{i['frequency_days']:g} j)" if i["frequency_days"] else " (inconnue — pas d'alerte possible)"))
        print(f"  Dernier dépôt      : {last:%d/%m/%Y %H:%M} UTC — {i['resource_title']}")
        print(f"  Délai              : {i['age_days']:g} j" + (f" (seuil d'alerte : {i['stale_after_days']:g} j)" if i["stale_after_days"] else ""))
        print(f"  Statut             : {'⚠ EN RETARD' if i['stale'] else '✓ à jour'}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
