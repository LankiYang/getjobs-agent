package com.getjobs.worker.service;

import java.net.InetAddress;
import java.net.URI;
import java.util.Locale;

/** Best-effort request screening; the browser's connection must also be restricted at the network layer. */
public final class BrowserNetworkPolicy {
    private BrowserNetworkPolicy() {}

    @FunctionalInterface
    interface Resolver {
        InetAddress[] resolve(String host) throws Exception;
    }

    public static boolean allows(String url) {
        return allows(url, InetAddress::getAllByName);
    }

    static boolean allows(String url, Resolver resolver) {
        try {
            URI uri = URI.create(url);
            String scheme = uri.getScheme();
            String host = uri.getHost();
            if (scheme == null || !(scheme.equalsIgnoreCase("http") || scheme.equalsIgnoreCase("https"))
                    || host == null || uri.getRawUserInfo() != null || uri.getPort() > 65535) {
                return false;
            }
            host = host.toLowerCase(Locale.ROOT);
            if (host.startsWith("[") && host.endsWith("]")) {
                host = host.substring(1, host.length() - 1);
            }
            if (host.contains("%") || host.equals("localhost") || host.endsWith(".localhost")
                    || host.endsWith(".local") || host.endsWith(".local.") || host.endsWith(".localhost.")) {
                return false;
            }
            InetAddress[] addresses = resolver.resolve(host);
            if (addresses.length == 0) return false;
            for (InetAddress address : addresses) {
                if (!isPublic(address)) return false;
            }
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    static boolean isPublic(InetAddress address) {
        byte[] b = address.getAddress();
        if (b.length == 4) {
            int a = b[0] & 255, c = b[1] & 255, d = b[2] & 255;
            return a != 0 && a != 10 && a != 127 && a < 224
                    && !(a == 100 && c >= 64 && c <= 127)
                    && !(a == 169 && c == 254)
                    && !(a == 172 && c >= 16 && c <= 31)
                    && !(a == 192 && (c == 168 || c == 0 || (c == 88 && d == 99)))
                    && !(a == 198 && (c == 18 || c == 19 || (c == 51 && d == 100)))
                    && !(a == 203 && c == 0 && d == 113);
        }
        if (b.length != 16) return false;
        // Only global unicast; exclude documentation and transition mechanisms that can embed IPv4.
        int first = b[0] & 255, second = b[1] & 255;
        if ((first & 0xe0) != 0x20 || (first == 0x20 && second == 0x02)) return false;
        if (first == 0x20 && second == 0x01) {
            int third = b[2] & 255, fourth = b[3] & 255;
            if ((third == 0 && fourth == 0) || (third == 0x0d && fourth == 0xb8)) return false;
        }
        return true;
    }
}
