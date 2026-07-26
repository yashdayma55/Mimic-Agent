import queue, threading, time

q = queue.Queue()

def writer():
    while True:
        event = q.get()          # sleeps here until something arrives
        if event is None:        # poison pill = shutdown signal
            break
        print("processing:", event)
        time.sleep(0.5)          # pretend this is the slow work
        q.task_done()

t = threading.Thread(target=writer, daemon=True)
t.start()

# main thread drops 5 events FAST, then quits
for i in range(5):
    q.put(f"event-{i}")
    print("dropped event", i)

q.put(None)    # poison pill
t.join()
print("done")