import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.Statement;

public class AccountRepo {
    private final Connection conn;

    public AccountRepo(Connection conn) {
        this.conn = conn;
    }

    public ResultSet byOwner(String owner) throws Exception {
        Statement st = conn.createStatement();
        return st.executeQuery("SELECT * FROM accounts WHERE owner = '" + owner + "'");
    }

    public ResultSet allActive() throws Exception {
        Statement st = conn.createStatement();
        String table = "accounts";
        return st.executeQuery("SELECT * FROM " + table + " WHERE active = 1");
    }
}
