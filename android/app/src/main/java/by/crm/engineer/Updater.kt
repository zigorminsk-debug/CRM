package by.crm.engineer

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.util.Log
import androidx.core.content.FileProvider
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/**
 * Автообновление приложения инженера.
 *
 * Схема подписана постоянным ключом проекта (`signing/android-release.p12`), поэтому
 * новая сборка ставится «поверх» установленной как обычное обновление, данные и настройки
 * сохраняются. Порядок действий:
 *   1) сервер отдаёт `/api/updates` — сведения о последнем релизе GitHub (версия, SHA-256, ссылка);
 *   2) сравниваем номер сборки с установленным;
 *   3) скачиваем APK, проверяем SHA-256 и открываем системный установщик.
 */
object Updater {

    private const val TAG = "CRM-Updater"
    private const val PREFS = "crm"
    private const val LAST_CHECK = "update_last_check"
    private const val GITHUB_MANIFEST =
        "https://github.com/zigorminsk-debug/CRM/releases/latest/download/update.json"

    data class Info(
        val version: String,
        val build: Int,
        val notes: String,
        val url: String,
        val sha256: String,
        val size: Long,
        val stale: Boolean = false
    )

    /** Последние сведения, полученные от сервера (для диагностики в 🔔). */
    var lastInfo: Info? = null
        private set

    /** Версия установленного приложения. */
    fun installedVersion(ctx: Context): Pair<String, Int> {
        return try {
            val pi = ctx.packageManager.getPackageInfo(ctx.packageName, 0)
            val code = if (Build.VERSION.SDK_INT >= 28) pi.longVersionCode.toInt() else @Suppress("DEPRECATION") pi.versionCode
            pi.versionName.orEmpty() to code
        } catch (e: Exception) {
            "1.0" to 0
        }
    }

    private fun serverBase(ctx: Context): String =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .getString("server", "http://10.0.2.2:8000")!!.trimEnd('/')

    private fun parseInfo(latest: JSONObject, fromGithub: Boolean): Info? {
        val apk = latest.optJSONObject("android") ?: return null
        val info = Info(
            version = latest.optString("version"),
            build = latest.optInt("build"),
            notes = latest.optString("notes"),
            url = apk.optString("url"),
            sha256 = apk.optString("sha256"),
            size = apk.optLong("size"),
            stale = !fromGithub && latest.optBoolean("stale", false)
        )
        lastInfo = info
        return if (info.url.isEmpty()) null else info
    }

    /** Запрос к серверу: что опубликовано в последнем релизе. Возвращает null при ошибке. */
    fun fetch(ctx: Context): Info? {
        return try {
            val url = URL("${serverBase(ctx)}/api/updates")
            val conn = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                connectTimeout = 8000
                readTimeout = 15000
                setRequestProperty("Accept", "application/json")
            }
            val text = conn.inputStream.bufferedReader(Charsets.UTF_8).use { it.readText() }
            conn.disconnect()
            val latest = JSONObject(text).optJSONObject("latest") ?: return null
            parseInfo(latest, fromGithub = false)
        } catch (e: Exception) {
            Log.w(TAG, "Не удалось проверить обновления: ${e.message}")
            null
        }
    }

    /**
     * Манифест последнего релиза ПРЯМО с GitHub — в обход сервера.
     * Нужен, когда компьютер сервера не может связаться с GitHub и раздаёт
     * устаревшие сведения (телефон обновился бы, а сервер об этом не знает).
     */
    private fun fetchFromGitHub(): Info? {
        return try {
            val url = URL(GITHUB_MANIFEST)
            val conn = (url.openConnection() as HttpURLConnection).apply {
                connectTimeout = 8000
                readTimeout = 15000
                setRequestProperty("Accept", "application/json")
            }
            val text = conn.inputStream.bufferedReader(Charsets.UTF_8).use { it.readText() }
            conn.disconnect()
            parseInfo(JSONObject(text), fromGithub = true)
        } catch (e: Exception) {
            Log.w(TAG, "GitHub напрямую: ${e.message}")
            null
        }
    }

    /**
     * Проверка обновления.
     * @param silent — не беспокоить инженера, если обновлений нет (проверка при запуске)
     * @param onAvailable — вызывается в UI-потоке, если доступна версия новее установленной
     */
    fun check(activity: Activity, silent: Boolean, onAvailable: (Info) -> Unit) {
        val (installedName, installedCode) = installedVersion(activity)
        activity.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit().putLong(LAST_CHECK, System.currentTimeMillis()).apply()

        Thread {
            // Сервер может раздавать устаревшие сведения (нет связи с GitHub) —
            // тогда дублируем запрос напрямую в GitHub и берём самое свежее.
            val fromServer = fetch(activity)
            val info = if (fromServer == null || fromServer.stale) fetchFromGitHub() ?: fromServer
                       else fromServer
            activity.runOnUiThread {
                when {
                    info == null -> if (!silent) alert(activity,
                        "Проверка обновлений",
                        "Не удалось получить сведения о релизе. Проверьте связь с сервером и интернет.")

                    info.build > installedCode -> onAvailable(info)

                    installedCode >= info.build && info.build > 0 -> {
                        val msg = StringBuilder()
                        if (installedCode > info.build) {
                            msg.append("Установлена $installedName — она НОВЕЕ, чем известно серверу (${info.version}).")
                            msg.append("\n\nЭто значит, что сервер давно не мог связаться с GitHub:")
                            msg.append("\n• обновите CRM-Server.exe на сервере;")
                            msg.append("\n• проверьте интернет на компьютере сервера.")
                            msg.append("\nПосле этого обновления снова будут приходить сами.")
                        } else {
                            msg.append("Установлена последняя версия $installedName.")
                        }
                        if (info.stale) {
                            msg.append("\n\n⚠ Сведения сервера устарели (нет связи с GitHub).")
                        }
                        alert(activity, "Обновление", msg.toString())
                    }

                    !silent -> alert(activity, "Обновление",
                        "Установлена последняя версия $installedName.")
                }
            }
        }.start()
    }

    /** Скачивание и установка «поверх» установленной версии. */
    fun downloadAndInstall(activity: Activity, info: Info) {
        val dialog = AlertDialog.Builder(activity)
            .setTitle("Обновление ${info.version}")
            .setMessage("Скачиваем и устанавливаем поверх версии ${installedVersion(activity).first}…")
            .setCancelable(false)
            .create()
        dialog.show()

        Thread {
            try {
                val dir = activity.getExternalFilesDir(null) ?: activity.filesDir
                val apk = File(dir, "CRM-Engineer-${info.version}.apk")
                val url = URL(info.url)
                val conn = (url.openConnection() as HttpURLConnection).apply {
                    connectTimeout = 15000
                    readTimeout = 60000
                }
                conn.inputStream.use { input ->
                    FileOutputStream(apk).use { out -> input.copyTo(out) }
                }
                conn.disconnect()

                if (info.sha256.isNotEmpty()) {
                    val digest = MessageDigest.getInstance("SHA-256")
                    apk.inputStream().use { ins ->
                        val buf = ByteArray(64 * 1024)
                        while (true) {
                            val n = ins.read(buf)
                            if (n <= 0) break
                            digest.update(buf, 0, n)
                        }
                    }
                    val got = digest.digest().joinToString("") { "%02x".format(it) }
                    if (!got.equals(info.sha256, ignoreCase = true)) {
                        apk.delete()
                        activity.runOnUiThread {
                            dialog.dismiss()
                            alert(activity, "Обновление", "Контрольная сумма файла не совпала — обновление отменено.")
                        }
                        return@Thread
                    }
                }
                activity.runOnUiThread {
                    dialog.dismiss()
                    install(activity, apk)
                }
            } catch (e: Exception) {
                Log.w(TAG, "Ошибка загрузки обновления: ${e.message}")
                activity.runOnUiThread {
                    dialog.dismiss()
                    alert(activity, "Обновление", "Не удалось скачать файл: ${e.message}")
                }
            }
        }.start()
    }

    private fun install(activity: Activity, apk: File) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            !activity.packageManager.canRequestPackageInstalls()
        ) {
            AlertDialog.Builder(activity)
                .setTitle("Разрешите установку")
                .setMessage("Android спросит подтверждение установки приложения из этого источника. " +
                            "Разрешите установку для CRM-инженера и повторите обновление.")
                .setPositiveButton("Открыть настройки") { _, _ ->
                    activity.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                        Uri.parse("package:${activity.packageName}")))
                }
                .setNegativeButton("Позже", null)
                .show()
            return
        }
        val uri: Uri = FileProvider.getUriForFile(activity, "${activity.packageName}.updates", apk)
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        activity.startActivity(intent)
    }

    private fun alert(activity: Activity, title: String, text: String) {
        AlertDialog.Builder(activity).setTitle(title).setMessage(text).setPositiveButton("ОК", null).show()
    }
}
