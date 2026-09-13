"""Nonblocking service connection and independent observation/action/stop lanes."""
import queue
import threading


class Client:
    def __init__(self, repo):
        self.results = queue.Queue()
        self.lanes = set()
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.socket = self.error = None
        def connect():
            try:
                from ui_service import ensure_service, request
                self.request = request
                self.socket = ensure_service(repo)
            except Exception as exc:
                self.error = str(exc)
            finally:
                self.ready.set()
        threading.Thread(target=connect, daemon=True).start()

    @staticmethod
    def lane(payload):
        if payload['op'] in {'stop', 'cancel_queue'}: return 'stop'
        if payload['op'] == 'snapshot': return 'snapshot'
        if payload['op'] == 'action': return 'poll'
        return 'action'

    def send(self, payload):
        lane = self.lane(payload)
        with self.lock:
            if lane in self.lanes: return False
            self.lanes.add(lane)
        def work():
            try:
                if not self.ready.wait(5): raise TimeoutError('Observer connection pending')
                if self.error: raise RuntimeError(self.error)
                self.results.put((payload, self.request(self.socket, payload), None))
            except Exception as exc:
                self.results.put((payload, None, str(exc)))
            finally:
                with self.lock: self.lanes.discard(lane)
        threading.Thread(target=work, daemon=True).start()
        return True
