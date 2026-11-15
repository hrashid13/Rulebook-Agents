using System.Threading;

public class Worker
{
    private Thread _thread;

    public void Start()
    {
        _thread = new Thread(Run);
        _thread.Start();
    }

    public void Stop()
    {
        // Shut the worker down immediately.
        _thread.Abort();
    }

    private void Run() { }
}
