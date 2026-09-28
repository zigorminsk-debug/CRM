package by.crm.engineer

import android.app.*
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
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
            startForeground(NOTIFICATION_ID, buildNotification("Обмен с сервером", "Ожидание новых заявок"))
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
                    for (i in 0 until events.length()) {
                        val ev = events.getJSONObject(i)
                        val kind = ev.optString("kind")
                        val payload = ev.optJSONObject("payload") ?: JSONObject()
                        val title = when (kind) {
                            "task.new" -> "Новая заявка"
                            "task.postponed" -> "Заявка перенесена"
                            "task.pickup" -> "Оформлен забор в офис"
                            "zone.replacement" -> "Вам передана зона (замена)"
                            else -> null
                        } ?: continue
                        val body = listOf(payload.optString("number"), payload.optString("address"),
                                          payload.optString("priority")).filter { it.isNotBlank() }.joinToString(" · ")
                        notify(title, body)
                        since = ev.optLong("id", since)
                    }
                    prefs.edit().putLong("feed_id", since).apply()
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
        }
    }

    private fun buildNotification(title: String, body: String): Notification {
        val intent = Intent(this, MainActivity::class.java)
        val pi = PendingIntent.getActivity(this, 0, intent,
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) PendingIntent.FLAG_IMMUTABLE else 0)
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .setContentIntent(pi)
            .build()
    }

    private fun notify(title: String, body: String) {
        val mgr = getSystemService(NotificationManager::class.java)
        mgr.notify(System.currentTimeMillis().toInt() and 0xFFFF, buildNotification(title, body))
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        running = false
        thread?.interrupt()
        super.onDestroy()
    }

    companion object {
        private const val CHANNEL_ID = "crm_tasks"
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
