package by.crm.engineer

import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.CheckBox
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView

private const val COLOR_EMERGENCY = 0xFFB91C1C.toInt()
private const val COLOR_URGENT = 0xFFB45309.toInt()
private const val COLOR_NORMAL = 0xFF1D4ED8.toInt()
private const val COLOR_PLANNED = 0xFF15803D.toInt()

/** Список заявок в маршруте: «Поехали», чекбоксы готовности, подробности, перенос. */
class TaskAdapter(
    private val onGo: (Task) -> Unit,
    private val onDone: (Task, String) -> Unit,
    private val onDetails: (Task) -> Unit,
    private val onPostpone: (Task) -> Unit
) : RecyclerView.Adapter<TaskAdapter.VH>() {

    private val items = mutableListOf<Task>()

    fun submit(list: List<Task>) {
        items.clear()
        items.addAll(list)
        notifyDataSetChanged()
    }

    class VH(view: View) : RecyclerView.ViewHolder(view) {
        val order: TextView = view.findViewById(R.id.itemOrder)
        val number: TextView = view.findViewById(R.id.itemNumber)
        val priority: TextView = view.findViewById(R.id.itemPriority)
        val eta: TextView = view.findViewById(R.id.itemEta)
        val work: TextView = view.findViewById(R.id.itemWork)
        val address: TextView = view.findViewById(R.id.itemAddress)
        val contact: TextView = view.findViewById(R.id.itemContact)
        val comment: TextView = view.findViewById(R.id.itemComment)
        val go: View = view.findViewById(R.id.btnGo)
        val details: View = view.findViewById(R.id.btnDetails)
        val postpone: View = view.findViewById(R.id.btnPostpone)
        val doneOnsite: CheckBox = view.findViewById(R.id.chkOnsite)
        val donePickup: CheckBox = view.findViewById(R.id.chkPickup)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH =
        VH(LayoutInflater.from(parent.context).inflate(R.layout.item_task, parent, false))

    override fun getItemCount(): Int = items.size

    override fun onBindViewHolder(holder: VH, position: Int) {
        val t = items[position]
        holder.order.text = if (t.order > 0) t.order.toString() else "•"
        holder.number.text = t.number
        holder.priority.text = t.priorityLabel
        holder.priority.setBackgroundColor(
            when (t.priority) {
                "emergency" -> COLOR_EMERGENCY
                "urgent" -> COLOR_URGENT
                "planned" -> COLOR_PLANNED
                else -> COLOR_NORMAL
            }
        )
        holder.eta.text = buildString {
            if (t.timeWindow.isNotBlank()) append(t.timeWindow)
            if (t.eta.isNotEmpty()) {
                if (isNotEmpty()) append("  ·  ")
                append("прибытие ~${t.eta}")
            }
        }
        holder.eta.visibility = if (holder.eta.text.isBlank()) View.GONE else View.VISIBLE
        holder.work.text = t.work
        holder.address.text = t.address
        holder.contact.text = buildString {
            append(t.contact)
            if (t.phone.isNotBlank()) append(" · ☎ ${t.phone}")
            if (t.legKm > 0) append(" · ${t.legKm} км / ${t.driveMinutes} мин")
        }
        // клик по строке контакта — звонок через штатную звонилку
        if (t.phone.isNotBlank()) {
            holder.contact.setTextColor(0xFF1D4ED8.toInt())
            holder.contact.paint.isUnderlineText = true
            holder.contact.setOnClickListener { v ->
                val p = t.phone.replace(Regex("[^+0-9]"), "")
                if (p.isNotEmpty()) v.context.startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:$p")))
            }
        } else {
            holder.contact.paint.isUnderlineText = false
            holder.contact.setOnClickListener(null)
        }
        holder.comment.text = t.comment
        holder.comment.visibility = if (t.comment.isBlank()) View.GONE else View.VISIBLE

        holder.doneOnsite.setOnCheckedChangeListener(null)
        holder.donePickup.setOnCheckedChangeListener(null)
        holder.doneOnsite.isChecked = t.status == "done_onsite"
        holder.donePickup.isChecked = t.status == "pickup_office"

        holder.go.setOnClickListener { onGo(t) }
        holder.details.setOnClickListener { onDetails(t) }
        holder.postpone.setOnClickListener { onPostpone(t) }
        holder.doneOnsite.setOnCheckedChangeListener { _, checked ->
            if (checked) onDone(t, "onsite")
        }
        holder.donePickup.setOnCheckedChangeListener { _, checked ->
            if (checked) onDone(t, "pickup_office")
        }
    }
}

/** Список доставок заказчикам после «забора в офис». */
class DeliveryAdapter(
    private val onDelivered: (Delivery) -> Unit,
    private val onPostpone: (Delivery) -> Unit,
    private val onNavigate: (Delivery) -> Unit
) : RecyclerView.Adapter<DeliveryAdapter.VH>() {

    private val items = mutableListOf<Delivery>()

    fun submit(list: List<Delivery>) {
        items.clear()
        items.addAll(list)
        notifyDataSetChanged()
    }

    class VH(view: View) : RecyclerView.ViewHolder(view) {
        val title: TextView = view.findViewById(R.id.deliveryTitle)
        val date: TextView = view.findViewById(R.id.deliveryDate)
        val address: TextView = view.findViewById(R.id.deliveryAddress)
        val contact: TextView = view.findViewById(R.id.deliveryContact)
        val note: TextView = view.findViewById(R.id.deliveryNote)
        val btnDone: View = view.findViewById(R.id.btnDelivered)
        val btnPostpone: View = view.findViewById(R.id.btnDeliveryPostpone)
        val btnNav: View = view.findViewById(R.id.btnDeliveryNav)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH =
        VH(LayoutInflater.from(parent.context).inflate(R.layout.item_delivery, parent, false))

    override fun getItemCount(): Int = items.size

    override fun onBindViewHolder(holder: VH, position: Int) {
        val d = items[position]
        holder.title.text = "${d.number} · ${d.work}"
        holder.date.text = "Выдача: ${d.scheduledDate}"
        holder.address.text = d.address
        holder.contact.text = listOf(d.contact, d.phone).filter { it.isNotBlank() }.joinToString(" · ")
        holder.note.text = if (d.postponeCount > 0) "Переносов: ${d.postponeCount}. ${d.comment}" else d.comment
        holder.btnDone.setOnClickListener { onDelivered(d) }
        holder.btnPostpone.setOnClickListener { onPostpone(d) }
        holder.btnNav.setOnClickListener { onNavigate(d) }
        holder.contact.setOnClickListener { v ->
            val p = d.phone.replace(Regex("[^+0-9]"), "")
            if (p.isNotEmpty()) v.context.startActivity(Intent(Intent.ACTION_DIAL, Uri.parse("tel:$p")))
        }
    }
}
