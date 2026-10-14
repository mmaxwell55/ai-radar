"""Weekly GitHub radar for the Friday AI repos and skills report.

Runs in GitHub Actions (or on the Mac Mini). Uses GitHub's own search API,
then writes data/latest.json plus a dated copy. The Friday report reads
latest.json from raw.githubusercontent.com.

Standard library only. Needs GITHUB_TOKEN in the environment (Actions
provides it automatically).
"""
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.github.com"
TOKEN = os.environ.get("GITHUB_TOKEN", "")
NOW = datetime.now(timezone.utc)
TODAY = NOW.date().isoformat()
D7 = (NOW - timedelta(days=7)).date().isoformat()
D14 = (NOW - timedelta(days=14)).date().isoformat()
D30 = (NOW - timedelta(days=30)).date().isoformat()
D60 = (NOW - timedelta(days=60)).date().isoformat()

# Topics that match the relevance tiers in the report prompt.
TOPICS = [
    "claude-code", "claude", "anthropic", "claude-skills", "agent-skills",
    "mcp", "mcp-server", "model-context-protocol",
    "ai-agents", "ai-agent", "agents", "llm", "ai-memory", "memory",
    "rag", "automation", "workflow-automation", "seo", "shopify",
]

# Keyword searches catch repos that don't tag topics.
KEYWORDS = [
    "claude skill", "claude code plugin", "mcp server",
    "agent memory", "ai agent framework",
]

# Repos whose recent commits are worth listing every week.
WATCH_COMMITS = ["anthropics/skills", "anthropics/claude-code"]

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")


def get(path, params=None):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ai-radar",
        **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}),
    })
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and attempt < 2:
                time.sleep(65)  # search limit resets each minute
                continue
            if e.code == 404:
                return None
            raise
    return None


def search(q, per_page=30):
    time.sleep(2.2)  # stay under 30 searches/minute
    res = get("/search/repositories",
              {"q": q, "sort": "stars", "order": "desc", "per_page": per_page})
    return (res or {}).get("items", [])


def slim(item):
    created = item["created_at"][:10]
    age = max((NOW.date() - datetime.fromisoformat(created).date()).days, 1)
    return {
        "full_name": item["full_name"],
        "url": item["html_url"],
        "description": item.get("description") or "",
        "stars": item["stargazers_count"],
        "forks": item["forks_count"],
        "language": item.get("language"),
        "topics": item.get("topics", []),
        "license": (item.get("license") or {}).get("spdx_id"),
        "created": created,
        "pushed": item["pushed_at"][:10],
        "archived": item.get("archived", False),
        "age_days": age,
        "stars_per_day_lifetime": round(item["stargazers_count"] / age, 1),
        "matched": [],
    }


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    repos = {}

    def add(items, bucket):
        for it in items:
            r = repos.setdefault(it["full_name"], slim(it))
            if bucket not in r["matched"]:
                r["matched"].append(bucket)

    for t in TOPICS:
        add(search(f"topic:{t} stars:>5000 pushed:>{D30} archived:false"), f"established:{t}")
        add(search(f"topic:{t} created:>{D60} stars:>100 archived:false"), f"new:{t}")
    for k in KEYWORDS:
        add(search(f"{k} in:name,description created:>{D60} stars:>50 archived:false"), f"new-kw:{k}")
        add(search(f"{k} in:name,description stars:>2000 pushed:>{D14} archived:false"), f"established-kw:{k}")

    # Week-over-week star growth from our own snapshots.
    history = {}
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH) as f:
            history = json.load(f)
    for name, r in repos.items():
        h = history.setdefault(name, {})
        h[TODAY] = r["stars"]
        older = sorted(d for d in h if d <= D7)
        r["stars_7d_ago"] = h[older[-1]] if older else None
        r["stars_gained_7d"] = (r["stars"] - h[older[-1]]) if older else None
        r["growth_basis"] = "snapshot" if older else (
            "new repo: stars since creation" if r["age_days"] <= 10 else "no prior snapshot yet")
        if r["growth_basis"].startswith("new repo"):
            r["stars_gained_7d"] = r["stars"]
    cutoff = (NOW - timedelta(days=120)).date().isoformat()
    history = {n: {d: s for d, s in h.items() if d >= cutoff} for n, h in history.items()}
    history = {n: h for n, h in history.items() if h}

    # Latest release for repos active this week (capped to protect rate limit).
    active = sorted((r for r in repos.values() if r["pushed"] >= D7),
                    key=lambda r: r["stars"], reverse=True)[:80]
    for r in active:
        rel = get(f"/repos/{r['full_name']}/releases/latest")
        if rel:
            r["latest_release"] = {"tag": rel.get("tag_name"),
                                   "published": (rel.get("published_at") or "")[:10],
                                   "url": rel.get("html_url")}

    commits = {}
    for name in WATCH_COMMITS:
        items = get(f"/repos/{name}/commits", {"since": D7 + "T00:00:00Z", "per_page": 50}) or []
        commits[name] = [{"date": c["commit"]["author"]["date"][:10],
                          "message": c["commit"]["message"].split("\n")[0][:200],
                          "url": c["html_url"]} for c in items]

    all_repos = sorted(repos.values(), key=lambda r: r["stars"], reverse=True)
    risers = sorted((r for r in all_repos if r["stars_gained_7d"] is not None),
                    key=lambda r: r["stars_gained_7d"], reverse=True)
    out = {
        "generated_at": NOW.isoformat(timespec="seconds"),
        "window": {"d7": D7, "d14": D14, "d30": D30, "d60": D60},
        "notes": "stars_gained_7d comes from this repo's own weekly snapshots. "
                 "In the first week it is null except for repos under 10 days old.",
        "counts": {"repos": len(all_repos)},
        "top_by_stars": all_repos[:60],
        "top_risers_7d": risers[:60],
        "newest_fast": sorted((r for r in all_repos if r["age_days"] <= 60),
                              key=lambda r: r["stars_per_day_lifetime"], reverse=True)[:40],
        "watched_commits_7d": commits,
    }
    with open(os.path.join(DATA_DIR, "latest.json"), "w") as f:
        json.dump(out, f, indent=1)
    with open(os.path.join(DATA_DIR, f"{TODAY}.json"), "w") as f:
        json.dump(out, f, indent=1)
    with open(HISTORY_PATH, "w") as f:
        json.dump(history, f, separators=(",", ":"))
    print(f"wrote {len(all_repos)} repos")


if __name__ == "__main__":
    main()
