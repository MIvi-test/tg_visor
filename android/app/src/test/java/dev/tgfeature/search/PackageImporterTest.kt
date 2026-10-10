package dev.tgfeature.search

import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.file.Files
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import org.junit.Assert.*
import org.junit.Test

class PackageImporterTest {
    private fun archive(name: String): ByteArray {
        val bytes = ByteArrayOutputStream()
        ZipOutputStream(bytes).use { zip ->
            zip.putNextEntry(ZipEntry(name))
            zip.write("test".toByteArray())
            zip.closeEntry()
        }
        return bytes.toByteArray()
    }

    @Test fun rejectsTraversalAndAbsolutePaths() {
        val root = Files.createTempDirectory("package-test").toFile()
        try {
            for ((index, name) in listOf("../outside", "/outside", "folder/../outside", "folder\\outside").withIndex()) {
                try { PackageImporter.extract(ByteArrayInputStream(archive(name)), File(root, "attempt-$index")); fail("accepted $name") }
                catch (_: IllegalArgumentException) { }
            }
        } finally { root.deleteRecursively() }
    }

    @Test fun extractsNestedRegularFile() {
        val root = Files.createTempDirectory("package-test").toFile()
        try {
            val destination = File(root, "snapshot")
            PackageImporter.extract(ByteArrayInputStream(archive("thumbnails/example.jpg")), destination)
            assertEquals("test", File(destination, "thumbnails/example.jpg").readText())
        } finally { root.deleteRecursively() }
    }
}
