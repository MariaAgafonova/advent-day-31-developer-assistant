package ru.natoday.app

import android.Manifest
import android.content.Intent
import android.app.NotificationManager
import android.os.Build
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.v2.createAndroidComposeRule
import androidx.datastore.preferences.core.edit
import ru.natoday.app.data.settingsStore
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.Assert.*
import ru.natoday.app.ui.shortDate
import java.time.LocalDate
import java.time.LocalTime

class PlannerUiTest {
    @get:Rule val ui = createAndroidComposeRule<MainActivity>()
    private val app get() = ui.activity.application as PlannerApplication

    @Before fun reset() = runBlocking {
        app.database.clearAllTables()
        app.reminders.setEnabled(false)
        app.settingsStore.edit { it.clear() }
        Unit
    }
    private fun awaitText(text: String) {
        ui.waitUntil(10_000) { ui.onAllNodesWithText(text).fetchSemanticsNodes().isNotEmpty() }
    }
    private fun add(title: String) {
        ui.onNodeWithText("Добавить задачу").performClick()
        ui.onNodeWithText("Что нужно сделать?").performTextInput(title)
        ui.onNodeWithText("Сохранить").performClick()
        awaitText(title)
    }
    @Test fun createEditCompleteUndoDeleteAndRecreate() {
        ui.onNodeWithText("Добавить задачу").performClick()
        ui.onNodeWithText("Сохранить").performClick()
        ui.onNodeWithText("Напишите название задачи").assertExists()
        ui.onNodeWithText("Отмена").performClick()
        add("Купить хлеб")
        ui.onNodeWithText("Купить хлеб").performClick()
        ui.onNode(hasSetTextAction()).performTextReplacement("Купить хлеб и молоко")
        ui.onNodeWithText("Сохранить").performClick()
        awaitText("Купить хлеб и молоко")
        ui.onNodeWithContentDescription("Выполнить «Купить хлеб и молоко»").performClick()
        awaitText("Выполнено 1 из 1")
        ui.onNodeWithText("Выполненные · 1 — показать").performClick()
        ui.onNodeWithContentDescription("Отменить выполнение «Купить хлеб и молоко»").performClick()
        awaitText("Выполнено 0 из 1")
        ui.onNodeWithContentDescription("Действия с задачей «Купить хлеб и молоко»").performClick()
        ui.onNodeWithText("Удалить").performClick()
        awaitText("Задача удалена")
        ui.onNodeWithText("Отменить").performClick()
        awaitText("Купить хлеб и молоко")
        ui.activityRule.scenario.recreate()
        awaitText("Купить хлеб и молоко")
    }
    @Test fun reviewMovesToTomorrowAndHistoryHasNoDuplicate() {
        add("Разобрать почту")
        ui.onNodeWithText("Подвести итоги дня").performScrollTo().performClick()
        val tomorrow = LocalDate.now().plusDays(1)
        ui.onNodeWithText("На завтра — " + shortDate(tomorrow)).performClick()
        awaitText("Выполнено за этот день: 0. Перенесено в этом разборе: 1.")
        ui.onNodeWithContentDescription("Назад").performClick()
        awaitText("Выполнено 0 из 0")
        ui.onNodeWithContentDescription("Следующий день").performClick()
        awaitText("Разобрать почту")
        runBlocking {
            assertTrue(app.tasks.observeDay(LocalDate.now()).first().isEmpty())
            assertEquals(1, app.tasks.observeDay(tomorrow).first().size)
        }
    }
    @Test fun editableVoicePreviewSavesWholeList() {
        ui.onNodeWithText("Голосом").performClick()
        ui.onNodeWithText("Распознанный текст").performTextInput("Купить хлеб и молоко новая задача Позвонить маме")
        ui.onNodeWithText("Заново разделить текст").performScrollTo().performClick()
        ui.onNodeWithTag("voice-list").performScrollToNode(hasText("Сохранить задачи (2)"))
        ui.onNodeWithText("Сохранить задачи (2)").performClick()
        awaitText("Купить хлеб и молоко")
        awaitText("Позвонить маме")
    }
    @Test fun notificationOpensItsDateAndBackReturnsToToday() {
        val launchedActivity = ui.activity
        val launchIntent = Intent(launchedActivity.intent)
        try {
        if (Build.VERSION.SDK_INT >= 33) {
            InstrumentationRegistry.getInstrumentation().uiAutomation.grantRuntimePermission(app.packageName, Manifest.permission.POST_NOTIFICATIONS)
        }
        val planned = runBlocking {
            app.reminders.setTime(LocalTime.now().plusHours(2).withSecond(0).withNano(0))
            app.reminders.setEnabled(true)
            app.settings.scheduled.first()!!
        }
        runBlocking { app.reminders.deliver(planned.token, planned.day, planned.at) }
        val notification = app.getSystemService(NotificationManager::class.java).activeNotifications.single { it.id == 31 }
        notification.notification.contentIntent.send()
        awaitText("Все дела разобраны")
        awaitText(ru.natoday.app.ui.dateLabel(LocalDate.ofEpochDay(planned.day)))
        ui.onNodeWithContentDescription("Назад").performClick()
        awaitText("Этот день пока свободен")
        runBlocking { app.reminders.setEnabled(false) }
        } finally {
            // ActivityScenario matches lifecycle callbacks against its original launch Intent.
            // Restore that descriptor only after verifying the notification navigation.
            ui.runOnUiThread { launchedActivity.intent = launchIntent }
        }
    }
}
