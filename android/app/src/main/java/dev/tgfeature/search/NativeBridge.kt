package dev.tgfeature.search

import org.json.JSONObject

object NativeBridge {
    init { System.loadLibrary("tg_search") }
    external fun version(): String
    external fun tokenize(path: String, text: String): String
    external fun open(path: String): String
    external fun search(handle: Long, request: String): String
    external fun cancel(handle: Long): String
    external fun close(handle: Long): String
    external fun facets(handle: Long): String

    fun value(response: String): Any {
        val envelope = JSONObject(response)
        check(envelope.getBoolean("ok")) { envelope.optString("error", "Ошибка ядра") }
        return envelope.get("value")
    }
}
