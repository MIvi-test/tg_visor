package dev.tgfeature.search

import org.junit.Assert.*
import org.junit.Test

class TelegramMessageLinkTest {
    @Test fun publicMessageOpensNativeClient() {
        val link = TelegramMessageLink.parse("https://t.me/example_channel/123")!!
        assertEquals("tg://resolve?domain=example_channel&post=123", link.appUri)
        assertEquals("https://t.me/example_channel/123", link.webUri)
    }

    @Test fun privateMessageUsesInternalChannelId() {
        val link = TelegramMessageLink.parse("https://t.me/c/1234567890/42")!!
        assertEquals("tg://privatepost?channel=1234567890&post=42", link.appUri)
    }

    @Test fun topicAlbumCommentAndTimestampArePreserved() {
        val link = TelegramMessageLink.parse("https://t.me/c/123/77/88?single&comment=9&t=10")!!
        assertEquals("tg://privatepost?channel=123&post=88&single&comment=9&t=10&thread=77", link.appUri)
    }

    @Test fun nativeMessageHasBrowserFallback() {
        val link = TelegramMessageLink.parse("tg://resolve?domain=example_channel&post=123")!!
        assertEquals("https://t.me/example_channel/123", link.webUri)
        assertEquals("https://t.me/c/123/42", TelegramMessageLink.parse("tg://privatepost?channel=123&post=42")!!.webUri)
    }

    @Test fun unsafeLinksAndNonMessageActionsAreRejected() {
        for (link in listOf(
            "https://t.me.evil.example/example/1", "https://t.me@evil.example/example/1",
            "https://evil.example/example/1", "https://t.me:443/example/1",
            "https://t.me/example/0", "https://t.me/example/-1", "https://t.me/example/2147483648",
            "https://t.me/example/1?text=send", "https://t.me/example/1?thread=2&thread=3",
            "tg://login?token=secret", "tg://proxy?server=example", "tg://join?invite=example",
            "tg://resolve?domain=example&text=send", "intent://example", "https://t.me/c/0/1"
        )) assertNull(link, TelegramMessageLink.parse(link))
    }
}
