#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""СОН-ПЕРИМЕТРА-0.1 · ночной сторож периметра (волна 2026-11-21, СИНГУЛЯР/СВЯЗЬ)

Совет района №5: «живое ядро — singulyar, cell-swarm, index, khozyain,
release-vault; остальным — СОН, не смерть: автоматы перевести на расписание
раз в сутки». Сторож — глаза периметра, не могильщик: раз в сутки тихо
обходит весь флот и записывает, кто на какой голове стоит. Сон — не смерть:
«не отвечает» значит «не отвечает», никаких похорон.

Режимы:
  check  — обход флота из son/fleet.json (или --only repo1,repo2) с выводом
           построчно: голова ветки → сверка с якорями registry/waves.json
           (СОВПАДАЕТ / УЕХАЛА / вне якорей) или «не отвечает»;
  write --out-dir D — тот же обход + датированный отчёт сон-2026-MM-DD.json
           и last.json (тот же отчёт; перезаписывается — это его собственный
           файл, допустимо). Если рядом лежит прошлый last.json — в отчёт
           добавляется сравнение «с_прошлой_ночи».

Якоря: https://raw.githubusercontent.com/rtynbirf/index/main/registry/waves.json
— берутся по сети БЕЗ ТОКЕНА. Токенов и секретов здесь нет и быть не может:
всё публичное. api.github.com на 80 репо не ходим (лимит без токена 60/час),
только git ls-remote — у него лимита нет. Вежливость: обход строго
последовательный, таймаут 15 с на репо, никакого параллельного шторма;
суммарное время обхода печатается.

Коды выхода:
  0 — обход прошёл, факты показаны. Дрейф (УЕХАЛА) — НОРМА автопилота,
      не ошибка: автоматы района пушат каждые минуты;
  2 — сверка сорвалась целиком: waves.json недоступен или не ответил ни
      один репо. Честно показываем и выходим с 2.
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FLEET_PATH = os.path.join(HERE, "fleet.json")

ORG = "rtynbirf"
WAVES_URL = "https://raw.githubusercontent.com/%s/index/main/registry/waves.json" % ORG
TIMEOUT_REPO = 15  # секунд на один git ls-remote — вежливость и предсказуемость


class WavesUnavailable(Exception):
    """waves.json недоступен — сверка с якорями невозможна (это не дрейф)."""


# ── входные данные: флот и якоря ─────────────────────────────────────────────

def load_fleet(path):
    """fleet.json: core (репо@ветка) + sleeping (репо). Проверяем структуру честно."""
    with open(path, encoding="utf-8") as f:
        fleet = json.load(f)
    core = fleet.get("core") or []
    sleeping = fleet.get("sleeping") or []
    for c in core:
        if not c.get("name") or not c.get("branch"):
            raise SystemExit("fleet.json: в core у каждого репо нужны name и branch")
    dupes = {c["name"] for c in core} & set(sleeping)
    if dupes:
        raise SystemExit("fleet.json: репо одновременно в core и sleeping: %s" % ", ".join(sorted(dupes)))
    return fleet, core, sleeping


def fetch_anchors():
    """Якоря волн из index@main registry/waves.json (raw, без токена).

    Возвращает (обновлено_реестра, число_волн, {(repo, branch): якорь}).
    Волны идут по порядку добавления (правило «только добавлять»), поэтому
    поздняя волна перезаписывает раннюю: сверяем с последним якорем ветки.
    """
    req = urllib.request.Request(WAVES_URL, headers={"User-Agent": "son-perimetra-0.1"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        raise WavesUnavailable("raw.githubusercontent.com вернул HTTP %s: %s" % (e.code, e.reason))
    except urllib.error.URLError as e:
        raise WavesUnavailable("raw.githubusercontent.com недоступен: %s" % e.reason)
    except ValueError as e:
        raise WavesUnavailable("waves.json не разобрался как JSON: %s" % str(e)[:120])

    anchors = {}
    waves = data.get("waves") or []
    for w in waves:
        for key, sha in (w.get("anchor") or {}).items():
            if "@" not in key or not sha:
                continue  # чужой формат ключа — пропускаем, не падаем
            repo, branch = key.split("@", 1)
            anchors[(repo, branch)] = {
                "sha": str(sha),
                "wave": w.get("id", ""),
                "date": w.get("date", ""),
            }
    return data.get("updated", ""), len(waves), anchors


# ── сеть: git ls-remote без токена, без лимитов ──────────────────────────────

def ls_remote(repo, ref, symref=False):
    """Голова ветки репо организации через git ls-remote (БЕЗ ТОКЕНА).

    symref=True — для спящих: заодно узнаём имя default_branch
    (строка «ref: refs/heads/<ветка>\tHEAD»). Таймаут 15 с.
    """
    cmd = ["git", "ls-remote"]
    if symref:
        cmd.append("--symref")
    cmd += ["https://github.com/%s/%s" % (ORG, repo), ref]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_REPO)
    except subprocess.TimeoutExpired:
        raise RuntimeError("таймаут %d с" % TIMEOUT_REPO)
    except OSError as e:
        raise RuntimeError("git не запустился: %s" % str(e)[:120])
    if out.returncode != 0:
        raise RuntimeError((out.stderr or "git ls-remote завершился с ошибкой").strip()[:200])

    sha, branch = None, None
    want = "refs/heads/%s" % ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
    for line in out.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] == "ref:" and len(parts) >= 3:
            branch = parts[1].replace("refs/heads/", "")  # symref-строка HEAD
            continue
        if len(parts) == 2 and parts[1] == want:
            sha = parts[0]
    if symref and sha is None:
        # HEAD без строки sha — репо пустое или совсем закрылось
        raise RuntimeError("HEAD не отдан ls-remote" + (" (пустое репо?)" if not out.stdout.strip() else ""))
    if not symref and sha is None:
        raise RuntimeError("ветка %s не найдена в ls-remote" % want)
    return sha, branch


# ── обход флота ──────────────────────────────────────────────────────────────

def survey(core, sleeping, anchors, anchors_ok, only=None):
    """Последовательный обход: core — по ветке из fleet.json, спящие — по HEAD."""
    targets = [(c["name"], c["branch"], True) for c in core]
    targets += [(n, None, False) for n in sleeping]
    if only:
        known = {t[0] for t in targets}
        unknown = [r for r in only if r not in known]
        if unknown:
            print("(--only: нет во флоте, пропущены: %s)" % ", ".join(unknown))
        targets = [t for t in targets if t[0] in set(only)]
        if not targets:
            raise SystemExit("--only: ни одно имя не нашлось во fleet.json")

    rows = []
    for repo, branch, is_core in targets:
        row = {"repo": repo, "ядро": is_core}
        try:
            if branch:
                sha, _ = ls_remote(repo, "refs/heads/%s" % branch)
                row["branch"] = branch
            else:
                sha, head_branch = ls_remote(repo, "HEAD", symref=True)
                row["branch"] = head_branch or "(default_branch не назван)"
            row["голова"] = sha
            a = anchors.get((repo, row["branch"]))
            if anchors_ok and a:
                match = sha.startswith(a["sha"]) or a["sha"].startswith(sha)
                row["якорь"] = a["sha"]
                row["волна_якоря"] = a["wave"]
                row["дата_якоря"] = a["date"]
                row["вердикт"] = "СОВПАДАЕТ" if match else "УЕХАЛА"
            elif anchors_ok:
                row["вердикт"] = "вне якорей (спит)"
                if is_core:
                    row["вердикт"] = "вне якорей (спит) — ядро, якоря в waves.json ещё нет"
            else:
                row["вердикт"] = "сверка с якорями невозможна (waves.json недоступен) — голова записана"
        except Exception as e:
            row["вердикт"] = "не отвечает"
            row["ошибка"] = str(e)[:200]
        rows.append(row)
    return rows


def print_rows(rows):
    for r in rows:
        label = "%s@%s" % (r["repo"], r.get("branch", "?"))
        if r["вердикт"] == "не отвечает":
            print("%s: НЕ ОТВЕЧАЕТ (%s) — сон периметра фиксирует, а не хоронит" % (label, r.get("ошибка", "?")))
        elif r["вердикт"] == "СОВПАДАЕТ":
            print("%s: якорь %s → голова %s → СОВПАДАЕТ (волна %s)" % (label, r["якорь"], r["голова"], r["волна_якоря"]))
        elif r["вердикт"] == "УЕХАЛА":
            print("%s: якорь %s → голова %s → УЕХАЛА (дрейф автоматов — норма; якорь от %s)"
                  % (label, r["якорь"], r["голова"], r["дата_якоря"]))
        else:
            print("%s: голова %s → %s" % (label, r["голова"], r["вердикт"]))


def delta_from_last(out_dir, rows):
    """Сравнение с прошлым last.json (если он лежит рядом): кто шевельнулся,
    кто замолчал. Дрейф считается не только по якорям, но и по прошлым ночам."""
    last_path = os.path.join(out_dir, "last.json")
    if not os.path.exists(last_path):
        return None
    try:
        with open(last_path, encoding="utf-8") as f:
            last = json.load(f)
    except (ValueError, OSError) as e:
        return {"база": "last.json не читается (%s)" % str(e)[:80], "замечено": []}
    prev = {r["repo"]: r for r in (last.get("совпадают") or []) + (last.get("уехали") or [])
            + (last.get("вне_якорей") or []) if r.get("голова")}
    silent_now = {r["repo"] for r in rows if r.get("вердикт") == "не отвечает"}
    changed, quiet = [], []
    for r in rows:
        head = r.get("голова")
        if not head:
            continue
        p = prev.get(r["repo"])
        if p is None:
            continue
        if r["repo"] in silent_now:
            continue
        if p["голова"] != head:
            changed.append({"repo": r["repo"], "ветка": r.get("branch"),
                            "было": p["голова"], "стало": head})
        elif p.get("вердикт") == "не отвечает":
            changed.append({"repo": r["repo"], "ветка": r.get("branch"),
                            "было": p["голова"], "стало": head, "пометка": "ночью молчал — теперь отвечает"})
    prev_silent = {r.get("repo") for r in (last.get("не_отвечает") or [])}
    for r in rows:
        if r.get("вердикт") == "не отвечает" and r["repo"] in prev_silent:
            quiet.append(r["repo"])
    return {"база": last.get("когда", "?"), "голова_сменилась": changed,
            "молчал_и_в_прошлый_раз": quiet}


def write_report(out_dir, fleet, anchors_meta, anchors_ok, rows, started, elapsed):
    """Датированный отчёт сон-2026-MM-DD.json + last.json (перезапись — допустимо)."""
    matched = [r for r in rows if r["вердикт"] == "СОВПАДАЕТ"]
    drifted = [r for r in rows if r["вердикт"] == "УЕХАЛА"]
    outside = [r for r in rows if r["вердикт"].startswith("вне якорей")]
    incomparable = [r for r in rows if r["вердикт"].startswith("сверка с якорями невозможна")]
    silent = [r for r in rows if r["вердикт"] == "не отвечает"]

    report = {
        "schema": "сон-периметра 0.1 · ночной обход флота; сон — не смерть; "
                  "«не отвечает» ≠ «умер»; дрейф автоматов — норма",
        "когда": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "флот": {
            "org": ORG,
            "всего_во_флоте": len(fleet["core"]) + len(fleet["sleeping"]),
            "ядро": len(fleet["core"]),
            "спящие": len(fleet["sleeping"]),
            "в_обходе": len(rows),
        },
        "якоря": {
            "источник": "index@main registry/waves.json (raw, без токена)",
            "доступен": anchors_ok,
            "волн_в_реестре": anchors_meta[1],
            "реестр_обновлён": anchors_meta[0],
        },
        "обход": {
            "отвечено": len(rows) - len(silent),
            "не_отвечает": len(silent),
            "время_обхода_сек": round(elapsed, 1),
            "вежливость": "последовательный обход, таймаут %d с на репо, без параллельного шторма" % TIMEOUT_REPO,
        },
        "совпадают": matched,
        "уехали": drifted,
        "вне_якорей": outside,
        "сверка_невозможна": incomparable,
        "не_отвечает": silent,
        "с_прошлой_ночи": delta_from_last(out_dir, rows),
        "границы": [
            "сторож ходит git ls-remote без токена — SHA голов видит, но даты последних "
            "пушей не видит: дрейф считается относительно якорей waves.json и прошлых отчётов",
            "«не отвечает» — не приговор: таймаут/сеть; сон периметра фиксирует, а не хоронит",
            "артефакт прогона Actions живёт ~90 дней; для вечной истории отчётов — коммит "
            "в репо (шаблон шага — в комментарии workflow)",
        ],
    }

    os.makedirs(out_dir, exist_ok=True)
    dated = os.path.join(out_dir, "сон-%s.json" % started.strftime("%Y-%m-%d"))
    for path in (dated, os.path.join(out_dir, "last.json")):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
            f.write("\n")
    return dated


def summarize(rows, elapsed):
    n_match = sum(1 for r in rows if r["вердикт"] == "СОВПАДАЕТ")
    n_drift = sum(1 for r in rows if r["вердикт"] == "УЕХАЛА")
    n_out = sum(1 for r in rows if r["вердикт"].startswith("вне якорей"))
    n_inc = sum(1 for r in rows if r["вердикт"].startswith("сверка с якорями невозможна"))
    n_silent = sum(1 for r in rows if r["вердикт"] == "не отвечает")
    print("Итог обхода: совпадает %d · уехало %d (дрейф — норма) · вне якорей %d · "
          "сверка невозможна %d · не отвечает %d" % (n_match, n_drift, n_out, n_inc, n_silent))
    print("время обхода: %.1f с" % elapsed)
    return n_silent


# ── режимы ───────────────────────────────────────────────────────────────────

def run(mode, args):
    fleet, core, sleeping = load_fleet(args.fleet)
    only = [s.strip() for s in args.only.split(",") if s.strip()] if args.only else None

    anchors_ok = True
    try:
        anchors_meta, anchors = None, None
        updated, n_waves, anchors = fetch_anchors()
        anchors_meta = (updated, n_waves)
        print("якоря: index@main registry/waves.json · волн: %d · реестр обновлён: %s" % (n_waves, updated))
    except WavesUnavailable as e:
        anchors_ok = False
        anchors_meta = ("?", 0)
        print("ЯКОРЯ НЕДОСТУПНЫ (%s) — головы запишем, сверку не обещаем." % str(e)[:160])

    started = datetime.datetime.now(datetime.timezone.utc)
    print("СОН-ПЕРИМЕТРА · обход флота %s: %d репо (ядро %d, спящие %d)%s"
          % (ORG, len(core) + len(sleeping), len(core), len(sleeping),
             " · только: %s" % ", ".join(only) if only else ""))
    rows = survey(core, sleeping, anchors or {}, anchors_ok, only=only)
    elapsed = (datetime.datetime.now(datetime.timezone.utc) - started).total_seconds()
    print_rows(rows)
    silent = summarize(rows, elapsed)

    if mode == "write":
        dated = write_report(args.out_dir, fleet, anchors_meta, anchors_ok, rows, started, elapsed)
        print("отчёт: %s" % os.path.abspath(dated))
        print("отчёт: %s (last.json перезаписан — это его собственный файл)" % os.path.abspath(os.path.join(args.out_dir, "last.json")))

    answered = sum(1 for r in rows if r["вердикт"] != "не отвечает")
    if not anchors_ok:
        print("код 2: сверка с якорями невозможна — waves.json недоступен (не дрейф).")
        return 2
    if answered == 0:
        print("код 2: не ответил ни один репо — периметр не виден (сеть?).")
        return 2
    print("код 0: обход прошёл, факты показаны (дрейф — норма, не ошибка).")
    return 0


def main():
    p = argparse.ArgumentParser(description="СОН-ПЕРИМЕТРА-0.1 · ночной сторож периметра (без токена)")
    sub = p.add_subparsers(dest="mode", required=True)

    pc = sub.add_parser("check", help="обход и построчная сверка, без файлов")
    pc.add_argument("--only", help="подмножество репо через запятую", default=None)
    pc.add_argument("--fleet", help="путь к fleet.json", default=FLEET_PATH)

    pw = sub.add_parser("write", help="обход + датированный отчёт + last.json")
    pw.add_argument("--out-dir", required=True, help="куда писать отчёты")
    pw.add_argument("--only", help="подмножество репо через запятую", default=None)
    pw.add_argument("--fleet", help="путь к fleet.json", default=FLEET_PATH)

    args = p.parse_args()
    return run(args.mode, args)


if __name__ == "__main__":
    sys.exit(main())
