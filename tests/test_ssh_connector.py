import socket
import threading
from unittest.mock import create_autospec

import paramiko
import pytest

from src.core.ssh_connector import SSHClientWrapper


@pytest.mark.parametrize('method', ['connect', 'connect_with_otp_callback'])
@pytest.mark.parametrize('auth', [{'password': 'test-password'}, {'key_filename': 'test-key'}])
def test_connection_arguments_match_installed_paramiko(monkeypatch, method, auth):
    # The real library signature must reject removed arguments as it would in production.
    client = create_autospec(paramiko.SSHClient, instance=True)
    monkeypatch.setattr(paramiko, 'SSHClient', lambda: client)
    wrapper = SSHClientWrapper()
    try:
        success, message = getattr(wrapper, method)('127.0.0.1', username='tester', **auth)
        assert success, message
        client.connect.assert_called_once()
        kwargs = client.connect.call_args.kwargs
        assert kwargs['hostname'] == '127.0.0.1'
        assert all(kwargs[key] == value for key, value in auth.items())
        client.get_transport.return_value.set_keepalive.assert_called_once_with(15)
    finally:
        wrapper.disconnect()


class LocalSSHServer(paramiko.ServerInterface):
    def __init__(self):
        self.command_received = threading.Event()

    def check_auth_password(self, username, password):
        if (username, password) == ('tester', 'test-password'):
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return 'password'

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == 'session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel, command):
        self.command_received.set()
        return command == b'echo ssh-test-ok'


@pytest.mark.parametrize('password, expected', [('test-password', True), ('wrong-password', False)])
def test_real_ssh_authentication_and_command(password, expected):
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(10)
    port = listener.getsockname()[1]
    server = LocalSSHServer()
    host_key = paramiko.RSAKey.generate(2048)
    errors = []
    transports = []

    def serve():
        try:
            conn, _ = listener.accept()
            transport = paramiko.Transport(conn)
            transports.append(transport)
            transport.add_server_key(host_key)
            transport.start_server(server=server)
            channel = transport.accept(5)
            if channel and server.command_received.wait(5):
                channel.sendall(b'ssh-test-ok\n')
                channel.send_exit_status(0)
                channel.close()
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    wrapper = SSHClientWrapper()
    try:
        success, message = wrapper.connect('127.0.0.1', port, 'tester', password, timeout=5)
        assert success is expected, message
        if expected:
            assert wrapper.execute('echo ssh-test-ok', timeout=5) == (0, 'ssh-test-ok\n', '')
        else:
            assert '认证失败' in message
    finally:
        wrapper.disconnect()
        for transport in transports:
            transport.close()
        listener.close()
        thread.join(timeout=10)
    assert not thread.is_alive()
    assert not errors
