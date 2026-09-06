# QixuAI 浏览器设备授权

使用 QixuAI 时不要要求用户复制、粘贴或发送 API Key。用户确认套图方案后，如本机尚未授权，完成一次设备登录即可继续直接生成，不增加报价步骤。

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

本 Skill 生成需要 `images:write`，上传本地参考图需要 `files:write`；默认授权器还会申请供其他 QixuAI Skill 共用的 `models:read chat:write billing:read`。

仅删除本机凭据：

```powershell
python scripts/qixuai_auth.py logout
```

远程撤销前往 [API 密钥与设备授权管理](https://token.qixuai.com/console/keys)。第三方程序只能读取环境变量时，可通过 `qixuai_auth.py run` 将令牌仅注入子进程，且不会打印令牌。
