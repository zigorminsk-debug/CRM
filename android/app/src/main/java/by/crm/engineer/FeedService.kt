package by.crm.engineer

import android.app.*
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.net.Uri
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import org.json.JSONObject

/**
 * Фоновое получение новых заявок и уведомления инженеру.
 * Сервер отдаёт ленту через long-poll /api/engineer/feed, при новом событии появляется уведомление.
 */
class FeedService : Service() {

    private var thread: Thread? = null
    @Volatile private var running = false

    override fun onCreate() {
        super.onCreate()
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!running) {
            running = true
            startForeground(NOTIFICATION_ID, buildNotification("Обмен с сервером", "Ожидание новых заявок", CHANNEL_ID))
            thread = Thread { loop() }.also { it.start() }
        }
        return START_STICKY
    }

    private fun loop() {
        val api = Api(this)
        val prefs = getSharedPreferences("crm", Context.MODE_PRIVATE)
        var since = prefs.getLong("feed_id", 0L)
        while (running) {
            if (api.token.isEmpty()) {
                sleep(10_000); continue
            }
            try {
                val res: JSONObject = api.feed(since, 25)
                val events = res.optJSONArray("events")
                if (events != null && events.length() > 0) {
                    // «Сегодня» по локальной дате устройства
                    val today = SimpleDateFormat("yyyy-MM-dd", Locale.US).format(Date())
                    var sawToday = false
                    for (i in 0 until events.length()) {
                        val ev = events.getJSONObject(i)
                        val kind = ev.optString("kind")
                        val payload = ev.optJSONObject("payload") ?: JSONObject()
                        val evDate = payload.optString("date")
                        val baseTitle = when (kind) {
                            "task.new" -> "Новая заявка"
                            "task.postponed" -> "Заявка перенесена"
                            "task.pickup" -> "Оформлен забор в офис"
                            "zone.replacement" -> "Вам передана зона (замена)"
                            else -> null
                        } ?: continue
                        // Монеты и фонарик — только для заявки на СЕГОДНЯ (или без даты —
                        // на случай старого сервера). На другой день — тихое уведомление.
                        val isToday = when (kind) {
                            "task.new" -> evDate.isBlank() || evDate == today
                            else -> false
                        }
                        val title = if (kind == "task.new" && !isToday && evDate.isNotBlank()) {
                            val pretty = try {
                                val d = SimpleDateFormat("yyyy-MM-dd", Locale.US).parse(evDate)
                                SimpleDateFormat("dd.MM", Locale.US).format(d!!)
                            } catch (e: Exception) { evDate }
                            "$baseTitle на $pretty"
                        } else baseTitle
                        val body = listOf(payload.optString("number"), payload.optString("address"),
                                          payload.optString("priority")).filter { it.isNotBlank() }.joinToString(" · ")
                        // Сегодняшняя — канал со звоном монет; остальное — обычный канал
                        notify(title, body, if (isToday) CHANNEL_NEW else CHANNEL_ID)
                        if (isToday) sawToday = true
                        since = ev.optLong("id", since)
                    }
                    prefs.edit().putLong("feed_id", since).apply()
                    // световое уведомление: мигание фонариком (один раз на партию заявок)
                    if (sawToday) NewRequestAlert.flash(this@FeedService)
                }
            } catch (e: Exception) {
                sleep(15_000)
            }
        }
    }

    private fun sleep(ms: Long) = try { Thread.sleep(ms) } catch (e: InterruptedException) { }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val mgr = getSystemService(NotificationManager::class.java)
            val ch = NotificationChannel(CHANNEL_ID, "Заявки инженеру", NotificationManager.IMPORTANCE_HIGH)
            ch.description = "Новые заявки, переносы, передача зон на время отпуска"
            mgr.createNotificationChannel(ch)

            // Канал новой заявки: звук — звон монет в кассу
            // (Kenney RPG Audio «handleCoins», лицензия CC0, см. tools/coin_sound/)
            val coins = NotificationChannel(CHANNEL_NEW, "Новая заявка (звон монет)",
                NotificationManager.IMPORTANCE_HIGH)
            coins.description = "Звон монет и мигание фонарика при поступлении новой заявки"
            val attrs = AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_NOTIFICATION_EVENT)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                .build()
            coins.setSound(Uri.parse("android.resource://$packageName/${R.raw.new_request_coins}"), attrs)
            coins.enableVibration(true)
            coins.vibrationPattern = longArrayOf(0, 150, 110, 150)
            coins.enableLights(true)
            coins.lightColor = 0xFFFFC24A.toInt()
            mgr.createNotificationChannel(coins)
        }
    }

    private fun buildNotification(title: String, body: String, channelId: String): Notification {
        val intent = Intent(this, MainActivity::class.java)
        val pi = PendingIntent.getActivity(this, 0, intent,
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) PendingIntent.FLAG_IMMUTABLE else 0)
        return NotificationCompat.Builder(this, channelId)
            .setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .setContentIntent(pi)
            .build()
    }

    private fun notify(title: String, body: String, channelId: String = CHANNEL_ID) {
        val mgr = getSystemService(NotificationManager::class.java)
        mgr.notify(System.currentTimeMillis().toInt() and 0xFFFF, buildNotification(title, body, channelId))
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        running = false
        thread?.interrupt()
        super.onDestroy()
    }

    companion object {
        private const val CHANNEL_ID = "crm_tasks"
        private const val CHANNEL_NEW = "crm_tasks_coins"
        private const val NOTIFICATION_ID = 1001

        fun start(ctx: Context) {
            val i = Intent(ctx, FeedService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) ctx.startForegroundService(i) else ctx.startService(i)
        }

        fun stop(ctx: Context) {
            ctx.stopService(Intent(ctx, FeedService::class.java))
        }
    }
}
