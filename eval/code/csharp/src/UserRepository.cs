using System.Data.SqlClient;

public class UserRepository
{
    private const string TableName = "Users";
    private readonly SqlConnection _connection;

    public UserRepository(SqlConnection connection)
    {
        _connection = connection;
    }

    public void Delete(string userId)
    {
        var cmd = new SqlCommand("DELETE FROM Users WHERE UserId = '" + userId + "'", _connection);
        cmd.ExecuteNonQuery();
    }

    public SqlDataReader Find(int id)
    {
        var cmd = new SqlCommand($"SELECT * FROM {TableName} WHERE Id = @id", _connection);
        cmd.Parameters.AddWithValue("@id", id);
        return cmd.ExecuteReader();
    }
}
