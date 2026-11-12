# Claude 娘桌宠 · claude-desk-pet

一个给 [Claude Code](https://claude.com/claude-code) 用的小插件：Claude 干活的时候，桌面上会有一个 Q 版 Claude 娘陪着你，实时显示它在做什么。

![六种状态预览](docs/preview.png)

![新状态和互动预览](docs/preview-new.png)

## 功能

**🧡 桌宠悬浮窗**：透明背景、永远置顶，可以拖到屏幕任何位置

| Claude 在做什么 | 桌宠的样子 |
|---|---|
| 新会话开始 | 挥手打招呼，按时间说早上好 / 晚上好 |
| 思考中 | 闭眼想事情，"思考中…"；想久了会报已经想了多久 |
| 在写回复 | 拿着羽毛笔写字 |
| 跑命令、改文件 | 敲键盘，气泡里显示正在改哪个文件；跑测试、装依赖、提交、推送各有各的台词 |
| 命令跑了 25 秒还没完 | 坐下喝口茶等，气泡里显示跑了多久 |
| 读文件 | 捧着书记笔记 |
| 搜索、上网查 | 举着放大镜找 |
| 派子代理干活 | 指挥迷你小助手，显示有几个在忙 |
| **要你批准才能继续**（权限确认、向你提问、请你看计划） | 举手："可以跑这个命令吗？"，并响一声提示音；你点了批准，手就放下 |
| 压缩上下文（`/compact` 或自动触发） | 把笔记收进箱子："整理记忆中…" |
| 回复完，等你说话 | 敲门："轮到你啦！" |
| 回复完，但上下文快满了（85% 以上） | 晕乎乎地提醒："轮到你啦！上下文 87%" |
| 出错 | 慌张冒汗 |
| 被你打断 | 心虚 |
| 干了 3 分钟以上的大活 | 撒花庆祝 🎉 |
| 等你超过 10 分钟 | 开始发呆 zZ |
| 等你超过 30 分钟（深夜是 8 分钟） | 抱着枕头睡着，有动静会惊醒 |
| 会话结束 | 挥手："下次见～" |

**👋 互动**

- **摸头**：鼠标在她头上来回蹭几下，会脸红冒爱心
- **戳一下**（单击）：闲着的时候陪你聊一句；干活的时候汇报进度（"本轮 2:31 · 工具 12 次"）；连戳 5 下会晕
- **拎起来**（按住拖动）：像小猫一样被拎着晃，松手落地，位置会记住
- **喂小饼干**（右键菜单）：吃得很开心；一分钟里喂太多会吃不下
- **夸她或说她**：消息里说"谢谢""太棒了"会脸红，说"还是不行""又报错了"会心虚（只看有没有这些词，不保存也不上传内容）
- **双击**：把 Claude 窗口叫到前台
- **右键**菜单：今日小结（完成了几轮、摸头几次）和好感度、喂饼干、调大小（小/中/大）、切到 Claude 时自动隐藏、提示音开关、状态栏语言、退出

还有几个彩蛋：连续出错 3 次会……石化 🗿；跑完测试、提交好代码会得意一下；深夜干完活会劝你早点睡。

**📊 Token 状态栏**：输入框下方常驻一行用量信息

> 当前会话 输入 12.3k · 输出 4.1k · 缓存命中 87% · 上下文 31% · 5h额度 23%

英文版：

> Session Input 12.3k · Output 4.1k · Cache hit 87% · Context 31% · 5h Limit 23%

默认跟随系统语言，也可以在桌宠右键菜单的「状态栏语言 / Language」里切换

**🔔 提示音**：回复完播放钢琴"叮咚"，出错播放复古 RPG"哔哔"，要你批准时是两声往上走的铃音

**命令**

- `/token-stats`：查看本会话和本项目累计的 token 用量、缓存命中率、额度
- `/pet`：不小心关掉了桌宠？用它重新召唤

## 运行要求

> 🍎 **macOS 用户**：这个仓库目前只支持 Windows，请使用 [wangmeng003](https://github.com/wangmeng003) 维护的 Mac 移植版：[claude-desk-pet-mac](https://github.com/wangmeng003/claude-desk-pet-mac)

- **Windows**（悬浮窗和提示音用的是 Windows 的接口）
- **Linux**：需要 `python3`、PyGObject、GTK3；Wayland 下还要 `gtk-layer-shell`（合成器要支持 layer-shell，如 niri、Sway、Hyprland），X11 下会退回成普通置顶窗口。提示音用 `pw-play` / `paplay` / `aplay` 播放。「切到 Claude 时自动隐藏」和双击叫出 Claude 目前只在 niri 下可用。Arch 上安装：`sudo pacman -S python-gobject gtk3 gtk-layer-shell`（Linux 版不需要 Pillow）
- **Python 3**，并且在命令行里输入 `python` 能直接运行
- **Pillow** 图像库，用下面这条命令安装：

```bash
pip install pillow
```

## 安装

**在终端里的 Claude Code**，输入：

```
/plugin install desk-pet --marketplace BingXing-ai/claude-desk-pet
```

然后按 `y` 添加插件市场，选择 user（用户）范围，按回车确认。

**也可以在命令行里运行：**

```bash
claude plugin install desk-pet --marketplace BingXing-ai/claude-desk-pet
```

**在 Claude 桌面端**：点输入框旁边的 **+** → **Plugins** → **Add marketplace**，填入 `BingXing-ai/claude-desk-pet`，再点 **Add plugin**。

装好后，新开一个会话，桌宠就会出现在屏幕右下角。

## 自定义

- **换立绘**：把 `pet/sprites/` 里的 PNG 换成你自己的图（透明背景，文件名保持不变）。后加的这些表情（`ask`、`write`、`search`、`helpers`、`tidy`、`tea`、`sleep`、`wave`、`dizzy`、`blush`、`lifted`、`cookie`）没画的话可以直接删掉，桌宠会用最早那批立绘里相近的一张顶上
- **换提示音**：替换 `sounds/done.wav`（完成）、`sounds/error.wav`（出错）和 `sounds/ask.wav`（要你批准）
- **改台词**：Claude 干活时的台词写在 `hooks/register.ts` 里；摸头、戳、喂饼干这些互动的台词，以及等多久发呆、睡着，写在 `pet/pet_core.py` 开头

桌宠的位置、大小、开关和状态栏语言（`lang`：`zh` / `en` / `auto`）保存在 `~/.claude/desk-pet/settings.json`，今天和累计的互动次数保存在旁边的 `stats.json`。

更新插件之后，正在运行的旧桌宠不会自己换成新的：右键选「退出桌宠」，再用 `/pet` 重新召唤。

## 开发

桌宠分两半：`hooks/register.ts` 是装进 Claude Code 的 mod，把 Claude 在做什么写进状态文件；`pet/` 是读状态文件的悬浮窗程序，其中 `pet_core.py` 是和平台无关的“大脑”（显示哪张立绘、说什么、怎么动），`pet.pyw`（Windows）和 `pet_linux.py`（Linux）只管窗口、画图和鼠标。

```bash
python3 -m unittest discover -s pet   # 大脑的测试
claude plugin test .                  # mod 的测试
claude plugin validate .              # 检查 mod 能不能被 Claude Code 加载
```

## 说明

- 这是个人粉丝作品，与 Anthropic 官方无关
- 立绘由作者用 AI 绘图生成，最初是为自己的视频做的素材；0.3.0 新增的 12 张由 ChatGPT 参照原立绘绘制
- 代码使用 MIT 许可证，详见 [LICENSE](LICENSE)
- 感谢 [wangmeng003](https://github.com/wangmeng003) 移植了 [macOS 版](https://github.com/wangmeng003/claude-desk-pet-mac)
