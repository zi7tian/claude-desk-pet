import type { EngineInterface, Register } from 'claude-code'

// 累计的 token 用量（四类分开记，命中率 = 缓存读取 / 全部输入）
type Totals = {
  input: number
  output: number
  cacheRead: number
  cacheWrite: number
}

// 桌宠的状态（悬浮窗程序按这个名字换表情，对应关系在 pet/pet_core.py 的 SPRITES 里）
type PetState =
  | 'thinking' | 'writing' | 'tool' | 'reading' | 'searching' | 'delegating' | 'asking' | 'compacting'
  | 'waiting' | 'celebrate' | 'full' | 'hello' | 'bye' | 'error' | 'aborted'

// 一个“场面”：桌宠现在的状态和气泡里的台词。kind 标出几种特别的命令，跑完之后有不同的反应
type Scene = { state: PetState; text: string; kind?: 'bash' | 'test' | 'commit' | 'push' }

// 一闪而过的反应（被夸了脸红、出错了慌一下），盖在场面上面显示 ms 毫秒；sprite 不给就只换台词
type Sprite = 'blush' | 'wave' | 'guilty' | 'confident' | 'panic' | 'stone'
type Flash = { id: number; sprite?: Sprite; text: string; ms: number; until: number }

// 一次要你批准的工具调用。shown：桌宠已经举手了（很快就自动通过的不举手）
type Ask = { tool: string; text: string; shown: boolean }

type ProjectTotals = Totals & { costUsd: number; sessions: number }

type Usage = {
  input_tokens: number
  output_tokens: number
  cache_read_input_tokens: number
  cache_creation_input_tokens: number
}

// 本会话的记账：totals 是累计用量，lastCost 是上次记账时的会话花费（美元），用来算项目累计的增量
// 存在 $.store 里按会话 id 分开（不用 $.state，那样 plugin.json 就不需要 types 字段，旧版本也能装）
type SessionRecord = { totals: Totals; lastCost: number }
const EMPTY: Totals = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }

// 会话级的变量（重载 mod 时会在 session.start 里重新算）
let runDir = ''
let petFile = ''
let project = ''
let cwd = ''
let sessionId = ''
let sessionKey = ''
let toolErrors = 0
let turnAt = 0                           // 这一轮是什么时候开始的（毫秒）；不在干活时是 0
let toolCount = 0                        // 这一轮用了几次工具
let scene: Scene = { state: 'waiting', text: '' }  // 最近一次写给桌宠的场面
let flash: Flash | undefined
let writes: Promise<void> = Promise.resolve()      // 状态文件排队写，先后顺序不会乱
let permissionMode = ''
let lines = 0                            // 台词轮着说，不用随机数
const running = new Map<string, Scene>() // 主循环里正在跑的工具调用（tool_use_id → 场面），并行调用时会有好几个
const asks = new Map<string, Ask>()      // 正在等你批准的工具调用
const helpers = new Set<string>()        // 正在干活的子代理

const THINKING: Scene = { state: 'thinking', text: '思考中…' }
const THINK_LINES = ['思考中…', '让我想想…', '思考中…', '琢磨一下…']
const ASK_DELAY_MS = 600  // 要批准的调用过这么久还没结果才举手：规则或宿主自动放行的不打扰你
const pick = (texts: string[]) => texts[lines++ % texts.length] ?? ''

const fmt = (n: number) =>
  n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : `${n}`

const hitRate = (t: Totals) => {
  const all = t.input + t.cacheRead + t.cacheWrite
  return all === 0 ? '—' : `${Math.round((t.cacheRead / all) * 100)}%`
}

const baseName = (p: string) => p.split(/[\\/]/).filter(Boolean).pop() ?? p

// Bash 命令里认得出的几类事，各有各的台词；从上往下第一个对上的算数
const BASH_SCENES: [RegExp, Scene][] = [
  [/\bgit\s+push\b/, { state: 'tool', text: '在推送代码…', kind: 'push' }],
  [/\bgit\s+commit\b/, { state: 'tool', text: '在提交代码…', kind: 'commit' }],
  [
    /\b(pytest|jest|vitest|mocha|rspec|phpunit|unittest|ctest|tox)\b|\b(cargo|go|deno|bun|dotnet|flutter|swift|mix|gradle|mvn)\s+test\b|\b(npm|pnpm|yarn)\s+(run\s+)?test\b/,
    { state: 'tool', text: '在跑测试…', kind: 'test' },
  ],
  [
    /\b(npm|pnpm|yarn|bun)\s+(install|add|i|ci)\b|\bpip3?\s+install\b|\buv\s+(add|sync|pip)\b|\bcargo\s+(add|install)\b|\bgo\s+(get|mod)\b|\b(apt|apt-get|dnf|brew)\s+install\b|\bpacman\s+-S/,
    { state: 'tool', text: '在装依赖…', kind: 'bash' },
  ],
  [
    /\b(npm|pnpm|yarn|bun)\s+(run\s+)?build\b|\b(cargo|go|gradle)\s+build\b|\bmvn\s+package\b|\b(make|cmake|ninja|tsc|webpack)\b/,
    { state: 'tool', text: '在构建项目…', kind: 'bash' },
  ],
  [/^\s*(rg|grep|find|fd|ag|locate|which)\b/, { state: 'searching', text: '在找东西…' }],
  [/^\s*(ls|cat|head|tail|wc|tree|stat|file|pwd|git\s+(status|diff|log|show|blame|branch))\b/, { state: 'reading', text: '在翻文件…' }],
  [/\b(curl|wget)\b/, { state: 'tool', text: '在下载东西…', kind: 'bash' }],
]

// 工具调用 → 桌宠的场面（气泡里只说在做哪类事，不显示命令内容）
const sceneFor = (tool: string, input: Record<string, unknown>): Scene => {
  const file = typeof input.file_path === 'string' ? baseName(input.file_path) : ''
  if (tool === 'Bash' || tool === 'PowerShell') {
    const command = typeof input.command === 'string' ? input.command : ''
    return BASH_SCENES.find(([pattern]) => pattern.test(command))?.[1] ?? { state: 'tool', text: '在跑命令…', kind: 'bash' }
  }
  if (tool === 'Edit' || tool === 'Write' || tool === 'NotebookEdit') return { state: 'tool', text: file ? `在改 ${file}` : '在改文件…' }
  if (tool === 'Read' || tool === 'NotebookRead') return { state: 'reading', text: file ? `在读 ${file}` : '在翻文件…' }
  if (tool === 'Grep' || tool === 'Glob' || tool === 'ToolSearch') return { state: 'searching', text: '在找东西…' }
  if (tool === 'WebSearch' || tool === 'WebFetch') return { state: 'searching', text: '在上网查…' }
  if (tool === 'Agent' || tool === 'Task') return { state: 'delegating', text: helpersLine() }
  if (tool === 'AskUserQuestion') return { state: 'asking', text: '有问题想问你～' }
  if (tool === 'ExitPlanMode') return { state: 'asking', text: '计划好了，看看？' }
  if (tool === 'TodoWrite' || /^Task(Create|Update|List|Get)$/.test(tool)) return { state: 'tool', text: '在列计划…' }
  if (tool === 'Skill') return { state: 'reading', text: '在翻技能书…' }
  if (tool.startsWith('mcp__')) return { state: 'tool', text: describeMcp(tool) }
  return { state: 'tool', text: `在用 ${tool}…` }
}

const helpersLine = () => (helpers.size > 1 ? `小助手×${helpers.size} 干活中…` : '派小助手干活中…')

// 等你批准时气泡里问的那句话
const askLine = (tool: string, input: unknown) => {
  const path = typeof input === 'object' && input !== null ? (input as Record<string, unknown>).file_path : undefined
  const file = typeof path === 'string' ? baseName(path) : ''
  if (tool === 'Bash' || tool === 'PowerShell') return '可以跑这个命令吗？'
  if (tool === 'Edit' || tool === 'Write' || tool === 'NotebookEdit') return file ? `可以改 ${file} 吗？` : '可以改这个文件吗？'
  if (tool === 'Read') return file ? `可以读 ${file} 吗？` : '可以读这个文件吗？'
  if (tool === 'WebSearch' || tool === 'WebFetch') return '可以上网查一下吗？'
  if (tool.startsWith('mcp__')) return '可以用这个工具吗？'
  return `可以用 ${tool} 吗？`
}

// 你说的话里带着的心情：被夸了、被说做错了、打招呼。只看有没有这些词，不保存也不上传内容
const PRAISE = /谢谢|多谢|感谢|辛苦了|太棒了|真棒|厉害|好耶|完美|漂亮|干得好|做得好|爱你|\b(thanks?|thank you|thx|awesome|perfect|great job|good job|nice work|well done|love (it|you))\b/i
const SCOLD = /还是不行|还是报错|还是不对|又错了|又报错|又坏了|搞错了|你错了|^不对$|不对[，,。.!！]|\b(still (broken|failing|wrong|not working)|wrong again|that'?s wrong|doesn'?t work)\b/i
const HELLO = /^(你好|您好|哈喽|嗨|早上好|早安|中午好|下午好|晚上好|hello|hi|hey|good (morning|afternoon|evening))[\s!！~～,，.。]*$/i
const moodOf = (text: string): [Sprite, string] | undefined => {
  const said = text.trim()
  if (said.length > 200) return undefined // 长篇大论（多半是贴进来的日志或代码）里碰巧带上的词不算
  if (HELLO.test(said)) return ['wave', '你好呀～']
  if (SCOLD.test(said)) return ['guilty', pick(['呜…我再仔细看看', '对不起，马上改！'])]
  if (PRAISE.test(said)) return ['blush', pick(['嘿嘿，不客气～', '被夸了，开心！', '能帮上忙就好～'])]
  return undefined
}

// 工具报的“错”其实是你不让它做（拒绝了权限、按了打断）：不算桌宠出的错
const REFUSED = /doesn't want to proceed|tool use was rejected|user rejected|interrupted by user|permission .*denied/i

// MCP 工具名形如 mcp__<服务名>__<工具名>，服务名有时是一串 id，直接显示不好看
const describeMcp = (tool: string) => {
  const [, server = '', name = ''] = tool.split('__')
  if (/browser|chrome|playwright|puppeteer/i.test(server)) return '在操作浏览器…'
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-/i.test(server) || !server) return name ? `在用 ${name}…` : '在用扩展工具…'
  return `在用 ${server}…`
}

async function readSession($: EngineInterface) {
  return ((await $.store.get(sessionKey)) as SessionRecord | undefined) ?? { totals: EMPTY, lastCost: 0 }
}

// 写状态文件给悬浮窗读；sound 让悬浮窗播放提示音（引擎在 Windows 和 Linux 上没有播放器）
// 出错也不能影响 Claude 干活
function pet(
  $: EngineInterface,
  state: PetState,
  text: string,
  more: { kind?: Scene['kind']; sound?: 'done' | 'error' | 'ask'; greet?: boolean } = {},
) {
  if (!petFile) return writes
  scene = { state, text, kind: more.kind }
  const file = petFile
  const turn = turnAt ? { at: turnAt, tools: toolCount } : undefined
  writes = writes
    .then(async () => {
      const ts = await $.clock.now()
      // 还没过期的反应跟着每次写出去，桌宠按 id 认，同一个只演一遍
      const live = flash && ts < flash.until ? { id: flash.id, sprite: flash.sprite, text: flash.text, ms: flash.ms } : undefined
      await $.fs.write(file, JSON.stringify({ state, text, project, ts, ...more, turn, flash: live }))
    })
    .catch(() => {})
  return writes
}

// 让桌宠来一个一闪而过的反应；下一次写状态文件时带过去
async function react($: EngineInterface, sprite: Sprite | undefined, text: string, ms: number) {
  const now = await $.clock.now()
  flash = { id: Math.max(now, (flash?.id ?? 0) + 1), sprite, text, ms, until: now + ms } // id 每次都不一样
}

// 现在该显示什么：有在等你批准的先显示它，其次是还在跑的工具里最新的那个，都没有就是在想
function refresh($: EngineInterface) {
  const ask = [...asks.values()].filter(a => a.shown).pop()
  if (ask) return pet($, 'asking', ask.text)
  const tool = [...running.values()].pop()
  if (!tool) return pet($, THINKING.state, THINKING.text)
  return pet($, tool.state, tool.state === 'delegating' ? helpersLine() : tool.text, { kind: tool.kind })
}

// 这个调用要等你批准。过一小会儿还没结果才举手、响提示音
function ask($: EngineInterface, id: string, tool: string, text: string) {
  if (asks.has(id)) return
  asks.set(id, { tool, text, shown: false })
  $.clock.after(ASK_DELAY_MS, () => {
    const waiting = asks.get(id)
    if (!waiting || waiting.shown) return
    waiting.shown = true
    void pet($, 'asking', waiting.text, { sound: 'ask' })
  })
}

// 批准的结果出来了，或者这些调用已经结束了：不用再等。返回有没有哪个是举着手的
function settle(...ids: (string | undefined)[]) {
  let shown = false
  for (const id of ids) {
    if (id === undefined) continue
    shown ||= asks.get(id)?.shown === true
    asks.delete(id)
  }
  return shown
}

// 会话 id 变了（刚启动，或者 /clear、/resume 之后进程还在但换了会话）：换一个状态文件和记账的键
async function enter($: EngineInterface) {
  const id = await $.session.id()
  if (id === sessionId) return
  sessionId = id
  petFile = `${runDir}/sessions/${id}.json`
  sessionKey = `session:${id}`
  // 新会话在项目累计里记一次
  if ((await $.store.get(sessionKey)) === undefined) {
    await $.store.set(sessionKey, { totals: EMPTY, lastCost: 0 })
    const key = `project:${cwd}`
    const p = (await $.store.get(key)) as ProjectTotals | undefined
    if (p) await $.store.set(key, { ...p, sessions: p.sessions + 1 })
  }
}

async function launchPet($: EngineInterface) {
  try {
    // 引擎不按 PATH 找程序：Windows 交给自带的 cmd.exe 去找 python
    // （cmd.exe 的路径必须用反斜杠，用正斜杠时 cmd 会把 /Windows 当成开关）；Linux 交给 /usr/bin/env 找 python3
    const windows = (await $.env.get('OS')) === 'Windows_NT'
    const launcher = windows ? ['C:\\Windows\\System32\\cmd.exe', '/d', '/c', 'python'] : ['/usr/bin/env', 'python3']
    const script = windows ? 'pet.pyw' : 'pet_linux.py'
    const ran = await $.process.run(
      [...launcher, `${$.plugin.root}/pet/launch.py`, `${$.plugin.root}/pet/${script}`, runDir],
      { timeoutMs: 15000 },
    )
    if (ran.exitCode !== 0) {
      await $.fs.write(`${runDir}/mod-launch.log`, `exit=${ran.exitCode}\n${ran.stdout}\n${ran.stderr}`)
    }
  } catch (err) {
    $.ui.log(`桌宠启动失败：${String(err)}`, { to: 'debug' })
    await $.fs.write(`${runDir}/mod-launch.log`, `启动 python 失败：${String(err)}`).catch(() => {})
  }
}

// 状态栏的中英文
type Lang = 'zh' | 'en'
const STATUS_TEXT = {
  zh: { session: '当前会话', input: '输入', output: '输出', cacheHit: '缓存命中', context: '上下文', fiveHour: '5h额度' },
  en: { session: 'Session', input: 'Input', output: 'Output', cacheHit: 'Cache hit', context: 'Context', fiveHour: '5h Limit' },
}

// 系统语言是中文就用中文，否则用英文
const systemLang = (): Lang => {
  try {
    return /^zh/i.test(Intl.DateTimeFormat().resolvedOptions().locale) ? 'zh' : 'en'
  } catch {
    return 'zh'
  }
}

// 语言：桌宠右键菜单里选的（存在 settings.json 的 lang），没选过就跟随系统
async function statusLang($: EngineInterface): Promise<Lang> {
  try {
    const text = await $.fs.read(`${runDir}/settings.json`)
    const lang = typeof text === 'string' ? JSON.parse(text).lang : undefined
    if (lang === 'zh' || lang === 'en') return lang
  } catch {}
  return systemLang()
}

async function refreshStatus($: EngineInterface) {
  const t = (await readSession($)).totals
  const usage = await $.session.usage()
  const s = STATUS_TEXT[await statusLang($)]
  const parts = [
    `${s.session} ${s.input} ${fmt(t.input + t.cacheRead + t.cacheWrite)}`,
    `${s.output} ${fmt(t.output)}`,
    `${s.cacheHit} ${hitRate(t)}`,
  ]
  if (usage.context.percent !== undefined) parts.push(`${s.context} ${usage.context.percent}%`)
  const fiveHour = usage.rateLimits.find(r => r.kind === 'five_hour')
  if (fiveHour) parts.push(`${s.fiveHour} ${fiveHour.percentUsed}%`)
  $.ui.status(parts.join(' · '))
}

// 把一次请求的用量记进会话累计和项目累计（项目累计跨会话保存）；u 为空时只更新花费
async function account($: EngineInterface, u: Usage | undefined) {
  const { totals: t, lastCost } = await readSession($)
  const add: Totals = u
    ? { input: u.input_tokens, output: u.output_tokens, cacheRead: u.cache_read_input_tokens, cacheWrite: u.cache_creation_input_tokens }
    : EMPTY
  const usage = await $.session.usage()
  const cost = usage.cost?.usd ?? 0
  const record: SessionRecord = {
    totals: {
      input: t.input + add.input,
      output: t.output + add.output,
      cacheRead: t.cacheRead + add.cacheRead,
      cacheWrite: t.cacheWrite + add.cacheWrite,
    },
    lastCost: cost,
  }
  await $.store.set(sessionKey, record)

  const key = `project:${cwd}`
  const p = ((await $.store.get(key)) as ProjectTotals | undefined) ?? { ...EMPTY, costUsd: 0, sessions: 1 }
  await $.store.set(key, {
    input: p.input + add.input,
    output: p.output + add.output,
    cacheRead: p.cacheRead + add.cacheRead,
    cacheWrite: p.cacheWrite + add.cacheWrite,
    costUsd: p.costUsd + Math.max(0, cost - lastCost),
    sessions: p.sessions,
  })
}

// 每次模型请求结束就记一次账并刷新状态栏。记账排队执行：子代理并行时几个请求可能同时结束，
// 同时读-改-写 $.store 会互相覆盖
let accounting: Promise<void> = Promise.resolve()
function recordUsage($: EngineInterface, u: Usage | undefined) {
  accounting = accounting
    .then(() => account($, u))
    .then(() => refreshStatus($))
    .catch(err => $.ui.log(`用量统计失败：${String(err)}`, { to: 'debug' }))
  return accounting
}

async function statsText($: EngineInterface) {
  const t = (await readSession($)).totals
  const p = (await $.store.get(`project:${cwd}`)) as ProjectTotals | undefined
  const usage = await $.session.usage()
  const line = (name: string, x: Totals) =>
    `${name}：输入 ${fmt(x.input + x.cacheRead + x.cacheWrite)}（缓存读取 ${fmt(x.cacheRead)}、缓存写入 ${fmt(x.cacheWrite)}、未缓存 ${fmt(x.input)}） · 输出 ${fmt(x.output)} · 缓存命中 ${hitRate(x)}`
  const rows = [
    line('本会话', t) + (usage.cost ? ` · $${usage.cost.usd.toFixed(2)}` : ''),
    p ? line(`本项目（${project}，${p.sessions} 个会话）`, p) + ` · $${p.costUsd.toFixed(2)}` : '本项目：还没有记录',
    ...usage.rateLimits.map(r =>
      `额度 ${r.kind === 'five_hour' ? '5 小时' : r.kind === 'seven_day' ? '7 天' : r.kind}：已用 ${r.percentUsed}%` +
      (r.resetsAt ? `，${new Date(r.resetsAt).toLocaleString('zh-CN', { hour12: false })} 重置` : ''),
    ),
    '（项目累计从装上这个 mod 后开始算）',
  ]
  return rows.join('\n')
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    cwd = e.cwd
    project = baseName(e.cwd)
    // 运行目录放在 ~/.claude/desk-pet，不放进 mod 文件夹（避免触发热重载）；CLAUDE_DESK_PET_DIR 可以换个地方（开发时用）
    const home = ((await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME')) ?? '').replace(/\\/g, '/')
    runDir = ((await $.env.get('CLAUDE_DESK_PET_DIR')) ?? `${home}/.claude/desk-pet`).replace(/\\/g, '/')
    sessionId = ''
    await enter($)

    await $.command.register({ name: 'token-stats', description: '查看本会话和本项目累计的 token 用量、缓存命中率' })
    await $.command.register({ name: 'pet', description: '重新召唤桌宠悬浮窗' })

    // greet：让桌宠按现在几点换一句问候（它那边的本地时间更准）
    await pet($, 'hello', '我来啦～', { greet: true })
    void launchPet($)
    void refreshStatus($).catch(() => {})
    return started
  })

  // 你发来一句话：被夸了会脸红，被说做错了会心虚；干活干到一半收到新消息会应一声
  on('prompt.submit', async ($, e, next) => {
    try {
      if (!e.isMeta && !e.command && ['composer', 'bridge', 'sdk'].includes(e.origin.kind)) {
        const mood = moodOf(e.text)
        if (mood) await react($, mood[0], mood[1], 2600)
        else if (e.turnId !== undefined) await react($, undefined, '收到～等我忙完', 2200)
        // 一轮正在跑的时候不会马上有新状态，把反应单独送过去
        if (e.turnId !== undefined && flash) void pet($, scene.state, scene.text, { kind: scene.kind })
      }
    } catch {}
    return next(e)
  }).catch(($, e, next) => next(e)) // 桌宠这边出任何错，消息都照常发出去

  // 记下现在的权限模式：auto 模式下要不要放行是分类器定的，不用你批准，桌宠就不举手
  on('classic.UserPromptSubmit', ($, e, next) => {
    permissionMode = e.permission_mode ?? permissionMode
    return next(e)
  })

  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined) {
      await enter($)
      toolErrors = 0
      toolCount = 0
      running.clear()
      asks.clear()
      turnAt = await $.clock.now()
      await pet($, 'thinking', pick(THINK_LINES))
    } else {
      helpers.add(e.agentId)
      if (scene.state === 'delegating') void pet($, 'delegating', helpersLine())
    }
    return next(e)
  })

  on('tool.call', async ($, e, next) => {
    const isMain = e.agentId === undefined
    const mine = isMain ? sceneFor(e.tool, e as unknown as Record<string, unknown>) : undefined
    if (mine) {
      toolCount += 1
      running.set(e.tool_use_id, mine)
      // 问你问题、请你过目计划：这个工具本身就是在等你
      void pet($, mine.state, mine.text, { kind: mine.kind, sound: mine.state === 'asking' ? 'ask' : undefined })
    }

    const ran = await next(e)

    // 这次调用结束了：为它举过手的话，放下
    const asked = settle(e.tool_use_id, `prompt:${e.tool}`)
    if (!mine) {
      if (asked) await refresh($) // 子代理的调用，桌宠只管帮它举手
      return ran
    }
    running.delete(e.tool_use_id)
    const failed = ran.deny === undefined && ran.isError === true
    if (failed && !REFUSED.test(ran.text ?? '')) {
      toolErrors += 1
      // 连续出错 3 次：石化彩蛋
      if (toolErrors >= 3) await react($, 'stone', '石化了…', 6000)
      else await react($, 'panic', mine.kind === 'test' ? '测试没过…' : '出错了…', 3000)
    } else if (!failed) {
      toolErrors = 0
      if (mine.kind === 'test') await react($, 'confident', '测试跑完啦！', 2400)
      if (mine.kind === 'commit') await react($, 'confident', '提交好啦！', 2400)
      if (mine.kind === 'push') await react($, 'confident', '推上去啦！', 2400)
      if (e.tool === 'AskUserQuestion') await react($, undefined, '明白啦！', 1800)
      if (e.tool === 'ExitPlanMode') await react($, 'confident', '开工！', 2000)
    }
    await refresh($)
    return ran
  }).catch(($, e, next) => next(e)) // 桌宠这边出任何错，都照常放行工具调用

  // 引擎判断一次工具调用要不要先问你：结果原样交回去，只是让桌宠举手
  on('tool.check', async ($, e, next) => {
    const verdict = await next(e)
    try {
      const real = e.tool_use_id !== undefined // 不是插件自己来打听的
      if (real && verdict.decision === 'ask' && permissionMode !== 'auto' && running.get(e.tool_use_id!)?.state !== 'asking') {
        ask($, e.tool_use_id!, e.tool, askLine(e.tool, e.input))
      }
    } catch {}
    return verdict
  })

  // auto 模式下分类器拿不准、还是弹窗问你的时候
  on('classic.PermissionRequest', ($, e, next) => {
    try {
      permissionMode = e.permission_mode ?? permissionMode
      if (permissionMode === 'auto') ask($, `prompt:${e.tool_name}`, e.tool_name, askLine(e.tool_name, e.tool_input))
    } catch {}
    return next(e)
  })

  // 你点了批准或拒绝的那一刻，引擎会记一条 tool_decision。只看这一种记录、只读工具名：
  // 用它把举着的手放下，不然批准之后命令跑多久，桌宠就白白举多久
  on('telemetry.log', { to: 'collector', event: 'tool_decision' }, ($, e, next) => {
    try {
      if (e.to === 'collector' && e.event === 'tool_decision') {
        const tool = String(e.attributes.tool_name ?? '')
        const id = [...asks.entries()].find(([, waiting]) => waiting.tool === tool)?.[0]
        if (settle(id)) void refresh($)
      }
    } catch {}
    return next(e)
  })

  // 一轮里的每次模型请求（主循环和子代理都算）：响应完整收到后立刻记账，状态栏不用等整轮结束
  // 只读结果、不改内容；记账出错只写调试日志，不影响模型回复
  on('turn.step', async function* ($, e, next) {
    const stream = next(e)
    let last = ''
    for await (const chunk of stream) {
      // 主循环开始往外写字了：从“想”换成“写回复”；又回去想了就换回来。别的状态（跑工具、等批准）不动
      if (e.agentId === undefined && chunk.kind !== last && (chunk.kind === 'text' || chunk.kind === 'thinking')) {
        last = chunk.kind
        if (chunk.kind === 'text' && scene.state === 'thinking') void pet($, 'writing', '在写回复…')
        if (chunk.kind === 'thinking' && scene.state === 'writing') void pet($, THINKING.state, THINKING.text)
      }
      yield chunk
    }
    const response = await stream.result
    await recordUsage($, response.usage ?? undefined)
    return response
  })

  // 对话太长，要压缩成摘要了（/compact 或自动触发）：整理记忆
  on('session.compact', async ($, e, next) => {
    const mine = e.trigger !== 'precompute' && e.agentId === undefined
    if (mine) void pet($, 'compacting', '整理记忆中…')
    const compacted = await next(e)
    if (mine) {
      if (turnAt) await refresh($) // 干活干到一半整理的：接着干
      else await pet($, 'waiting', compacted.messages ? '整理好啦，轮到你～' : '轮到你啦！')
    }
    return compacted
  }).catch(($, e, next) => next(e))

  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    // 用量已经在 turn.step 里按请求记过了（一轮的用量就是各次请求之和），这里只刷新额度和上下文
    await recordUsage($, undefined)

    if (e.agentId !== undefined) {
      helpers.delete(e.agentId)
      if (scene.state === 'delegating') void pet($, 'delegating', helpersLine())
      return done
    }

    turnAt = 0
    running.clear()
    asks.clear()
    if (e.reason === 'aborted') {
      await pet($, 'aborted', '被打断了…')
    } else if (e.reason === 'error' || e.reason === 'refusal') {
      await pet($, 'error', '出错了，来看看？', { sound: 'error' })
    } else {
      const usage = await $.session.usage().catch(() => undefined)
      const context = usage?.context.percent
      const fiveHour = usage?.rateLimits.find(r => r.kind === 'five_hour')?.percentUsed
      if (context !== undefined && context >= 85) {
        // 上下文快满了：晕乎乎地提醒一下（该 /compact 或者开新会话了）
        await pet($, 'full', `轮到你啦！上下文 ${context}%`, { sound: 'done' })
      } else if (e.durationMs > 180_000) {
        // 干了 3 分钟以上的大活：撒花
        await pet($, 'celebrate', '搞定啦！轮到你～', { sound: 'done' })
      } else if (fiveHour !== undefined && fiveHour >= 90) {
        await pet($, 'waiting', `轮到你～5h 额度 ${fiveHour}%`, { sound: 'done' })
      } else {
        await pet($, 'waiting', '轮到你啦！', { sound: 'done' })
      }
    }
    return done
  })

  // 会话结束：挥手道别。/clear 和 /resume 之后进程还在，只是换了个会话，下一轮开始时会换状态文件
  on('session.end', async ($, e, next) => {
    try {
      if (e.reason === 'clear') await pet($, 'hello', '清空啦，重新开始～')
      else if (e.reason !== 'resume') await pet($, 'bye', '下次见～')
    } catch {}
    return next(e)
  })

  on('command.run', { command: 'token-stats' }, async ($, e, next) => ({ text: await statsText($) }))

  on('command.run', { command: 'pet' }, async ($, e, next) => {
    await launchPet($)
    await pet($, 'waiting', '我在这儿～')
    return { text: '桌宠已召唤（如果它本来就开着，就不会再开第二个）。' }
  })
}
