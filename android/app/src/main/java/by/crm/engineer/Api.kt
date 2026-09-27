package by.crm.engineer

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** Клиент API сервера CRM (заявки, маршрут на день, чекбоксы готовности). */
class Api(private val ctx: Context) {

    val base: String get() = prefs().getString("server", "http://10.0.2.2:8000")!!
    var token: String
        get() = prefs().getString("token", "")!!
        set(v) = prefs().edit().putString("token", v).apply()

    private fun prefs() = ctx.getSharedPreferences("crm", Context.MODE_PRIVATE)

    private fun request(method: String, path: String, body: JSONObject? = null): JSONObject {
        try {
            val url = URL(base.trimEnd('/') + path)
            val conn = url.openConnection() as HttpURLConnection
            conn.requestMethod = method
            conn.connectTimeout = 15000
            conn.readTimeout = 40000
            conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            conn.setRequestProperty("Accept", "application/json")
            if (token.isNotEmpty()) conn.setRequestProperty("Authorization", "Bearer $token")
            if (body != null) {
                conn.doOutput = true
                conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            }
            val code = conn.responseCode
            val stream = if (code in 200..299) conn.inputStream else (conn.errorStream ?: conn.inputStream)
            val text = BufferedReader(InputStreamReader(stream, Charsets.UTF_8)).use { it.readText() }
            val json = if (text.trim().startsWith("{")) JSONObject(text) else JSONObject("""{"items":$text}""")
            if (code !in 200..299) {
                throw ApiException(json.optString("error", json.optString("detail", "HTTP $code")))
            }
            return json
        } catch (e: ApiException) {
            throw e
        } catch (e: java.net.UnknownHostException) {
            throw ApiException("Сервер «${e.message}» не найден. Проверьте адрес сервера и Wi-Fi")
        } catch (e: java.net.ConnectException) {
            throw ApiException("Сервер отклонил подключение. Проверьте, что сервер запущен, и разрешите порт в брандмауэре Windows")
        } catch (e: java.net.SocketTimeoutException) {
            throw ApiException("Сервер не отвечает (таймаут). Проверьте, что сервер запущен и доступен по этому адресу")
        } catch (e: java.io.IOException) {
            throw ApiException("Нет связи с сервером (${e.javaClass.simpleName}). Проверьте адрес, интернет и брандмауэр")
        } catch (e: org.json.JSONException) {
            throw ApiException("По этому адресу отвечает не CRM-сервер. Проверьте адрес (должен быть вида http://IP:8000)")
        }
    }

    class ApiException(message: String) : Exception(message)

    // ------------------------------------------------------------------ служебные
    /** Проверка доступности сервера: GET /api/health (без авторизации). */
    fun health(): JSONObject = request("GET", "/api/health")

    // ------------------------------------------------------------------ вход
    fun saveServer(url: String) {
        var u = url.trim()
        if (!u.startsWith("http")) u = "http://$u"
        prefs().edit().putString("server", u.trimEnd('/')).apply()
    }

    fun login(username: String, password: String): JSONObject {
        val body = JSONObject()
            .put("username", username)
            .put("password", password)
            .put("device", "android")
        val res = request("POST", "/api/auth/login", body)
        token = res.optString("token")
        prefs().edit().putString("engineer", res.optString("full_name")).apply()
        return res
    }

    fun me(): JSONObject = request("GET", "/api/auth/me")

    // --------------------------------------------------------------- маршрут
    fun route(day: String): JSONObject = request("GET", "/api/engineer/route?day=$day")

    fun tasks(): JSONObject = request("GET", "/api/engineer/tasks")

    /** Кнопка «Поехали» — заявка переходит в работу, приложение открывает Яндекс.Навигатор. */
    fun go(id: Int): JSONObject = request("POST", "/api/engineer/task/$id/go", JSONObject())

    /** Чекбокс готовности: onsite — выполнено на месте, pickup_office — забор в офис. */
    fun done(id: Int, result: String, comment: String): JSONObject {
        val body = JSONObject().put("result", result).put("comment", comment)
        return request("POST", "/api/engineer/task/$id/done", body)
    }

    fun postponeTask(id: Int, newDay: String, reason: String): JSONObject =
        request("POST", "/api/requests/$id/postpone", JSONObject().put("new_day", newDay).put("reason", reason))

    fun deliveryDone(id: Int): JSONObject = request("POST", "/api/engineer/delivery/$id/done", JSONObject())

    fun deliveryPostpone(id: Int, days: Int, comment: String): JSONObject =
        request("POST", "/api/engineer/delivery/$id/postpone",
            JSONObject().put("days", days).put("comment", comment))

    /** Лента событий (новые заявки, переносы, передача зоны на время отпуска). */
    fun feed(since: Long, waitSec: Int): JSONObject =
        request("GET", "/api/engineer/feed?since=$since&wait=$waitSec")

    companion object {
        fun encode(s: String): String = URLEncoder.encode(s, "UTF-8")
    }
}
