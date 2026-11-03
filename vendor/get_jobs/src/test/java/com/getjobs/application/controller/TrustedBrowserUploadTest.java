package com.getjobs.application.controller;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import java.nio.file.Files;
import java.nio.file.Path;
import static org.junit.jupiter.api.Assertions.*;

class TrustedBrowserUploadTest {
    @TempDir Path root;

    @Test void acceptsOnlyDirectStagedResumeFiles() throws Exception {
        Path uploads = Files.createDirectory(root.resolve("chat-uploads"));
        Path pdf = Files.write(uploads.resolve("resume.pdf"), new byte[]{1, 2});
        Path image = Files.write(uploads.resolve("resume.gif"), new byte[]{3});
        assertArrayEquals(new byte[]{1, 2}, TrustedBrowserUpload.read(root, pdf.toString()).buffer);
        assertEquals("image/gif", TrustedBrowserUpload.read(root, image.toString()).mimeType);
        Path outside = Files.write(root.resolve("secret.pdf"), new byte[]{4});
        assertThrows(IllegalArgumentException.class, () -> TrustedBrowserUpload.read(root, outside.toString()));
        assertThrows(IllegalArgumentException.class, () -> TrustedBrowserUpload.read(root, uploads.resolve("../secret.pdf").toString()));
        Path nested = Files.createDirectory(uploads.resolve("nested"));
        Path inner = Files.write(nested.resolve("resume.pdf"), new byte[]{5});
        assertThrows(IllegalArgumentException.class, () -> TrustedBrowserUpload.read(root, inner.toString()));
    }

    @Test void rejectsOversizedAndUnsupportedFiles() throws Exception {
        Path uploads = Files.createDirectory(root.resolve("chat-uploads"));
        Path large = uploads.resolve("large.pdf");
        try (var file = new java.io.RandomAccessFile(large.toFile(), "rw")) {
            file.setLength(20L * 1024 * 1024 + 1);
        }
        assertThrows(IllegalArgumentException.class, () -> TrustedBrowserUpload.read(root, large.toString()));
        Path unsupported = Files.write(uploads.resolve("payload.exe"), new byte[]{1});
        assertThrows(IllegalArgumentException.class, () -> TrustedBrowserUpload.read(root, unsupported.toString()));
    }

    @Test void rejectsSymbolicLinksWhenSupported() throws Exception {
        Path uploads = Files.createDirectory(root.resolve("chat-uploads"));
        Path secret = Files.write(root.resolve("secret.pdf"), new byte[]{1});
        Path link = uploads.resolve("resume.pdf");
        try {
            Files.createSymbolicLink(link, secret);
        } catch (UnsupportedOperationException | java.nio.file.FileSystemException e) {
            org.junit.jupiter.api.Assumptions.assumeTrue(false, "Symbolic links unavailable");
        }
        assertThrows(IllegalArgumentException.class, () -> TrustedBrowserUpload.read(root, link.toString()));
    }
}
