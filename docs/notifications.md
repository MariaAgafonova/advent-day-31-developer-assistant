# Evening reminder

NaToday uses a one-shot inexact AlarmManager.setAndAllowWhileIdle(RTC_WAKEUP) alarm. No SCHEDULE_EXACT_ALARM or USE_EXACT_ALARM permission is declared. Delivery is approximate and can be delayed by power management, Doze or device policies.

## Scheduling

Settings are stored in Preferences DataStore: enabled (default false), local time (default 22:00). There is one stable immutable broadcast PendingIntent. A changed time replaces the pending alarm. Disabling cancels the alarm and the visible notification.

A persisted token rejects stale deliveries after settings changes. Claiming a delivery is atomic in DataStore and avoids repeated reminders for the same planned day. After a delivery the scheduler computes the next local day, accounting for ZoneId.systemDefault and daylight saving transitions.

The receiver handles BOOT_COMPLETED, TIME_SET, TIMEZONE_CHANGED, DATE_CHANGED and MY_PACKAGE_REPLACED. Opening the app also ensures scheduling. Force-stopped apps cannot deliver reminders until opened again. The app does not request exemption from battery optimization.

## Notification and navigation

Android 13+ requires POST_NOTIFICATIONS. Settings show a warning if permission or the notification channel is disabled and provide a link to Android notification settings.

The title is «Как прошёл день?» and the text is «Отметь выполненные дела». The PendingIntent opens MainActivity with the date recorded when the alarm was scheduled, including a delayed delivery after midnight. Navigation Compose opens the review route over Today, so Back returns to the planner.

## Source files

`app/src/main/java/ru/natoday/app/reminders/ReminderTime.kt` calculates local reminder dates and instants. `ReminderScheduler.kt` owns scheduling and notifications. `ReminderReceiver.kt` performs short asynchronous work using goAsync, with a timeout. The manifest declares only microphone, notification and boot permissions.
