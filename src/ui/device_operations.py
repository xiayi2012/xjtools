"""One pending connection/action, with immutable target and stale-result protection."""

from copy import deepcopy
from uuid import uuid4


class DeviceOperationRouter:
    def __init__(self):
        self.pending = None

    def request(self, server, action, current_id, busy=False, confirm_switch=lambda: True):
        if busy or self.pending:
            return 'blocked', None
        if not server.get('id'):
            return 'blocked', None
        if current_id == server['id']:
            return 'ready', None
        if current_id and not confirm_switch():
            return 'cancelled', None
        token = str(uuid4())
        self.pending = (token, deepcopy(server), action)
        return 'connect', token

    def matches(self, token):
        return bool(self.pending and self.pending[0] == token)

    def complete(self, token, success, connected_id):
        if not self.matches(token):
            return None
        _, server, action = self.pending
        self.pending = None
        return (server, action) if success and connected_id == server['id'] else None
