package by.crm.engineer

import android.content.Context
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.os.Build
import java.util.concurrent.atomic.AtomicLong

/**
 * Световое уведомление о новой заявке: мигание фонариком (факел основной камеры).
 * Звон монет играет сам канал уведомлений (res/raw/new_request_coins.ogg),
 * поэтому здесь — только фонарик. Фонарик без звука сработает даже при
 * выключенной громкости уведомлений.
 */
object NewRequestAlert {

    private val lastFlash = AtomicLong(0L)

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
