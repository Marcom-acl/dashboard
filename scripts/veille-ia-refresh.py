#!/usr/bin/env python3
"""
Veille IA & Marcom — Régénération hebdomadaire.
Interroge l'API Anthropic + web_search pour 4 thèmes × 3 requêtes.
Génère data/veille-ia-data.json, lu ensuite par le backend Railway.

Usage : ANTHROPIC_API_KEY=... python scripts/veille-ia-refresh.py
"""
import os
import json
import re
import time
import datetime
from pathlib import Path

import anthropic

REPO_ROOT = Path(__file__).parent.parent
DATA_FILE = REPO_ROOT / "data" / "veille-ia-data.json"
TODAY = datetime.date.today().isoformat()

SYSTEM_PROMPT = """Tu es un expert en veille marketing stratégique pour une équipe marketing de club automobile européen.
Effectue une recherche web approfondie sur le sujet demandé. Consulte plusieurs sources variées et récentes.

Sources à privilégier selon le sujet :
- Marketing & contenu : Marketing Week, HubSpot Blog, Content Marketing Institute, Econsultancy, Adweek, MarketingProfs, Social Media Examiner, Sprout Social Blog, Later Blog, Buffer Blog, Hootsuite Blog
- CRM & email : Mailchimp Blog, Brevo Blog, Klaviyo Blog, Salesforce Blog, Campaign Monitor Blog
- SEO & digital : Search Engine Journal, Moz Blog, Ahrefs Blog, Backlinko, Google Search Central Blog, Semrush Blog
- IA & Tech : Anthropic Blog, OpenAI Blog, Google DeepMind Blog, MIT Technology Review, TechCrunch, VentureBeat, The Verge, Wired, Ars Technica
- Design & UX : Smashing Magazine, Nielsen Norman Group, UX Collective, Adobe Blog, Figma Blog, Awwwards
- Stratégie & management : Harvard Business Review, MIT Sloan Management Review, Forrester, Gartner, McKinsey Digital
- Data & plateformes : Think with Google, Meta for Business, LinkedIn Marketing Solutions Blog, Statista

Cite au minimum 5 sources différentes avec leur nom complet et l'URL directe vers l'article ou la page concernée.

Retourne UNIQUEMENT un objet JSON valide (sans texte avant ni après) respectant ce format :
{
  "titre": "titre concis et informatif (max 80 caractères)",
  "synthese": "résumé factuel des principales tendances, 4 à 6 phrases, en français",
  "actionACL": "recommandation d'action concrète pour l'équipe marketing, 2 à 3 phrases précises",
  "horizon": "court terme (0-3 mois)" or "moyen terme (3-12 mois)" or "long terme (12+ mois)",
  "sources": [{"name": "Nom de la publication", "url": "URL directe", "date": "YYYY-MM"}]
}
Le tableau sources doit contenir entre 5 et 7 entrées."""

THEMES = [
    {
        "id": "ia-marketing",
        "label": "IA générative en marketing",
        "color": "#8B5CF6",
        "queries": [
            "Quelles sont les dernières avancées en IA générative appliquée au marketing digital ? "
            "Présente les outils les plus récents, des cas d'usage concrets et des ROI mesurés par des marques européennes.",
            "Comment les marques utilisent-elles aujourd'hui l'IA générative pour personnaliser leurs "
            "newsletters et leurs communications avec leurs membres ? Exemples et données récentes.",
            "Benchmark actuel des outils IA pour petites équipes marketing (Jasper, Copy.ai, Canva Magic Studio, "
            "Notion AI, ChatGPT) : fonctionnalités, tarifs et adéquation pour une équipe de 3 à 5 personnes.",
        ],
    },
    {
        "id": "crm-member",
        "label": "CRM & member marketing",
        "color": "#0B996E",
        "queries": [
            "Quelles sont les tendances actuelles en CRM et fidélisation membres pour les associations automobiles, "
            "clubs et organisations membres en Europe ? Exemples concrets et innovations récentes.",
            "Meilleures pratiques actuelles de marketing lifecycle pour les membres d'associations : onboarding, "
            "rétention long terme, réactivation des membres inactifs — avec des données et exemples récents.",
            "Quelles sont les dernières nouveautés en email marketing automation B2C (Brevo, Mailchimp, HubSpot) "
            "et leur impact mesurable sur l'engagement et la rétention des membres ?",
        ],
    },
    {
        "id": "social-ads",
        "label": "Social & Ads",
        "color": "#1877F2",
        "queries": [
            "Quelles sont les évolutions récentes des algorithmes Facebook et Instagram et leurs implications "
            "concrètes pour les marques communautaires et institutionnelles ?",
            "LinkedIn Ads : quelles sont les nouvelles options de ciblage et les formats publicitaires les plus "
            "performants actuellement pour atteindre les professionnels et membres de clubs automobiles en Europe ?",
            "Benchmarks publicité sociale actuels : CPC, CTR, ROAS par secteur pour l'automobile, "
            "l'assurance et les services aux membres — données les plus récentes disponibles.",
        ],
    },
    {
        "id": "design-branding",
        "label": "Design & branding",
        "color": "#F97316",
        "queries": [
            "Quelles sont les tendances design de marque actuelles (typographie, couleur, identité visuelle) "
            "pour les associations et marques institutionnelles européennes établies depuis plus de 50 ans ?",
            "Motion design et vidéo courte pour les marques B2C : Reels Instagram, YouTube Shorts — "
            "quelles sont les meilleures pratiques et métriques de performance actuellement ?",
            "Comment les équipes marketing réduites gèrent-elles la cohérence de leur design system "
            "à l'ère de l'IA générative ? Défis, solutions et outils recommandés aujourd'hui.",
        ],
    },
]

# Pause entre chaque appel API pour respecter le rate limit (5 req/min sur claude-sonnet-4-6)
INTER_QUERY_DELAY = 15  # secondes


def extract_json(text):
    """Bracket-matching : extrait le premier objet JSON complet du texte."""
    clean = re.sub(r"```(?:json)?\s*", "", text).strip()
    start = clean.find("{")
    if start < 0:
        return None
    depth, in_str, esc = 0, False, False
    for i, c in enumerate(clean[start:], start):
        if esc:
            esc = False
            continue
        if c == "\\" and in_str:
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(clean[start : i + 1])
                except Exception:
                    pass
                break
    return None


def fetch_query(client, query):
    """Lance une requête via Claude + web_search. Retourne le dict JSON résultat."""
    messages = [{"role": "user", "content": query}]
    text = ""
    for _ in range(10):
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            system=SYSTEM_PROMPT,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=messages,
        )
        if resp.stop_reason != "tool_use":
            text = "".join(
                b.text for b in resp.content if getattr(b, "type", "") == "text"
            )
            break
        messages.append(
            {"role": "assistant", "content": [b.model_dump() for b in resp.content]}
        )
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": b.id,
                    "content": getattr(b, "content", []) or [],
                }
                for b in resp.content
                if b.type == "tool_use"
            ],
        })

    result = extract_json(text)
    if not result:
        return {"error": "Réponse non structurée", "raw": text[:300]}
    return result


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY manquante dans l'environnement")

    client = anthropic.Anthropic(api_key=api_key)
    output = {"generated_at": TODAY, "themes": []}
    total = sum(len(t["queries"]) for t in THEMES)
    done = 0

    for theme in THEMES:
        theme_out = {
            "id": theme["id"],
            "label": theme["label"],
            "color": theme["color"],
            "results": [],
        }
        for qi, query in enumerate(theme["queries"]):
            done += 1
            print(f"[{done}/{total}] {theme['id']} q{qi} — {query[:70]}…", flush=True)
            try:
                result = fetch_query(client, query)
                print(f"       ✓ {result.get('titre', result.get('error', '?'))[:60]}", flush=True)
            except Exception as e:
                result = {"error": str(e)}
                print(f"       ✗ Erreur : {e}", flush=True)

            theme_out["results"].append({"query": query, **result})

            if done < total:
                print(f"       → pause {INTER_QUERY_DELAY}s…", flush=True)
                time.sleep(INTER_QUERY_DELAY)

        output["themes"].append(theme_out)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n[OK] {DATA_FILE} mis à jour — {done} requêtes, générées le {TODAY}.")


if __name__ == "__main__":
    main()
