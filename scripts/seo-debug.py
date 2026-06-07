#!/usr/bin/env python3
"""
Script de debug — teste UN mot-clé via l'endpoint Live DataForSEO
et affiche la structure brute de la réponse pour diagnostiquer le parsing.

Usage : DATAFORSEO_AUTH=<base64> python scripts/seo-debug.py
"""

import json
import os
import sys
import requests

API_BASE = "https://api.dataforseo.com/v3/serp/google/organic"
HEADERS  = {
    "Authorization": f"Basic {os.environ.get('DATAFORSEO_AUTH', '')}",
    "Content-Type": "application/json",
}

def main():
    if not os.environ.get("DATAFORSEO_AUTH"):
        print("[ERROR] DATAFORSEO_AUTH manquant")
        sys.exit(1)

    # Teste UN mot-clé via Live (synchrone, résultat immédiat)
    payload = [{
        "keyword":       "prix carburant luxembourg",
        "language_name": "French",
        "location_name": "Luxembourg",
        "se_domain":     "google.lu",
        "device":        "mobile",
        "depth":         10,
    }]

    print("[INFO] Appel DataForSEO Live (1 mot-clé)…")
    r = requests.post(f"{API_BASE}/live/advanced", headers=HEADERS, json=payload, timeout=60)
    print(f"[INFO] HTTP {r.status_code}")

    data = r.json()
    print("\n=== RÉPONSE BRUTE (top level) ===")
    print(f"status_code: {data.get('status_code')}")
    print(f"tasks_count: {data.get('tasks_count')}")
    tasks = data.get("tasks", [])
    print(f"len(tasks): {len(tasks)}")

    if not tasks:
        print("[WARN] Aucune tâche dans la réponse")
        return

    task = tasks[0]
    print(f"\n=== TASK status_code: {task.get('status_code')} — {task.get('status_message')} ===")
    result = task.get("result") or []
    print(f"len(result): {len(result)}")

    if not result:
        print("[WARN] result vide — coût task:", task.get("cost"))
        print("task data:", json.dumps(task.get("data", {}), ensure_ascii=False))
        return

    r0 = result[0]
    items = r0.get("items") or []
    print(f"\n=== ITEMS ({len(items)} total) ===")
    print(f"keyword: {r0.get('keyword')}")
    print(f"se_domain: {r0.get('se_domain')}")
    print(f"device: {r0.get('device')}")

    # Affiche les 10 premiers items avec type + domain
    for i, item in enumerate(items[:15]):
        t = item.get("type", "?")
        domain = item.get("domain", "—")
        rank = item.get("rank_absolute", "?")
        url = (item.get("url") or "")[:60]
        print(f"  [{i+1:2d}] type={t:20s} rank={rank:3s} domain={domain:30s} url={url}")

    # Types présents
    types = {}
    for item in items:
        t = item.get("type", "unknown")
        types[t] = types.get(t, 0) + 1
    print(f"\nTypes présents: {types}")

    # Coût
    print(f"\nCoût de la requête: ${task.get('cost', '?')}")

if __name__ == "__main__":
    main()
