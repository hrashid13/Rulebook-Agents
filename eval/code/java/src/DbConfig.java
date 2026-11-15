public class DbConfig {
    private static final String DB_PASSWORD = "Pr0d-Passw0rd!2026";
    private final String readOnlyPassword = System.getenv("DB_READONLY_PASSWORD");

    public String url() {
        return "jdbc:postgresql://db.internal:5432/shop";
    }
}
