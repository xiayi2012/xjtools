import io
from types import SimpleNamespace

from src.core.ssh_connector import SSHClientWrapper


class Channel:
    def __init__(self, out=b'', err=b'', done=True):
        self.out, self.err, self.done, self.closed = out, err, done, False
    def recv_ready(self): return bool(self.out)
    def recv_stderr_ready(self): return bool(self.err)
    def recv(self, size):
        data, self.out = self.out, b''
        return data
    def recv_stderr(self, size):
        data, self.err = self.err, b''
        return data
    def exit_status_ready(self): return self.done
    def recv_exit_status(self):
        assert self.done, 'must not wait past deadline'
        return 0
    def close(self): self.closed = True


def wrapper_for(channel):
    wrapper = SSHClientWrapper()
    wrapper.client = SimpleNamespace(exec_command=lambda *a, **k: (io.StringIO(), SimpleNamespace(channel=channel), None))
    wrapper._ensure_active_connection = lambda: (True, '')
    return wrapper


def test_stderr_is_drained_and_channel_closed():
    channel = Channel(b'output', b'permission denied')
    wrapper = wrapper_for(channel)
    lines = list(wrapper.execute_stream('test'))
    assert 'output' in ''.join(lines) and 'permission denied' in ''.join(lines)
    assert channel.closed


def test_timeout_and_generator_close_release_channel():
    channel = Channel(done=False)
    wrapper = wrapper_for(channel)
    code, _, error = wrapper.execute('sleep 100', timeout=0)
    assert code == -1 and '超时' in error and channel.closed
    channel = Channel(b'first', done=False)
    wrapper = wrapper_for(channel)
    stream = wrapper.execute_stream('sleep 100')
    assert next(stream) == 'first'
    stream.close()
    assert channel.closed


def test_sudo_password_uses_stdin_and_applies_to_future_commands(capsys):
    wrapper = SSHClientWrapper()
    commands, passwords, channels = [], [], []
    class Input(io.StringIO):
        def close(self):
            passwords.append(self.getvalue())
            super().close()
    def execute(command, **kwargs):
        commands.append(command)
        channel = Channel(b'0\n' if 'id -u' in command else b'root-output\n')
        channels.append(channel)
        return Input(), SimpleNamespace(channel=channel), None
    wrapper.client = SimpleNamespace(exec_command=execute)
    wrapper._ensure_active_connection = lambda: (True, '')
    secret = 'p@ss$(echo should-not-run)"'
    assert wrapper.enable_sudo(secret)[0]
    assert wrapper.execute('whoami')[1] == 'root-output\n'
    assert list(wrapper.execute_stream('hostname')) == ['root-output\n']
    assert all('sudo -S' in command and 'exec 0</dev/null;' in command for command in commands)
    assert all(secret not in command for command in commands)
    assert passwords == [secret + '\n'] * 3
    assert secret not in capsys.readouterr().out
    assert all(c.closed for c in channels)
    wrapper.disconnect()
    assert wrapper._sudo_password is None
