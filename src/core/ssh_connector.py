"""
SSH 连接器模块
基于 paramiko 实现 SSH 连接、命令执行和文件上传功能
支持 OTP (一次性密码) 认证
"""

import paramiko
from paramiko.ssh_exception import (
    AuthenticationException,
    SSHException,
    NoValidConnectionsError,
    BadAuthenticationType
)
import time
import shlex
import codecs
import socket
from typing import Optional, Tuple, Generator, Callable, List
from pathlib import Path


class SSHClientWrapper:
    """SSH 客户端封装类，提供 SSH 连接、命令执行和文件上传功能"""
    
    def __init__(self):
        """初始化 SSH 客户端"""
        self.client: Optional[paramiko.SSHClient] = None
        self.sftp: Optional[paramiko.SFTPClient] = None
        self.is_connected: bool = False
        self._host: str = ""
        self._port: int = 22
        self._sudo_password = None
        self._stop_requested: bool = False  # 停止请求标志
        self._connect_params: dict = {}
        self._use_otp_connection: bool = False
        self._otp_challenge_callback: Optional[Callable] = None
    
    def request_stop(self):
        """请求停止当前命令执行"""
        self._stop_requested = True
    
    def clear_stop_request(self):
        """清除停止请求标志"""
        self._stop_requested = False
    
    def connect(
        self,
        host: str,
        port: int = 22,
        username: str = "",
        password: Optional[str] = None,
        key_filename: Optional[str] = None,
        timeout: int = 30
    ) -> Tuple[bool, str]:
        """
        连接到 SSH 服务器
        
        Args:
            host: 服务器地址
            port: SSH 端口，默认 22
            username: 用户名
            password: 密码（与 key_filename 二选一）
            key_filename: SSH 私钥文件路径（与 password 二选一）
            timeout: 连接超时时间（秒）
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        try:
            identity = (host, port, username)
            old_identity = tuple(self._connect_params.get(k) for k in ('host', 'port', 'username'))
            sudo_password = self._sudo_password if identity == old_identity else None
            self.disconnect()
            self._sudo_password = sudo_password
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            connect_kwargs = {
                "hostname": host,
                "port": port,
                "username": username,
                "timeout": timeout,
                "look_for_keys": False,
                "allow_agent": False,
                "banner_timeout": timeout,
                "auth_timeout": timeout,
                "channel_timeout": timeout,
                "disabled_algorithms": None,
                "compress": True
            }
            
            if password:
                connect_kwargs["password"] = password
            elif key_filename:
                connect_kwargs["key_filename"] = key_filename
            else:
                return False, "必须提供密码或 SSH 私钥"
            
            # Windows socket 修复：添加重试机制
            import sys
            max_retries = 3 if sys.platform == "win32" else 1
            
            for attempt in range(max_retries):
                try:
                    if attempt > 0:
                        time.sleep(1)
                        # 重新创建客户端
                        self.client = paramiko.SSHClient()
                        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    
                    self.client.connect(**connect_kwargs)
                    transport = self.client.get_transport()
                    if transport:
                        transport.set_keepalive(15)
                    self.is_connected = True
                    self._host = host
                    self._port = port
                    self._connect_params = {
                        "host": host,
                        "port": port,
                        "username": username,
                        "password": password,
                        "key_filename": key_filename,
                        "timeout": timeout,
                    }
                    self._use_otp_connection = False
                    self._otp_challenge_callback = None
                    return True, f"成功连接到 {host}:{port}"
                    
                except OSError as e:
                    # Windows socket 错误 (WinError 10038 等)
                    if sys.platform == "win32" and attempt < max_retries - 1:
                        continue
                    raise
            
            return False, f"连接失败：重试{max_retries}次后仍然失败"
            
        except AuthenticationException:
            return False, "认证失败：用户名或密码错误"
        except SSHException as e:
            return False, f"SSH 错误：{str(e)}"
        except NoValidConnectionsError:
            return False, f"无法连接到 {host}:{port}，请检查服务器地址和端口"
        except TimeoutError:
            return False, f"连接超时：{host}:{port} 无响应"
        except Exception as e:
            return False, f"连接异常：{str(e)}"
    
    def connect_with_otp_callback(
        self,
        host: str,
        port: int = 22,
        username: str = "",
        password: Optional[str] = None,
        key_filename: Optional[str] = None,
        timeout: int = 30,
        otp_challenge_callback: Optional[Callable] = None
    ) -> Tuple[bool, str]:
        """
        连接到 SSH 服务器，支持 OTP 认证
        
        支持两种OTP模式：
        1. keyboard-interactive认证（服务器配置的OTP）
        2. 密码认证后的二次验证（动态密钥方式）
        
        Args:
            host: 服务器地址
            port: SSH 端口，默认 22
            username: 用户名
            password: 密码（用于第一阶段认证）
            key_filename: SSH 私钥文件路径
            timeout: 连接超时时间（秒）
            otp_challenge_callback: OTP挑战回调函数
                签名: callback(challenge_text) -> str
                - challenge_text: 服务器返回的挑战信息（如 dynamic key）
                返回: 用户计算后的OTP密码
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        self.disconnect()
        try:
            
            # 创建 SSH 客户端
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # 基础连接参数
            connect_kwargs = {
                "hostname": host,
                "port": port,
                "username": username,
                "timeout": timeout,
                "look_for_keys": False,
                "allow_agent": False,
                "banner_timeout": timeout
            }
            
            if password:
                connect_kwargs["password"] = password
            elif key_filename:
                connect_kwargs["key_filename"] = key_filename
            else:
                return False, "必须提供密码或 SSH 私钥"
            
            # 先尝试普通密码连接
            try:
                self.client.connect(**connect_kwargs)
                transport = self.client.get_transport()
                if transport:
                    transport.set_keepalive(15)
            except AuthenticationException as e:
                return False, f"密码认证失败：{str(e)}"
            
            # 密码认证成功，检查是否需要OTP验证
            self.is_connected = True
            self._host = host
            self._port = port
            self._connect_params = {
                "host": host,
                "port": port,
                "username": username,
                "password": password,
                "key_filename": key_filename,
                "timeout": timeout,
            }
            self._use_otp_connection = True
            self._otp_challenge_callback = otp_challenge_callback
            
            if otp_challenge_callback:
                otp_result = self._handle_post_auth_otp(otp_challenge_callback, timeout)
                
                if not otp_result[0]:
                    self.is_connected = False
                    return otp_result
                
                # 检查是否需要使用shell执行
                if "使用shell执行" in otp_result[1]:
                    pass
                else:
                    pass
            
            # 最终验证连接状态
            transport = self.client.get_transport() if self.client else None
            if not transport or not transport.is_active():
                self.is_connected = False
                return False, "连接已断开"
            
            # 确保is_connected状态正确
            self.is_connected = True
            
            return True, f"成功连接到 {host}:{port}（OTP验证完成）"
            
        except AuthenticationException:
            return False, "认证失败：用户名或密码错误"
        except SSHException as e:
            return False, f"SSH 错误：{str(e)}"
        except NoValidConnectionsError:
            return False, f"无法连接到 {host}:{port}，请检查服务器地址和端口"
        except TimeoutError:
            return False, f"连接超时：{host}:{port} 无响应"
        except Exception as e:
            return False, f"连接异常：{str(e)}"
    
    def _handle_post_auth_otp(
        self, 
        otp_challenge_callback: Callable,
        timeout: int = 30
    ) -> Tuple[bool, str]:
        """
        处理密码认证后的OTP二次验证
        
        某些服务器在密码认证成功后会返回动态密钥，需要用户计算OTP
        例如: "dynamic key: 414348s\nDasPassword: "
        
        重要：OTP验证后需要重新建立连接，因为invoke_shell会改变会话状态
        """
        if not self.client:
            return False, "SSH客户端未连接"
        
        import time
        
        try:
            
            # 使用exec_command执行一个简单的命令来触发OTP提示
            # 某些服务器会在执行命令时要求OTP
            try:
                # 先尝试用exec_command获取challenge
                # 执行一个会触发PAM验证的命令
                stdin, stdout, stderr = self.client.exec_command("echo 'otp_check'", timeout=5)
                
                # 检查是否立即完成（不需要OTP）
                if stdout.channel.exit_status_ready():
                    output = stdout.read().decode('utf-8', errors='ignore')
                    if "otp_check" in output:
                        return True, ""
            except Exception as e:
                pass
            
            # 如果上面没触发OTP，使用invoke_shell
            channel = self.client.invoke_shell()
            channel.settimeout(timeout)
            
            # 等待并读取服务器发送的challenge
            time.sleep(1)
            
            challenge_data = ""
            otp_detected = False
            
            # 读取challenge，最多等待10秒
            for _ in range(10):
                if channel.recv_ready():
                    chunk = channel.recv(4096).decode('utf-8', errors='ignore')
                    challenge_data += chunk
                    
                    # 检查是否收到了OTP提示
                    if any(keyword in challenge_data.lower() for keyword in 
                           ["daspassword", "password:", "dynamic key", "verification", "otp", "passcode"]):
                        otp_detected = True
                        time.sleep(0.5)  # 等待更多数据
                        if channel.recv_ready():
                            chunk = channel.recv(4096).decode('utf-8', errors='ignore')
                            challenge_data += chunk
                        break
                
                # 检查是否已经进入shell（不需要OTP）
                if any(prompt in challenge_data for prompt in ["$ ", "# ", ">", "~$"]):
                    channel.close()
                    return True, ""
                
                time.sleep(1)
            
            
            if not otp_detected and not challenge_data:
                channel.close()
                # 不认为是错误，可能是OTP已经完成或不需要
                return True, ""
            
            # 回调获取OTP密码
            otp_password = otp_challenge_callback(challenge_data)
            
            if not otp_password:
                channel.close()
                return False, "OTP验证取消"
            
            # 发送OTP密码
            channel.send(otp_password + "\n")
            time.sleep(2)
            
            # 读取响应
            response = ""
            while channel.recv_ready():
                chunk = channel.recv(4096).decode('utf-8', errors='ignore')
                response += chunk
            
            
            # 检查是否验证成功
            if any(fail_msg in response.lower() for fail_msg in 
                   ["denied", "failed", "permission denied", "authentication failure"]):
                channel.close()
                return False, f"OTP验证失败：{response[:200]}"
            
            # 关键检查：如果仍然显示密码提示符而没有shell提示符，说明OTP被拒绝
            # 服务器在OTP错误时通常会重新显示密码提示
            response_lower = response.lower()
            has_password_prompt = any(prompt in response_lower for prompt in 
                                      ["password:", "daspassword:", "passcode:"])
            has_shell_prompt = any(prompt in response for prompt in ["$ ", "# ", "~$", "[root@", "root@"])
            
            if has_password_prompt and not has_shell_prompt:
                channel.close()
                return False, f"OTP验证失败：密码错误或已过期"
            
            # OTP验证成功！
            # 关键问题：每个新shell channel都需要重新OTP验证
            # 解决方案：保留原始OTP验证后的shell channel，用于执行所有命令
            
            # 保留shell channel不关闭，用于后续命令执行
            self._otp_shell = channel  # 保留OTP验证后的shell
            self._use_shell_execution = True
            
            # 清空shell的初始提示符输出
            time.sleep(0.3)
            initial_output = ""
            while channel.recv_ready():
                chunk = channel.recv(4096).decode('utf-8', errors='ignore')
                initial_output += chunk
            
            # 测试shell是否可以执行命令
            channel.send(b"echo SHELL_READY\n")
            time.sleep(1)
            test_output = ""
            while channel.recv_ready():
                chunk = channel.recv(4096).decode('utf-8', errors='ignore')
                test_output += chunk
            
            if "SHELL_READY" in test_output:
                pass
            else:
                pass
            
            return True, "OTP验证成功_使用shell执行"
            
        except Exception as e:
            return False, f"OTP验证异常：{str(e)}"

    def _mark_disconnected(self):
        """同步连接状态，避免 transport 已断开但界面仍认为已连接。"""
        self.is_connected = False
        self._otp_shell = None

    def _transport_is_active(self) -> bool:
        try:
            if not self.client:
                return False
            transport = self.client.get_transport()
            return bool(transport and transport.is_active())
        except Exception:
            return False

    def _open_replacement_shell(self):
        """Open a fresh interactive shell on the existing authenticated SSH transport."""
        if not self.client or not self._transport_is_active():
            return None

        try:
            shell = self.client.invoke_shell()
            shell.settimeout(2.0)
            time.sleep(0.5)

            clear_attempts = 0
            while shell.recv_ready() and clear_attempts < 10:
                shell.recv(4096)
                clear_attempts += 1

            self._otp_shell = shell
            self._use_shell_execution = True
            self.is_connected = True
            return shell
        except Exception as e:
            return None

    def _reconnect_otp_session(self) -> Tuple[bool, str]:
        """Reconnect an OTP session and recreate the authenticated shell channel."""
        if not self._connect_params or not self._otp_challenge_callback:
            return False, "缺少OTP自动重连参数"

        try:
            if self.client:
                self.client.close()
        except Exception:
            pass

        params = dict(self._connect_params)
        return self.connect_with_otp_callback(
            otp_challenge_callback=self._otp_challenge_callback,
            **params
        )

    def _ensure_active_connection(self) -> Tuple[bool, str]:
        """确保 SSH transport 可用；普通连接断开时自动重连一次。"""
        if self._transport_is_active():
            self.is_connected = True
            return True, ""

        self._mark_disconnected()
        if not self._connect_params:
            return False, "连接已断开"

        if self._use_otp_connection or getattr(self, '_use_shell_execution', False):
            if self._otp_challenge_callback and self._connect_params:
                success, message = self._reconnect_otp_session()
                if success:
                    return True, ""
                return False, f"连接已断开，OTP自动重连失败：{message}"
            return False, "连接已断开，请重新连接服务器后再执行"

        params = dict(self._connect_params)
        success, message = self.connect(**params)
        if success:
            return True, ""
        return False, f"连接已断开，自动重连失败：{message}"
    
    def _open_exec(self, command, timeout, sudo_password=None):
        password = self._sudo_password if sudo_password is None else sudo_password
        if password is not None:
            command = "sudo -S -p '' -- sh -c " + shlex.quote('exec 0</dev/null; ' + command)
        stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
        try:
            if password is not None:
                try:
                    stdin.write(password + '\n')
                    stdin.flush()
                except (OSError, EOFError):
                    if not stdout.channel.exit_status_ready():
                        raise
            stdin.close()
        except BaseException:
            stdout.channel.close()
            raise
        return stdout.channel

    def _exec_chunks(self, command, timeout, sudo_password=None):
        channel = None
        deadline = time.monotonic() + timeout
        decoders = {key: codecs.getincrementaldecoder('utf-8')(errors='replace') for key in ('stdout', 'stderr')}
        try:
            channel = self._open_exec(command, timeout, sudo_password)
            last_data = time.monotonic()
            while True:
                if self._stop_requested:
                    yield 'error', '命令已停止'
                    yield 'exit', -1
                    return
                if time.monotonic() >= deadline:
                    yield 'error', '命令执行超时'
                    yield 'exit', -1
                    return
                received = False
                for key, ready, read in (('stdout', channel.recv_ready, channel.recv),
                                         ('stderr', channel.recv_stderr_ready, channel.recv_stderr)):
                    if ready():
                        received = True
                        text = decoders[key].decode(read(65535))
                        if text:
                            yield key, text
                if received:
                    last_data = time.monotonic()
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    for key, decoder in decoders.items():
                        tail = decoder.decode(b'', final=True)
                        if tail:
                            yield key, tail
                    yield 'exit', channel.recv_exit_status()
                    return
                if not received:
                    if time.monotonic() - last_data >= 1:
                        yield 'heartbeat', ''
                        last_data = time.monotonic()
                    time.sleep(.025)
        except socket.timeout:
            yield 'error', '命令执行超时'
            yield 'exit', -1
        except Exception as exc:
            yield 'error', str(exc)
            yield 'exit', -1
        finally:
            if channel is not None:
                channel.close()

    def enable_sudo(self, password, timeout=15):
        if getattr(self, '_use_shell_execution', False):
            return False, '当前交互式连接不支持 sudo 命令模式'
        if '\n' in password or '\r' in password:
            return False, '密码不能包含换行符'
        ok, message = self._ensure_active_connection()
        if not ok:
            return False, message
        output, errors, code = [], [], -1
        for kind, data in self._exec_chunks('id -u', timeout, sudo_password=password):
            if kind == 'stdout': output.append(data)
            elif kind in ('stderr', 'error'): errors.append(data)
            elif kind == 'exit': code = data
        if code == 0 and ''.join(output).strip() == '0':
            self._sudo_password = password
            return True, ''
        self._sudo_password = None
        return False, ''.join(errors).strip() or '当前账户没有 sudo 权限或密码不正确'

    def execute(self, command: str, timeout: int = 60) -> Tuple[int, str, str]:
        if not self.client:
            return -1, '', 'SSH客户端未初始化'
        ok, message = self._ensure_active_connection()
        if not ok:
            return -1, '', message
        if getattr(self, '_use_shell_execution', False):
            return self._execute_via_shell(command, timeout)
        output, errors, code = [], [], -1
        for kind, data in self._exec_chunks(command, timeout):
            if kind == 'stdout': output.append(data)
            elif kind in ('stderr', 'error'): errors.append(data)
            elif kind == 'exit': code = data
        return code, ''.join(output), ''.join(errors)

    def _execute_via_shell(self, command: str, timeout: int = 60) -> Tuple[int, str, str]:
        """通过OTP验证后的shell执行命令（复用同一个shell）"""
        import time
        import uuid
        
        
        # 清除停止请求
        self._stop_requested = False
        
        # 使用保留的OTP验证后的shell channel
        shell = getattr(self, '_otp_shell', None)
        is_new_shell = False
        
        if not shell or (hasattr(shell, 'closed') and shell.closed):
            if self._use_otp_connection and self._otp_challenge_callback:
                success, message = self._reconnect_otp_session()
                if not success:
                    return -1, "", f"OTP自动重连失败：{message}"
                shell = getattr(self, '_otp_shell', None)
            elif self._transport_is_active():
                shell = self._open_replacement_shell()
            else:
                self._mark_disconnected()
                return -1, "", "连接已断开，请重新连接服务器后再执行"
            is_new_shell = True
            if not shell:
                return -1, "", "OTP Shell已不可用，请重新连接服务器后再执行"
        else:
            if not self._transport_is_active():
                self._mark_disconnected()
                if self._use_otp_connection and self._otp_challenge_callback:
                    success, message = self._reconnect_otp_session()
                    if not success:
                        return -1, "", f"OTP自动重连失败：{message}"
                    shell = getattr(self, '_otp_shell', None)
                    if not shell:
                        return -1, "", "OTP Shell已不可用，请重新连接服务器后再执行"
                else:
                    return -1, "", "连接已断开，请重新连接服务器后再执行"
        
        try:
            # 清空shell的输出缓冲区，确保shell处于干净状态
            clear_attempts = 0
            while shell.recv_ready() and clear_attempts < 10:
                try:
                    cleared = shell.recv(4096)
                    clear_attempts += 1
                except:
                    break
            
            # 使用UUID生成唯一的marker，避免引号冲突
            marker = f"__CMD_{uuid.uuid4().hex[:16]}__"
            start_marker = f"{marker}_START"
            end_marker = f"{marker}_END"
            
            # 使用 base64 编码命令，避免所有特殊字符问题
            import base64
            encoded_cmd = base64.b64encode(command.encode('utf-8')).decode('ascii')
            
            # 构建完整的执行命令
            # 使用 printf 输出 marker，避免 echo 的回显问题
            # 使用子 shell 执行命令，隔离环境；避免每条命令启动 login shell 影响交互会话稳定性。
            full_command = f'printf "%s\\n" "{start_marker}"; echo {encoded_cmd} | base64 -d | bash 2>&1; printf "%s\\n" "{end_marker}"'
            
            
            # 检查shell是否可用
            if not shell or (hasattr(shell, 'closed') and shell.closed):
                print(f"[ERROR] Shell is not available before sending command, trying OTP session reconnect")
                if self._use_otp_connection and self._otp_challenge_callback:
                    success, message = self._reconnect_otp_session()
                    if not success:
                        return -1, "", f"OTP自动重连失败：{message}"
                    shell = getattr(self, '_otp_shell', None)
                elif self._transport_is_active():
                    shell = self._open_replacement_shell()
                else:
                    self._mark_disconnected()
                    return -1, "", "连接已断开，请重新连接服务器后再执行"
                if not shell:
                    return -1, "", "OTP Shell已不可用，请重新连接服务器后再执行"
            
            try:
                shell.send((full_command + "\n").encode())
            except Exception as send_error:
                print(f"[ERROR] Failed to send command: {send_error}")
                if not self._transport_is_active():
                    self._mark_disconnected()
                    if self._use_otp_connection and self._otp_challenge_callback:
                        success, message = self._reconnect_otp_session()
                        if not success:
                            return -1, "", f"OTP自动重连失败：{message}"
                        shell = getattr(self, '_otp_shell', None)
                    else:
                        return -1, "", "连接已断开，请重新连接服务器后再执行"
                else:
                    shell = self._open_replacement_shell()
                if shell:
                    try:
                        shell.send((full_command + "\n").encode())
                    except Exception as retry_error:
                        return -1, "", f"命令发送失败：{retry_error}"
                else:
                    return -1, "", f"命令发送失败：{send_error}"
            
            # 等待命令完成（通过检测结束marker）
            output = ""
            start_time = time.time()
            last_recv_time = start_time
            
            while time.time() - start_time < timeout:
                # 检查停止请求
                if self._stop_requested:
                    # 发送 Ctrl+C 中断命令
                    try:
                        shell.send(b'\x03')
                        time.sleep(0.2)
                        if shell.recv_ready():
                            shell.recv(4096)
                    except:
                        pass
                    return -1, output, "命令执行被用户中断"
                
                try:
                    if shell.recv_ready():
                        chunk = shell.recv(8192).decode('utf-8', errors='ignore')
                        output += chunk
                        last_recv_time = time.time()
                        
                        # 调试：记录接收到的数据
                        if len(chunk) > 0:
                            pass
                        
                        # 检查是否收到结束标记
                        if end_marker in output:
                            # 继续接收2秒的数据，确保获取到所有输出
                            remaining_start = time.time()
                            while time.time() - remaining_start < 2:
                                if shell.recv_ready():
                                    remaining_chunk = shell.recv(8192).decode('utf-8', errors='ignore')
                                    output += remaining_chunk
                                else:
                                    time.sleep(0.1)
                            break
                    else:
                        # 如果超过10秒没有收到数据，检查是否已经完成
                        no_data_time = time.time() - last_recv_time
                        if no_data_time > 10 and start_marker in output:
                            break
                        # 如果超过30秒没有收到任何数据，认为命令卡住
                        if no_data_time > 30:
                            # 发送 Ctrl+C 中断命令
                            try:
                                shell.send(b'\x03')
                                time.sleep(0.2)
                                if shell.recv_ready():
                                    shell.recv(4096)
                            except:
                                pass
                            return -1, output, "命令执行超时（30秒无响应）"
                        time.sleep(0.1)
                except Exception as recv_error:
                    # 检查是否是超时
                    if "timeout" in str(recv_error).lower():
                        if time.time() - last_recv_time > 10:
                            break
                    else:
                        if not self._transport_is_active():
                            self._mark_disconnected()
                            if self._use_otp_connection and self._otp_challenge_callback:
                                success, message = self._reconnect_otp_session()
                                if success:
                                    retry_code, retry_output, retry_error = self._execute_via_shell(command, timeout)
                                    return retry_code, output + retry_output, retry_error
                                return -1, output, f"OTP自动重连失败：{message}"
                            return -1, output, "连接已断开，请重新连接服务器后再执行"
                        raise
            
            
            # 提取开始和结束marker之间的内容
            # 使用rfind找到最后一个start_marker，避免shell回显中的marker
            last_start_idx = output.rfind(start_marker)
            if last_start_idx != -1:
                output = output[last_start_idx + len(start_marker):]
                last_end_idx = output.rfind(end_marker)
                if last_end_idx != -1:
                    output = output[:last_end_idx]
            
            
            # 清理输出（移除命令回显和提示符）
            lines = output.split('\n')
            clean_lines = []
            
            for line in lines:
                line_stripped = line.strip()
                
                # 跳过空行
                if not line_stripped:
                    continue
                
                # 跳过marker相关的内容
                if marker in line_stripped:
                    continue
                
                # 跳过shell提示符（各种格式）
                if line_stripped.startswith('-bash-'):
                    continue
                if line_stripped.startswith('[root@'):
                    continue
                if line_stripped.startswith('root@'):
                    continue
                if line_stripped.startswith('[') and ']#' in line_stripped:
                    continue
                if line_stripped.endswith('# ') or line_stripped.endswith('$ '):
                    continue
                if line_stripped.endswith('#') and '@' in line_stripped:
                    continue
                
                # 跳过 printf 命令回显
                if line_stripped.startswith('printf'):
                    continue
                
                # 跳过 echo 命令回显（包括 base64 编码的命令）
                if line_stripped.startswith('echo ') and 'base64' in line_stripped:
                    continue
                if line_stripped.startswith('echo ') and '| base64' in line_stripped:
                    continue
                
                # 跳过以 "; echo" 开头的命令回显
                if line_stripped.startswith('"; echo'):
                    continue
                if line_stripped.startswith("'") and 'echo' in line_stripped and 'base64' in line_stripped:
                    continue
                
                # 跳过包含完整命令的回显行
                if '| base64 -d | bash' in line_stripped:
                    continue
                
                # 跳过包含base64编码的行（可能是命令回显）
                if 'ZnJlZSAtaCA' in line_stripped or 'cHMgYXV4IC' in line_stripped:
                    continue
                
                # 跳过包含"2>/dev/null"的行（可能是命令回显）
                if '2>/dev/null' in line_stripped and 'echo' not in line_stripped:
                    continue
                
                # 保留有意义的输出
                clean_lines.append(line)
            
            clean_output = '\n'.join(clean_lines).strip()
            
            # 调试：显示清理前后的输出对比
            
            # 显示被过滤掉的行
            filtered_count = len(lines) - len(clean_lines) - 1  # 减1是因为split会产生额外空行
            if filtered_count > 0:
                # 显示被过滤的行示例
                for i, line in enumerate(lines):
                    if line.strip() and line.strip() not in clean_output:
                        if i > 5:  # 只显示前5个被过滤的行
                            break
            
            # 如果清理后输出为空，但原始输出不为空，使用原始输出
            if not clean_output and output.strip():
                print(f"[WARNING] Clean output is empty but raw output is not. Using raw output.")
                clean_output = output.strip()
            
            if len(clean_output) > 0:
                pass
            else:
                pass
            
            return 0, clean_output, ""
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            if not self._transport_is_active():
                self._mark_disconnected()
                return -1, "", "连接已断开，请重新连接服务器后再执行"
            return -1, "", str(e)
    
    
    def execute_stream(self, command: str, timeout: int = 300) -> Generator[str, None, None]:
        if not self.client:
            yield '执行错误: 未连接到服务器'
            return
        ok, message = self._ensure_active_connection()
        if not ok:
            yield f'执行错误: {message}'
            return
        if getattr(self, '_use_shell_execution', False):
            code, output, error = self._execute_via_shell(command, timeout)
            if output:
                yield output
            if error or code != 0:
                yield f'执行错误: {error or "命令退出码 " + str(code)}'
            return
        chunks = self._exec_chunks(command, timeout)
        try:
            for kind, data in chunks:
                if kind in ('stdout', 'heartbeat'):
                    yield data
                elif kind == 'stderr':
                    yield f'[stderr] {data}'
                elif kind == 'error':
                    yield f'执行错误: {data}'
                elif kind == 'exit' and data != 0:
                    yield f'执行错误: 命令退出码 {data}'
        finally:
            chunks.close()

    def upload_file(
        self,
        local_path: str,
        remote_path: str
    ) -> Tuple[bool, str]:
        """
        上传文件到远程服务器
        
        Args:
            local_path: 本地文件路径
            remote_path: 远程文件路径
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        if not self.is_connected or not self.client:
            return False, "未连接到服务器"
        
        try:
            # 检查本地文件是否存在
            local_file = Path(local_path)
            if not local_file.exists():
                return False, f"本地文件不存在：{local_path}"
            
            # 创建 SFTP 客户端
            if not self.sftp:
                self.sftp = self.client.open_sftp()
            
            # 上传文件
            self.sftp.put(local_path, remote_path)
            
            return True, f"文件上传成功：{local_path} -> {remote_path}"
            
        except FileNotFoundError:
            return False, f"远程路径不存在：{remote_path}"
        except PermissionError:
            return False, f"权限不足：无法写入 {remote_path}"
        except Exception as e:
            return False, f"文件上传异常：{str(e)}"
    
    def download_file(
        self,
        remote_path: str,
        local_path: str
    ) -> Tuple[bool, str]:
        """
        从远程服务器下载文件
        
        Args:
            remote_path: 远程文件路径
            local_path: 本地文件路径
            
        Returns:
            Tuple[bool, str]: (是否成功, 消息)
        """
        if not self.is_connected or not self.client:
            return False, "未连接到服务器"
        
        try:
            # 创建 SFTP 客户端
            if not self.sftp:
                self.sftp = self.client.open_sftp()
            
            # 下载文件
            self.sftp.get(remote_path, local_path)
            
            return True, f"文件下载成功：{remote_path} -> {local_path}"
            
        except FileNotFoundError:
            return False, f"远程文件不存在：{remote_path}"
        except PermissionError:
            return False, f"权限不足：无法写入 {local_path}"
        except Exception as e:
            return False, f"文件下载异常：{str(e)}"
    
    def disconnect(self) -> None:
        """断开 SSH 连接"""
        self._sudo_password = None
        self._use_shell_execution = False
        self._otp_shell = None
        if self.sftp:
            try:
                self.sftp.close()
            except Exception:
                pass
            self.sftp = None
        
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None
        
        self.is_connected = False
    
    def run_with_pty(self, command: str, password: str, password_prompt: str = "Password:", timeout: int = 10) -> Tuple[int, str, str]:
        """使用PTY执行需要交互输入的命令（如su）
        
        Args:
            command: 要执行的命令（如 "su - root -c whoami"）
            password: 交互式输入的密码
            password_prompt: 密码提示符（默认 "Password:"）
            timeout: 超时时间（秒）
            
        Returns:
            Tuple[int, str, str]: (返回码, 标准输出, 标准错误)
        """
        if not self.client:
            return -1, "", "SSH客户端未连接"
        
        transport = self.client.get_transport()
        if not transport or not transport.is_active():
            return -1, "", "连接已断开"
        
        try:
            # 打开会话并请求PTY
            channel = transport.open_session()
            channel.get_pty(width=80, height=24)
            channel.exec_command(command)
            
            # 等待密码提示符出现
            start_time = time.time()
            output = ""
            password_sent = False
            authenticated = False
            loop_count = 0
            max_loops = int(timeout / 0.05)  # 0.05秒 * max_loops = timeout
            
            while loop_count < max_loops:
                loop_count += 1
                
                if channel.recv_ready():
                    chunk = channel.recv(4096).decode('utf-8', errors='ignore')
                    output += chunk
                    
                    # 检测到密码提示符，发送密码
                    if not password_sent and password_prompt in chunk:
                        time.sleep(0.1)
                        channel.send((password + '\n').encode('utf-8'))
                        password_sent = True
                        continue
                    
                    # 检测认证失败（再次出现密码提示）
                    if password_sent and not authenticated and password_prompt in chunk:
                        break
                    
                    # 检测su成功执行完毕（出现root提示符或命令输出包含root）
                    if password_sent and not authenticated:
                        if 'root' in output.lower() or '#' in output:
                            authenticated = True
                
                # 检查channel是否已关闭
                if channel.exit_status_ready():
                    break
                
                time.sleep(0.05)
            
            
            # 读取所有剩余输出
            channel.settimeout(1.0)
            try:
                while True:
                    if channel.recv_ready():
                        chunk = channel.recv(4096).decode('utf-8', errors='ignore')
                        output += chunk
                    else:
                        break
            except socket.timeout:
                pass
            except Exception as e:
                pass
            
            # 获取退出状态
            try:
                exit_status = channel.recv_exit_status()
            except Exception as e:
                exit_status = -1
            
            channel.close()
            
            return exit_status, output, ""
            
        except Exception as e:
            return -1, "", str(e)
    
    def __enter__(self):
        """支持 with 语句"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出 with 语句时自动断开连接"""
        self.disconnect()
    
    def __del__(self):
        """析构时断开连接"""
        self.disconnect()
