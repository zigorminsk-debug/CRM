package by.crm.engineer

import android.content.Context
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import java.util.concurrent.atomic.AtomicLong

/**
 * Комплексное уведомление о новой заявке на сегодня:
 *   1) звон монет — MediaPlayer прямо из сервиса, на ГРОМКОСТИ МУЛЬТИМЕДИА
 *      (та самая, которую поднимают кнопками громкости; громкость уведомлений
 *      на многих телефонах выключена — из-за этого звук «пропадал»);
 *   2) вибрация — короткий двойной импульс;
 *   3) мигание фонариком (факел основной камеры).
 * Каждую составляющую можно отключить в настройках сигнала приложения
 * (SharedPreferences "crm": alert_sound / alert_vibrate / alert_flash).
 */
object NewRequestAlert {

    private val lastAlert = AtomicLong(0L)
    private val lastFlash = AtomicLong(0L)

    private fun pref(context: Context, key: String) =
        context.getSharedPreferences("crm", Context.MODE_PRIVATE).getBoolean(key, true)

    /** Звон монет + вибрация + вспышки (по настройкам, не чаще раза в 4 с). */
    fun alert(context: Context) {
        val now = System.currentTimeMillis()
        val prev = lastAlert.get()
        if (now - prev < 4000) return
        if (!lastAlert.compareAndSet(prev, now)) return
        perform(context)
    }

    /** Проверка сигнала из настроек: проиграть сразу, не обращая внимания на паузу. */
    fun test(context: Context) {
        lastAlert.set(0L)
        lastFlash.set(0L)
        perform(context)
    }

    private fun perform(context: Context) {
        if (pref(context, "alert_sound")) playCoins(context)
        if (pref(context, "alert_vibrate")) vibrate(context)
        if (pref(context, "alert_flash")) flash(context)
    }

    /** Звон монет из res/raw на громкости МУЛЬТИМЕДИА (USAGE_MEDIA). */
    private fun playCoins(context: Context) {
        try {
            val attrs = AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ALARM)   // громкость будильника: не на нуле
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                .build()
            val mp = MediaPlayer.create(context, R.raw.new_request_coins, attrs, 0)
            if (mp != null) {
                mp.setOnCompletionListener { it.release() }
                mp.start()
            }
        } catch (e: Exception) {
            // звук не критичен — тишина лучше падения сервиса
        }
    }

    /** Двойной виброимпульс. */
    private fun vibrate(context: Context) {
        try {
            val vib: Vibrator? = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                (context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager)?.defaultVibrator
            } else {
                @Suppress("DEPRECATION")
                context.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
            }
            vib?.vibrate(VibrationEffect.createWaveform(longArrayOf(0, 150, 110, 150), -1))
        } catch (e: Exception) {
            // нет вибромотора — не страшно
        }
    }

    /** Серия из 6 вспышек (~1,6 с); не чаще одного раза в 4 секунды. */
    fun flash(context: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return
        val now = System.currentTimeMillis()
        val prev = lastFlash.get()
        if (now - prev < 4000) return
        if (!lastFlash.compareAndSet(prev, now)) return

        Thread {
            val cm = context.getSystemService(Context.CAMERA_SERVICE) as? CameraManager
                ?: return@Thread
            val cameraId = try {
                cm.cameraIdList.firstOrNull { id ->
                    cm.getCameraCharacteristics(id)
                        .get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true
                }
            } catch (e: Exception) {
                null
            } ?: return@Thread          // нет вспышки (например, планшет) — тихо выходим
            try {
                repeat(6) {
                    cm.setTorchMode(cameraId, true)
                    Thread.sleep(130)
                    cm.setTorchMode(cameraId, false)
                    Thread.sleep(130)
                }
            } catch (e: Exception) {
                // камера занята или вспышка недоступна — не критично
            } finally {
                try { cm.setTorchMode(cameraId, false) } catch (ignored: Exception) { }
            }
        }.start()
    }
}
