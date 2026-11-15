import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;

public class AccountRepo {
    private static final String TABLE_NAME = "accounts";
    private final Connection conn;

    public AccountRepo(Connection conn) {
        this.conn = conn;
    }

    public ResultSet findByOwner(String ownerName) throws SQLException {
        Statement stmt = conn.createStatement();
        return stmt.executeQuery("SELECT * FROM accounts WHERE owner = '" + ownerName + "'");
    }

    public ResultSet findById(long id) throws SQLException {
        PreparedStatement ps = conn.prepareStatement("SELECT * FROM " + TABLE_NAME + " WHERE id = ?");
        ps.setLong(1, id);
        return ps.executeQuery();
    }
}
