# IPTV播放列表检查工具

这是一个用于检查 IPTV 播放列表可用性的工具,使用 GitHub Actions 自动化运行。

## 功能特点

- 支持检查在线和本地的 M3U/M3U8 播放列表
- 多线程并发检查,提高效率
- 自动过滤失效频道
- 支持各种 IPTV 协议(HTTP/HTTPS/RTMP/RTSP等)
- 详细的检查报告和统计
- 支持自定义用户代理和请求头
- 可通过 GitHub Actions 自动运行

## 使用方法

### 本地运行

1. 安装依赖:
```bash
pip install -r requirements.txt
```

2. 运行检查:
```bash
# 检查单个播放列表
python iptvcheck.py -p playlist.m3u -s output.m3u -t 4 -ft 25

# 检查 input 文件夹下的所有播放列表
python iptvcheck.py -file
```

参数说明:
- `-p, --playlist`: 播放列表的URL或本地文件路径
- `-s, --save`: 保存检查结果的文件路径
- `-t, --threads`: 并发检查的线程数(默认: 4)
- `-ft, --ffmpeg-timeout`: FFmpeg 超时时间(秒,默认: 25)
- `-file`: 处理 input 文件夹中的所有播放列表文件

### GitHub Actions 运行

1. Fork 本仓库
2. 进入 Actions 页面
3. 选择 "IPTV Playlist Checker" 工作流
4. 点击 "Run workflow"
5. 输入以下参数:
   - PLAYLIST_URL: 要检查的播放列表URL
   - NUM_THREADS: 线程数
   - TIMEOUT: FFmpeg 超时时间
6. 运行完成后可在 Artifacts 中下载检查结果

## 输出说明

- `playlistchecked.m3u8`: 经过检查的可用频道列表
- `other/skipped.txt`: 由于超时等原因跳过的频道列表
- `iptv_check.log`: 详细的检查日志

## 注意事项

1. 确保系统已安装 FFmpeg
2. 检查时间取决于播放列表大小和网络状况
3. 建议适当调整线程数和超时时间
4. 某些频道可能需要特定的用户代理或请求头

## 许可证

本项目基于 GNU Affero General Public License v3.0 (GNU AGPLv3) 开源。
