"""
Reconstruction historique précise de data/car-registrations-data.json.

À la différence de car-registrations-refresh.py (qui recalcule seulement les
ROLLING_MONTHS derniers mois depuis le dernier instantané disponible), ce
script reconstruit TOUT l'historique depuis MIN_MONTH en lisant, pour CHAQUE
mois cible M, l'export publié le mois suivant (M+1) — le plus proche possible
dans le temps de l'événement — au lieu de dériver tous les mois d'un seul
instantané récent. Ça évite le biais de survie qui s'accumule avec l'âge du
mois (un véhicule immatriculé neuf puis sorti du parc depuis n'apparaît plus
dans un instantané tardif).

Pourquoi M+1 : "Parc_Automobile_202609.xml" (publié début septembre) reflète
l'état du parc jusqu'à peu avant sa publication — il ne contient quasiment
aucune immatriculation de septembre, mais les immatriculations d'août sont
quasi complètes. Le mois M se lit donc dans l'export étiqueté M+1.

Filtre PAYPVN == 'LU' appliqué à M1 seulement (pas à M1G) : validé
empiriquement sur 4 points de référence LuxInsights indépendants (août 2026,
juillet 2026, août 2025, cumul jan-août 2026) — appliquer ce filtre aux DEUX
catégories donnait un écart de -2 à -2.3 % partout ; ne l'appliquer qu'à M1
(laisser M1G intégralement, quelle que soit sa provenance) resserre l'écart
à +0.3 à +1.6 % sur ces 4 points. Hypothèse la plus probable : les flottes de
leasing cross-border qui gonflent les immatriculations LU concernent surtout
des berlines/citadines classiques (M1), rarement des SUV/4x4 M1G. Écart
résiduel sur le cumul jan-août 2025 (-6.9 %) non expliqué — probablement une
particularité de la méthodologie LuxInsights pour ce point précis, qu'on n'a
pas pu isoler faute de chiffres mensuels de référence pour jan-juil. 2025.

Coûteux (télécharge et parse un export ~900 Mo par mois d'historique) : à
lancer ponctuellement (reconstruction initiale, extension de MIN_MONTH, ou
si l'historique doit être régénéré), pas à chaque refresh mensuel — c'est le
rôle de car-registrations-refresh.py.

Usage : python scripts/car-registrations-backfill.py
"""

import os
import sys
import json
import time
import datetime
import tempfile
import collections
import xml.etree.ElementTree as ET

import requests

DATASET_API_URL = "https://data.public.lu/api/1/datasets/parc-automobile-du-luxembourg/"
MIN_MONTH = "2024-11"
CAR_CATEGORIES_EU = ("M1", "M1G")

DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "car-registrations-data.json")

FUEL_MAP = {
    "Standard Essence": "Essence",
    "Standard Diesel": "Diesel",
    "Hybride Electrique Essence": "HEV",
    "Hybride Electrique Diesel": "HEV",
    "Plug-in Hybride Electrique Essence": "PHEV",
    "Plug-in Hybride Electrique Diesel": "PHEV",
    "Pur Electrique": "Electrique",
}

# LIBCAR (libellé carrosserie européen) n'a que 9 valeurs distinctes observées
# sur notre période (nov. 2024 -> auj.), stables depuis la migration TRVIM de
# 11/2023 : on les regroupe en 6 catégories lisibles. "Multi-usages" est un
# code européen fourre-tout (SUV ET monospaces) — on ne peut pas les séparer
# avec ce seul champ, d'où le libellé assumé "SUV / Monospace" plutôt qu'un
# "SUV" qui serait trompeusement précis.
BODYSTYLE_MAP = {
    "Berline": "Berline",
    "Break (familiale)": "Break",
    "Voiture à hayon arrière": "Compacte / Hayon",
    "à usages multiples": "Multi-usages (SUV / Monospace)",
    "Coupé": "Coupé / Cabriolet",
    "Cabriolet": "Coupé / Cabriolet",
}
BODYSTYLES = ["Berline", "Break", "Compacte / Hayon", "Multi-usages (SUV / Monospace)", "Coupé / Cabriolet", "Autre"]
ORIGINS = ["Neuf", "Occasion importée"]
OWNERS = ["Particulier", "Société", "Non déterminé"]
BRAND_ALIASES = {
    "MERCEDES": "MERCEDES-BENZ",
    "FORD (D)": "FORD",
    "DS": "DS AUTOMOBILES",
}
COLOR_HEX = {
    "Gris": "#9CA3AF", "Noir": "#1A1A1A", "Blanc": "#F1F1EE", "Bleu": "#2563EB",
    "Rouge": "#DC2626", "Vert": "#16A34A", "Jaune": "#FACC15", "Brun": "#78350F",
    "Orange": "#F97316", "Violet": "#7C3AED", "Beige": "#D6CDBB", "Marron": "#6B3F1D",
    "Argentée": "#C0C0C0", "Mauve": "#9F7AEA", "Rose": "#EC4899", "Bordeaux": "#7F1D1D",
    "Bronze": "#8C6A3F", "Dorée": "#D4AF37", "Non-spécifiée": "#B7B3AA",
}
DEFAULT_COLOR_HEX = "#B7B3AA"


def shift_month(ym, n):
    y, m = (int(p) for p in ym.split("-"))
    total = y * 12 + (m - 1) + n
    return f"{total // 12}-{total % 12 + 1:02d}"


def fuel_category(libcrb):
    if not libcrb:
        return "Autre"
    return FUEL_MAP.get(libcrb.strip(), "Autre")


def bodystyle_category(libcar):
    return BODYSTYLE_MAP.get((libcar or "").strip(), "Autre")


def owner_category(infouti):
    return {"PP": "Particulier", "PM": "Société"}.get((infouti or "").strip(), "Non déterminé")


def origin_category(datcirprm, datcir_gd):
    prm = (datcirprm or "").strip()
    return "Neuf" if prm and prm == datcir_gd else "Occasion importée"


def parse_float(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def color_hex_for(name):
    if name in COLOR_HEX:
        return COLOR_HEX[name]
    return COLOR_HEX.get(name.split(" ")[0], DEFAULT_COLOR_HEX)


def get_resource_map():
    """{'YYYY-MM': (url, title)} pour chaque export XML mensuel disponible,
    indexé par le mois qui apparaît dans son nom de fichier (pas le mois
    qu'il décrit le mieux — voir docstring)."""
    import re
    r = requests.get(DATASET_API_URL, timeout=30)
    r.raise_for_status()
    by_month = {}
    for res in r.json().get("resources", []):
        title = res.get("title") or ""
        if res.get("format") != "xml":
            continue
        m = re.search(r"(\d{6})", title)
        if not m:
            continue
        ym = f"{m.group(1)[0:4]}-{m.group(1)[4:6]}"
        if ym not in by_month:  # la liste est déjà triée du plus récent au plus ancien
            by_month[ym] = (res["url"], title)
    return by_month


def download(url, dest_path, attempts=4):
    """Le serveur data.public.lu coupe parfois la connexion en cours de
    streaming sur ces gros fichiers (~900 Mo) — observé 2 fois sur des runs
    différents, à des positions différentes dans la séquence, donc probable
    instabilité réseau/serveur plutôt qu'un fichier précis en cause. Retry
    avec backoff plutôt que de faire échouer tout le backfill sur un hoquet
    transitoire."""
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            with requests.get(url, stream=True, timeout=180) as r:
                r.raise_for_status()
                with open(dest_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        f.write(chunk)
            return
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_err = e
            print(f"    téléchargement échoué (tentative {attempt}/{attempts}) : {e}", file=sys.stderr)
            if os.path.exists(dest_path):
                os.remove(dest_path)
            if attempt < attempts:
                time.sleep(5 * attempt)
    raise last_err


def extract_month(xml_path, target_month, cube, stats):
    """Ajoute au cube les lignes concernant exactement target_month, et
    accumule dans stats[target_month] les sommes/compteurs nécessaires aux
    moyennes CO2 / consommation / autonomie électrique."""
    n_total = n_kept = 0
    t0 = time.time()
    for _, elem in ET.iterparse(xml_path, events=("end",)):
        if elem.tag != "VEHICLE":
            continue
        n_total += 1
        cateu = elem.findtext("CATEU")
        if cateu not in CAR_CATEGORIES_EU:
            elem.clear(); continue
        # PAYPVN == 'LU' n'est requis que pour M1 (voitures standard) — voir
        # docstring pour la validation empirique de cette asymétrie.
        if cateu == "M1" and (elem.findtext("PAYPVN") or "").strip() != "LU":
            elem.clear(); continue
        datcir_gd = (elem.findtext("DATCIR_GD") or "").strip()
        if len(datcir_gd) != 8 or not datcir_gd.isdigit():
            elem.clear(); continue
        ym = f"{datcir_gd[0:4]}-{datcir_gd[4:6]}"
        if ym != target_month:
            elem.clear(); continue

        brand = (elem.findtext("LIBMRQ") or "").strip() or "INCONNU"
        brand = BRAND_ALIASES.get(brand, brand)
        model = (elem.findtext("TYPCOM") or "").strip() or "N/D"
        color = (elem.findtext("COUL") or "").strip() or "Non-spécifiée"
        fuel = fuel_category(elem.findtext("LIBCRB"))
        bodystyle = bodystyle_category(elem.findtext("LIBCAR"))
        owner = owner_category(elem.findtext("INFOUTI"))
        origin = origin_category(elem.findtext("DATCIRPRM"), datcir_gd)

        cube[(ym, brand, model, fuel, color, origin, owner, bodystyle)] += 1
        n_kept += 1

        # CO2WLTP (norme actuelle) prioritaire sur INFCO2 (legacy NEDC) ; pour
        # les électriques ce champ n'est jamais alimenté côté SNCA — 0 g/km à
        # l'échappement est la convention réglementaire UE, pas une valeur
        # manquante à exclure.
        st = stats[ym]
        co2 = 0.0 if fuel == "Electrique" else parse_float(elem.findtext("CO2WLTP") or elem.findtext("INFCO2"))
        if co2 is not None:
            st["co2_sum"] += co2; st["co2_n"] += 1
        l100 = parse_float(elem.findtext("L100KM"))
        if l100 is not None:
            st["l100_sum"] += l100; st["l100_n"] += 1
        if fuel == "Electrique":
            auto = parse_float(elem.findtext("AUTOELEC"))
            if auto is not None:
                st["auto_sum"] += auto; st["auto_n"] += 1

        elem.clear()
    print(f"    {n_total} scannés, {n_kept} retenus pour {target_month} ({time.time()-t0:.0f}s)", file=sys.stderr)


def build_output(cube, stats):
    months = sorted({k[0] for k in cube})
    brands = sorted({k[1] for k in cube})
    models = sorted({k[2] for k in cube})
    fuels = ["Essence", "Diesel", "HEV", "PHEV", "Electrique", "Autre"]
    colors = sorted({k[4] for k in cube})
    month_idx = {v: i for i, v in enumerate(months)}
    brand_idx = {v: i for i, v in enumerate(brands)}
    model_idx = {v: i for i, v in enumerate(models)}
    fuel_idx = {v: i for i, v in enumerate(fuels)}
    color_idx = {v: i for i, v in enumerate(colors)}
    origin_idx = {v: i for i, v in enumerate(ORIGINS)}
    owner_idx = {v: i for i, v in enumerate(OWNERS)}
    bodystyle_idx = {v: i for i, v in enumerate(BODYSTYLES)}
    rows = [
        [month_idx[ym], brand_idx[b], model_idx[mo], fuel_idx[f], color_idx[c],
         origin_idx[o], owner_idx[ow], bodystyle_idx[bs], count]
        for (ym, b, mo, f, c, o, ow, bs), count in cube.items()
    ]

    def avg_or_none(sum_key, n_key, ym):
        st = stats.get(ym)
        if not st or not st[n_key]:
            return None
        return round(st[sum_key] / st[n_key], 1)

    return {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "source_file": "backfill: exports mensuels contemporains de chaque mois (data.public.lu)",
        "source_dataset": "https://data.public.lu/fr/datasets/parc-automobile-du-luxembourg/",
        "min_month": MIN_MONTH,
        "rolling_months": 3,
        "months": months,
        "brands": brands,
        "models": models,
        "fuels": fuels,
        "colors": colors,
        "colorHex": {c: color_hex_for(c) for c in colors},
        "origins": ORIGINS,
        "owners": OWNERS,
        "bodystyles": BODYSTYLES,
        "cube": rows,
        "co2Avg": [avg_or_none("co2_sum", "co2_n", ym) for ym in months],
        "consoAvg": [avg_or_none("l100_sum", "l100_n", ym) for ym in months],
        "autoElecAvg": [avg_or_none("auto_sum", "auto_n", ym) for ym in months],
    }


def main():
    resource_map = get_resource_map()
    latest_snapshot_month = max(resource_map.keys())
    last_target = shift_month(latest_snapshot_month, -1)  # le dernier mois complet qu'on peut calculer
    targets = []
    m = MIN_MONTH
    while m <= last_target:
        targets.append(m)
        m = shift_month(m, 1)
    print(f"Reconstruction de {targets[0]} à {targets[-1]} ({len(targets)} mois)…", file=sys.stderr)

    cube = collections.Counter()
    stats = collections.defaultdict(lambda: {"co2_sum": 0.0, "co2_n": 0, "l100_sum": 0.0, "l100_n": 0, "auto_sum": 0.0, "auto_n": 0})
    with tempfile.TemporaryDirectory() as tmp:
        for target in targets:
            snapshot_month = shift_month(target, 1)
            if snapshot_month not in resource_map:
                print(f"IGNORÉ {target} : pas d'export pour {snapshot_month}", file=sys.stderr)
                continue
            url, title = resource_map[snapshot_month]
            print(f"{target} <- {title}", file=sys.stderr)
            xml_path = os.path.join(tmp, "snapshot.xml")
            download(url, xml_path)
            extract_month(xml_path, target, cube, stats)
            os.remove(xml_path)

    output = build_output(cube, stats)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\nÉcrit {DATA_PATH} : {len(output['cube'])} lignes, {len(output['months'])} mois, {len(output['brands'])} marques", file=sys.stderr)


if __name__ == "__main__":
    main()
