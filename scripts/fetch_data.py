#!/usr/bin/env python3
"""Refresh data/contributions.json from the GitHub GraphQL API (stdlib only).

Window: first day of the month 11 months ago -> now, so the monthly chart covers
12 whole calendar months. Featured repos = your pinned repositories.
Token: GH_TOKEN or GITHUB_TOKEN. Add a PAT with read:user as PROFILE_TOKEN
to count private contributions (the workflow prefers it when set).
"""
import datetime as dt
import json
import os
import sys
import urllib.request
from pathlib import Path

LOGIN = os.environ.get("PROFILE_LOGIN", "Abhimanyu254a")
OUT = Path(__file__).resolve().parent.parent / "data" / "contributions.json"

QUERY = """
query($login:String!,$from:DateTime!,$to:DateTime!){
  user(login:$login){
    contributionsCollection(from:$from,to:$to){
      contributionCalendar{weeks{contributionDays{date contributionCount}}}
    }
    pinnedItems(first:6,types:REPOSITORY){nodes{... on Repository{
      name description url stargazerCount forkCount primaryLanguage{name color}
    }}}
  }
}"""


def main():
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("set GH_TOKEN or GITHUB_TOKEN")
    now = dt.datetime.now(dt.timezone.utc)
    y, m = now.year, now.month - 11
    while m < 1:
        m, y = m + 12, y - 1
    variables = {"login": LOGIN, "from": f"{y}-{m:02d}-01T00:00:00Z", "to": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json", "User-Agent": "profile-cards"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.load(r)
    if res.get("errors") or not res.get("data", {}).get("user"):
        sys.exit(f"GraphQL error: {res.get('errors')}")
    u = res["data"]["user"]
    days = [{"date": d["date"], "count": d["contributionCount"]}
            for w in u["contributionsCollection"]["contributionCalendar"]["weeks"] for d in w["contributionDays"]]
    days = [d for d in days if d["date"] <= now.strftime("%Y-%m-%d")]
    repos = [{"name": n["name"], "description": n["description"], "url": n["url"],
              "stars": n["stargazerCount"], "forks": n["forkCount"], "language": n["primaryLanguage"]}
             for n in u["pinnedItems"]["nodes"]]
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps({"login": LOGIN, "generated": now.isoformat(timespec="seconds"),
                               "days": days, "repos": repos}, indent=1) + "\n")
    print(f"{len(days)} days, {len(repos)} pinned repos")


if __name__ == "__main__":
    main()
