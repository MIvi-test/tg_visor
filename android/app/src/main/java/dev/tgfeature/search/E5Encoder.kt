package dev.tgfeature.search

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import java.io.File
import java.nio.LongBuffer
import java.security.MessageDigest
import org.json.JSONObject
import kotlin.math.sqrt

class E5Encoder(private val directory: File) : AutoCloseable {
    private val environment = OrtEnvironment.getEnvironment()
    private val session: OrtSession
    val space: String

    init {
        val manifest = JSONObject(File(directory, "model.json").readText())
        require(manifest.getString("model_id") == "intfloat/multilingual-e5-small") { "Неподдерживаемая модель" }
        require(manifest.getInt("dimension") == 384) { "Неверная размерность модели" }
        require(manifest.getJSONObject("files").getJSONObject("model.onnx").getString("sha256") == MODEL_SHA256 && manifest.getJSONObject("files").getJSONObject("tokenizer.json").getString("sha256") == TOKENIZER_SHA256) { "Эта сборка поддерживает только проверенный артефакт E5-small" }
        for (name in listOf("model.onnx", "tokenizer.json")) {
            val file = File(directory, name)
            require(file.length() in 1..(256L * 1024 * 1024)) { "Недопустимый размер модели" }
            require(file.length() == manifest.getJSONObject("files").getJSONObject(name).getLong("size")) { "Неверный размер файла модели" }
            require(sha256(file) == manifest.getJSONObject("files").getJSONObject(name).getString("sha256")) { "Повреждён файл модели: $name" }
        }
        space = manifest.getString("embedding_space")
        val expectedSpace = "e5-small-" + manifest.getJSONObject("files").getJSONObject("model.onnx").getString("sha256").take(16) + "-" + manifest.getJSONObject("files").getJSONObject("tokenizer.json").getString("sha256").take(16)
        require(space == expectedSpace && manifest.getString("pooling") == "masked-mean" && manifest.getString("normalization") == "l2" && manifest.getInt("max_length") == 512) { "Несовместимые параметры энкодера" }
        val options = OrtSession.SessionOptions()
        options.setIntraOpNumThreads(2)
        try { session = environment.createSession(File(directory, "model.onnx").absolutePath, options) }
        finally { options.close() }
    }

    fun encode(query: String): FloatArray {
        require(query.isNotBlank()) { "Введите запрос" }
        val encoded = NativeBridge.value(NativeBridge.tokenize(File(directory, "tokenizer.json").absolutePath, query)) as JSONObject
        val ids = encoded.getJSONArray("input_ids")
        val length = ids.length()
        val shape = longArrayOf(1, length.toLong())
        val tensors = mutableMapOf<String, OnnxTensor>()
        try {
            for (name in session.inputNames) {
                val array = encoded.getJSONArray(name)
                val values = LongArray(length) { array.getLong(it) }
                tensors[name] = OnnxTensor.createTensor(environment, LongBuffer.wrap(values), shape)
            }
            session.run(tensors).use { result ->
                @Suppress("UNCHECKED_CAST")
                val output = result[0].value as Array<Array<FloatArray>>
                val pooled = FloatArray(384)
                var count = 0
                val mask = encoded.getJSONArray("attention_mask")
                for (i in 0 until length) {
                    if (mask.getInt(i) == 0) continue
                    count++
                    require(output[0][i].size == 384) { "Неверный выход модели" }
                    for (j in pooled.indices) pooled[j] += output[0][i][j]
                }
                require(count > 0)
                for (j in pooled.indices) pooled[j] /= count.toFloat()
                val norm = sqrt(pooled.sumOf { it.toDouble() * it.toDouble() }).toFloat()
                require(norm.isFinite() && norm > 0)
                for (j in pooled.indices) pooled[j] /= norm
                return pooled
            }
        } finally { tensors.values.forEach { it.close() } }
    }

    override fun close() { session.close() }

    companion object {
        private const val MODEL_SHA256 = "dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88"
        private const val TOKENIZER_SHA256 = "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"
        fun sha256(file: File): String {
            val digest = MessageDigest.getInstance("SHA-256")
            file.inputStream().buffered().use { input ->
                val buffer = ByteArray(65536)
                while (true) {
                    val read = input.read(buffer)
                    if (read < 0) break
                    digest.update(buffer, 0, read)
                }
            }
            return digest.digest().joinToString("") { "%02x".format(it) }
        }
    }
}
