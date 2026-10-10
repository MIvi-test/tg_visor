package dev.tgfeature.search

import android.content.Context
import java.io.File
import java.net.URL
import java.util.UUID
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import javax.net.ssl.HttpsURLConnection
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import org.json.JSONObject

/** Downloads only the pinned public model; archives and queries never leave the device. */
object ModelDownloader {
    suspend fun download(context: Context, progress: suspend (Long, Long) -> Unit): File {
        val manifestBytes = context.assets.open("e5-model.json").use { it.readBytes() }
        val manifest = JSONObject(manifestBytes.toString(Charsets.UTF_8))
        val staging = File(context.cacheDir, "model-download-${UUID.randomUUID()}").apply { mkdirs() }
        val archive = File(context.cacheDir, "model-package-${UUID.randomUUID()}.zip")
        val files = manifest.getJSONObject("files")
        val total = files.getJSONObject("model.onnx").getLong("size") + files.getJSONObject("tokenizer.json").getLong("size")
        var received = 0L
        try {
            require(staging.usableSpace > total * 2 + 32L * 1024 * 1024) { "Недостаточно места для модели" }
            val base = "https://huggingface.co/intfloat/multilingual-e5-small/resolve/${manifest.getString("revision")}/"
            for ((name, remote) in listOf("model.onnx" to "onnx/model_qint8_avx512_vnni.onnx", "tokenizer.json" to "tokenizer.json")) {
                val metadata = files.getJSONObject(name)
                val expected = metadata.getLong("size")
                val connection = connect(URL(base + remote))
                var length = 0L
                var lastProgress = 0L
                try {
                    connection.inputStream.buffered().use { input ->
                        File(staging, name).outputStream().buffered().use { output ->
                            val buffer = ByteArray(65536)
                            while (true) {
                                currentCoroutineContext().ensureActive()
                                val read = input.read(buffer)
                                if (read < 0) break
                                length += read
                                require(length <= expected) { "Неверный размер скачанной модели" }
                                output.write(buffer, 0, read)
                                received += read
                                if (received - lastProgress >= 1024 * 1024) {
                                    progress(received, total)
                                    lastProgress = received
                                }
                            }
                        }
                    }
                } finally { connection.disconnect() }
                require(length == expected && E5Encoder.sha256(File(staging, name)) == metadata.getString("sha256")) { "Скачанный файл повреждён: $name" }
            }
            File(staging, "model.json").writeBytes(manifestBytes)
            ZipOutputStream(archive.outputStream().buffered()).use { zip ->
                for (name in listOf("model.onnx", "tokenizer.json", "model.json")) {
                    currentCoroutineContext().ensureActive()
                    zip.putNextEntry(ZipEntry(name))
                    File(staging, name).inputStream().use { it.copyTo(zip) }
                    zip.closeEntry()
                }
            }
            progress(total, total)
            return archive
        } catch (error: Throwable) { archive.delete(); throw error }
        finally { staging.deleteRecursively() }
    }

    private fun connect(initial: URL): HttpsURLConnection {
        var url = initial
        repeat(6) {
            require(url.protocol == "https" && url.userInfo == null) { "Недопустимый адрес модели" }
            val connection = (url.openConnection() as HttpsURLConnection).apply {
                connectTimeout = 20000
                readTimeout = 60000
                instanceFollowRedirects = false
            }
            try {
                val code = connection.responseCode
                if (code in listOf(301, 302, 303, 307, 308)) {
                    val location = connection.getHeaderField("Location") ?: error("Редирект без адреса")
                    url = URL(url, location)
                    connection.disconnect()
                } else {
                    require(code == 200) { "Сервер модели вернул HTTP $code" }
                    return connection
                }
            } catch (error: Exception) { connection.disconnect(); throw error }
        }
        error("Слишком много редиректов модели")
    }
}
