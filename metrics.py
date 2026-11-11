#!/usr/bin/env python3
"""metrics.py — АВТОМАТ живых цифр аккаунта rtynbirf (живёт в репо index).
Каждый час GitHub Actions вызывает его: меряет все репо через API и коммитит
STATUS.md (человекочитаемый реестр) + metrics.json (машинный SSOT).
Токен: env METRICS_TOKEN (PAT, видит всё) или GITHUB_TOKEN (Actions). stdlib only."""
import base64, json, os, sys, time, urllib.request, urllib.error

OWNER = "rtynbirf"
TOKEN = os.environ.get("METRICS_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
API = "https://api.github.com"

def api(method, path, body=None):
    req = urllib.request.Request(API + path, method=method, headers={
        "Authorization": f"token {TOKEN}", "Accept": "application/vnd.github+json",
        "User-Agent": "rtynbirf-metrics-bot"},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            b = r.read()
            return r.status, (json.loads(b) if b else None)
    except urllib.error.HTTPError as e:
        return e.code, None

def main():
    repos, page = [], 1
    while True:
        st, chunk = api("GET", f"/users/{OWNER}/repos?per_page=100&page={page}")
        if st != 200 or not chunk:
            break
        repos += chunk
        if len(chunk) < 100:
            break
        page += 1
    if not repos:
        print("не удалось получить список репо"); sys.exit(1)

    items = []
    for r in repos:
        items.append({
            "name": r["name"], "size_kb": r["size"], "archived": r["archived"],
            "fork": r["fork"], "description": (r.get("description") or "").strip(),
            "topics": r.get("topics") or [], "pushed_at": r["pushed_at"][:10],
        })
    items.sort(key=lambda x: (x["archived"], -x["size_kb"]))

    live = [x for x in items if not x["archived"]]
    arch = [x for x in items if x["archived"]]
    mb = lambda kb: kb / 1024.0
    total_kb = sum(x["size_kb"] for x in items)
    now = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())

    # недоделки: топики<3, нет описания, ЖИР>1МБ
    todo = []
    t_short = [x["name"] for x in live if len(x["topics"]) < 3]
    if t_short:
        todo.append(("детелинг", f"topics<3: {len(t_short)} живых — " + ", ".join(t_short[:6]) + ("…" if len(t_short) > 6 else "")))
    no_desc = [x["name"] for x in live if not x["description"]]
    if no_desc:
        todo.append(("детелинг", "нет описания: " + ", ".join(no_desc)))
    fat = [x for x in live if x["size_kb"] > 1024]
    if fat:
        todo.append(("клининг", "FAT>1МБ: " + ", ".join(f"{x['name']} {mb(x['size_kb']):.0f}МБ" for x in fat)))

    L = []
    L.append(f"# STATUS — живой реестр аккаунта rtynbirf")
    L.append("")
    L.append(f"Пересчитано автоматом: **{now}** (cron каждый час). Это не декларация — это замер.")
    L.append("")
    L.append(f"**Репо: {len(items)}** (живых {len(live)}, архив {len(arch)}) · **Вес: {mb(total_kb):.0f} МБ**")
    L.append("")
    L.append("## Живые")
    L.append("")
    L.append("| Репо | МБ | topics | описание, зн | push |")
    L.append("|---|---|---|---|---|")
    for x in live:
        L.append(f"| [{x['name']}](https://github.com/rtynbirf/{x['name']}) | {mb(x['size_kb']):.1f} | {len(x['topics'])} | {len(x['description'])} | {x['pushed_at']} |")
    L.append("")
    L.append("## Недоделки (авто-контроль)")
    L.append("")
    if todo:
        for kind, what in todo:
            L.append(f"- **{kind}:** {what}")
    else:
        L.append("- пусто — всё по канону")
    L.append("")
    L.append("## Архив (43)")
    L.append("")
    L.append("<details><summary>развернуть</summary>")
    L.append("")
    L.append("| Репо | МБ |")
    L.append("|---|---|")
    for x in arch:
        L.append(f"| {x['name']} | {mb(x['size_kb']):.1f} |")
    L.append("")
    L.append("</details>")
    L.append("")
    L.append("---")
    L.append("Канон правил: [`.github/AGENTS.md`](https://github.com/rtynbirf/.github/blob/main/AGENTS.md) · "
             "SSOT: [`projects.json`](./projects.json) · Протокол клининга v4.")
    status_md = "\n".join(L) + "\n"

    metrics = {"schema": 1, "owner": OWNER, "measured": now,
               "total_repos": len(items), "live": len(live), "archived": len(arch),
               "total_kb": total_kb, "todo": [{"kind": k, "what": w} for k, w in todo],
               "repos": items}

    def put(path, text, msg):
        st2, cur = api("GET", f"/repos/{OWNER}/index/contents/{path}")
        body = {"message": msg, "content": base64.b64encode(text.encode()).decode(),
                "committer": {"name": "metrics-bot", "email": "metrics-bot@users.noreply.github.com"}}
        if st2 == 200 and cur:
            body["sha"] = cur["sha"]
        st3, _ = api("PUT", f"/repos/{OWNER}/index/contents/{path}", body)
        print(f"{path}: HTTP {st3}")
        return st3 in (200, 201)

    ok = put("STATUS.md", status_md, f"metrics: автообновление {now}") and \
         put("metrics.json", json.dumps(metrics, ensure_ascii=False, indent=1), f"metrics: автообновление {now}")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
