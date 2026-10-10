package dev.tgfeature.search

import android.app.Activity
import android.app.Instrumentation
import android.os.Bundle
import java.io.File
import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.abs

/** Device integration probe on synthetic data, invoked explicitly via ADB. */
class SmokeInstrumentation : Instrumentation() {
    override fun onCreate(arguments: Bundle?) {
        super.onCreate(arguments)
        start()
    }

    override fun onStart() {
        val output = Bundle()
        var handle = 0L
        try {
            check(NativeBridge.value(NativeBridge.version()) == "0.1.0")
            val root = File(targetContext.filesDir, "device-test")
            E5Encoder(File(root, "model")).use { encoder ->
                val expected = JSONObject(File(root, "request.json").readText())
                val started = System.nanoTime()
                val vector = encoder.encode(expected.getString("text"))
                val elapsed = (System.nanoTime() - started) / 1_000_000
                val reference = expected.getJSONArray("vector")
                var maxError = 0.0
                for (i in vector.indices) maxError = maxOf(maxError, abs(vector[i] - reference.getDouble(i)))
                check(maxError < 0.01) { "PC/Android vector mismatch: $maxError" }
                val opened = NativeBridge.value(NativeBridge.open(File(root, "snapshot").absolutePath)) as JSONObject
                handle = opened.getLong("handle")
                expected.put("vector", JSONArray(vector.toList()))
                val hits = NativeBridge.value(NativeBridge.search(handle, expected.toString())) as JSONArray
                check(hits.length() > 0 && hits.getJSONObject(0).getString("image_id") == "sleep") { "Wrong semantic result" }
                expected.put("filters", JSONObject().put("sender_id", "bob"))
                val filtered = NativeBridge.value(NativeBridge.search(handle, expected.toString())) as JSONArray
                for (i in 0 until filtered.length()) {
                    val sources = filtered.getJSONObject(i).getJSONArray("sources")
                    for (j in 0 until sources.length()) check(sources.getJSONObject(j).getString("sender_id") == "bob")
                }
                NativeBridge.value(NativeBridge.close(handle))
                check(!JSONObject(NativeBridge.search(handle, expected.toString())).getBoolean("ok"))
                handle = 0L
                output.putString("stream", "PASS: JNI, E5, semantic search, filters, close; query_ms=$elapsed; max_vector_error=$maxError\n")
            }
            finish(Activity.RESULT_OK, output)
        } catch (error: Throwable) {
            output.putString("stream", "FAIL: ${error.stackTraceToString()}\n")
            finish(Activity.RESULT_CANCELED, output)
        } finally { if (handle != 0L) NativeBridge.close(handle) }
    }
}
