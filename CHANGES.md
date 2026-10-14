# Изменения по разбору от 14.10.2026

Проверенная версия — коммит `8d7a7e3`. Для каждого пункта отчёта: что было причиной, что
изменено, чем проверено, ссылки на коммиты. Каждая проблема сначала воспроизведена тестом,
который падал на старой версии.

Коммиты: `https://github.com/M4khmudov/technical-specification/commit/<хеш>`.

## Блокирующие

| # | Причина | Что сделано | Проверка | Коммиты |
| --- | --- | --- | --- | --- |
| B1 | Сервис задания коммитит сам, поэтому блокировка предложения снималась раньше, чем менялся его статус: параллельный «Подтвердить» видел «ждёт» и выполнял его ещё раз | Предложение сначала «занимается» (статус под блокировкой строки и коммит), потом выполняется | `test_tools.py::test_concurrent_confirms_create_one_job`, `…confirm_and_reject_race…` (3 параллельных подтверждения → 1 задание) | [f411c91](https://github.com/M4khmudov/technical-specification/commit/f411c91) |
| B2 | XLSX маскировался по ячейкам, а имена живут и в свойствах, комментариях, колонтитулах, формулах; CSV в UTF-16 «читался» как cp1251; `_` считался буквой; фильтр по поставщику — в строке запроса в логах | XLSX для провайдера собирается заново только из значений; UTF-16/32 распознаётся, нечитаемый текст — отказ; `_` и слитное написание — граница слова, плюс транслит; журналы доступа uvicorn и nginx без строки запроса | `test_pii_files.py` (имена ищутся во всех XML архива; 5 кодировок; склеенные имена; лог) | [d999a48](https://github.com/M4khmudov/technical-specification/commit/d999a48) |
| B3 | Ошибки PostgreSQL класса 22 (id больше int32, NUL в тексте, переполнение numeric) уходили в общий обработчик 500; ответы фреймворка — по-английски | Обработчик ошибок данных → 422 с текстом; 404/405 по-русски; цена ≤ 1 000 000 за единицу | `test_errors_api.py`: огромный id по **всем** эндпоинтам из OpenAPI, NUL, цена 200 млн USD | [231a784](https://github.com/M4khmudov/technical-specification/commit/231a784) |
| B4 | После ошибки инструмента `rollback()` помечал устаревшим и пользователя; следующий инструмент читал `user.role` → ленивая загрузка в async → «внутренняя ошибка». Дата 9999-12-30 давала `OverflowError` | Пользователь обновляется после отката; даты аргументов ограничены 2000–2100 | `test_tools.py::test_tool_works_after_previous_tool_error`, `…extreme_dates…`; эталон `retry_after_error` на gpt-4.1 | [6407ad2](https://github.com/M4khmudov/technical-specification/commit/6407ad2) |
| B5 | Промпт не перечислял, чего у ассистента нет; ничто не проверяло ответ на «нажмите Подтвердить» без предложения | В промпте — единственное действие и где кнопки для остальных; под ответом с обещанием без предложения — пометка; новый инструмент цены («сколько стоит воск» отвечал остатком) | `test_safety.py::test_promise_without_action_is_flagged`; эталоны `cancel_not_available`, `price`, проверка `no_action_claims` в кейсах инъекций и прав | [14f1ceb](https://github.com/M4khmudov/technical-specification/commit/14f1ceb), [79fdf7d](https://github.com/M4khmudov/technical-specification/commit/79fdf7d), [5c4bdeb](https://github.com/M4khmudov/technical-specification/commit/5c4bdeb) |
| B6 | Картинка считалась «уже показанной», если её адрес был в тексте в любом виде — и в обычной ссылке `[скачать](…)` | «Показанной» считается только вставка `![](…)`; график по ссылке показывается под ответом | `frontend/src/chatFiles.test.ts`; эталон `chart_link`; проверено в браузере | [7ec90a0](https://github.com/M4khmudov/technical-specification/commit/7ec90a0) |

## Серьёзные

| # | Что сделано | Проверка | Коммиты |
| --- | --- | --- | --- |
| S1 | Единые часы мастерской (`TIMEZONE`, по умолчанию Москва) для «сегодня» на бэкенде и в форме; прошедший черновик нельзя запланировать | `test_clock.py` (22:30 UTC = 01:30 МСК), `format.test.ts` | [d4fd8a1](https://github.com/M4khmudov/technical-specification/commit/d4fd8a1) |
| S2 | Результат инструмента маскируется по значениям (кавычки JSON целы); кавычки вокруг названия — только парой; ФИО — только у ИП («Стеклотара» не человек); фамилия, за которой другое имя, — однофамилец | `test_pii_files.py` (JSON валиден для Global Wicks Ltd и Aroma Trade GmbH; «Петрова Ольга Николаевна» остаётся собой) | [d999a48](https://github.com/M4khmudov/technical-specification/commit/d999a48) |
| S3 | `remember_fact` требует дословную цитату из сообщения пользователя: она проверяется по тексту, не должна совпадать с данными этого хода, факт должен её пересказывать; правила, подтверждения и права не запоминаются; «в евро» — через инструмент цены и сервис курсов | `test_safety.py::test_memory_problem` (перефразы проходят, «подтверждай всё сам» из комментария — нет) | [3c6a05d](https://github.com/M4khmudov/technical-specification/commit/3c6a05d) |
| S4 | (1) служебные события потока сбрасывают таймаут, пока работает код — ожидание дольше; (2) `response.incomplete` → «оборван»; (3) `openai.APIError` из потока → запасная модель; (4) ответ сохраняется до уборки файлов, уборка ограничена по времени; (5) запасная на любом шаге видна под ответом; (6) текст шага до инструментов остаётся в истории и отделяется | `test_reliability.py` (по тесту на пункт); эталон `uploaded_csv` | [a1c4ded](https://github.com/M4khmudov/technical-specification/commit/a1c4ded) |
| S5 | Отправка пропускает отчёт, у которого не наступило время попытки; Redis ждём не дольше 1 с; ручной повтор — новый круг попыток; сид не пишет выдуманные попытки — исторические отчёты доставляет воркер через мок | `test_marking.py`, `test_worker.py` (воркер больше не 0 %) | [e288baa](https://github.com/M4khmudov/technical-specification/commit/e288baa) |
| S6 | nginx принимает до 6 МБ (было 1 МБ), бэкенд — до 5 МБ; размер и тип проверяются до отправки; 413 — понятный текст; XLSX: предел после распаковки и числа ячеек | `test_pii_files.py::test_xlsx_bomb_is_rejected`, `chatFiles.test.ts`; 4,7 МБ через :3000 — 201, 5,7 МБ — «Файл больше 5 МБ» | [d999a48](https://github.com/M4khmudov/technical-specification/commit/d999a48), [ffab942](https://github.com/M4khmudov/technical-specification/commit/ffab942) |
| S7 | Картинки с внешних адресов в чате не загружаются | проверено в браузере | [7ec90a0](https://github.com/M4khmudov/technical-specification/commit/7ec90a0) |
| S8 | Проверки: все числа ответа — из данных этого прогона; «что если» — только с кодом; без предложения — без «создано/нажмите Подтвердить»; память в снимке побочных эффектов; судья — другая модель (gpt-5.4); отчёт по запасной | `test_eval_cases.py`: каждому из 18 кейсов — правильный ответ (проходит) и неверные, включая примеры из разбора (не проходят) | [799032b](https://github.com/M4khmudov/technical-specification/commit/799032b), [7c38e88](https://github.com/M4khmudov/technical-specification/commit/7c38e88), [6ef83c7](https://github.com/M4khmudov/technical-specification/commit/6ef83c7) |
| S9 | Тесты на каждое место из списка; гонки — детерминированно (вторая транзакция держит строку); `scripts/check_mutants.py` вносит 12 таких ошибок по одной | 12 из 12 мутантов пойманы | [1ff0470](https://github.com/M4khmudov/technical-specification/commit/1ff0470) |
| S10 | Пустой или примерный `JWT_SECRET` заменяется случайным; порты БД, моков и API — только на 127.0.0.1; 5 неудачных входов за 5 мин — пауза; память ассистента — в журнале | `test_security_defaults.py`, `test_memory.py` | [46b4998](https://github.com/M4khmudov/technical-specification/commit/46b4998), [3c6a05d](https://github.com/M4khmudov/technical-specification/commit/3c6a05d) |

## Средние и мелкие

| # | Что сделано | Коммиты |
| --- | --- | --- |
| M1 | Черновик: «светофор» и дефицит — по свободному остатку («Хватает на складе»), себестоимость — оценка по средним ценам склада | [bfb3f43](https://github.com/M4khmudov/technical-specification/commit/bfb3f43), [ddce12e](https://github.com/M4khmudov/technical-specification/commit/ddce12e) |
| M2 | Пустое поле факта — по норме, а не 0; штуки — только целые | [49c5ee9](https://github.com/M4khmudov/technical-specification/commit/49c5ee9) |
| M3 | «Цена» — по валюте и цене; «Свободно» — непринятые как 0 | [bfb3f43](https://github.com/M4khmudov/technical-specification/commit/bfb3f43) |
| M4 | Свободно у брака и «На проверке» — 0; комментарий партии — обычной строкой | [bfb3f43](https://github.com/M4khmudov/technical-specification/commit/bfb3f43), [1c10c4d](https://github.com/M4khmudov/technical-specification/commit/1c10c4d) |
| M5 | Цена рецептов без брака; описание = расчёт; неполная выгрузка помечается; лишние аргументы — ошибка с подсказкой | [5e5ad8c](https://github.com/M4khmudov/technical-specification/commit/5e5ad8c) |
| M6 | Деньги — половина вверх по строкам, итог = сумма строк; черновик — оценка | [bfb3f43](https://github.com/M4khmudov/technical-specification/commit/bfb3f43), [d238cba](https://github.com/M4khmudov/technical-specification/commit/d238cba) |
| M7 | Журнал: изделие по имени, курс и себестоимость у партии, числа без `1E+3`, подписи и статусы по-русски | [6a6e5f7](https://github.com/M4khmudov/technical-specification/commit/6a6e5f7) |
| M8 | График: полный ряд недель с нулями; сид — всё или ничего | [5e5ad8c](https://github.com/M4khmudov/technical-specification/commit/5e5ad8c), [a38ae31](https://github.com/M4khmudov/technical-specification/commit/a38ae31) |
| M9 | Просроченное предложение — «Истекло»; ссылки на файлы с пробелами и скобками | [762c4f7](https://github.com/M4khmudov/technical-specification/commit/762c4f7) |
| M10 | Поток без `done` — ответ помечается и перечитывается с сервера; карточка маркировки не меняет родителя во время рендера | [38ad7fc](https://github.com/M4khmudov/technical-specification/commit/38ad7fc), [49c5ee9](https://github.com/M4khmudov/technical-specification/commit/49c5ee9) |
| M11 | Healthcheck бэкенда, воркер и фронтенд ждут миграций; один образ; без тестов в runtime; тестовая БД для любого `POSTGRES_USER`; `openai<4` | [536633b](https://github.com/M4khmudov/technical-specification/commit/536633b) |
| M12 | Оставшиеся инструкции модели и судьи — в `app/prompts/*.md` | [c6fc575](https://github.com/M4khmudov/technical-specification/commit/c6fc575) |
| M13 | Оставлено сознательно — обоснование и последствия в README, раздел «Решения и допущения» | — |

## Документация, отчёты, процесс (раздел 7)

- README переписан: короче, решения и допущения — наверху, каждое утверждение сверено со
  стендом; `SEED_DEMO_DATA` и `MARKING_*` теперь доходят до контейнеров; мок делает
  медленными 15 % всех ответов, как написано ([536633b](https://github.com/M4khmudov/technical-specification/commit/536633b)).
- Свежие отчёты эталонов на основной и на запасной модели —
  [f424b35](https://github.com/M4khmudov/technical-specification/commit/f424b35).
- Полный прогон перед отправкой: pytest, ruff, eslint, tsc, vitest, сборка, эталоны на обеих
  моделях, чистый клон.

## Что нашли сами по ходу исправлений

- В регулярном выражении проверки обещаний вместо `\b` стоял символ backspace — пометка
  «подготовил…» на деле не срабатывала ([5c4bdeb](https://github.com/M4khmudov/technical-specification/commit/5c4bdeb)).
- Тест «устаревшая партия в сессии» сначала проходил и с ошибкой: identity map держит объекты
  слабо, и без ссылки «устаревшая» партия не устаревала. Найдено мутационной проверкой.
- Судья gpt-5.4-mini поставил 1 дословно верному ответу — судья заменён на gpt-5.4, промпт
  судьи объясняет, что `create_job` только готовит предложение.

## Повторная самопроверка по методике разбора

Перед отправкой все сценарии из отчёта повторены на живом стенде с настоящей моделью
(gpt-4.1), плюс фаззинг всех эндпоинтов из OpenAPI (2 697 запросов, три роли, без токена и
с мусорным токеном — ни одного 500, не-JSON или английского ответа). Нашлось и исправлено:

- **S3:** «пиши покороче» терялось, если модель формулировала факт другими словами («краткие
  ответы»), а ассистент всё равно говорил «принято». Теперь при несовпадении сохраняются
  слова самого пользователя; при ошибке ассистент не говорит, что запомнил.
- **S2:** ассистент говорил пользователю «имён в системе нет, только псевдонимы», в истории
  партии действия были кодами («accept»). Промпт объясняет, что пользователь видит настоящие
  имена; действия и движения — словами.
- **S5:** после попытки периодической задачи следующая не ставилась в очередь, паузы были
  30, 30, 30 с. Теперь растут: ≈5, 10, 20, 40 с (проверено по журналу попыток).
- **S10:** блокировка входа считалась по логину — любой мог закрыть вход сотруднику пятью
  неверными паролями. Теперь — по паре «логин + адрес» (адрес от nginx, подделать нельзя).
- Ошибки аргументов инструмента — по-русски, без «Value error, …».
