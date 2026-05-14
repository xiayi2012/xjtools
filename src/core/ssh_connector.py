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
                "gss_auth": False,
                "gss_kex": False,
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
                        print(f"[DEBUG] SSH连接重试 {attempt + 1}/{max_retries}")
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
                        print(f"[DEBUG] Windows socket错误，重试: {str(e)}")
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
        try:
            print(f"[DEBUG] connect_with_otp_callback called")
            print(f"[DEBUG] host={host}, username={username}, has_password={bool(password)}")
            print(f"[DEBUG] has_otp_callback={bool(otp_challenge_callback)}")
            
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
            print(f"[DEBUG] Attempting password authentication...")
            try:
                self.client.connect(**connect_kwargs)
                transport = self.client.get_transport()
                if transport:
                    transport.set_keepalive(15)
                print(f"[DEBUG] Password auth succeeded, checking for OTP challenge...")
            except AuthenticationException as e:
                print(f"[DEBUG] Password auth failed: {e}")
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
                print(f"[DEBUG] Checking for OTP challenge from server...")
                otp_result = self._handle_post_auth_otp(otp_challenge_callback, timeout)
                
                if not otp_result[0]:
                    self.is_connected = False
                    return otp_result
                
                # 检查是否需要使用shell执行
                if "使用shell执行" in otp_result[1]:
                    print(f"[DEBUG] OTP completed, will use shell-based command execution")
                else:
                    print(f"[DEBUG] OTP verification completed, connection ready")
            
            # 最终验证连接状态
            transport = self.client.get_transport() if self.client else None
            if not transport or not transport.is_active():
                self.is_connected = False
                return False, "连接已断开"
            
            # 确保is_connected状态正确
            self.is_connected = True
            
            print(f"[DEBUG] Connection fully established and verified")
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
            print(f"[DEBUG] Creating channel for OTP challenge...")
            
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
                        print(f"[DEBUG] No OTP required, command succeeded")
                        return True, ""
            except Exception as e:
                print(f"[DEBUG] exec_command attempt: {e}")
            
            # 如果上面没触发OTP，使用invoke_shell
            print(f"[DEBUG] Using invoke_shell for OTP challenge...")
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
                    print(f"[DEBUG] Received chunk: {repr(chunk[:100])}...")
                    
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
                    print(f"[DEBUG] Shell ready, no OTP challenge detected")
                    channel.close()
                    return True, ""
                
                time.sleep(1)
            
            print(f"[DEBUG] Challenge data: {repr(challenge_data)}")
            
            if not otp_detected and not challenge_data:
                print(f"[DEBUG] No OTP challenge detected after timeout")
                channel.close()
                # 不认为是错误，可能是OTP已经完成或不需要
                return True, ""
            
            # 回调获取OTP密码
            otp_password = otp_challenge_callback(challenge_data)
            
            if not otp_password:
                channel.close()
                return False, "OTP验证取消"
            
            # 发送OTP密码
            print(f"[DEBUG] Sending OTP password...")
            channel.send(otp_password + "\n")
            time.sleep(2)
            
            # 读取响应
            response = ""
            while channel.recv_ready():
                chunk = channel.recv(4096).decode('utf-8', errors='ignore')
                response += chunk
            
            print(f"[DEBUG] OTP response: {repr(response[:200])}")
            
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
                print(f"[DEBUG] OTP rejected: password prompt still shown after submission")
                channel.close()
                return False, f"OTP验证失败：密码错误或已过期"
            
            # OTP验证成功！
            # 关键问题：每个新shell channel都需要重新OTP验证
            # 解决方案：保留原始OTP验证后的shell channel，用于执行所有命令
            print(f"[DEBUG] OTP verification completed successfully")
            print(f"[DEBUG] CRITICAL: Keeping OTP-authenticated shell channel for all commands")
            
            # 保留shell channel不关闭，用于后续命令执行
            self._otp_shell = channel  # 保留OTP验证后的shell
            self._use_shell_execution = True
            
            # 清空shell的初始提示符输出
            time.sleep(0.3)
            initial_output = ""
            while channel.recv_ready():
                chunk = channel.recv(4096).decode('utf-8', errors='ignore')
                initial_output += chunk
            print(f"[DEBUG] Cleared initial shell output: {repr(initial_output[:200])}")
            
            # 测试shell是否可以执行命令
            print(f"[DEBUG] Testing shell with simple echo command...")
            channel.send(b"echo SHELL_READY\n")
            time.sleep(1)
            test_output = ""
            while channel.recv_ready():
                chunk = channel.recv(4096).decode('utf-8', errors='ignore')
                test_output += chunk
            print(f"[DEBUG] Shell test output: {repr(test_output[:200])}")
            
            if "SHELL_READY" in test_output:
                print(f"[DEBUG] Shell is working correctly!")
            else:
                print(f"[DEBUG] WARNING: Shell test failed, output doesn't contain SHELL_READY")
            
            return True, "OTP验证成功_使用shell执行"
            
        except Exception as e:
            print(f"[DEBUG] OTP handling error: {e}")
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
            print("[DEBUG] Opening replacement OTP shell on active transport")
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
            print(f"[DEBUG] Failed to open replacement OTP shell: {e}")
            return None

    def _reconnect_otp_session(self) -> Tuple[bool, str]:
        """Reconnect an OTP session and recreate the authenticated shell channel."""
        if not self._connect_params or not self._otp_challenge_callback:
            return False, "缺少OTP自动重连参数"

        print("[DEBUG] Reconnecting full OTP session")
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
                print("[DEBUG] OTP SSH transport inactive, trying automatic OTP reconnect")
                success, message = self._reconnect_otp_session()
                if success:
                    return True, ""
                return False, f"连接已断开，OTP自动重连失败：{message}"
            return False, "连接已断开，请重新连接服务器后再执行"

        print("[DEBUG] SSH transport inactive, trying one automatic reconnect")
        params = dict(self._connect_params)
        success, message = self.connect(**params)
        if success:
            return True, ""
        return False, f"连接已断开，自动重连失败：{message}"
    
    def execute(
        self,
        command: str,
        timeout: int = 60
    ) -> Tuple[int, str, str]:
        """
        执行远程命令
        
        Args:
            command: 要执行的命令
            timeout: 命令执行超时时间（秒）
            
        Returns:
            Tuple[int, str, str]: (返回码, 标准输出, 标准错误)
        """
        print(f"[DEBUG] execute() called: is_connected={self.is_connected}, client={self.client is not None}, use_shell={getattr(self, '_use_shell_execution', False)}")
        
        if not self.client:
            print(f"[DEBUG] execute() failed: client is None")
            return -1, "", "SSH客户端未初始化"
        
        ok, message = self._ensure_active_connection()
        if not ok:
            print(f"[DEBUG] execute() failed: {message}")
            return -1, "", message
        
        # 对于OTP验证后的服务器，使用shell执行命令
        if getattr(self, '_use_shell_execution', False):
            print(f"[DEBUG] execute() using shell-based execution for OTP server")
            return self._execute_via_shell(command, timeout)
        
        try:
            print(f"[DEBUG] execute() calling exec_command: {command[:50]}...")
            # 对于普通服务器，使用exec_command
            stdin, stdout, stderr = self.client.exec_command(
                command,
                timeout=timeout
            )
            
            # 关闭stdin
            if stdin:
                stdin.close()
            
            # 设置channel超时
            stdout.channel.settimeout(min(timeout, 30))
            stderr.channel.settimeout(5)
            
            # 非阻塞方式读取输出
            output_parts = []
            error_parts = []
            start_time = time.time()
            
            while True:
                # 检查超时
                if time.time() - start_time > timeout:
                    print(f"[DEBUG] execute() timeout after {timeout}s")
                    break
                
                # 检查命令是否完成
                if stdout.channel.exit_status_ready():
                    # 使用 stdout.read() 读取所有剩余输出
                    try:
                        remaining = stdout.read().decode('utf-8', errors='ignore')
                        if remaining:
                            output_parts.append(remaining)
                    except Exception as e:
                        print(f"[DEBUG] execute() read remaining error: {e}")
                    break
                
                # 读取可用数据
                try:
                    if stdout.channel.recv_ready():
                        chunk = stdout.channel.recv(65535).decode('utf-8', errors='ignore')
                        if chunk:
                            output_parts.append(chunk)
                    else:
                        time.sleep(0.1)
                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"[DEBUG] execute() recv error: {e}")
                    break
            
            # 读取错误输出
            try:
                error = stderr.read().decode('utf-8', errors='ignore')
            except:
                error = ""
            
            # 获取退出码
            exit_code = stdout.channel.recv_exit_status()
            
            output = ''.join(output_parts)
            
            print(f"[DEBUG] execute() result: code={exit_code}, output_len={len(output)}, error_len={len(error)}")
            return exit_code, output, error
        except socket.timeout:
            print(f"[DEBUG] execute() socket timeout")
            return -1, "", "命令执行超时"
        except Exception as e:
            print(f"[DEBUG] execute() error: {e}")
            return -1, "", str(e)
    
    def _execute_via_shell(self, command: str, timeout: int = 60) -> Tuple[int, str, str]:
        """通过OTP验证后的shell执行命令（复用同一个shell）"""
        import time
        import uuid
        
        print(f"[DEBUG] _execute_via_shell called: command={command[:80]}")
        print(f"[DEBUG] _use_shell_execution: {getattr(self, '_use_shell_execution', False)}")
        print(f"[DEBUG] client exists: {self.client is not None}")
        print(f"[DEBUG] is_connected: {self.is_connected}")
        
        # 清除停止请求
        self._stop_requested = False
        
        # 使用保留的OTP验证后的shell channel
        shell = getattr(self, '_otp_shell', None)
        print(f"[DEBUG] _otp_shell exists: {shell is not None}, closed: {getattr(shell, 'closed', 'N/A') if shell else 'N/A'}")
        is_new_shell = False
        
        if not shell or (hasattr(shell, 'closed') and shell.closed):
            print(f"[DEBUG] _execute_via_shell: OTP shell not available, trying OTP session reconnect")
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
            print(f"[DEBUG] Using existing OTP shell")
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
            print(f"[DEBUG] Clearing shell output buffer...")
            clear_attempts = 0
            while shell.recv_ready() and clear_attempts < 10:
                try:
                    cleared = shell.recv(4096)
                    print(f"[DEBUG] Cleared {len(cleared)} bytes from shell buffer")
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
            
            print(f"[DEBUG] Sending command to shell...")
            print(f"[DEBUG] Encoded command: {encoded_cmd[:50]}...")
            print(f"[DEBUG] Full command: {full_command[:100]}...")
            print(f"[DEBUG] Is new shell: {is_new_shell}")
            
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
                print(f"[DEBUG] Command sent successfully")
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
                        print(f"[DEBUG] Command sent successfully after shell replacement")
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
                    print(f"[DEBUG] Stop requested, breaking")
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
                            print(f"[DEBUG] Received chunk ({len(chunk)} chars): {repr(chunk[:100])}")
                        
                        # 检查是否收到结束标记
                        if end_marker in output:
                            print(f"[DEBUG] End marker found, waiting for remaining output...")
                            # 继续接收2秒的数据，确保获取到所有输出
                            remaining_start = time.time()
                            while time.time() - remaining_start < 2:
                                if shell.recv_ready():
                                    remaining_chunk = shell.recv(8192).decode('utf-8', errors='ignore')
                                    output += remaining_chunk
                                    print(f"[DEBUG] Received remaining chunk ({len(remaining_chunk)} chars)")
                                else:
                                    time.sleep(0.1)
                            print(f"[DEBUG] Finished collecting output")
                            break
                    else:
                        # 如果超过10秒没有收到数据，检查是否已经完成
                        no_data_time = time.time() - last_recv_time
                        if no_data_time > 10 and start_marker in output:
                            print(f"[DEBUG] No data for 10 seconds, assuming command completed")
                            break
                        # 如果超过30秒没有收到任何数据，认为命令卡住
                        if no_data_time > 30:
                            print(f"[DEBUG] No data for 30 seconds, assuming command hung")
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
                    print(f"[DEBUG] Recv error: {recv_error}")
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
            
            print(f"[DEBUG] Raw output length: {len(output)}")
            print(f"[DEBUG] Raw output sample: {repr(output[:500])}")
            
            # 提取开始和结束marker之间的内容
            # 使用rfind找到最后一个start_marker，避免shell回显中的marker
            last_start_idx = output.rfind(start_marker)
            if last_start_idx != -1:
                output = output[last_start_idx + len(start_marker):]
                last_end_idx = output.rfind(end_marker)
                if last_end_idx != -1:
                    output = output[:last_end_idx]
            
            print(f"[DEBUG] After marker extraction: {len(output)} chars")
            
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
            print(f"[DEBUG] Raw output length: {len(output)}")
            print(f"[DEBUG] Raw output lines: {len(lines)}")
            print(f"[DEBUG] Clean output length: {len(clean_output)}")
            print(f"[DEBUG] Clean output lines: {len(clean_lines)}")
            
            # 显示被过滤掉的行
            filtered_count = len(lines) - len(clean_lines) - 1  # 减1是因为split会产生额外空行
            if filtered_count > 0:
                print(f"[DEBUG] Filtered out {filtered_count} lines")
                # 显示被过滤的行示例
                for i, line in enumerate(lines):
                    if line.strip() and line.strip() not in clean_output:
                        print(f"[DEBUG] Filtered line {i}: {repr(line[:100])}")
                        if i > 5:  # 只显示前5个被过滤的行
                            break
            
            # 如果清理后输出为空，但原始输出不为空，使用原始输出
            if not clean_output and output.strip():
                print(f"[WARNING] Clean output is empty but raw output is not. Using raw output.")
                clean_output = output.strip()
            
            print(f"[DEBUG] _execute_via_shell result: output_len={len(clean_output)}")
            if len(clean_output) > 0:
                print(f"[DEBUG] _execute_via_shell sample: {repr(clean_output[:300])}")
            else:
                print(f"[DEBUG] _execute_via_shell: EMPTY OUTPUT")
                print(f"[DEBUG] Raw output was: {repr(output[:500])}")
            
            return 0, clean_output, ""
            
        except Exception as e:
            print(f"[DEBUG] _execute_via_shell error: {e}")
            import traceback
            traceback.print_exc()
            if not self._transport_is_active():
                self._mark_disconnected()
                return -1, "", "连接已断开，请重新连接服务器后再执行"
            return -1, "", str(e)
    
    def _execute_via_exec_command(self, command: str, timeout: int = 60) -> Tuple[int, str, str]:
        """通过exec_command执行命令（作为shell失败时的备用方案）"""
        import traceback
        
        print(f"[DEBUG] _execute_via_exec_command called: command={command[:80]}")
        
        if not self.client:
            print(f"[DEBUG] _execute_via_exec_command: client is None")
            return -1, "", "SSH客户端未初始化"
        
        try:
            stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
            if stdin:
                stdin.close()
            
            output = stdout.read().decode('utf-8', errors='ignore')
            error = stderr.read().decode('utf-8', errors='ignore')
            exit_code = stdout.channel.recv_exit_status()
            
            print(f"[DEBUG] exec_command result: code={exit_code}, output_len={len(output)}, error_len={len(error)}")
            return exit_code, output, error
        except Exception as e:
            print(f"[DEBUG] _execute_via_exec_command error: {e}")
            traceback.print_exc()
            if not self._transport_is_active():
                self._mark_disconnected()
            return -1, "", str(e)
    
    def execute_stream(
        self,
        command: str,
        timeout: int = 300
    ) -> Generator[str, None, None]:
        """
        执行命令并实时输出结果
        
        Args:
            command: 要执行的命令
            timeout: 命令执行超时时间（秒）
            
        Yields:
            str: 命令输出的每一行
        """
        print(f"[DEBUG] execute_stream called: command={command[:50]}, is_connected={self.is_connected}, client={self.client is not None}, use_shell={getattr(self, '_use_shell_execution', False)}")
        
        if not self.client:
            print(f"[DEBUG] execute_stream failed: not connected")
            yield "错误：未连接到服务器"
            return

        ok, message = self._ensure_active_connection()
        if not ok:
            print(f"[DEBUG] execute_stream failed: {message}")
            yield f"错误：{message}"
            return
        
        # 对于OTP服务器，使用shell执行命令
        if getattr(self, '_use_shell_execution', False):
            print(f"[DEBUG] execute_stream: OTP server detected, using shell-based execution")
            # 使用shell执行命令（不支持流式，返回完整结果后一次性yield）
            exit_code, output, error = self._execute_via_shell(command, timeout)
            if output:
                yield output
            if error:
                yield f"[stderr] {error}"
            return
        
        try:
            print(f"[DEBUG] execute_stream: calling exec_command...")
            # 不使用get_pty，避免卡死
            stdin, stdout, stderr = self.client.exec_command(
                command,
                timeout=timeout
            )
            
            # 关闭stdin
            if stdin:
                stdin.close()
            
            # 设置channel超时
            stdout.channel.settimeout(2.0)
            stderr.channel.settimeout(1.0)
            
            start_time = time.time()
            last_data_time = start_time
            heartbeat_interval = 5  # 每5秒发送一次心跳，让调用者可以检查 is_running
            
            # 非阻塞方式读取输出
            while True:
                # 检查总超时
                if time.time() - start_time > timeout:
                    print(f"[DEBUG] execute_stream: timeout after {timeout}s")
                    break
                
                # 检查停止请求
                if self._stop_requested:
                    print(f"[DEBUG] execute_stream: stop requested")
                    break
                
                # 检查命令是否完成
                if stdout.channel.exit_status_ready():
                    # 读取剩余输出
                    try:
                        while stdout.channel.recv_ready():
                            chunk = stdout.channel.recv(65535).decode('utf-8', errors='ignore')
                            if chunk:
                                print(f"[DEBUG] execute_stream: got {len(chunk)} chars")
                                yield chunk
                    except:
                        pass
                    break
                
                # 读取可用数据
                try:
                    if stdout.channel.recv_ready():
                        chunk = stdout.channel.recv(65535).decode('utf-8', errors='ignore')
                        if chunk:
                            print(f"[DEBUG] execute_stream: got {len(chunk)} chars")
                            last_data_time = time.time()
                            yield chunk
                    else:
                        # 如果超过 heartbeat_interval 秒没有收到数据，发送心跳
                        # 这让调用者可以检查 is_running 并决定是否继续等待
                        if time.time() - last_data_time >= heartbeat_interval:
                            print(f"[DEBUG] execute_stream: heartbeat (no data for {heartbeat_interval}s)")
                            last_data_time = time.time()
                            yield ""  # 空字符串作为心跳标记
                        time.sleep(0.1)
                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"[DEBUG] execute_stream recv error: {e}")
                    break
                
        except Exception as e:
            print(f"[DEBUG] execute_stream error: {e}")
            yield f"命令执行异常：{str(e)}"
    
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
                    print(f"[DEBUG] run_with_pty recv: {repr(chunk[:80])}")
                    
                    # 检测到密码提示符，发送密码
                    if not password_sent and password_prompt in chunk:
                        time.sleep(0.1)
                        channel.send((password + '\n').encode('utf-8'))
                        password_sent = True
                        print(f"[DEBUG] run_with_pty: password sent")
                        continue
                    
                    # 检测认证失败（再次出现密码提示）
                    if password_sent and not authenticated and password_prompt in chunk:
                        print(f"[DEBUG] run_with_pty: password prompt appeared again, auth failed")
                        break
                    
                    # 检测su成功执行完毕（出现root提示符或命令输出包含root）
                    if password_sent and not authenticated:
                        if 'root' in output.lower() or '#' in output:
                            authenticated = True
                            print(f"[DEBUG] run_with_pty: authentication appears successful")
                
                # 检查channel是否已关闭
                if channel.exit_status_ready():
                    print(f"[DEBUG] run_with_pty: channel closed")
                    break
                
                time.sleep(0.05)
            
            print(f"[DEBUG] run_with_pty: loop ended, loops={loop_count}, elapsed={(time.time() - start_time):.2f}s")
            
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
                print(f"[DEBUG] run_with_pty: error reading remaining output: {e}")
            
            # 获取退出状态
            try:
                exit_status = channel.recv_exit_status()
            except Exception as e:
                print(f"[DEBUG] run_with_pty: recv_exit_status error: {e}")
                exit_status = -1
            
            channel.close()
            
            print(f"[DEBUG] run_with_pty: final - exit_status={exit_status}, output_len={len(output)}, authenticated={authenticated}")
            return exit_status, output, ""
            
        except Exception as e:
            print(f"[DEBUG] run_with_pty error: {e}")
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
