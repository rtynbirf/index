#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""Линтер воркфлоу дома: запрет склеек «uses:…#…with:» (А1-5, П-23/#41 «Оракул корня»).

Корень инцидента 11-06: пин-скрипт склеил комментарий пина с ключом `with:`
на одной строке (`# vXwith:`) → PyYAML/GitHub «Invalid workflow file», 0 jobs,
guardian-tripwire краснел одним корнем на 6 телах.

Канон теперь: строка с `uses:` НЕ содержит `with:` нигде после `#`
(комментарий пина обязан заканчиваться переводом строки, `with:` — на своей).

Проверки (fail-closed):
  1. YAML-парс: каждый *.yml/*.yaml воркфлоу обязан парситься (yaml.safe_load);
     парсера нет (PyYAML отсутствует) — файл проверяется только текстово, помечается.
  2. Склейка: regex `uses:\S+@\S+\s*#.*\bwith:` на сыром тексте → дефект.
  3. Опечатка склейки `Xwith:`/`Xwith :` (след старого пин-скрипта) → дефект.

Режимы:
  --selftest          без сети: встроенный дефектный шаблон отвергнут, годный прошёл;
  --dir DIR           линт всех воркфлоу локального дерева (по умолчанию cwd);
  --all               живой обход: все не-архивные репо роя через API (нужен токен);
  --repos R1,R2       ограничить живой обход списком.

Гейт CI: exit 1, если есть дефекты или ошибки проверки. Только stdlib
(PyYAML — опционально: если есть, включается YAML-парс). Токен из env
GITHUB_TOKEN/GH_TOKEN; без токена --all — exit 2.

Вывод: JSON (--json FILE) + таблица stdout. Числа считаются, не декларируются.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

OWNER = "rtynbirf"
API_ROOT = "https://api.github.com"
PER_PAGE = 100

# Дефект «Оракула корня»: uses: … @sha # vX(склейка)with: …  — всё на одной строке.
GLUED_USES_WITH = re.compile(r"^\s*uses:\s*\S+@\S+.*#.*\bwith:", re.IGNORECASE)
# След старого пин-скрипта: буквальная склейка комментария пина с ключом.
GLUED_XWITH = re.compile(r"\bv\d+with\b", re.IGNORECASE)


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def take_token():
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def lint_text(raw, name):
    """Текстовые проверки одной строки файла. Возвращает список дефектов."""
    defects = []
    for i, line in enumerate(raw.splitlines(), 1):
        if GLUED_USES_WITH.search(line):
            defects.append("%s:%d: склейка uses:…#…with: на одной строке" % (name, i))
        if GLUED_XWITH.search(line):
            defects.append("%s:%d: след пин-склейки «Xwith»" % (name, i))
    return defects


def lint_file(path):
    """Полный линт одного файла: YAML-парс (если есть PyYAML) + текстовые проверки."""
    raw = open(path, encoding="utf-8", errors="replace").read()
    defects = lint_text(raw, os.path.basename(path))
    yaml_ok = None
    try:
        import yaml  # noqa: F401
        have_yaml = True
    except ImportError:
        have_yaml = False
    if have_yaml and not defects:
        import yaml
        try:
            yaml.safe_load(raw)
            yaml_ok = True
        except yaml.YAMLError as exc:
            yaml_ok = False
            defects.append("%s: YAML не парсится: %s" % (os.path.basename(path), exc))
    return {"path": path, "defects": defects, "yaml_ok": yaml_ok}


def find_workflow_files(root):
    """Все .github/workflows/*.y|aml локального дерева."""
    out = []
    wf_root = os.path.join(root, ".github", "workflows")
    if os.path.isdir(wf_root):
        for name in sorted(os.listdir(wf_root)):
            if name.endswith((".yml", ".yaml")):
                out.append(os.path.join(wf_root, name))
    return out


def api_get(token, url, raw=False):
    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("Accept", "application/vnd.github.raw" if raw
                   else "application/vnd.github+json")
    req.add_header("User-Agent", "polimat-hub-workflow-lint")
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read()
            return body.decode("utf-8", errors="replace") if raw \
                else json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def list_repos(token, only=None):
    repos = []
    page = 1
    while True:
        batch = api_get(token, "%s/user/repos?type=owner&per_page=%d&page=%d"
                        % (API_ROOT, PER_PAGE, page))
        if not batch:
            break
        for item in batch:
            if item.get("archived"):
                continue
            if only and item["name"] not in only:
                continue
            repos.append({"name": item["name"],
                          "default_branch": item.get("default_branch", "main")})
        if len(batch) < PER_PAGE:
            break
        page += 1
    return repos


def scan_repo_workflows(token, name, ref):
    """Живой линт воркфлоу одного репо через деревья API. → (записей-файлов, дефектов, ошибок)."""
    ref_q = urllib.parse.quote(ref, safe="")
    tree = api_get(token, "%s/repos/%s/%s/git/trees/%s?recursive=1"
                   % (API_ROOT, OWNER, urllib.parse.quote(name, safe=""), ref_q))
    if tree is None:
        return [], ["%s: дерево %s не получено (404)" % (name, ref)]
    files = [t["path"] for t in tree.get("tree", [])
             if t.get("type") == "blob"
             and t["path"].startswith(".github/workflows/")
             and t["path"].endswith((".yml", ".yaml"))]
    all_defects = []
    for path in files:
        path_q = urllib.parse.quote(path, safe="/")
        raw = api_get(token, "%s/repos/%s/%s/contents/%s?ref=%s"
                      % (API_ROOT, OWNER, urllib.parse.quote(name, safe=""), path_q, ref_q),
                      raw=True)
        if raw is None:
            all_defects.append("%s/%s: содержимое не получено (404)" % (name, path))
            continue
        all_defects.extend(lint_text(raw, "%s/%s" % (name, path)))
        # YAML-парс живого текста — тот же канон, что в локальном режиме
        try:
            import yaml  # noqa: F401
        except ImportError:
            continue
        import yaml
        try:
            yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            all_defects.append("%s/%s: YAML не парсится: %s" % (name, path, exc))
    return files, all_defects, []


def selftest():
    """Без сети: дефектный шаблон отвергнут, годный прошёл (приёмка А1-5)."""
    good = (
        "name: good\n"
        "on: workflow_dispatch\n"
        "jobs:\n"
        "  j:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4\n"
        "        with:\n"
        "          fetch-depth: 1\n"
    )
    bad = (
        "name: bad\n"
        "on: workflow_dispatch\n"
        "jobs:\n"
        "  j:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4with:\n"
        "          fetch-depth: 1\n"
    )
    assert lint_text(good, "good.yml") == [], "годный шаблон не должен краснеть"
    bad_defects = lint_text(bad, "bad.yml")
    assert bad_defects, "дефектный шаблон НЕ отвергнут — линтер слеп"
    # шаблон с дефектом обязан отваливаться и на YAML-парсе
    import yaml
    try:
        yaml.safe_load(bad)
        parsed = True
    except yaml.YAMLError:
        parsed = False
    assert not parsed, "дефектная склейка обязана ломать YAML-парс"

    import tempfile
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, ".github", "workflows"))
    with open(os.path.join(root, ".github", "workflows", "good.yml"), "w") as fh:
        fh.write(good)
    with open(os.path.join(root, ".github", "workflows", "bad.yml"), "w") as fh:
        fh.write(bad)
    files = find_workflow_files(root)
    assert len(files) == 2, "поиск воркфлоу нашёл не все файлы"
    results = [lint_file(p) for p in files]
    bad_results = [r for r in results if r["defects"]]
    assert len(bad_results) == 1, "должен быть отвергнут ровно один дефектный шаблон"

    print("SELFTEST OK: дефектный шаблон отвергнут (%d дефектов), годный прошёл"
          % len(bad_defects))


def main():
    parser = argparse.ArgumentParser(
        description="Линтер воркфлоу: запрет склеек uses:…#…with: (А1-5)")
    parser.add_argument("--selftest", action="store_true",
                        help="самопроверка без сети")
    parser.add_argument("--dir", metavar="DIR", default=None,
                        help="линт локального дерева (воркфлоу DIR)")
    parser.add_argument("--all", action="store_true",
                        help="живой обход всех не-архивных репо роя через API")
    parser.add_argument("--repos", metavar="R1,R2", default=None,
                        help="ограничить живой обход списком")
    parser.add_argument("--json", dest="json_path", metavar="FILE", default=None,
                        help="куда записать JSON-отчёт")
    args = parser.parse_args()

    if args.selftest:
        selftest()
        return

    all_defects = []
    errors = []
    scanned = []

    if args.all:
        token = take_token()
        if not token:
            sys.stderr.write("ЖИВОЙ ЛИНТ НЕВОЗМОЖЕН: нет токена (exit 2)\n")
            raise SystemExit(2)
        only = {x.strip() for x in args.repos.split(",")} if args.repos else None
        repos = list_repos(token, only)
        for r in repos:
            try:
                files, defects, errs = scan_repo_workflows(token, r["name"],
                                                           r["default_branch"])
            except (urllib.error.HTTPError, RuntimeError) as exc:
                errors.append("%s: API: %s" % (r["name"], exc))
                continue
            scanned.append({"repo": r["name"], "workflows": len(files),
                            "defects": len(defects)})
            all_defects.extend(defects)
            errors.extend(errs)
    else:
        root = args.dir or os.getcwd()
        files = find_workflow_files(root)
        results = [lint_file(p) for p in files]
        scanned.append({"repo": "(local:%s)" % root, "workflows": len(files),
                        "defects": sum(1 for r in results if r["defects"])})
        for r in results:
            all_defects.extend(r["defects"])

    report = {
        "generated": utc_now_iso(),
        "mode": "all" if args.all else "dir",
        "workflows_scanned": sum(s["workflows"] for s in scanned),
        "repos_scanned": len(scanned),
        "defects": len(all_defects),
        "errors": len(errors),
        "scanned": scanned,
        "all_defects": all_defects,
        "all_errors": errors,
    }
    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        sys.stderr.write("JSON-отчёт: %s (%d байт)\n"
                         % (args.json_path, os.path.getsize(args.json_path)))

    sys.stdout.write("## Линтер воркфлоу (А1-5) · %s\n" % report["generated"])
    sys.stdout.write("Воркфлоу просканировано: %d · репо: %d · дефектов: %d · ошибок: %d\n"
                     % (report["workflows_scanned"], report["repos_scanned"],
                        report["defects"], report["errors"]))
    for d in all_defects:
        sys.stdout.write("  ДЕФЕКТ: %s\n" % d)
    for e in errors:
        sys.stdout.write("  ОШИБКА: %s\n" % e)

    if all_defects or errors:
        sys.stderr.write("ГАЙТ: дефектов %d, ошибок %d — машина отвергнута\n"
                         % (len(all_defects), len(errors)))
        raise SystemExit(1)
    sys.stdout.write("ГАЙТ ЗЕЛЁНЫЙ: склеек uses:…#…with: нет, все воркфлоу проходят\n")
    raise SystemExit(0)


if __name__ == "__main__":
    main()
