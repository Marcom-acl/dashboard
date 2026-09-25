"""
ACL Marcom Dashboard — Immatriculations de voitures neuves au Luxembourg
============================================================================
Source : "Parc Automobile du Luxembourg" (SNCA / data.public.lu, licence CC0).
Ce jeu de données publie chaque mois un instantané complet du parc automobile
en circulation (tous véhicules confondus). Il n'existe pas de fichier "nouvelles
immatriculations" séparé : on reconstitue les nouvelles immatriculations de
voitures particulières (catégorie européenne M1) en regroupant les véhicules
encore en circulation par leur date de première mise en circulation au
Luxembourg (DATCIR_GD).

Filtres retenus (validés empiriquement contre le dashboard LuxInsights,
considéré comme référence, sur 4 points indépendants : août 2026, juillet
2026, août 2025, cumul jan-août 2026) :
- CATEU in ('M1', 'M1G') : voitures particulières classiques + SUV/4x4 à
  garde au sol élevée (catégorie européenne dédiée mais qui reste, pour le
  marché, des voitures).
- PAYPVN == 'LU' appliqué à M1 SEULEMENT, pas à M1G : le Luxembourg
  immatricule chaque mois un volume important de véhicules qui arrivent avec
  un historique de leasing/location à l'étranger (flottes corporate
  cross-border) — un phénomène statistique bien connu qui gonfle les chiffres
  bruts d'immatriculation par rapport au marché réel. Ces flottes concernent
  surtout des berlines/citadines classiques (M1) ; appliquer le même filtre à
  M1G dégradait la précision (testé : -2 à -2.3 % partout avec le filtre sur
  les deux catégories, contre +0.3 à +1.6 % en le limitant à M1).
Sans ces filtres, tous les volumes par marque étaient ~30 % trop hauts (ex.
Volkswagen 350 vs 199 chez LuxInsights). LO, INDUTI et leurs combinaisons
donnaient des écarts nettement plus grands, écartés. Un écart résiduel
subsiste probablement pour des raisons de méthodologie propre à LuxInsights
qu'on ne peut pas reproduire à l'identique depuis ce jeu de données public
(notamment sur le cumul jan-août 2025, encore à -6.9 % sans qu'on ait pu
isoler pourquoi faute de références mensuelles individuelles pour cette
période).

IMPORTANT — sens du nom de fichier : "Parc_Automobile_202609.xml" (publié le
4 septembre 2026) reflète l'état du parc jusqu'à peu avant sa date de
publication — il ne contient quasiment AUCUNE immatriculation de septembre,
mais contient les immatriculations d'août quasi complètes. Autrement dit,
pour obtenir le volume du mois M, il faut lire l'export étiqueté M+1. Le
script de rafraîchissement mensuel (celui-ci) exploite ça nativement : il
prend toujours le dernier export disponible, qui correspond donc au mois M+1
par rapport au dernier mois qu'il permet de calculer.

Limite connue (biais de survie) : un véhicule immatriculé neuf puis sorti du
parc depuis (accident, export, réexportation leasing) n'apparaît plus dans
un instantané ultérieur. Le script ne recalcule donc PAS tout l'historique à
chaque exécution : seuls les ROLLING_MONTHS derniers mois sont recalculés
depuis le nouvel instantané (ils continuent de se stabiliser au fil des
semaines suivant leur publication) ; les mois plus anciens sont gelés à leur
valeur déjà persistée dans data/car-registrations-data.json, pour ne pas les
voir dériver silencieusement à la baisse mois après mois. L'historique
initial (nov. 2024 → août 2026) a été construit une fois avec
scripts/car-registrations-backfill.py, qui lit l'export M+1 propre à CHAQUE
mois plutôt qu'un seul instantané récent — ça évite d'accumuler le biais de
survie sur les mois anciens (validé : l'écart sur août 2025 est passé de
-6.7 % à -2.3 % avec cette méthode).

Sortie : data/car-registrations-data.json (format compact indexé, voir
build_output()) consommé par l'onglet "Marché automobile" du dashboard.
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
CAR_CATEGORIES_EU = ("M1", "M1G")  # voitures particulières (classiques + SUV/4x4)
ROLLING_MONTHS = 3       # nombre de mois récents recalculés à chaque run ; plus anciens = gelés

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

# Doublons connus dans le référentiel marque de la SNCA
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


def new_stats_entry():
    return {"co2_sum": 0.0, "co2_n": 0, "l100_sum": 0.0, "l100_n": 0, "auto_sum": 0.0, "auto_n": 0}


def color_hex_for(name):
    if name in COLOR_HEX:
        return COLOR_HEX[name]
    first = name.split(" ")[0]
    return COLOR_HEX.get(first, DEFAULT_COLOR_HEX)


def shift_month(ym, n):
    """'2026-08' shifted by n (can be negative) -> '2026-05' etc."""
    y, m = (int(p) for p in ym.split("-"))
    total = y * 12 + (m - 1) + n
    return f"{total // 12}-{total % 12 + 1:02d}"


def find_latest_xml_resource():
    """Interroge l'API data.public.lu et retourne (url, title, last_modified) de l'export XML
    'Parc Automobile' le plus récent. Les ressources sont déjà triées du plus
    récent au plus ancien par l'API udata."""
    r = requests.get(DATASET_API_URL, timeout=30)
    r.raise_for_status()
    resources = r.json().get("resources", [])
    for res in resources:
        title = (res.get("title") or "")
        if res.get("format") == "xml" and title.lower().startswith("parc_automobile"):
            return res["url"], title, res.get("last_modified")
    raise RuntimeError("Aucune ressource XML 'Parc_Automobile_*' trouvée sur data.public.lu")


def download(url, dest_path):
    with requests.get(url, stream=True, timeout=180) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)


def extract_cube(xml_path, min_month=MIN_MONTH):
    """Parcourt le XML en streaming et retourne (cube, stats) :
    - cube : Counter (month, brand, model, fuel, color, origin, owner,
      bodystyle) -> count, limité aux voitures particulières (M1/M1G) de
      provenance Luxembourg (PAYPVN == 'LU'), immatriculées pour la première
      fois au Luxembourg à partir de min_month.
    - stats : dict month -> sommes/compteurs pour les moyennes CO2 /
      consommation / autonomie électrique (voir build_output)."""
    cube = collections.Counter()
    stats = collections.defaultdict(new_stats_entry)
    n_total = 0
    n_kept = 0
    t0 = time.time()

    for _, elem in ET.iterparse(xml_path, events=("end",)):
        if elem.tag != "VEHICLE":
            continue
        n_total += 1
        cateu = elem.findtext("CATEU")
        if cateu not in CAR_CATEGORIES_EU:
            elem.clear()
            continue
        # PAYPVN == 'LU' n'est requis que pour M1 (voir docstring).
        if cateu == "M1" and (elem.findtext("PAYPVN") or "").strip() != "LU":
            elem.clear()
            continue

        datcir_gd = (elem.findtext("DATCIR_GD") or "").strip()
        if len(datcir_gd) != 8 or not datcir_gd.isdigit():
            elem.clear()
            continue
        ym = f"{datcir_gd[0:4]}-{datcir_gd[4:6]}"
        if ym < min_month:
            elem.clear()
            continue

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

        if n_total % 1_000_000 == 0:
            print(f"  ... {n_total} véhicules scannés, {n_kept} conservés ({time.time()-t0:.0f}s)", file=sys.stderr)

    print(f"Terminé : {n_total} véhicules scannés, {n_kept} immatriculations M1/M1G/LU retenues depuis {min_month} ({time.time()-t0:.0f}s)", file=sys.stderr)
    return cube, stats


def load_existing_cube():
    """Relit data/car-registrations-data.json s'il existe et le reconvertit en
    tuples bruts (month, brand, model, fuel, color, origin, owner, bodystyle)
    -> count, plus les moyennes CO2/conso/autonomie déjà persistées par mois,
    pour pouvoir fusionner avec une nouvelle extraction. Tolère l'ancien
    format à 5 dimensions (avant l'ajout origin/owner/bodystyle) en complétant
    avec "Non déterminé"/"Autre"."""
    if not os.path.exists(DATA_PATH):
        return None, None
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            old = json.load(f)
        origins = old.get("origins", ORIGINS)
        owners = old.get("owners", OWNERS)
        bodystyles = old.get("bodystyles", BODYSTYLES)
        cube = collections.Counter()
        for row in old["cube"]:
            month_i, brand_i, model_i, fuel_i, color_i = row[:5]
            if len(row) >= 9:
                origin_i, owner_i, bodystyle_i, count = row[5:9]
                origin, owner, bodystyle = origins[origin_i], owners[owner_i], bodystyles[bodystyle_i]
            else:
                count = row[5]
                origin, owner, bodystyle = "Neuf", "Non déterminé", "Autre"
            key = (old["months"][month_i], old["brands"][brand_i], old["models"][model_i],
                   old["fuels"][fuel_i], old["colors"][color_i], origin, owner, bodystyle)
            cube[key] += count

        avgs = {}
        for i, ym in enumerate(old["months"]):
            avgs[ym] = {
                "co2": (old.get("co2Avg") or [None] * len(old["months"]))[i],
                "conso": (old.get("consoAvg") or [None] * len(old["months"]))[i],
                "auto": (old.get("autoElecAvg") or [None] * len(old["months"]))[i],
            }
        return cube, avgs
    except Exception as e:
        print(f"Impossible de relire l'existant ({e}) — reconstruction complète.", file=sys.stderr)
        return None, None


def compute_avgs(stats, months):
    def avg_or_none(st, sum_key, n_key):
        if not st[n_key]:
            return None
        return round(st[sum_key] / st[n_key], 1)
    return {
        ym: {
            "co2": avg_or_none(stats[ym], "co2_sum", "co2_n"),
            "conso": avg_or_none(stats[ym], "l100_sum", "l100_n"),
            "auto": avg_or_none(stats[ym], "auto_sum", "auto_n"),
        }
        for ym in months if ym in stats
    }


def merge_avgs(new_avgs, old_avgs, cutoff, old_months_available):
    if old_avgs is None:
        return new_avgs
    merged = {}
    for ym in set(new_avgs) | set(old_avgs):
        if ym >= cutoff or ym not in old_months_available:
            merged[ym] = new_avgs.get(ym) or old_avgs.get(ym)
        else:
            merged[ym] = old_avgs.get(ym) or new_avgs.get(ym)
    return merged


def merge_cubes(new_cube, old_cube, rolling_months=ROLLING_MONTHS):
    """Mois récents (rolling_months derniers mois de new_cube) : valeurs
    fraîches de new_cube. Mois plus anciens : gelés à old_cube s'ils y sont
    déjà présents (sinon repli sur new_cube, ex. tout premier run)."""
    if old_cube is None:
        return new_cube

    new_months = sorted({k[0] for k in new_cube})
    if not new_months:
        return old_cube
    latest = new_months[-1]
    cutoff = shift_month(latest, -(rolling_months - 1))  # mois à partir duquel on rafraîchit

    old_months_available = {k[0] for k in old_cube}
    merged = collections.Counter()
    all_months = sorted(set(new_months) | old_months_available)

    for ym in all_months:
        if ym >= cutoff or ym not in old_months_available:
            source, label = new_cube, "frais"
        else:
            source, label = old_cube, "gelé"
        for key, count in source.items():
            if key[0] == ym:
                merged[key] += count
        print(f"  {ym}: {label}", file=sys.stderr)

    return merged


def build_output(cube, source_title, avgs, source_last_modified=None):
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
        [month_idx[ym], brand_idx[brand], model_idx[model], fuel_idx[fuel], color_idx[color],
         origin_idx[origin], owner_idx[owner], bodystyle_idx[bodystyle], count]
        for (ym, brand, model, fuel, color, origin, owner, bodystyle), count in cube.items()
    ]

    return {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "source_file": source_title,
        # Date du dépôt source intégré : affichée « Données au … » dans le dashboard.
        "source_last_modified": source_last_modified,
        "source_dataset": "https://data.public.lu/fr/datasets/parc-automobile-du-luxembourg/",
        "min_month": MIN_MONTH,
        "rolling_months": ROLLING_MONTHS,
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
        "co2Avg": [(avgs.get(ym) or {}).get("co2") for ym in months],
        "consoAvg": [(avgs.get(ym) or {}).get("conso") for ym in months],
        "autoElecAvg": [(avgs.get(ym) or {}).get("auto") for ym in months],
    }


def main():
    url, title, last_modified = find_latest_xml_resource()
    print(f"Ressource la plus récente : {title}\n  {url}", file=sys.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        xml_path = os.path.join(tmp, "parc-automobile.xml")
        print("Téléchargement…", file=sys.stderr)
        download(url, xml_path)
        print(f"Téléchargé ({os.path.getsize(xml_path) / 1e6:.0f} Mo), extraction…", file=sys.stderr)

        new_cube, new_stats = extract_cube(xml_path)

    old_cube, old_avgs = load_existing_cube()
    print(f"Fusion (fenêtre glissante = {ROLLING_MONTHS} derniers mois) :", file=sys.stderr)
    cube = merge_cubes(new_cube, old_cube)

    new_months = sorted({k[0] for k in new_cube})
    cutoff = shift_month(new_months[-1], -(ROLLING_MONTHS - 1)) if new_months else MIN_MONTH
    old_months_available = {k[0] for k in old_cube} if old_cube else set()
    new_avgs = compute_avgs(new_stats, new_months)
    avgs = merge_avgs(new_avgs, old_avgs, cutoff, old_months_available)

    output = build_output(cube, title, avgs, last_modified)

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Écrit {DATA_PATH} ({len(output['cube'])} lignes, {len(output['months'])} mois, {len(output['brands'])} marques)", file=sys.stderr)


if __name__ == "__main__":
    main()
