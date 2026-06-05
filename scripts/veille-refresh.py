#!/usr/bin/env python3
"""
Veille sectorielle ACL — Mise à jour automatique quotidienne.
Récupère les actualités des clubs automobiles européens via RSS,
les analyse avec Claude et met à jour data/veille-data.json.

Requiert: ANTHROPIC_API_KEY dans l'environnement.
"""

import json
import os
import re
import datetime
from pathlib import Path

import anthropic
import feedparser
import requests

# ─────────────────────────────────────────────────────────────────────────────
# Configuration des sources RSS
# ─────────────────────────────────────────────────────────────────────────────
RSS_SOURCES = [
    {
        "club": "ADAC",
        "category": "EU",
        "url": "https://www.adac.de/rss/default/",
    },
    {
        "club": "RAC",
        "category": "EU",
        "url": "https://www.rac.co.uk/drive/news/feed/",
    },
    {
        "club": "AIT/Alliance Internationale de Tourisme",
        "category": "Monde",
        "url": "https://www.aitglobal.com/en/feed/",
    },
    {
        "club": "TCS Suisse",
        "category": "EU",
        "url": "https://www.tcs.ch/fr/rss/",
    },
    {
        "club": "ACEA",
        "category": "EU",
        "url": "https://www.acea.auto/feed/",
    },
    {
        "club": "Touring Belgique",
        "category": "EU",
        "url": "https://www.touring.be/fr/rss",
    },
    {
        "club": "ACI Italia",
        "category": "EU",
        "url": "https://www.aci.it/rss-news.html",
    },
    {
        "club": "ÖAMTC",
        "category": "EU",
        "url": "https://www.oeamtc.at/rss/",
    },
    {
        "club": "Automobile Club de Monaco",
        "category": "EU",
        "url": "https://www.acm.mc/rss",
    },
    {
        "club": "Actualité mobilité EU",
        "category": "Mobilité",
        "url": "https://www.electrive.com/feed/",
    },
]

# ─────────────────────────────────────────────────────────────────────────────
# Chemins
# ─────────────────────────────────────────────────────────────────────────────
REPO_ROOT  = Path(__file__).parent.parent
DATA_FILE  = REPO_ROOT / "data" / "veille-data.json"
TODAY      = datetime.date.today().isoformat()
CUTOFF     = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Collecte des articles RSS récents
# ─────────────────────────────────────────────────────────────────────────────
def fetch_articles():
    articles = []
    for src in RSS_SOURCES:
        try:
            feed = feedparser.parse(src["url"])
            for entry in feed.entries[:10]:          # max 10 par source
                pub = getattr(entry, "published", None) or getattr(entry, "updated", None) or ""
                # Convertit en date ISO si possible
                try:
                    import email.utils
                    dt = email.utils.parsedate_to_datetime(pub)
                    pub_iso = dt.date().isoformat()
                except Exception:
                    pub_iso = TODAY                  # fallback = aujourd'hui

                if pub_iso < CUTOFF:
                    continue                          # trop ancien

                title   = entry.get("title", "").strip()
                summary = entry.get("summary", "").strip()
                # Nettoie le HTML basique
                summary = re.sub(r"<[^>]+>", " ", summary)
                summary = re.sub(r"\s{2,}", " ", summary).strip()
                link    = entry.get("link", "")

                if not title:
                    continue

                articles.append({
                    "club":     src["club"],
                    "category": src["category"],
                    "date":     pub_iso,
                    "title":    title,
                    "summary":  summary[:500],
                    "url":      link,
                })
        except Exception as e:
            print(f"[WARN] Erreur RSS {src['club']}: {e}")

    return articles


# ─────────────────────────────────────────────────────────────────────────────
# Analyse Claude
# ─────────────────────────────────────────────────────────────────────────────
def analyse_with_claude(articles, existing_ids):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY manquante dans l'environnement")

    client = anthropic.Anthropic(api_key=api_key)

    if not articles:
        print("[INFO] Aucun article récent à analyser.")
        return []

    articles_text = "\n\n".join(
        f"[{i+1}] {a['date']} | {a['club']} | {a['category']}\n"
        f"Titre : {a['title']}\n"
        f"Résumé : {a['summary']}\n"
        f"URL : {a['url']}"
        for i, a in enumerate(articles)
    )

    prompt = f"""Tu es l'analyste veille sectorielle de l'ACL Luxembourg (Automobile Club du Grand-Duché de Luxembourg, 191 000 membres, secteur automobile et mobilité).

Analyse les articles ci-dessous publiés par des clubs automobiles ou organisations de mobilité européennes.
Pour chaque article PERTINENT pour l'ACL, génère une fiche de veille.

## Articles à analyser (derniers 7 jours)

{articles_text}

## Instructions

Retourne UNIQUEMENT un tableau JSON valide, sans markdown ni explication.
Sélectionne seulement les articles qui ont une vraie pertinence stratégique pour un club automobile de taille moyenne (150k-200k membres).
Ignore les articles purement locaux, sans intérêt sectoriel, ou trop génériques.
Limite à 8 fiches maximum par exécution.

Format exact de chaque fiche :
{{
  "id": "YYYY-MM-DD-clubslug-N",        // date + slug du club + numéro unique
  "date": "YYYY-MM-DD",
  "club": "Nom du club source",
  "category": "EU" | "Monde" | "Mobilité",
  "content": "Synthèse factuelle en 1-2 phrases, en français. Inclure chiffres clés si disponibles.",
  "relevance": "Analyse de la pertinence pour l'ACL en 1-2 phrases. Proposer une question ou action concrète.",
  "badge": "NOTABLE" | "SURVEILLER" | "INFO",
  "url": "URL source"
}}

Critères de badge :
- NOTABLE : innovation, pratique transposable directement à l'ACL, signal fort du marché
- SURVEILLER : tendance à suivre, risque ou opportunité à confirmer
- INFO : information utile mais sans urgence

Ta réponse doit commencer par [ et se terminer par ].
"""

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    text = msg.content[0].text.strip()

    # Extraction JSON robuste
    new_items = []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            new_items = parsed
    except Exception:
        m = re.search(r"\[[\s\S]*\]", text)
        if m:
            try:
                new_items = json.loads(m.group())
            except Exception:
                pass

    # Déduplication par id
    new_items = [i for i in new_items if isinstance(i, dict) and i.get("id") and i["id"] not in existing_ids]
    print(f"[INFO] {len(new_items)} nouvelles fiches générées.")
    return new_items


# ─────────────────────────────────────────────────────────────────────────────
# Mise à jour du fichier JSON
# ─────────────────────────────────────────────────────────────────────────────
def update_data(new_items):
    if DATA_FILE.exists():
        with open(DATA_FILE, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"generated_at": TODAY, "next_edition": TODAY, "editions": [], "items": [], "trends": [], "signals": []}

    existing_ids = {i["id"] for i in data.get("items", []) if "id" in i}

    if not new_items:
        # Met quand même à jour la date de génération
        data["generated_at"] = TODAY
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("[INFO] Pas de nouvelles fiches. Date de génération mise à jour.")
        return

    # Vérifie les doublons à nouveau (sécurité)
    to_add = [i for i in new_items if i.get("id") not in existing_ids]

    # Prépend les nouveaux items (plus récents en premier)
    data["items"] = to_add + data.get("items", [])
    data["generated_at"] = TODAY

    # Maintient l'historique des éditions (dedupliqué)
    edition_dates = {e["date"] for e in data.get("editions", [])}
    if TODAY not in edition_dates:
        data.setdefault("editions", []).append({"date": TODAY, "label": f"Mise à jour du {TODAY}"})

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[OK] veille-data.json mis à jour — {len(to_add)} nouvelles fiches ajoutées.")


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print(f"[START] Veille sectorielle — {TODAY}")

    # Charge les IDs existants pour dédupliquer
    existing_ids = set()
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                existing = json.load(f)
            existing_ids = {i["id"] for i in existing.get("items", []) if "id" in i}
        except Exception:
            pass

    articles = fetch_articles()
    print(f"[INFO] {len(articles)} articles collectés.")

    new_items = analyse_with_claude(articles, existing_ids)
    update_data(new_items)


if __name__ == "__main__":
    main()
