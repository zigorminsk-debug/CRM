package by.crm.engineer

import android.app.AlertDialog
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.text.InputType
import android.view.View
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import java.text.SimpleDateFormat
import java.util.*

/**
 * Приложение инженера:
 *  - вход (адрес сервера + логин/пароль);
 *  - маршрут на день: заявки отсортированы по срочности и географии, с временем прибытия;
 *  - кнопка «Поехали» → Яндекс.Навигатор по координатам заявки;
 *  - чекбоксы готовности: «на месте» либо «забор в офис» (доставка на следующий рабочий день);
 *  - доставки заказчикам с переносом даты, если оборудование не готово.
 *
 * Работа с сетью — в фоновом потоке (apiCall), обновление интерфейса — в UI-потоке.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var api: Api
    private lateinit var taskAdapter: TaskAdapter
    private lateinit var deliveryAdapter: DeliveryAdapter
    private var day: String = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
    private var tasks: MutableList<Task> = mutableListOf()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        api = Api(this)

        findViewById<EditText>(R.id.serverEdit).setText(api.base)
        findViewById<Button>(R.id.loginButton).setOnClickListener { doLogin() }
        findViewById<Button>(R.id.logoutButton).setOnClickListener {
            api.token = ""
            showLogin(true)
        }
        findViewById<Button>(R.id.refreshButton).setOnClickListener { refresh() }
        findViewById<Button>(R.id.dayButton).setOnClickListener { pickDay() }
        findViewById<Button>(R.id.routeAllButton).setOnClickListener { openFullRoute() }
        findViewById<Button>(R.id.updateButton).setOnClickListener { checkUpdates(true) }

        taskAdapter = TaskAdapter(
            onGo = { task -> go(task) },
            onDone = { task, result -> markDone(task, result) },
            onDetails = { task -> showDetails(task) },
            onPostpone = { task -> postponeDialog(task) }
        )
        deliveryAdapter = DeliveryAdapter(
            onDelivered = { d ->
                apiCall({ api.deliveryDone(d.id) }) { toast("Доставлено заказчику"); refresh() }
            },
            onPostpone = { d -> postponeDeliveryDialog(d) },
            onNavigate = { d -> openNavigatorUrl(d.navIntent, "https://yandex.ru/maps/?rtext=${d.lat},${d.lon}&rtt=auto") }
        )

        findViewById<RecyclerView>(R.id.tasksList).apply {
            layoutManager = LinearLayoutManager(this@MainActivity)
            adapter = taskAdapter
            isNestedScrollingEnabled = false
        }
        findViewById<RecyclerView>(R.id.deliveriesList).apply {
            layoutManager = LinearLayoutManager(this@MainActivity)
            adapter = deliveryAdapter
            isNestedScrollingEnabled = false
        }

        if (api.token.isEmpty()) showLogin(true) else {
            showLogin(false)
            refresh()
        }
        FeedService.start(this)
        checkUpdates(false)     // обновления проверяем сами: сборки публикуются на GitHub
    }

    // ------------------------------------------------------------- обновления
    /**
     * Проверка новой версии: сервер берёт сведения из последнего релиза GitHub,
     * приложение скачивает APK и ставит его поверх установленного (данные сохраняются).
     */
    private fun checkUpdates(manual: Boolean) {
        Updater.check(this, silent = !manual) { info ->
            AlertDialog.Builder(this)
                .setTitle("Доступна версия ${info.version}")
                .setMessage(
                    "Установлена ${Updater.installedVersion(this).first}.\n\n" +
                        (if (info.notes.isBlank()) "" else "Что нового:\n${info.notes}\n\n") +
                        "Скачать и установить поверх установленной версии? Профиль инженера и настройки сохранятся."
                )
                .setPositiveButton("Обновить") { _, _ -> Updater.downloadAndInstall(this, info) }
                .setNegativeButton("Позже", null)
                .show()
        }
    }

    override fun onResume() {
        super.onResume()
        if (api.token.isNotEmpty() && findViewById<LinearLayout>(R.id.mainPanel).visibility == View.VISIBLE) refresh()
    }

    // ------------------------------------------------------------------ вход
    private fun doLogin() {
        val server = findViewById<EditText>(R.id.serverEdit).text.toString()
        val user = findViewById<EditText>(R.id.userEdit).text.toString()
        val pass = findViewById<EditText>(R.id.passEdit).text.toString()
        if (user.isBlank() || pass.isBlank()) {
            toast("Укажите логин и пароль")
            return
        }
        api.saveServer(server)
        findViewById<Button>(R.id.loginButton).isEnabled = false
        apiCall({
            val res = api.login(user, pass)
            runOnUiThread {
                findViewById<Button>(R.id.loginButton).isEnabled = true
                toast("Вход выполнен: " + res.optString("full_name"))
                showLogin(false)
            }
            refresh()
        })
    }

    private fun showLogin(show: Boolean) {
        findViewById<LinearLayout>(R.id.loginPanel).visibility = if (show) View.VISIBLE else View.GONE
        findViewById<LinearLayout>(R.id.mainPanel).visibility = if (show) View.GONE else View.VISIBLE
    }

    // --------------------------------------------------------------- маршрут
    private fun refresh() {
        findViewById<TextView>(R.id.routeMeta).text = "Загрузка маршрута…"
        apiCall({
            val plan = api.route(day)
            val list = mutableListOf<Task>()
            val arr = plan.optJSONArray("route") ?: org.json.JSONArray()
            for (i in 0 until arr.length()) {
                val t = Task.from(arr.getJSONObject(i))
                if (t.status != "done_onsite" && t.status != "delivered" && t.status != "closed") list.add(t)
            }
            val deliveries = mutableListOf<Delivery>()
            val darr = plan.optJSONArray("deliveries") ?: org.json.JSONArray()
            for (i in 0 until darr.length()) deliveries.add(Delivery.from(darr.getJSONObject(i)))
            val engineer = plan.optJSONObject("engineer")?.optString("full_name") ?: "Инженер"
            val meta = "Заявок: ${list.size} · ${plan.optDouble("total_km")} км · ~${plan.optDouble("total_hours")} ч"
            val shownDay = plan.optString("day", day)
            runOnUiThread {
                tasks = list
                findViewById<TextView>(R.id.engineerName).text = engineer
                findViewById<TextView>(R.id.routeMeta).text = meta
                findViewById<TextView>(R.id.dayLabel).text = shownDay
                findViewById<TextView>(R.id.emptyHint).visibility = if (list.isEmpty()) View.VISIBLE else View.GONE
                taskAdapter.submit(list)
                deliveryAdapter.submit(deliveries)
                findViewById<TextView>(R.id.deliveriesTitle).text =
                    if (deliveries.isEmpty()) "Доставок заказчикам нет"
                    else "Доставка заказчикам (забор в офис): ${deliveries.size}"
            }
        })
    }

    private fun pickDay() {
        val input = EditText(this).apply {
            inputType = InputType.TYPE_CLASS_DATETIME
            setText(day)
        }
        AlertDialog.Builder(this)
            .setTitle("Маршрут на дату (ГГГГ-ММ-ДД)")
            .setView(input)
            .setPositiveButton("Показать") { _, _ ->
                day = input.text.toString().trim().ifBlank { day }
                refresh()
            }
            .setNegativeButton("Отмена", null)
            .show()
    }

    private fun openFullRoute() {
        val points = tasks.filter { it.hasCoords }.map { "${it.lat},${it.lon}" }
        if (points.isEmpty()) {
            toast("В маршруте нет заявок с координатами")
            return
        }
        startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://yandex.ru/maps/?rtext=" + points.joinToString("~") + "&rtt=auto")))
    }

    // ------------------------------------------------------------- «Поехали»
    private fun go(task: Task) {
        apiCall({ api.go(task.id) }, silent = true) { refresh() }
        openNavigatorUrl(task.navIntent, task.navHttps)
    }

    private fun openNavigatorUrl(deepLink: String, https: String) {
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(deepLink))
        try {
            startActivity(intent)                      // откроется Яндекс.Навигатор, если установлен
        } catch (e: Exception) {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(https)))   // иначе — карты в браузере
        }
    }

    // ------------------------------------------------------ готовность/забор
    private fun markDone(task: Task, result: String) {
        val onsite = result == "onsite"
        val input = EditText(this).apply { hint = "Комментарий для диспетчера" }
        AlertDialog.Builder(this)
            .setTitle(if (onsite) "Готово на месте — что сделано?" else "Забор в офис — что забираем?")
            .setView(input)
            .setPositiveButton("Подтвердить") { _, _ ->
                val comment = input.text.toString().ifBlank {
                    if (onsite) "Работы выполнены, заказчик принял" else "Картридж/оборудование забираем в офис"
                }
                apiCall({ api.done(task.id, result, comment) }) {
                    refresh()
                    if (onsite) {
                        toast("Отмечено: выполнено на месте")
                    } else {
                        AlertDialog.Builder(this)
                            .setTitle("Забор оформлен")
                            .setMessage("Сервер поставил доставку заказчику на следующий рабочий день.\n" +
                                    "Если оборудование не готово, дату можно перенести в разделе «Доставка заказчикам».")
                            .setPositiveButton("Понятно", null)
                            .show()
                    }
                }
            }
            .setNegativeButton("Отмена", null)
            .show()
    }

    private fun postponeDialog(task: Task) {
        val input = EditText(this).apply { setText(day) }
        AlertDialog.Builder(this)
            .setTitle("Перенести заявку на дату (ГГГГ-ММ-ДД)")
            .setView(input)
            .setPositiveButton("Перенести") { _, _ ->
                apiCall({ api.postponeTask(task.id, input.text.toString().trim(), "Перенос из мобильного приложения") }) {
                    toast("Заявка перенесена")
                    refresh()
                }
            }
            .setNegativeButton("Отмена", null)
            .show()
    }

    private fun postponeDeliveryDialog(d: Delivery) {
        val input = EditText(this).apply { hint = "Что не готово?" }
        AlertDialog.Builder(this)
            .setTitle("Перенести доставку на следующий рабочий день")
            .setView(input)
            .setPositiveButton("Перенести") { _, _ ->
                apiCall({ api.deliveryPostpone(d.id, 1, input.text.toString().ifBlank { "Оборудование не готово" }) }) {
                    toast("Перенесено")
                    refresh()
                }
            }
            .setNegativeButton("Отмена", null)
            .show()
    }

    // ------------------------------------------------------------------ детали
    private fun showDetails(t: Task) {
        val text = buildString {
            append("Контрагент: ${t.contractor}\n")
            append("Контактное лицо: ${t.contact}\n")
            append("Телефон: ${t.phone}\n")
            append("Работа: ${t.work}\n")
            append("Срочность: ${t.priorityLabel}\n")
            append("Статус: ${t.statusLabel}\n")
            if (t.zone.isNotBlank()) append("Район: ${t.zone}\n")
            append("Адрес: ${t.address}\n")
            if (t.equipment.isNotBlank()) append("Оборудование: ${t.equipment} ${t.serial}\n")
            if (t.plannedDate.isNotBlank()) append("Плановая дата: ${t.plannedDate}\n")
            if (t.comment.isNotBlank()) append("\nПояснение: ${t.comment}")
        }
        AlertDialog.Builder(this)
            .setTitle("${t.number} — ${t.work}")
            .setMessage(text)
            .setPositiveButton("Поехали") { _, _ -> go(t) }
            .setNeutralButton("Позвонить") { _, _ ->
                startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:${t.phone.replace(" ", "")}")))
            }
            .setNegativeButton("Закрыть", null)
            .show()
    }

    // --------------------------------------------------------- многопоточность
    private fun apiCall(work: () -> Unit, silent: Boolean = false, onOk: (() -> Unit)? = null) {
        Thread {
            try {
                work()
                if (onOk != null) runOnUiThread(onOk)
            } catch (e: Exception) {
                if (!silent) runOnUiThread { toast("Ошибка: ${e.message}") }
            }
        }.start()
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
}
