package dev.tgfeature.search

import java.net.URI
import java.net.URLDecoder
import java.net.URLEncoder

/** Only message navigation is accepted; login, proxies, drafts and invites are rejected. */
data class TelegramMessageLink(val appUri: String, val webUri: String) {
    companion object {
        fun parse(value: String): TelegramMessageLink? = runCatching {
            if (value.length > 2048) return null
            val uri = URI(value)
            if (uri.userInfo != null || uri.port != -1) return null
            val query = linkedMapOf<String, String>()
            for (part in uri.rawQuery.orEmpty().split('&').filter { it.isNotEmpty() }) {
                val pieces = part.split('=', limit = 2)
                val key = URLDecoder.decode(pieces[0], "UTF-8")
                if (query.containsKey(key)) return null
                query[key] = URLDecoder.decode(pieces.getOrElse(1) { "" }, "UTF-8")
            }
            var domain: String? = null
            var channel: String? = null
            val post: String
            var topic: String? = null
            when (uri.scheme?.lowercase()) {
                "https" -> {
                    if (uri.host?.lowercase() != "t.me") return null
                    val parts = uri.path.orEmpty().trim('/').split('/')
                    if (parts.firstOrNull() == "c") {
                        if (parts.size !in 3..4) return null
                        channel = parts[1]
                        post = parts.last()
                        if (parts.size == 4) topic = parts[2]
                    } else {
                        if (parts.size !in 2..3) return null
                        domain = parts[0]
                        post = parts.last()
                        if (parts.size == 3) topic = parts[1]
                    }
                    if (query.keys.any { it !in setOf("single", "thread", "comment", "t") }) return null
                }
                "tg" -> {
                    if (uri.path.orEmpty() !in setOf("", "/")) return null
                    when (uri.host) {
                        "resolve" -> domain = query.remove("domain") ?: return null
                        "privatepost" -> channel = query.remove("channel") ?: return null
                        else -> return null
                    }
                    post = query.remove("post") ?: return null
                    if (query.keys.any { it !in setOf("single", "thread", "comment", "t") }) return null
                }
                else -> return null
            }
            if (domain != null && !domain.matches(Regex("[A-Za-z][A-Za-z0-9_]{0,31}"))) return null
            if (channel != null && !positiveId(channel, Long.MAX_VALUE)) return null
            if (!positiveId(post)) return null
            if (topic != null && !positiveId(topic)) return null
            if (query["thread"] != null) {
                if (!positiveId(query.getValue("thread"))) return null
                if (topic != null && topic != query["thread"]) return null
            }
            if (query["comment"] != null && !positiveId(query.getValue("comment"))) return null
            if (query["t"] != null && !query.getValue("t").matches(Regex("[0-9hms:]{1,32}"))) return null
            topic?.let { query["thread"] = it }
            val suffix = query.entries.joinToString("&") { (key, argument) ->
                if (key == "single") "single" else "$key=${URLEncoder.encode(argument, "UTF-8") }"
            }
            val target = if (domain != null) "resolve?domain=$domain" else "privatepost?channel=$channel"
            val app = "tg://$target&post=$post" + if (suffix.isEmpty()) "" else "&$suffix"
            val path = if (domain != null) "$domain/$post" else "c/$channel/$post"
            val web = "https://t.me/$path" + if (suffix.isEmpty()) "" else "?$suffix"
            TelegramMessageLink(app, web)
        }.getOrNull()

        private fun positiveId(value: String, maximum: Long = Int.MAX_VALUE.toLong()): Boolean =
            value.matches(Regex("[0-9]+")) && value.toLongOrNull()?.let { it in 1..maximum } == true
    }
}
