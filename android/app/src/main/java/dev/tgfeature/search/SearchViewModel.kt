package dev.tgfeature.search

import android.app.Application
import android.net.Uri
import android.content.Intent
import android.util.AtomicFile
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import java.io.File
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

class SearchViewModel(application: Application) : AndroidViewModel(application) {
    var status by mutableStateOf("Импортируйте модель и архив для автономного поиска")
        private set
    var hits by mutableStateOf<List<JSONObject>>(emptyList())
        private set
    var snapshotRoot by mutableStateOf<File?>(null)
        private set
    var busy by mutableStateOf(false)
        private set
    var ready by mutableStateOf(false)
        private set
    var facets by mutableStateOf(JSONObject())
        private set
    var modelStatus by mutableStateOf("Модель не загружена")
        private set
    var archiveStatus by mutableStateOf("Архив не загружен")
        private set
    var history by mutableStateOf<List<String>>(emptyList())
        private set
    var darkTheme by mutableStateOf(true)
        private set
    private var handle = 0L
    private var encoder: E5Encoder? = null
    private val mutex = Mutex()
    private var searchJob: Job? = null
    private var generation = 0
    private val root = File(application.filesDir, "search").apply { mkdirs() }
    private val active = AtomicFile(File(root, "active.json"))
    private var modelDirectory: File? = null
    private val preferences = application.getSharedPreferences("updates", 0)

    init {
        darkTheme = preferences.getBoolean("dark_theme", true)
        val savedHistory = runCatching { JSONArray(preferences.getString("history", "[]")) }.getOrDefault(JSONArray())
        history = (0 until savedHistory.length()).map { savedHistory.getString(it) }.take(20)
        viewModelScope.launch {
            withContext(Dispatchers.IO) {
                mutex.withLock {
                    try {
                        NativeBridge.value(NativeBridge.version())
                        if (active.baseFile.exists()) {
                            val config = JSONObject(active.openRead().use { it.readBytes().toString(Charsets.UTF_8) })
                            preferences.edit().putLong("imported_at", config.optLong("imported_at")).apply()
                            val modelName = config.optString("model")
                            val snapshotName = config.optString("snapshot")
                            if (modelName.isNotEmpty()) {
                                modelDirectory = child(modelName)
                                encoder = E5Encoder(modelDirectory!!)
                            }
                            if (snapshotName.isNotEmpty()) {
                                val opened = NativeBridge.value(NativeBridge.open(child(snapshotName).absolutePath)) as JSONObject
                                handle = opened.getLong("handle")
                                withContext(Dispatchers.Main) { snapshotRoot = child(snapshotName) }
                            }
                            updateReady()
                        }
                    } catch (error: Exception) {
                        withContext(Dispatchers.Main) { status = error.message ?: "Ошибка открытия архива" }
                    }
                }
            }
        }
    }

    private fun child(name: String): File {
        require(name.matches(Regex("[a-z]+-[a-f0-9-]+"))) { "Повреждён указатель архива" }
        return File(root, name)
    }

    private fun saveActive(model: File?, snapshot: File?) {
        val content = JSONObject().put("model", model?.name ?: "").put("snapshot", snapshot?.name ?: "").put("imported_at", if (snapshot != snapshotRoot) System.currentTimeMillis() else preferences.getLong("imported_at", 0)).toString().toByteArray()
        val out = active.startWrite()
        try { out.write(content); active.finishWrite(out) }
        catch (error: Exception) { active.failWrite(out); throw error }
    }

    private suspend fun updateReady() {
        val manifest = if (snapshotRoot != null) JSONObject(File(snapshotRoot, "manifest.json").readText()) else null
        val compatible = encoder != null && handle != 0L && encoder!!.space == manifest?.getString("embedding_space")
        val available = if (handle != 0L) NativeBridge.value(NativeBridge.facets(handle)) as JSONObject else JSONObject()
        withContext(Dispatchers.Main) {
            modelStatus = if (encoder != null) "E5-small · INT8 · загружена" else "Модель не загружена"
            val imported = preferences.getLong("imported_at", 0)
            archiveStatus = if (manifest == null) "Архив не загружен" else {
                val date = if (imported > 0) java.time.Instant.ofEpochMilli(imported).atZone(java.time.ZoneId.systemDefault()).format(java.time.format.DateTimeFormatter.ofPattern("dd.MM.yyyy HH:mm")) else "дата неизвестна"
                "${manifest.getInt("image_count")} картинок · v${manifest.getLong("version")}\nИмпорт: $date"
            }
            ready = compatible
            facets = available
            status = when {
                compatible -> "Архив: ${manifest!!.getInt("image_count")} картинок · ${manifest.getString("created_at")}"
                encoder == null -> "Импортируйте модель E5-small"
                handle == 0L -> "Импортируйте архив картинок"
                else -> "Модель и архив несовместимы. Импортируйте соответствующую версию"
            }
        }
    }

    fun import(uri: Uri, model: Boolean, expected: JSONObject? = null, temporaryPackage: File? = null) {
        if (busy) return
        generation++
        searchJob?.cancel()
        if (handle != 0L) NativeBridge.cancel(handle)
        busy = true
        status = "Проверка и импорт…"
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) {
                    mutex.withLock {
                        val directory = File(root, "${if (model) "model" else "snapshot"}-${UUID.randomUUID()}")
                        var newEncoder: E5Encoder? = null
                        var newHandle = 0L
                        var committed = false
                        try {
                            val input = getApplication<Application>().contentResolver.openInputStream(uri) ?: error("Файл недоступен")
                            PackageImporter.extract(input, directory)
                            if (model) {
                                newEncoder = E5Encoder(directory)
                                saveActive(directory, snapshotRoot)
                                val previous = encoder
                                encoder = newEncoder
                                modelDirectory = directory
                                committed = true
                                previous?.close()
                            } else {
                                val opened = NativeBridge.value(NativeBridge.open(directory.absolutePath)) as JSONObject
                                newHandle = opened.getLong("handle")
                                if (expected != null) {
                                    val actual = opened.getJSONObject("manifest")
                                    require(actual.getString("dataset_id") == expected.getString("dataset_id") && actual.getLong("version") == expected.getLong("version")) { "Пакет не соответствует обновлению" }
                                }
                                saveActive(modelDirectory, directory)
                                preferences.edit().putLong("imported_at", System.currentTimeMillis()).apply()
                                val previous = handle
                                handle = newHandle
                                committed = true
                                withContext(Dispatchers.Main) { snapshotRoot = directory; hits = emptyList() }
                                if (previous != 0L) NativeBridge.close(previous)
                            }
                            updateReady()
                        } finally {
                            if (!committed) {
                                newEncoder?.close()
                                if (newHandle != 0L) NativeBridge.close(newHandle)
                                directory.deleteRecursively() // Only the new staging directory, never an active snapshot.
                            }
                        }
                    }
                }
            } catch (error: Exception) { status = error.message ?: "Не удалось импортировать" }
            finally { temporaryPackage?.delete(); busy = false }
        }
    }

    fun toggleTheme() {
        darkTheme = !darkTheme
        preferences.edit().putBoolean("dark_theme", darkTheme).apply()
    }

    fun clearHistory() {
        history = emptyList()
        preferences.edit().remove("history").apply()
    }

    fun downloadModel() {
        if (busy) return
        busy = true
        viewModelScope.launch {
            var packageFile: File? = null
            var handedOff = false
            try {
                packageFile = withContext(Dispatchers.IO) {
                    ModelDownloader.download(getApplication<Application>()) { received, total ->
                        withContext(Dispatchers.Main) { status = "Загрузка E5-small: ${received * 100 / total}%" }
                    }
                }
                busy = false
                val file = packageFile!!
                import(Uri.fromFile(file), true, temporaryPackage = file)
                packageFile = null
                handedOff = true
            } catch (error: kotlinx.coroutines.CancellationException) { throw error }
            catch (error: Exception) { status = error.message ?: "Не удалось скачать модель" }
            finally { packageFile?.delete(); if (!handedOff) busy = false }
        }
    }

    fun setUpdateFolder(uri: Uri) {
        getApplication<Application>().contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
        preferences.edit().putString("folder", uri.toString()).apply()
        status = "Папка обновлений выбрана"
    }

    fun checkUpdates() {
        if (busy) return
        val folder = preferences.getString("folder", null)
        if (folder == null) { status = "Сначала выберите папку обновлений"; return }
        busy = true
        viewModelScope.launch {
            var downloaded: File? = null
            try {
                val update = withContext(Dispatchers.IO) {
                    val application = getApplication<Application>()
                    val tree = androidx.documentfile.provider.DocumentFile.fromTreeUri(application, Uri.parse(folder)) ?: error("Папка недоступна")
                    val latest = tree.findFile("latest.json") ?: error("Манифест обновления ещё не доставлен")
                    val bytes = application.contentResolver.openInputStream(latest.uri)!!.use { it.readBytesLimited(65536) }
                    val metadata = JSONObject(bytes.toString(Charsets.UTF_8))
                    val old = snapshotRoot?.let { JSONObject(File(it, "manifest.json").readText()) }
                    if (old != null) {
                        require(metadata.getString("dataset_id") == old.getString("dataset_id")) { "В папке другой архив. Импортируйте его вручную" }
                        if (metadata.getLong("version") <= old.getLong("version")) return@withContext null
                    }
                    val name = metadata.getString("file")
                    require(name.matches(Regex("[a-zA-Z0-9_.-]+\\.zip"))) { "Недопустимое имя пакета" }
                    val document = tree.findFile(name) ?: error("Пакет обновления ещё не доставлен")
                    val size = metadata.getLong("size")
                    require(size in 1..(2L * 1024 * 1024 * 1024) && document.length() == size) { "Пакет ещё не полностью доставлен" }
                    val digest = java.security.MessageDigest.getInstance("SHA-256")
                    val packageFile = File(application.cacheDir, "update-${UUID.randomUUID()}.zip")
                    downloaded = packageFile
                    require(packageFile.parentFile!!.usableSpace > size + 32L * 1024 * 1024) { "Недостаточно места для обновления" }
                    var received = 0L
                    application.contentResolver.openInputStream(document.uri)!!.buffered().use { input ->
                        packageFile.outputStream().buffered().use { out ->
                            val buffer = ByteArray(65536)
                            while (true) {
                                val read = input.read(buffer)
                                if (read < 0) break
                                received += read
                                require(received <= size) { "Размер пакета изменился" }
                                digest.update(buffer, 0, read)
                                out.write(buffer, 0, read)
                            }
                        }
                    }
                    val hash = digest.digest().joinToString("") { "%02x".format(it) }
                    require(received == size && hash == metadata.getString("sha256")) { "Пакет обновления повреждён или ещё доставляется" }
                    Pair(packageFile, metadata)
                }
                busy = false
                if (update == null) status = "Установлена актуальная версия"
                else { import(Uri.fromFile(update.first), false, update.second, update.first); downloaded = null }
            } catch (error: Exception) { busy = false; status = error.message ?: "Не удалось проверить обновления" }
            finally { downloaded?.delete() }
        }
    }

    fun search(query: String, filters: JSONObject = JSONObject()) {
        if (!ready || query.isBlank() || busy) return
        history = (listOf(query.trim()) + history.filter { it != query.trim() }).take(20)
        preferences.edit().putString("history", JSONArray(history).toString()).apply()
        val current = ++generation
        searchJob?.cancel()
        NativeBridge.cancel(handle)
        status = "Поиск…"
        searchJob = viewModelScope.launch {
            try {
                val results = withContext(Dispatchers.IO) {
                    mutex.withLock {
                        val vector = encoder!!.encode(query)
                        val request = JSONObject().put("text", query).put("vector", JSONArray(vector.toList()))
                            .put("embedding_space", encoder!!.space).put("filters", filters).put("top_k", 20)
                        val array = NativeBridge.value(NativeBridge.search(handle, request.toString())) as JSONArray
                        (0 until array.length()).map { array.getJSONObject(it) }
                    }
                }
                if (current == generation) { hits = results; status = "Найдено: ${results.size}" }
            } catch (error: kotlinx.coroutines.CancellationException) { throw error }
            catch (error: Exception) { if (current == generation) status = error.message ?: "Ошибка поиска" }
        }
    }

    override fun onCleared() {
        if (handle != 0L) NativeBridge.cancel(handle)
        // Wait for initialization/inference before closing the final session, including
        // a session opened after onCleared started while initialization was in flight.
        kotlinx.coroutines.CoroutineScope(Dispatchers.IO).launch {
            mutex.withLock {
                if (handle != 0L) { NativeBridge.close(handle); handle = 0L }
                encoder?.close()
                encoder = null
            }
        }
        super.onCleared()
    }
}

private fun java.io.InputStream.readBytesLimited(limit: Int): ByteArray {
    val out = java.io.ByteArrayOutputStream()
    val buffer = ByteArray(4096)
    while (true) {
        val read = read(buffer)
        if (read < 0) break
        require(out.size() + read <= limit) { "Файл слишком большой" }
        out.write(buffer, 0, read)
    }
    return out.toByteArray()
}
