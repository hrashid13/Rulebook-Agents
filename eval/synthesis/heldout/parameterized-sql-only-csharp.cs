using System.Data.SqlClient;

class Queries
{
    void Run(SqlConnection conn, string userId, string name)
    {
        // ruleid: parameterized-sql-only-csharp
        var a = new SqlCommand("DELETE FROM Users WHERE Id = '" + userId + "'", conn);
        // ruleid: parameterized-sql-only-csharp
        var b = new SqlCommand($"SELECT * FROM Users WHERE Name = '{name}'", conn);
        // ruleid: parameterized-sql-only-csharp
        var c = new SqlCommand(string.Format("SELECT * FROM Users WHERE Id = {0}", userId), conn);
        // ok: parameterized-sql-only-csharp
        var d = new SqlCommand("SELECT * FROM Users WHERE Id = @id", conn);
        // ok: parameterized-sql-only-csharp
        var e = new SqlCommand("SELECT COUNT(*) FROM Users", conn);
    }
}
