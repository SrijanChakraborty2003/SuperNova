import json
from typing import List, Dict, Any, Optional

try:
    import redis
except ImportError:
    redis = None


class RedisChatBuffer:
    """Log-based Redis Chat Memory Buffer maintaining past N conversation messages per session."""

    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0, password: Optional[str] = None):
        self.host = host
        self.port = port
        self.db = db
        self.password = password
        self.r = None
        self._in_memory_fallback: Dict[str, List[Dict[str, str]]] = {}
        self._connect()

    def _connect(self):
        if redis is None:
            print("[RedisChatBuffer] 'redis' package not installed. Using in-memory chat buffer fallback.")
            return

        try:
            self.r = redis.Redis(
                host=self.host,
                port=self.port,
                db=self.db,
                password=self.password,
                decode_responses=True,
                socket_timeout=2.0
            )
            self.r.ping()
            print(f"[RedisChatBuffer] Connected to Redis server at {self.host}:{self.port}")
        except Exception as e:
            print(f"[RedisChatBuffer] Redis server unavailable at {self.host}:{self.port} ({e}). Using in-memory log buffer fallback.")
            self.r = None

    def add_message(self, session_id: str, role: str, content: str, max_messages: int = 8):
        """Appends a message log to Redis list and trims to past max_messages (default 8)."""
        msg = {"role": role, "content": content}
        key = f"supernova:chat_history:{session_id}"

        if self.r:
            try:
                self.r.rpush(key, json.dumps(msg))
                # Keep only the last max_messages (past 8 messages context)
                self.r.ltrim(key, -max_messages, -1)
                return
            except Exception as e:
                print(f"[RedisChatBuffer] Redis append notice: {e}")

        # Fallback to in-memory dictionary list
        if session_id not in self._in_memory_fallback:
            self._in_memory_fallback[session_id] = []
        self._in_memory_fallback[session_id].append(msg)
        if len(self._in_memory_fallback[session_id]) > max_messages:
            self._in_memory_fallback[session_id] = self._in_memory_fallback[session_id][-max_messages:]

    def get_history(self, session_id: str, max_messages: int = 8) -> List[Dict[str, str]]:
        """Retrieves the past max_messages (default 8) from Redis log."""
        key = f"supernova:chat_history:{session_id}"

        if self.r:
            try:
                raw_msgs = self.r.lrange(key, -max_messages, -1)
                return [json.loads(m) for m in raw_msgs]
            except Exception as e:
                print(f"[RedisChatBuffer] Redis get history notice: {e}")

        # Fallback to in-memory list
        return self._in_memory_fallback.get(session_id, [])[-max_messages:]

    def clear_history(self, session_id: str):
        """Clears chat log buffer for a session."""
        key = f"supernova:chat_history:{session_id}"
        if self.r:
            try:
                self.r.delete(key)
            except Exception:
                pass
        self._in_memory_fallback.pop(session_id, None)
