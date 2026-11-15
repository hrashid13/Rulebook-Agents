import java.sql.*;

class Queries {
    void run(Connection conn, Statement stmt, String name, String id) throws SQLException {
        // ruleid: parameterized-sql-only-java
        stmt.executeQuery("SELECT * FROM users WHERE name = '" + name + "'");
        // ruleid: parameterized-sql-only-java
        stmt.executeUpdate("DELETE FROM users WHERE id = " + id);
        // ruleid: parameterized-sql-only-java
        stmt.execute(String.format("UPDATE users SET name = '%s'", name));
        // ok: parameterized-sql-only-java
        PreparedStatement ps = conn.prepareStatement("SELECT * FROM users WHERE name = ?");
        // ok: parameterized-sql-only-java
        stmt.executeQuery("SELECT COUNT(*) FROM users");
    }
}
