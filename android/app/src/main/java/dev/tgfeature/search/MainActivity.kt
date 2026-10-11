package dev.tgfeature.search

import android.content.Intent
import android.content.Context
import android.graphics.BitmapFactory
import android.os.Bundle
import android.provider.DocumentsContract
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import java.io.File
import java.time.LocalDate
import java.time.ZoneOffset
import org.json.JSONObject

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            val model: SearchViewModel = viewModel()
            SideEffect {
                window.statusBarColor = if (model.darkTheme) android.graphics.Color.rgb(20, 20, 24) else android.graphics.Color.WHITE
                window.navigationBarColor = window.statusBarColor
                window.decorView.systemUiVisibility = if (model.darkTheme) 0 else android.view.View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR or android.view.View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR
            }
            MaterialTheme(colorScheme = if (model.darkTheme) darkColorScheme() else lightColorScheme()) {
                SearchScreen(model)
            }
        }
    }
}

@Composable
private fun SearchScreen(model: SearchViewModel = viewModel()) {
    var filtersExpanded by remember { mutableStateOf(false) }
    var historyExpanded by remember { mutableStateOf(false) }
    var downloadConfirmation by remember { mutableStateOf(false) }
    var query by remember { mutableStateOf("") }
    var chat by remember { mutableStateOf<String?>(null) }
    var sender by remember { mutableStateOf<String?>(null) }
    var from by remember { mutableStateOf("") }
    var to by remember { mutableStateOf("") }
    var dateError by remember { mutableStateOf<String?>(null) }
    val context = LocalContext.current
    val importModel = rememberLauncherForActivityResult(ImportDocument()) { it?.let { uri -> model.import(uri, true) } }
    val importSnapshot = rememberLauncherForActivityResult(ImportDocument()) { it?.let { uri -> model.import(uri, false) } }
    val updateFolder = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { it?.let { uri -> model.setUpdateFolder(uri) } }
    if (downloadConfirmation) AlertDialog(
        onDismissRequest = { downloadConfirmation = false },
        title = { Text("Скачать E5-small?") },
        text = { Text("Около 136 МБ с Hugging Face. Лучше использовать Wi-Fi. Файлы проверяются по SHA-256; ваши запросы и архив не отправляются.") },
        confirmButton = { TextButton(onClick = { downloadConfirmation = false; model.downloadModel() }) { Text("Скачать") } },
        dismissButton = { TextButton(onClick = { downloadConfirmation = false }) { Text("Отмена") } }
    )
    Surface(Modifier.fillMaxSize()) {
        LazyColumn(Modifier.fillMaxSize().systemBarsPadding().padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
          item {
           Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text("tgVisor", style = MaterialTheme.typography.headlineSmall)
                TextButton(onClick = { model.toggleTheme() }) { Text(if (model.darkTheme) "Светлая" else "Тёмная") }
            }
            Text("По смыслу — в вашем архиве", style = MaterialTheme.typography.bodyMedium)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(enabled = !model.busy, onClick = { importModel.launch(arrayOf("*/*")) }) { Text("Модель") }
                OutlinedButton(enabled = !model.busy, onClick = { importSnapshot.launch(arrayOf("*/*")) }) { Text("Импорт архива") }
            }
            Text(model.modelStatus, color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.titleSmall)
            TextButton(enabled = !model.busy, onClick = { downloadConfirmation = true }) { Text("Скачать E5-small из интернета") }
            Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer), modifier = Modifier.fillMaxWidth()) {
                Text(model.archiveStatus, modifier = Modifier.padding(12.dp), color = MaterialTheme.colorScheme.onSecondaryContainer, style = MaterialTheme.typography.titleSmall)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                TextButton(enabled = !model.busy, onClick = { updateFolder.launch(null) }) { Text("Папка обновлений") }
                TextButton(enabled = !model.busy, onClick = { model.checkUpdates() }) { Text("Обновить") }
            }
            OutlinedTextField(value = query, onValueChange = { query = it }, label = { Text("Например: спящий человек") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
            Box {
                TextButton(enabled = model.history.isNotEmpty(), onClick = { historyExpanded = true }) { Text("История (${model.history.size}) ▾") }
                DropdownMenu(historyExpanded, onDismissRequest = { historyExpanded = false }) {
                    model.history.forEach { previous ->
                        DropdownMenuItem(text = { Text(previous) }, onClick = { query = previous; historyExpanded = false })
                    }
                    DropdownMenuItem(text = { Text("Очистить историю") }, onClick = { model.clearHistory(); historyExpanded = false })
                }
            }
            TextButton(onClick = { filtersExpanded = !filtersExpanded }) { Text(if (filtersExpanded) "Фильтры ▴" else "Фильтры ▾") }
            if (filtersExpanded) {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                ChoiceFilter("Все чаты", model.facets.optJSONArray("chats"), chat) { chat = it }
                ChoiceFilter("Все отправители", model.facets.optJSONArray("senders"), sender) { sender = it }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(from, { from = it }, label = { Text("С даты (ГГГГ-ММ-ДД)") }, singleLine = true, modifier = Modifier.weight(1f))
                OutlinedTextField(to, { to = it }, label = { Text("По дату") }, singleLine = true, modifier = Modifier.weight(1f))
            }
            TextButton(onClick = { chat = null; sender = null; from = ""; to = ""; dateError = null }) { Text("Сбросить фильтры") }
            }
            Button(enabled = model.ready && !model.busy && query.isNotBlank(), onClick = {
                try {
                    val filters = JSONObject()
                    chat?.let { filters.put("chat_id", it) }
                    sender?.let { filters.put("sender_id", it) }
                    if (from.isNotBlank()) filters.put("from_date", LocalDate.parse(from).atStartOfDay().toEpochSecond(ZoneOffset.UTC))
                    if (to.isNotBlank()) filters.put("to_date", LocalDate.parse(to).plusDays(1).atStartOfDay().toEpochSecond(ZoneOffset.UTC) - 1)
                    dateError = null
                    model.search(query, filters)
                } catch (_: java.time.format.DateTimeParseException) { dateError = "Введите дату в формате ГГГГ-ММ-ДД" }
            }, modifier = Modifier.fillMaxWidth()) { Text("Найти") }
            dateError?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            Text(model.status, style = MaterialTheme.typography.bodySmall)
            if (model.busy) LinearProgressIndicator(Modifier.fillMaxWidth())
           }
          }
                items(model.hits, key = { it.getString("image_id") }) { hit ->
                    Card(Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            val thumbnail = hit.optString("thumbnail").takeUnless { it.isEmpty() || it == "null" }
                            val bitmap = remember(model.snapshotRoot, thumbnail) { thumbnail?.let { decodeThumbnail(File(model.snapshotRoot, it)) } }
                            bitmap?.let { Image(it.asImageBitmap(), contentDescription = hit.getString("description"), modifier = Modifier.fillMaxWidth().heightIn(max = 180.dp)) }
                            Text(hit.getString("description"))
                            val sources = hit.getJSONArray("sources")
                            for (i in 0 until sources.length()) {
                                val source = sources.getJSONObject(i)
                                val link = source.optString("link").takeUnless { it.isEmpty() || it == "null" }
                                val sender = source.optString("sender_name").takeUnless { it == "null" } ?: ""
                                val label = "${source.getString("chat_name")} · $sender"
                                if (link != null && TelegramMessageLink.parse(link) != null) {
                                    TextButton(onClick = {
                                        if (!TelegramLinkOpener.open(context, link)) {
                                            android.widget.Toast.makeText(context, "Не удалось открыть сообщение", android.widget.Toast.LENGTH_SHORT).show()
                                        }
                                    }) { Text(label) }
                                } else { Text(label, style = MaterialTheme.typography.bodySmall) }
                            }
                        }
                    }
                }
        }
    }
}

/** OEM file providers use different MIME types for ZIP and may omit new files from Recents. */
private class ImportDocument : ActivityResultContracts.OpenDocument() {
    override fun createIntent(context: Context, input: Array<String>): Intent =
        super.createIntent(context, input).putExtra(
            DocumentsContract.EXTRA_INITIAL_URI,
            DocumentsContract.buildDocumentUri("com.android.externalstorage.documents", "primary:Download")
        )
}

@Composable
private fun ChoiceFilter(label: String, choices: org.json.JSONArray?, selected: String?, change: (String?) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    val entries = (0 until (choices?.length() ?: 0)).map { choices!!.getJSONObject(it) }
    Box {
        OutlinedButton(onClick = { expanded = true }) { Text(entries.firstOrNull { it.getString("id") == selected }?.getString("name") ?: label) }
        DropdownMenu(expanded, onDismissRequest = { expanded = false }) {
            DropdownMenuItem(text = { Text(label) }, onClick = { change(null); expanded = false })
            entries.forEach { entry -> DropdownMenuItem(text = { Text(entry.getString("name")) }, onClick = { change(entry.getString("id")); expanded = false }) }
        }
    }
}

private fun decodeThumbnail(file: File): android.graphics.Bitmap? {
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeFile(file.absolutePath, bounds)
    if (bounds.outWidth !in 1..30000 || bounds.outHeight !in 1..30000) return null
    var scale = 1
    while (bounds.outWidth / scale > 512 || bounds.outHeight / scale > 512) scale *= 2
    return BitmapFactory.decodeFile(file.absolutePath, BitmapFactory.Options().apply { inSampleSize = scale })
}
