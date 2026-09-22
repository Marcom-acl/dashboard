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

Filtre PAYPVN == 'LU' : le Luxembourg immatricule chaque mois un volume
important de véhicules qui arrivent avec un historique de leasing/location
à l'étranger (flottes corporate cross-border) — un phénomène statistique bien
connu qui gonfle les chiffres bruts d'immatriculation par rapport au marché
réel. Comparaison empirique avec le dashboard LuxInsights (considéré comme
référence) sur août 2026 : sans filtre, tous les volumes par marque étaient
~30 % trop hauts (ex. Volkswagen 350 vs 199) ; avec PAYPVN == 'LU', l'écart
tombe à 1-3 % par marque (Volkswagen 196 vs 199, Skoda 187 vs 189, Opel 133
vs 134). C'est le filtre le plus proche testé (LO, INDUTI et leurs
combinaisons donnaient des écarts nettement plus grands) — on l'adopte donc,
tout en sachant qu'un écart résiduel de quelques % subsiste probablement pour
des raisons de méthodologie propre à LuxInsights qu'on ne peut pas reproduire
à l'identique depuis ce jeu de données public.

Limite connue (biais de survie) : un véhicule immatriculé neuf puis sorti du
parc depuis (accident, export, réexportation leasing) n'apparaît plus dans
l'instantané courant. Plus un mois est ancien, plus ce biais s'accumule et
plus le volume reconstruit pour ce mois risque d'être sous-estimé. C'est pour
cette raison que le script ne recalcule PAS tout l'historique à chaque
exécution : seuls les ROLLING_MONTHS derniers mois sont recalculés depuis le
nouvel instantané (les mois plus anciens continuent de se stabiliser au fil
des semaines suivant leur publication) ; les mois plus anciens que ça sont
gelés à leur valeur déjà persistée dans data/car-registrations-data.json,
pour ne pas les voir dériver silencieusement à la baisse mois après mois.

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
CAR_CATEGORY_EU = "M1"  # voitures particulières
ROLLING_MONTHS = 3       # nombre de mois récents recalculés à chaque run ; plus anciens = gelés

DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "car-registrations-data.json")

FUEL_MAP = {
    "Standard Essence": "Essence",
    "Standard Diesel": "Diesel",
    "Hybride Electrique Essence": "Hybride",
    "Hybride Electrique Diesel": "Hybride",
    "Plug-in Hybride Electrique Essence": "Hybride",
    "Plug-in Hybride Electrique Diesel": "Hybride",
    "Pur Electrique": "Electrique",
}

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
    """Interroge l'API data.public.lu et retourne (url, title) de l'export XML
    'Parc Automobile' le plus récent. Les ressources sont déjà triées du plus
    récent au plus ancien par l'API udata."""
    r = requests.get(DATASET_API_URL, timeout=30)
    r.raise_for_status()
    resources = r.json().get("resources", [])
    for res in resources:
        title = (res.get("title") or "")
        if res.get("format") == "xml" and title.lower().startswith("parc_automobile"):
            return res["url"], title
    raise RuntimeError("Aucune ressource XML 'Parc_Automobile_*' trouvée sur data.public.lu")


def download(url, dest_path):
    with requests.get(url, stream=True, timeout=180) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)


def extract_cube(xml_path, min_month=MIN_MONTH):
    """Parcourt le XML en streaming et retourne un Counter
    (month, brand, model, fuel, color) -> count, limité aux voitures
    particulières (M1) de provenance Luxembourg (PAYPVN == 'LU'), immatriculées
    pour la première fois au Luxembourg à partir de min_month."""
    cube = collections.Counter()
    n_total = 0
    n_kept = 0
    t0 = time.time()

    for _, elem in ET.iterparse(xml_path, events=("end",)):
        if elem.tag != "VEHICLE":
            continue
        n_total += 1
        if elem.findtext("CATEU") != CAR_CATEGORY_EU:
            elem.clear()
            continue
        if (elem.findtext("PAYPVN") or "").strip() != "LU":
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

        cube[(ym, brand, model, fuel, color)] += 1
        n_kept += 1
        elem.clear()

        if n_total % 1_000_000 == 0:
            print(f"  ... {n_total} véhicules scannés, {n_kept} conservés ({time.time()-t0:.0f}s)", file=sys.stderr)

    print(f"Terminé : {n_total} véhicules scannés, {n_kept} immatriculations M1/LU retenues depuis {min_month} ({time.time()-t0:.0f}s)", file=sys.stderr)
    return cube


def load_existing_cube():
    """Relit data/car-registrations-data.json s'il existe et le reconvertit en
    tuples bruts (month, brand, model, fuel, color) -> count, pour pouvoir le
    fusionner avec une nouvelle extraction."""
    if not os.path.exists(DATA_PATH):
        return None
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            old = json.load(f)
        cube = collections.Counter()
        for month_i, brand_i, model_i, fuel_i, color_i, count in old["cube"]:
            key = (old["months"][month_i], old["brands"][brand_i], old["models"][model_i],
                   old["fuels"][fuel_i], old["colors"][color_i])
            cube[key] += count
        return cube
    except Exception as e:
        print(f"Impossible de relire l'existant ({e}) — reconstruction complète.", file=sys.stderr)
        return None


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


def build_output(cube, source_title):
    months = sorted({k[0] for k in cube})
    brands = sorted({k[1] for k in cube})
    models = sorted({k[2] for k in cube})
    fuels = ["Essence", "Diesel", "Hybride", "Electrique", "Autre"]
    colors = sorted({k[4] for k in cube})

    month_idx = {v: i for i, v in enumerate(months)}
    brand_idx = {v: i for i, v in enumerate(brands)}
    model_idx = {v: i for i, v in enumerate(models)}
    fuel_idx = {v: i for i, v in enumerate(fuels)}
    color_idx = {v: i for i, v in enumerate(colors)}

    rows = [
        [month_idx[ym], brand_idx[brand], model_idx[model], fuel_idx[fuel], color_idx[color], count]
        for (ym, brand, model, fuel, color), count in cube.items()
    ]

    return {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "source_file": source_title,
        "source_dataset": "https://data.public.lu/fr/datasets/parc-automobile-du-luxembourg/",
        "min_month": MIN_MONTH,
        "rolling_months": ROLLING_MONTHS,
        "months": months,
        "brands": brands,
        "models": models,
        "fuels": fuels,
        "colors": colors,
        "colorHex": {c: color_hex_for(c) for c in colors},
        "cube": rows,
    }


def main():
    url, title = find_latest_xml_resource()
    print(f"Ressource la plus récente : {title}\n  {url}", file=sys.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        xml_path = os.path.join(tmp, "parc-automobile.xml")
        print("Téléchargement…", file=sys.stderr)
        download(url, xml_path)
        print(f"Téléchargé ({os.path.getsize(xml_path) / 1e6:.0f} Mo), extraction…", file=sys.stderr)

        new_cube = extract_cube(xml_path)

    old_cube = load_existing_cube()
    print(f"Fusion (fenêtre glissante = {ROLLING_MONTHS} derniers mois) :", file=sys.stderr)
    cube = merge_cubes(new_cube, old_cube)

    output = build_output(cube, title)

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Écrit {DATA_PATH} ({len(output['cube'])} lignes, {len(output['months'])} mois, {len(output['brands'])} marques)", file=sys.stderr)


if __name__ == "__main__":
    main()
