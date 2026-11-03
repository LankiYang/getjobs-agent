package com.getjobs.application.controller;

import com.microsoft.playwright.options.FilePayload;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.SeekableByteChannel;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.Locale;
import java.util.Set;

/** Reads only directly staged runner uploads, without following links. */
final class TrustedBrowserUpload {
    private static final int MAX_BYTES = 20 * 1024 * 1024;

    static FilePayload read(Path workingDirectory, String requested) throws IOException {
        Path root = workingDirectory.toAbsolutePath().normalize();
        Path directory = root.resolve("chat-uploads");
        Path input = Path.of(requested);
        if (!input.isAbsolute() || !input.equals(input.normalize()) || !directory.equals(input.getParent())
                || Files.isSymbolicLink(directory) || !directory.toRealPath().equals(root.toRealPath().resolve("chat-uploads"))
                || Files.isSymbolicLink(input) || !Files.isRegularFile(input, LinkOption.NOFOLLOW_LINKS)) {
            throw new IllegalArgumentException("只允许上传当前用户暂存的简历文件");
        }
        String name = input.getFileName().toString();
        String lower = name.toLowerCase(Locale.ROOT);
        String mime = lower.endsWith(".pdf") ? "application/pdf"
                : lower.endsWith(".doc") ? "application/msword"
                : lower.endsWith(".docx") ? "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                : lower.endsWith(".png") ? "image/png"
                : lower.endsWith(".jpg") || lower.endsWith(".jpeg") ? "image/jpeg"
                : lower.endsWith(".gif") ? "image/gif" : null;
        if (mime == null) throw new IllegalArgumentException("只支持 pdf/doc/docx/png/jpg/gif 文件");
        try (SeekableByteChannel channel = Files.newByteChannel(input, Set.of(StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS))) {
            long size = channel.size();
            if (size > MAX_BYTES) throw new IllegalArgumentException("文件不能超过 20MB");
            ByteBuffer bytes = ByteBuffer.allocate((int) size + 1);
            while (bytes.hasRemaining() && channel.read(bytes) != -1) { }
            if (bytes.position() > MAX_BYTES || bytes.position() > size) {
                throw new IllegalArgumentException("文件大小已改变或超过 20MB");
            }
            return new FilePayload(name, mime, java.util.Arrays.copyOf(bytes.array(), bytes.position()));
        }
    }
}
