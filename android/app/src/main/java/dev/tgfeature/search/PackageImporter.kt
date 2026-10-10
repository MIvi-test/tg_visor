package dev.tgfeature.search

import java.io.File
import java.io.InputStream
import java.util.zip.ZipInputStream

object PackageImporter {
    // ZIP entries are always written as ordinary files; symlinks are never created.
    fun extract(input: InputStream, destination: File) {
        check(destination.mkdirs()) { "Не удалось создать папку импорта" }
        var total = 0L
        var entries = 0
        val seen = mutableSetOf<String>()
        ZipInputStream(input.buffered()).use { zip ->
            while (true) {
                val entry = zip.nextEntry ?: break
                require(++entries <= 100_000) { "Слишком много файлов" }
                val name = entry.name
                val parts = name.trimEnd('/').split('/')
                require(name.isNotEmpty() && !name.startsWith('/') && !name.contains('\\') && parts.all { it.isNotEmpty() && it != "." && it != ".." }) { "Недопустимый путь в архиве" }
                require(seen.add(name)) { "Повторяющийся файл в архиве" }
                val target = File(destination, name)
                require(target.canonicalPath.startsWith(destination.canonicalPath + File.separator))
                if (entry.isDirectory) { target.mkdirs(); continue }
                target.parentFile?.mkdirs()
                require(!target.exists()) { "Повторяющийся путь в архиве" }
                target.outputStream().buffered().use { out ->
                    val buffer = ByteArray(65536)
                    while (true) {
                        val read = zip.read(buffer)
                        if (read < 0) break
                        total += read
                        require(total <= 2L * 1024 * 1024 * 1024) { "Архив слишком большой" }
                        require(destination.usableSpace >= 32L * 1024 * 1024) { "Недостаточно свободного места" }
                        out.write(buffer, 0, read)
                    }
                }
                zip.closeEntry()
            }
        }
        require(entries > 0) { "Пустой архив" }
    }
}
