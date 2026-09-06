# QixuAI 浏览器设备授权

使用 QixuAI 时不要要求用户复制、粘贴或发送 API Key。设备授权必须与套图方案确认、上传报价确认和付费生成确认分开。

## 两阶段授权

先解释权限用途并取得用户同意，再启动授权：

```powershell
python scripts/qixuai_auth.py login
```

该命令只申请设备码、将设备码加密保存在 `~/.qixuai/pending-device.json`、打开完整授权 URL，然后立即退出。服务端设备码最长有效 900 秒，因此不应让 Agent 在单次命令中持续等待。

用户可在网页完成登录。服务端通过 HttpOnly Cookie 保存安全的本站回跳路径，登录后会回到原 `/device?user_code=...` 授权页。用户核对验证码、权限说明和剩余时间后自行点击允许。

网页允许后运行：

```powershell
python scripts/qixuai_auth.py login --complete
python scripts/qixuai_auth.py status --check
```

`--complete` 每次最多轮询 45 秒；仍未允许时退出码为 2，待用户操作后可再次运行。可以用 `--wait-seconds 0` 只检查一次。不要因为本地等待超时而重新创建设备码。

## 存储和权限

正式凭据位于 `~/.qixuai/credentials.json`，由使用 `client_id=qixuai-skill-cli` 的 QixuAI Skills 共用：

- Windows 使用当前用户 DPAPI 加密令牌。
- macOS/Linux 使用仅当前用户可读的目录和 `0600` 文件。
- 日志、计划、清单、命令参数和聊天均不出现明文令牌。
- 环境变量优先级最高，方便显式使用测试 Key。
- 设备凭据只会发送到主机精确等于 `token.qixuai.com` 的 HTTPS URL。

本 Skill 需要 `images:write files:write billing:read`；默认授权器还会申请供其他 QixuAI Skill 共用的 `models:read chat:write`。旧令牌缺少 `billing:read` 时重新授权。

仅删除本机凭据：

```powershell
python scripts/qixuai_auth.py logout
```

远程撤销前往 [API 密钥与设备授权管理](https://token.qixuai.com/console/keys)。第三方程序只能读取环境变量时，可通过 `qixuai_auth.py run` 将令牌仅注入子进程，且不会打印令牌。
