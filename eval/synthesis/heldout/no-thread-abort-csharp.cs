using System.Threading;

class Workers
{
    void Stop(Thread worker, Thread[] pool, CancellationTokenSource cts, Upload upload)
    {
        // ruleid: no-thread-abort-csharp
        worker.Abort();
        // ruleid: no-thread-abort-csharp
        pool[0].Abort();
        // ok: no-thread-abort-csharp
        cts.Cancel();
        // ok: no-thread-abort-csharp
        worker.Join();
    }
}
