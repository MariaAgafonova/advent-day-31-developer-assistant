# Voice input

## Recognition and task splitting

The app uses Android SpeechRecognizer with RECORD_AUDIO permission and the Russian language hint ru-RU. Recognition depends on the service installed on the device. That service may send speech to its servers; offline recognition is not guaranteed. NaToday does not record or store audio files.

The explicit spoken separator is «новая задача» (new task). Example: «Купить продукты новая задача закончить отчёт новая задача позвонить маме». Matching is case insensitive, including Cyrillic. A conjunction such as «и» and a comma do not split a task.

## Editable preview

The recognized text is shown before any task is inserted. Users can correct the raw text, rebuild its task list, edit individual tasks, remove or add rows, merge a row with the next row, or split at a manually inserted line break or explicit separator. Empty tasks cannot be saved. The entire confirmed list is inserted on the date chosen before opening voice input.

Rebuilding the list from raw text replaces previous preview edits; the UI explains this. Leaving the preview without saving does not create tasks.

## Lifecycle and errors

`app/src/main/java/ru/natoday/app/voice/SpeechInput.kt` owns SpeechRecognizer on the main thread. It cancels and destroys the recognizer when the screen stops or leaves composition. It handles missing recognition service, denied microphone permission, empty speech, unsupported Russian language, network failures and a busy recognizer.

`VoiceParser.kt` contains separator logic; `ui/VoiceViewModel.kt` holds the draft and list editing actions. Error messages and UI labels are in `app/src/main/res/values/strings.xml`.
