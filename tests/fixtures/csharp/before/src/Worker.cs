using System.Threading;

public class Worker
{
    private Thread _thread;

    public void Start()
    {
        _thread = new Thread(Run);
        _thread.Start();
    }

    private void Run() { }
}
