# План реализации «На сегодня»

## Цель MVP

Пользователь планирует дела на любой день, отмечает выполнение, редактирует и переносит задачи без дубликатов. Вечером получает неточное напоминание и вручную разбирает оставшиеся дела. Все данные локальные; без регистрации, синхронизации и собственного сервера.

## Этапы и критерии готовности

1. Каркас: один app-модуль, Kotlin DSL/Version Catalog, Compose Material 3, Navigation Compose. Debug APK собирается.
2. Данные: Room Task(id, title, scheduledDate, createdAt, completedAt), DAO и repository; Preferences DataStore(enabled, local time). Выполнение/отмена/перенос проверены тестами.
3. Сегодня: выбор даты, предыдущий/следующий день, возврат сегодня, счётчик, выполненные сворачиваются, CRUD, перенос, Snackbar отмены удаления. Пустое название запрещено.
4. Голос: RECORD_AUDIO, SpeechRecognizer, явный разделитель «новая задача», редактируемый предпросмотр, удаление/добавление/объединение/разделение, ошибки и освобождение recognizer.
5. Разбор: конкретная дата, только незавершённые, действия сохраняются сразу. Завтра считается от даты разбора. Итог показывает выполненные задачи этого дня и переносы текущего сеанса.
6. Напоминания: DataStore, 22:00, runtime POST_NOTIFICATIONS, inexact AlarmManager, одна PendingIntent, корректный экран и Back, перепланирование после boot/time/timezone/package replaced.
7. Календарь: прошлое и будущее, границы месяца/года/високосный день. Перенос не считается выполнением.
8. Приёмка: unit, instrumented Room/UI, lint, APK, ручная проверка эмулятора, темы и крупный шрифт. Невыполненные проверки явно перечислены.

## Архитектура

MainActivity → Navigation Compose → Day/Review/Voice/Settings экраны → ViewModel StateFlow → repository → Room/DataStore. Ручное внедрение через Application container, без DI-фреймворка, сервера, дополнительных модулей.

## Семантика дат и состояния

Дата плана — LocalDate/epochDay, независима от зоны. Временные отметки — Instant/epoch millis. Сегодня вычисляется в текущей зоне и обновляется при возврате в приложение и смене даты/зоны. Календарь Material использует UTC millis только для отображения выбранной даты.

Счётчик разбора: число выполненных записей, запланированных на его дату; переносы — количество успешных перемещений в текущем сеансе. После нового открытия счётчик переносов начинается с нуля; изменение конфигурации сохраняет сеанс.

## Напоминания

Одноразовый AlarmManager.setAndAllowWhileIdle(RTC_WAKEUP), фиксированная PendingIntent, следующее срабатывание по LocalTime и ZoneId.systemDefault. Смена настроек отменяет предыдущее событие. При доставке используется дата исходного alarm, даже при задержке за полночь. Для защиты от устаревших событий проверяется сохранённый token. После reboot/time/date/timezone reschedule. Exact alarms не запрашиваются.

## Голос и приватность

SpeechRecognizer работает только по явному действию пользователя, на main thread, cancel/destroy при выходе. Нет записи аудио в файлы. Распознанный текст не сохраняется до подтверждения. Провайдер распознавания может передавать аудио на свои серверы; офлайн-работа не гарантируется.

## Официальные источники, проверены 15.10.2026

- https://developer.android.com/develop/background-work/services/alarms
- https://developer.android.com/develop/ui/views/notifications/notification-permission
- https://developer.android.com/reference/android/speech/SpeechRecognizer
- https://developer.android.com/jetpack/androidx/releases/room
- https://developer.android.com/jetpack/androidx/releases/datastore
- https://developer.android.com/jetpack/androidx/releases/navigation
