import java.security.MessageDigest;

class Hashing {
    void hash(byte[] password) throws Exception {
        // ruleid: no-weak-password-hashing-java
        MessageDigest a = MessageDigest.getInstance("MD5");
        // ruleid: no-weak-password-hashing-java
        MessageDigest b = MessageDigest.getInstance("SHA-1");
        // ruleid: no-weak-password-hashing-java
        MessageDigest c = MessageDigest.getInstance("SHA1");
        // ok: no-weak-password-hashing-java
        MessageDigest d = MessageDigest.getInstance("SHA-256");
        // ok: no-weak-password-hashing-java
        MessageDigest e = MessageDigest.getInstance("SHA-512");
    }
}
