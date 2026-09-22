"""
ACL Marcom Dashboard — Immatriculations de voitures neuves au Luxembourg
============================================================================
Source : "Parc Automobile du Luxembourg" (SNCA / data.public.lu, licence CC0).
Ce jeu de données publie chaque mois un instantané complet du parc automobile
en circulation (tous véhicules confondus). Il n'existe pas de fichier "nouvelles
immatriculations" séparé : on reconstitue les nouvelles immatriculations de
voitures particulières (catégorie européenne M1) en regroupant les véhicules
encore en circulation par leur date de première mise en circulation au
Luxembourg (DATCIR_GD). Un même instantané suffit donc à reconstruire tout
l'historique — inutile de télécharger un fichier par mois.

Limite connue : un véhicule immatriculé neuf puis sorti du parc depuis (accident,
export, réexportation leasing) n'apparaît plus dans l'instantané courant. Les
volumes des mois anciens peuvent donc être révisés (très légèrement) à la
baisse d'un mois sur l'autre. C'est la même limite que rencontre toute
reconstruction à partir de ce jeu de données open data.

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
    particulières (M1) immatriculées pour la première fois au Luxembourg
    à partir de min_month."""
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

    print(f"Terminé : {n_total} véhicules scannés, {n_kept} immatriculations M1 retenues depuis {min_month} ({time.time()-t0:.0f}s)", file=sys.stderr)
    return cube


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

        cube = extract_cube(xml_path)

    output = build_output(cube, title)

    out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "car-registrations-data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Écrit {out_path} ({len(output['cube'])} lignes, {len(output['months'])} mois, {len(output['brands'])} marques)", file=sys.stderr)


if __name__ == "__main__":
    main()
