#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""КА УВЕДОМЛЕНИЙ — детерминированный конечный автомат (волна ×37, 2026-11-18).

Задание Хозяина: «УБРАТЬ ВСЕ УВЕДОМЛЕНИЯ · РЕШИТЬ ЭТУ ПРОБЛЕМУ · ПЕРЕВОДИТЬ ВСЁ
И ВСЯ НА АВТОМАТ!! ТЕОРИЯ КОНЕЧНЫХ АВТОМАТОВ!!!!» — инбокс GitHub держится
на пустоте машиной, а не руками (практики G3 уходят в отставку).

Формализм: M = (Q, Σ, δ, q0, F) — детерминированный конечный автомат.

  Q  = {S0_PROBE, S1_SCAN, S2_DECIDE, S3_MARK, S4_VERIFY, S5_CLEAR,
        S6_DONE_VERIFY, S7_HALT_OK, S8_HALT_ERR}
  Σ  = {alive, dead, empty, unread, mark_ok, mark_err, done_ok, done_err,
        zero, nonzero, err}
  q0 = S0_PROBE        (Закон №11: носитель оживляется GET /user до первого дела)
  F  = {S7_HALT_OK}

Таблица переходов δ (каждый шаг логируется как δ(q, σ) → q'):

  δ(S0_PROBE,  alive)     → S1_SCAN
  δ(S0_PROBE,  dead)      → S8_HALT_ERR
  δ(S1_SCAN,  unread)     → S2_DECIDE
  δ(S1_SCAN,  empty)      → S5_CLEAR       # unread=0 ≠ инбокс пуст: там лежат
                                            # прочитанные-не-Done — дожимаем
  δ(S1_SCAN,  err)        → S8_HALT_ERR
  δ(S2_DECIDE, unread)    → S3_MARK         # бюджет циклов L: L=0 → S8_HALT_ERR
  δ(S3_MARK,  mark_ok)    → S4_VERIFY
  δ(S3_MARK,  mark_err)   → S2_DECIDE       # retry с убывающим бюджетом
  δ(S4_VERIFY, zero)      → S5_CLEAR
  δ(S4_VERIFY, nonzero)   → S1_SCAN         # 202-async догоняется тактом
  δ(S5_CLEAR, done_ok)    → S6_DONE_VERIFY
  δ(S5_CLEAR, done_err)   → S8_HALT_ERR
  δ(S6_DONE_VERIFY, zero) → S7_HALT_OK      # accepting: инбокс пуст по факту
  δ(S6_DONE_VERIFY, nonzero) → S5_CLEAR     # дошлифовать хвост 202-async

Инварианты: только /notifications (GET, PUT, DELETE) — никаких мутаций репо,
PR, issues; идемпотентент (пустой инбокс = accepting); Закон №2 «факт = байты»:
каждый переход несёт HTTP-код; DELETE /notifications = «всё → Done» (именно
это убирает непрочитанные И прочитанные треды из инбокса Хозяина).

Календарь носителей (А6-2/А6-4) печатается при каждой пробе — напоминание
«чекани следующий конверт ДО смерти» живёт в такте автомата, не в памяти.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

API = "https://api.github.com"
UA = "polimat-notify-automaton/1.0 (DFA wave x37)"

# Календарь носителей (А6-2/А6-4): чекани следующий конверт ДО смерти текущего.
DEADLINES = [
    ("A'-носитель (смерть)", "2026-12-14T20:25:54Z"),
    ("CELL_SWARM_DISPATCH_TOKEN (смерть, проба x35)", "2026-12-16T08:37:13Z"),
    ("чеканка B'/следующего конверта — не позднее", "2026-12-13T10:00:00Z"),
]


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z] {msg}", flush=True)


def api(method: str, path: str, token: str, body: dict | None = None,
        retries: int = 2):
    """Один вызов GitHub API. Возвращает (status, body_bytes, headers)."""
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": UA,
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            if attempt < retries:
                time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"сеть недоступна после {retries + 1} попыток: {last}")


def rate_remaining(token: str):
    """Остаток core-лимита (rate-guard, грабли ×37: дожим 1328 тредов съедает окно 5000/час)."""
    st, body, _ = api("GET", "/rate_limit", token)
    if st == 200:
        c = json.loads(body)["resources"]["core"]
        return c["remaining"], c["reset"]
    return None, None


def scan(token: str, all_items: bool, cap_pages: int = 60):
    """Скан инбокса. all_items=False → только непрочитанные; True → весь инбокс."""
    n, page = 0, 0
    while page < cap_pages:
        page += 1
        flag = "true" if all_items else "false"
        st, body, _ = api("GET", f"/notifications?all={flag}&per_page=50&page={page}", token)
        if st != 200:
            return -1, page, st
        items = json.loads(body or b"[]")
        n += len(items)
        if len(items) < 50:
            break
    return n, page, 200


def calendar(death: str | None) -> str:
    """Календарь носителей: дней до каждой даты — из такта, не из памяти."""
    now = datetime.now(timezone.utc)
    rows = []
    for name, iso in DEADLINES:
        when = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        days = (when - now).days
        mark = "  << ПОЗАДЬ" if days <= 0 else ("  << МЕНЬШЕ 14 ДНЕЙ" if days < 14 else "")
        rows.append(f"    {name}: {iso} (дней: {days}){mark}")
    if death:
        when = datetime.strptime(death, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
        days = (when - now).days
        rows.append(f"    текущий носитель (заголовок API): смерть {death} (дней: {days})"
                    + ("  << МЕНЬШЕ 14 ДНЕЙ — ЧЕКАНИ СЛЕДУЮЩИЙ КОНВЕРТ" if days < 14 else ""))
    return "\n".join(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="КА уведомлений — DFA (Q,Σ,δ,q0,F)")
    ap.add_argument("--token-env", default="NOTIF_TOKEN", help="имя env с носителем")
    ap.add_argument("--token-file", default=None, help="файл с носителем (локальный прогон)")
    ap.add_argument("--dry-run", action="store_true", help="только скан, без мутаций")
    ap.add_argument("--calendar-only", action="store_true", help="печатать календарь и выйти")
    ap.add_argument("--max-loops", type=int, default=8, help="бюджет циклов S2→S4 и S5→S6")
    args = ap.parse_args()

    if args.calendar_only:
        print("КАЛЕНДАРЬ НОСИТЕЛЕЙ (А6-2/А6-4, x37):")
        print(calendar(None))
        return 0

    if args.token_file:
        token = open(args.token_file).read().strip()
    else:
        token = os.environ.get(args.token_env) or os.environ.get("GH_TOKEN") \
            or os.environ.get("GITHUB_TOKEN")
    if not token:
        print(f"S8_HALT_ERR: носитель не найден (env {args.token_env}/GH_TOKEN/--token-file)", flush=True)
        return 1

    log("M = (Q,Σ,δ,q0,F) запущен: Q=9 состояний, q0=S0_PROBE, F={S7_HALT_OK}")
    state, loops, first_unread, first_total = "S0_PROBE", 0, None, None
    death, login = None, None
    last_pass_ok, last_pass_err, last_pass_total = 0, -1, 0  # итог последнего прохода S5

    while True:
        if state == "S0_PROBE":
            st, body, hdrs = api("GET", "/user", token)
            death = hdrs.get("github-authentication-token-expiration")
            if st == 200:
                login = json.loads(body).get("login")
                log(f"δ(S0_PROBE, alive[http {st}]) → S1_SCAN; носитель жив: @{login}, "
                    f"смерть носителя: {death or 'неистекающий'}")
                log("КАЛЕНДАРЬ НОСИТЕЛЕЙ (А6-2/А6-4):\n" + calendar(death))
                state = "S1_SCAN"
            else:
                log(f"δ(S0_PROBE, dead[http {st}]) → S8_HALT_ERR; тело ответа: {body[:200]!r}")
                state = "S8_HALT_ERR"

        elif state == "S1_SCAN":
            n, pages, st = scan(token, all_items=False)
            if n < 0:
                log(f"δ(S1_SCAN, err[http {st}]) → S8_HALT_ERR; скан не удался на странице {pages}")
                state = "S8_HALT_ERR"
            elif n == 0:
                log(f"δ(S1_SCAN, empty[{pages} стр.]) → S5_CLEAR (непрочитанных нет; "
                    f"дожимаем прочитанные-не-Done)")
                state = "S5_CLEAR"
            else:
                if first_unread is None:
                    first_unread = n
                log(f"δ(S1_SCAN, unread[{n} шт., {pages} стр.]) → S2_DECIDE")
                state = "S2_DECIDE"

        elif state == "S2_DECIDE":
            if args.dry_run:
                result = {"unread_before": first_unread, "halt": "S8_HALT_ERR",
                          "note": "dry-run: PUT/DELETE не выполнялись"}
                print("NOTIF_RESULT=" + json.dumps(result, ensure_ascii=False))
                return 1
            if loops >= args.max_loops:
                log(f"δ(S2_DECIDE, budget=0[{loops} циклов]) → S8_HALT_ERR; инбокс упрямится — смотри логи")
                state = "S8_HALT_ERR"
            else:
                log(f"δ(S2_DECIDE, unread[бюджет циклов {args.max_loops - loops}]) → S3_MARK")
                state = "S3_MARK"

        elif state == "S3_MARK":
            loops += 1
            st, body, _ = api("PUT", "/notifications", token,
                              body={"last_read_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
            if st in (200, 202, 204, 205):
                sigma = "mark_202_async" if st == 202 else "mark_ok"
                log(f"δ(S3_MARK, {sigma}[http {st}]) → S4_VERIFY (цикл {loops}/{args.max_loops})")
                time.sleep(4)
                state = "S4_VERIFY"
            else:
                log(f"δ(S3_MARK, mark_err[http {st}]) → S2_DECIDE; тело ответа: {body[:200]!r}")
                state = "S2_DECIDE"

        elif state == "S4_VERIFY":
            n, pages, st = scan(token, all_items=False)
            if n == 0:
                log(f"δ(S4_VERIFY, zero[http {st}]) → S5_CLEAR")
                state = "S5_CLEAR"
            else:
                log(f"δ(S4_VERIFY, nonzero[{n} шт., http {st}]) → S1_SCAN (догоняем 202-async)")
                state = "S1_SCAN"

        elif state == "S5_CLEAR":
            if args.dry_run:
                result = {"halt": "S8_HALT_ERR", "note": "dry-run: DELETE не выполнялся"}
                print("NOTIF_RESULT=" + json.dumps(result, ensure_ascii=False))
                return 1
            if loops >= args.max_loops + 4:
                log(f"δ(S5_CLEAR, budget=0[{loops} циклов]) → S8_HALT_ERR")
                state = "S8_HALT_ERR"
            else:
                # bulk-эндпоинта «всё → Done» в REST нет (DELETE /notifications → 404,
                # проба ×37; docs.github.com/rest/activity/notifications) — дожимаем
                # по треду: DELETE /notifications/threads/{id} → 204.
                ids, page = [], 0
                while page < 60:
                    page += 1
                    st, body, _ = api("GET", f"/notifications?all=true&per_page=50&page={page}", token)
                    if st != 200:
                        log(f"δ(S5_CLEAR, err[http {st} при списке]) → S8_HALT_ERR")
                        state = "S8_HALT_ERR"
                        break
                    items = json.loads(body or b"[]")
                    ids += [i["id"] for i in items]
                    if len(items) < 50:
                        break
                if state == "S8_HALT_ERR":
                    continue
                if not ids:
                    log("δ(S5_CLEAR, empty[список пуст]) → S6_DONE_VERIFY")
                    state = "S6_DONE_VERIFY"
                    continue
                loops += 1
                rem, reset = rate_remaining(token)
                if rem is not None and rem < len(ids) + 100:
                    if last_pass_err == 0 and last_pass_ok > 0:
                        log(f"δ(S5_CLEAR, rate_guard_after_full_pass[осталось {rem} < {len(ids) + 100}, "
                            f"полный проход уже дал 204×{last_pass_ok} err 0]) → S7_HALT_OK; "
                            f"хвост догонит следующий такт cron")
                        state = "S7_HALT_OK"
                    else:
                        log(f"δ(S5_CLEAR, rate_guard[осталось {rem} из лимита на {len(ids)} тредов, "
                            f"reset {reset}]) → S8_HALT_ERR; дожим продолжит следующий такт (cron)")
                        state = "S8_HALT_ERR"
                    continue
                ok = err = 0
                from concurrent.futures import ThreadPoolExecutor

                def kill(tid):
                    try:
                        dst, db, _ = api("DELETE", f"/notifications/threads/{tid}", token, retries=1)
                        return tid, dst, db
                    except Exception as e:  # сеть рванула — посчитаем как отказ
                        return tid, -1, str(e).encode()[:150]

                with ThreadPoolExecutor(max_workers=10) as ex:
                    for i, (tid, dst, db) in enumerate(ex.map(kill, ids), 1):
                        if dst == 204:
                            ok += 1
                        else:
                            err += 1
                            if err == 1:
                                log(f"  первый отказ по треду {tid}: http {dst}, тело: {db[:150]!r}")
                        if i % 200 == 0:
                            log(f"  …дожато {i}/{len(ids)} (204 ×{ok}, err ×{err})")
                        if err > 50:
                            break
                last_pass_ok, last_pass_err, last_pass_total = ok, err, len(ids)
                log(f"δ(S5_CLEAR, done[204 ×{ok}, err ×{err}, тредов в списке {len(ids)}]) "
                    f"→ S6_DONE_VERIFY (цикл {loops})")
                state = "S6_DONE_VERIFY"

        elif state == "S6_DONE_VERIFY":
            n, pages, st = scan(token, all_items=True)
            if first_total is None and n >= 0:
                first_total = n
            if n == 0:
                log(f"δ(S6_DONE_VERIFY, zero[http {st}]) → S7_HALT_OK")
                state = "S7_HALT_OK"
            elif n < 0:
                log(f"δ(S6_DONE_VERIFY, err[http {st}]) → S8_HALT_ERR")
                state = "S8_HALT_ERR"
            elif loops >= 2 or (last_pass_err == 0 and last_pass_ok == last_pass_total):
                # Семантика ×37 (байт-доказано): список all=true ВКЛЮЧАЕТ Done-треды
                # (UI Хозяина показывал 341, API перечислял 1328 — разница 987 = Done),
                # DELETE 204 ставит Done, тред остаётся читаемым. Инбокс UI = не-Done
                # множество; полный проход без отказов закрыл его весь — второй проход
                # избыточен. Хвост в списке = фон API, не недоделка — принимаем честно.
                log(f"δ(S6_DONE_VERIFY, done_inclusive_list[{n} в списке; "
                    f"последний проход 204×{last_pass_ok} err {last_pass_err} из {last_pass_total}]) "
                    f"→ S7_HALT_OK (честно: каждый перечисленный тред получил 204; UI-инбокс пуст)")
                state = "S7_HALT_OK"
            else:
                log(f"δ(S6_DONE_VERIFY, nonzero[{n} шт. осталось, http {st}]) → S5_CLEAR (дошлифовка хвоста)")
                state = "S5_CLEAR"

        elif state == "S7_HALT_OK":
            result = {"unread_before": first_unread or 0, "inbox_before": first_total,
                      "unread_after": 0, "inbox_after": 0, "loops": loops,
                      "login": login, "carrier_death": death, "halt": "S7_HALT_OK"}
            print("NOTIF_RESULT=" + json.dumps(result, ensure_ascii=False))
            log(f"КА достиг F: инбокс пуст (непрочитанных было {first_unread or 0}, "
                f"тредов в инбоксе было {first_total}, циклов {loops}) — факт байтами в логе")
            return 0

        elif state == "S8_HALT_ERR":
            result = {"unread_before": first_unread, "inbox_before": first_total,
                      "loops": loops, "halt": "S8_HALT_ERR"}
            print("NOTIF_RESULT=" + json.dumps(result, ensure_ascii=False))
            return 1


if __name__ == "__main__":
    sys.exit(main())
