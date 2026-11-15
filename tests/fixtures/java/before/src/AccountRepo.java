import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.Statement;

public class AccountRepo {
    private final Connection conn;

    public AccountRepo(Connection conn) {
        this.conn = conn;
    }
}
