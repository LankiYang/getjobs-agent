package com.getjobs.worker.service;

import org.junit.jupiter.api.Test;
import java.net.InetAddress;
import static org.junit.jupiter.api.Assertions.*;

class BrowserNetworkPolicyTest {
    private static InetAddress ip(String value) throws Exception { return InetAddress.getByName(value); }

    @Test void rejectsPrivateAndSpecialAddresses() throws Exception {
        for (String address : new String[]{"127.0.0.1", "10.0.0.1", "172.31.0.1", "192.168.1.1",
                "169.254.169.254", "100.64.1.1", "0.0.0.0", "::1", "fc00::1", "fe80::1",
                "::ffff:127.0.0.1", "2002:7f00:1::1"}) {
            assertFalse(BrowserNetworkPolicy.isPublic(ip(address)), address);
        }
        assertTrue(BrowserNetworkPolicy.isPublic(ip("8.8.8.8")));
        assertTrue(BrowserNetworkPolicy.isPublic(ip("2606:4700:4700::1111")));
    }

    @Test void checksEveryResolvedAddressAndRejectsMalformedUrls() throws Exception {
        BrowserNetworkPolicy.Resolver mixed = host -> new InetAddress[]{ip("8.8.8.8"), ip("10.0.0.1")};
        assertFalse(BrowserNetworkPolicy.allows("https://example.com/", mixed));
        assertFalse(BrowserNetworkPolicy.allows("http://localhost/", host -> new InetAddress[]{ip("8.8.8.8")}));
        assertFalse(BrowserNetworkPolicy.allows("file:///etc/passwd", mixed));
        assertFalse(BrowserNetworkPolicy.allows("https://user@example.com/", mixed));
        assertFalse(BrowserNetworkPolicy.allows("https://example.com/", host -> { throw new Exception(); }));
        assertTrue(BrowserNetworkPolicy.allows("https://example.com/", host -> new InetAddress[]{ip("8.8.8.8")}));
    }
}
