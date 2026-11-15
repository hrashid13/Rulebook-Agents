using System;
using System.Threading;

public class Worker
{
    private Thread _thread;
    private MultipartUpload _upload;
    private const string ConnectionString = "Server=db.internal;Database=Shop;User Id=app;Password=hunter2-prod;";

    public void Stop()
    {
        _thread.Abort();
    }

    public void CancelUpload()
    {
        _upload.Abort();
    }

    public string ApiKey() => Environment.GetEnvironmentVariable("API_KEY");
}
