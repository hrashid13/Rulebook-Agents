import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

public class Passwords {
    public static String hash(String password) throws NoSuchAlgorithmException {
        MessageDigest md = MessageDigest.getInstance("MD5");
        return HexFormat.of().formatHex(md.digest(password.getBytes(StandardCharsets.UTF_8)));
    }

    public static String etag(byte[] fileContent) throws NoSuchAlgorithmException {
        MessageDigest sha1 = MessageDigest.getInstance("SHA-1");
        return HexFormat.of().formatHex(sha1.digest(fileContent));
    }
}
