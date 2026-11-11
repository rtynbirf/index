# index — SSOT-хаб аккаунта rtynbirf

Единственный источник правды: **где что лежит и что проверено**. Код не копируется.

- [`projects.json`](projects.json) — реестр: `id → repository → ref (SHA проверенной версии) → interfaces → tests → status`.
- Правила агентов: [`rtynbirf/.github → AGENTS.md`](https://github.com/rtynbirf/.github/blob/main/AGENTS.md)
- Конституция веса: 🥇 ≤10KB · 🥈 ≤250KB · 🥉 ≤1MB · ЖИР >1MB — клининг немедленно.
- Метод минимизации загрузки: `git clone --depth=1 --filter=blob:none --sparse`.

Обновление: после каждого значимого push обнови `ref` в `projects.json` (реальный SHA).
