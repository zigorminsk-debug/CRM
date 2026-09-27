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
        try {
            val pInfo = packageManager.getPackageInfo(packageName, 0)
            findViewById<TextView>(R.id.versionLabel).text = "Версия приложения: " + pInfo.versionName
        } catch (_: Exception) { }
        findViewById<Button>(R.id.loginButton).setOnClickListener { doLogin() }
        findViewById<Button>(R.id.checkButton).setOnClickListener { doCheck() }
        findViewById<Button>(R.id.logoutButton).setOnClickListener {
            api.token = ""
            showLogin(true)
        }
        findViewById<Button>(R.id.refreshButton).setOnClickListener { refresh() }
        findViewById<Button>(R.id.dayButton).setOnClickListener { pickDay() }
        findViewById<Button>(R.id.routeAllButton).setOnClickListener { openFullRoute() }
        findViewById<Button>(R.id.historyButton).setOnClickListener { showHistory() }
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

    /** История: исполненные заявки инженера (done/closed), свежие сверху. */
    private fun showHistory() {
        apiCall({
            val res = api.history()
            val arr = res.optJSONArray("items") ?: org.json.JSONArray()
            val titles = mutableListOf<String>()
            val subs = mutableListOf<String>()
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                val done = o.optString("done_at")
                val d = if (done.length >= 10) done.substring(0, 10) else o.optString("planned_date")
                titles.add("${o.optString("number")} · ${o.optString("status_label")} · $d")
                subs.add(listOf(o.optString("work_name"), o.optString("address"))
                    .filter { it.isNotBlank() }.joinToString(" · "))
            }
            runOnUiThread {
                if (titles.isEmpty()) {
                    AlertDialog.Builder(this).setTitle("История")
                        .setMessage("Исполненных заявок пока нет.")
                        .setPositiveButton("Закрыть", null).show()
                } else {
                    val adapter = object : android.widget.ArrayAdapter<String>(
                        this, android.R.layout.simple_list_item_2, titles
                    ) {
                        override fun getView(position: Int, convertView: View?, parent: android.view.ViewGroup): View {
                            val v = super.getView(position, convertView, parent)
                            val sub = v.findViewById<TextView>(android.R.id.text2)
                            sub.text = subs[position]
                            return v
                        }
                    }
                    AlertDialog.Builder(this)
                        .setTitle("Исполненные заявки: ${titles.size}")
                        .setAdapter(adapter, null)
                        .setPositiveButton("Закрыть", null).show()
                }
            }
        })
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
        if (server.isBlank()) {
            toast("Укажите адрес сервера (например, http://192.168.1.35:8000)")
            return
        }
        if (user.isBlank() || pass.isBlank()) {
            toast("Укажите логин и пароль")
            return
        }
        val btn = findViewById<Button>(R.id.loginButton)
        api.saveServer(server)
        btn.isEnabled = false
        btn.text = "Вхожу…"
        apiCall({
            val res = api.login(user, pass)
            runOnUiThread {
                btn.isEnabled = true
                btn.text = getString(R.string.login_button)
                toast("Вход выполнен: " + res.optString("full_name"))
                showLogin(false)
            }
            refresh()
        }, onError = {
            // Кнопка обязана оживать при ЛЮБОЙ ошибке (адрес, сеть, пароль),
            // иначе после первой же неудачи вход блокируется навсегда.
            btn.isEnabled = true
            btn.text = getString(R.string.login_button)
        })
    }

    /**
     * Диагностика без попытки входа: доступен ли сервер по указанному адресу.
     * Отделяет сетевые проблемы (брандмауэр, Wi-Fi, адрес) от ошибок логина.
     */
    private fun doCheck() {
        val server = findViewById<EditText>(R.id.serverEdit).text.toString()
        if (server.isBlank()) {
            toast("Укажите адрес сервера")
            return
        }
        api.saveServer(server)
        toast("Проверяю " + server.trim() + " …")
        apiCall({
            val h = api.health()
            runOnUiThread {
                val zones = h.optInt("zone_count", -1)
                toast("Сервер отвечает, версия " + h.optString("version") +
                      (if (zones >= 0) ", зон: " + zones else "") +
                      ". Теперь вход должен пройти.")
            }
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
        val cal = java.util.Calendar.getInstance()
        try {
            val parts = day.split("-").map { it.toInt() }
            if (parts.size == 3) cal.set(parts[0], parts[1] - 1, parts[2])
        } catch (_: Exception) { }
        android.app.DatePickerDialog(this, { _, y, m, dOfM ->
            day = String.format("%04d-%02d-%02d", y, m + 1, dOfM)
            refresh()
        }, cal.get(java.util.Calendar.YEAR), cal.get(java.util.Calendar.MONTH),
            cal.get(java.util.Calendar.DAY_OF_MONTH)).show()
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
        val cal = java.util.Calendar.getInstance()
        try {
            val parts = day.split("-").map { it.toInt() }
            if (parts.size == 3) cal.set(parts[0], parts[1] - 1, parts[2])
        } catch (_: Exception) { }
        android.app.DatePickerDialog(this, { _, y, m, dOfM ->
            val nd = String.format("%04d-%02d-%02d", y, m + 1, dOfM)
            apiCall({ api.postponeTask(task.id, nd, "Перенос из мобильного приложения") }) {
                toast("Заявка перенесена на $nd")
                refresh()
            }
        }, cal.get(java.util.Calendar.YEAR), cal.get(java.util.Calendar.MONTH),
            cal.get(java.util.Calendar.DAY_OF_MONTH)).show()
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
        val html = buildString {
            append("Контрагент: ${t.contractor}<br>")
            append("Контактное лицо: ${t.contact}<br>")
            if (t.phone.isNotBlank()) {
                val p = t.phone.replace(Regex("[^+0-9]"), "")
                append("Телефон: <a href=\"tel:$p\"><b>${t.phone}</b></a><br>")
            }
            append("Работа: ${t.work}<br>")
            append("Срочность: ${t.priorityLabel}<br>")
            append("Статус: ${t.statusLabel}<br>")
            if (t.timeWindow.isNotBlank()) append("Окно визита: ${t.timeWindow}<br>")
            if (t.zone.isNotBlank()) append("Район: ${t.zone}<br>")
            append("Адрес: ${t.address}<br>")
            if (t.equipment.isNotBlank()) append("Оборудование: ${t.equipment} ${t.serial}<br>")
            if (t.plannedDate.isNotBlank()) append("Плановая дата: ${t.plannedDate}<br>")
            if (t.comment.isNotBlank()) append("<br>Пояснение: ${t.comment}")
        }
        val dlg = AlertDialog.Builder(this)
            .setTitle("${t.number} — ${t.work}")
            .setMessage(android.text.Html.fromHtml(html.toString(), android.text.Html.FROM_HTML_MODE_LEGACY))
            .setPositiveButton("Поехали") { _, _ -> go(t) }
            .setNegativeButton("Закрыть", null)
            .show()
        // телефон в тексте кликабелен: открывается штатная звонилка
        dlg.findViewById<TextView>(android.R.id.message)?.movementMethod =
            android.text.method.LinkMovementMethod.getInstance()
    }

    // --------------------------------------------------------- многопоточность
    private fun apiCall(work: () -> Unit, silent: Boolean = false, onOk: (() -> Unit)? = null,
                        onError: (() -> Unit)? = null) {
        Thread {
            try {
                work()
                if (onOk != null) runOnUiThread(onOk)
            } catch (e: Exception) {
                if (onError != null) runOnUiThread(onError)
                if (!silent) runOnUiThread { toast("Ошибка: ${e.message}") }
            }
        }.start()
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
}
