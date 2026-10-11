package dev.tgfeature.search

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri

object TelegramLinkOpener {
    fun open(context: Context, value: String): Boolean {
        val link = TelegramMessageLink.parse(value) ?: return false
        fun launch(uri: String): Boolean = try {
            context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(uri)).apply {
                addCategory(Intent.CATEGORY_BROWSABLE)
                if (context !is android.app.Activity) addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            })
            true
        } catch (_: ActivityNotFoundException) {
            false
        } catch (_: SecurityException) {
            false
        }
        // A tg scheme bypasses the browser's default association for HTTPS links.
        return launch(link.appUri) || launch(link.webUri)
    }
}
