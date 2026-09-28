package by.crm.engineer

import org.json.JSONObject

/** Заявка в маршруте инженера. */
data class Task(
    val id: Int,
    val number: String,
    val priority: String,
    val priorityLabel: String,
    val status: String,
    val statusLabel: String,
    val order: Int,
    val eta: String,
    val address: String,
    val lat: Double,
    val lon: Double,
    val contractor: String,
    val contact: String,
    val phone: String,
    val work: String,
    val comment: String,
    val equipment: String,
    val serial: String,
    val plannedDate: String,
    val timeFrom: String,
    val timeTo: String,
    val legKm: Double,
    val driveMinutes: Int,
    val workMinutes: Int,
    val zone: String
) {
    val hasCoords: Boolean get() = lat != 0.0 && lon != 0.0
    /** Желаемое окно визита «с … по …» (если указано диспетчером). */
    val timeWindow: String
        get() = if (timeFrom.isNotBlank()) "с $timeFrom по ${timeTo.ifBlank { "…" }}" else ""
    val navIntent: String get() = "yandexnavi://build_route_on_map?lat_to=$lat&lon_to=$lon"
    val navHttps: String get() = "https://yandex.ru/maps/?rtext=$lat,$lon&rtt=auto"

    companion object {
        fun from(j: JSONObject): Task = Task(
            id = j.optInt("id"),
            number = j.optString("number"),
            priority = j.optString("priority"),
            priorityLabel = j.optString("priority_label"),
            status = j.optString("status"),
            statusLabel = j.optString("status_label"),
            order = j.optInt("order"),
            eta = j.optString("eta"),
            address = j.optString("address"),
            lat = j.optDouble("lat", 0.0),
            lon = j.optDouble("lon", 0.0),
            contractor = j.optString("contractor"),
            contact = j.optString("contact_person"),
            phone = j.optString("phone_formatted", j.optString("phone")),
            work = j.optString("work_name"),
            comment = j.optString("comment"),
            equipment = j.optString("equipment"),
            serial = j.optString("serial"),
            plannedDate = j.optString("planned_date"),
            timeFrom = j.optString("time_from").let { if (it == "null") "" else it },
            timeTo = j.optString("time_to").let { if (it == "null") "" else it },
            legKm = j.optDouble("leg_km", 0.0),
            driveMinutes = j.optInt("drive_minutes"),
            workMinutes = j.optInt("work_minutes"),
            zone = j.optString("zone_name")
        )
    }
}

/** Выдача оборудования/картриджей заказчику после «забора в офис». */
data class Delivery(
    val id: Int,
    val number: String,
    val scheduledDate: String,
    val address: String,
    val contact: String,
    val phone: String,
    val work: String,
    val comment: String,
    val postponeCount: Int,
    val status: String,
    val lat: Double,
    val lon: Double
) {
    val navIntent: String get() = "yandexnavi://build_route_on_map?lat_to=$lat&lon_to=$lon"

    companion object {
        fun from(j: JSONObject): Delivery = Delivery(
            id = j.optInt("id"),
            number = j.optString("number"),
            scheduledDate = j.optString("scheduled_date"),
            address = j.optString("address"),
            contact = j.optString("contact_person"),
            phone = j.optString("phone"),
            work = j.optString("work_name"),
            comment = j.optString("comment"),
            postponeCount = j.optInt("postpone_count"),
            status = j.optString("status"),
            lat = j.optDouble("lat", 0.0),
            lon = j.optDouble("lon", 0.0)
        )
    }
}
