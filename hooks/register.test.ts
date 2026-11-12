// mod 的测试：claude plugin test <这个仓库的目录>
// 这里的每个 on(...) 都站在 mod 下面、替引擎回答；$ 是引擎，按它平时的样子发事件。
// 检查的是 mod 写给桌宠的状态文件（<运行目录>/sessions/<会话 id>.json）。
import { expect, mock, test } from 'claude-code/testing'
import type { On, SessionMessage } from 'claude-code'

type Written = {
  state: string
  text: string
  kind?: string
  sound?: string
  greet?: boolean
  turn?: { at: number; tools: number }
  flash?: { id: number; sprite?: string; text: string; ms: number }
}

// mod 下面的世界：内存里的时钟、存储和环境变量；写出去的状态文件都记下来
function world(on: On, usage: { context?: number; fiveHour?: number } = {}) {
  const clock = mock.clock(on, { now: 1_000_000 })
  mock.store(on)
  mock.env(on, { HOME: '/home/me', CLAUDE_DESK_PET_DIR: '/run/pet' })
  const files: { path: string; record: Written }[] = []
  on('fs.write', (_, e) => {
    if (e.path.includes('/sessions/')) files.push({ path: e.path, record: JSON.parse(e.text) as Written })
    return { value: undefined }
  })
  on('fs.read', () => ({ value: '{}' }))
  on('process.run', () => ({ value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }))
  on('command.register', (_, e) => ({ value: { command: e.name } }))
  const status: string[] = []
  on('ui.status', (_, e) => {
    status.push(e.text ?? '')
    return { value: undefined }
  })
  on('ui.log', () => ({ value: undefined }))
  let id = 'S1'
  on('session.id', () => ({ value: id }))
  on('session.usage', () => ({
    value: {
      startedAt: 0,
      context: { percent: usage.context, window: 200_000 },
      rateLimits: usage.fiveHour === undefined ? [] : [{ kind: 'five_hour', percentUsed: usage.fiveHour }],
    },
  }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.end', (_, e) => ({ sessionId: e.sessionId }))
  on('turn.start', (_, e) => ({ turnId: e.turnId }))
  on('turn.complete', (_, e) => ({ text: e.answer }))
  on('prompt.submit', (_, e) => ({ text: e.text }))
  on('classic.UserPromptSubmit', () => ({}))
  on('telemetry.log', () => ({ value: undefined }))
  return {
    clock,
    files,
    status,
    last: () => files[files.length - 1]!.record,
    states: () => files.map(f => `${f.record.state}:${f.record.text}`),
    switchSession: (next: string) => {
      id = next
    },
  }
}

const START = { cwd: '/work/my-project', surface: 'desktop', isInteractive: true } as const
const turn = (turnId = 'T1') => ({ text: 'hi', turnId })
const done = (extra: object = {}) => ({ answer: 'ok', durationMs: 4_000, isAborted: false, turnId: 'T1', reason: 'answer', ...extra }) as const

test('a session greets, thinks, and hands the turn back', async ($, on) => {
  const w = world(on)
  await $.session.start(START)
  expect(w.files[0]!.path).toBe('/run/pet/sessions/S1.json')
  expect(w.last()).toMatchObject({ state: 'hello', text: '我来啦～', greet: true, project: 'my-project' })

  await $.turn.start(turn())
  expect(w.last()).toMatchObject({ state: 'thinking', turn: { at: 1_000_000, tools: 0 } })
  expect(w.last().text.endsWith('…')).toBe(true)

  await $.turn.complete(done())
  expect(w.last()).toMatchObject({ state: 'waiting', text: '轮到你啦！', sound: 'done' })
  expect(w.last().turn).toBeUndefined()
})

test('each kind of tool gets its own look, then back to thinking', async ($, on) => {
  const w = world(on)
  on('tool.call', () => ({ result: {} }))
  on('tool.check', () => ({ decision: 'allow' }))
  await $.session.start(START)
  await $.turn.start(turn())
  const seen = async (input: Parameters<typeof $.tool.call>[0]) => {
    const from = w.files.length
    await $.tool.call(input)
    return w.states().slice(from)
  }
  expect(await seen({ tool: 'Edit', file_path: '/work/my-project/src/app.ts', old_string: 'a', new_string: 'b' })).toEqual([
    'tool:在改 app.ts',
    'thinking:思考中…',
  ])
  expect(await seen({ tool: 'Read', file_path: '/work/my-project/README.md' })).toEqual(['reading:在读 README.md', 'thinking:思考中…'])
  expect((await seen({ tool: 'ToolSearch', query: 'x', max_results: 5 }))[0]).toBe('searching:在找东西…')
  expect((await seen({ tool: 'WebSearch', query: 'x', mode: 'standard' }))[0]).toBe('searching:在上网查…')
  expect((await seen({ tool: 'Bash', command: 'ls -la' }))[0]).toBe('reading:在翻文件…')
  expect((await seen({ tool: 'Bash', command: 'rg TODO src' }))[0]).toBe('searching:在找东西…')
  expect((await seen({ tool: 'Bash', command: 'cd web && npm install' }))[0]).toBe('tool:在装依赖…')
  expect((await seen({ tool: 'Bash', command: 'python3 build.py' }))[0]).toBe('tool:在跑命令…')
  expect(w.last().turn).toMatchObject({ tools: 8 })
})

test('a finished test run, commit and push each get a little reaction', async ($, on) => {
  const w = world(on)
  on('tool.call', () => ({ result: {} }))
  on('tool.check', () => ({ decision: 'allow' }))
  await $.session.start(START)
  await $.turn.start(turn())
  await $.tool.call({ tool: 'Bash', command: 'npm test' })
  expect(w.last()).toMatchObject({ state: 'thinking', flash: { sprite: 'confident', text: '测试跑完啦！' } })
  await w.clock.advance(5_000)
  await $.tool.call({ tool: 'Bash', command: 'git add -A && git commit -m wip' })
  expect(w.last().flash).toMatchObject({ text: '提交好啦！' })
  await w.clock.advance(5_000)
  await $.tool.call({ tool: 'Bash', command: 'git push origin main' })
  expect(w.last().flash).toMatchObject({ text: '推上去啦！' })
  // 反应过期之后，不再跟着状态文件走
  await w.clock.advance(5_000)
  await $.tool.call({ tool: 'Read', file_path: '/a.txt' })
  expect(w.last().flash).toBeUndefined()
})

test('errors make her panic, three in a row turn her to stone, a refusal is not her error', async ($, on) => {
  const w = world(on)
  let answer: object = { isError: true, result: 'boom', text: 'Exit code 1' }
  on('tool.call', () => answer as never)
  on('tool.check', () => ({ decision: 'allow' }))
  await $.session.start(START)
  await $.turn.start(turn())
  await $.tool.call({ tool: 'Bash', command: 'make' })
  expect(w.last()).toMatchObject({ state: 'thinking', flash: { sprite: 'panic', text: '出错了…' } })
  await $.tool.call({ tool: 'Bash', command: 'pytest -q' })
  expect(w.last().flash).toMatchObject({ sprite: 'panic', text: '测试没过…' })
  await $.tool.call({ tool: 'Bash', command: 'make' })
  expect(w.last().flash).toMatchObject({ sprite: 'stone', text: '石化了…' })

  // 成功一次就不再连着算
  answer = { result: {} }
  await $.tool.call({ tool: 'Bash', command: 'make' })
  answer = { isError: true, result: 'x', text: "The user doesn't want to proceed with this tool use." }
  await w.clock.advance(10_000)
  await $.tool.call({ tool: 'Bash', command: 'rm -rf build' })
  expect(w.last().flash).toBeUndefined()
})

test('a call that needs approval raises her hand, and the decision lowers it', async ($, on) => {
  const w = world(on)
  on('tool.check', () => ({ decision: 'ask' }))
  // 弹窗问你：5 秒后你点了批准
  on('classic.PermissionRequest', async () => {
    await w.clock.sleep(5_000)
    return { decision: { behavior: 'allow' } }
  })
  on('tool.call', async () => {
    await w.clock.sleep(60_000) // 批准之后，这个命令要跑一分钟
    return { result: {} }
  })
  await $.session.start(START)
  await $.turn.start(turn())

  const call = $.tool.call({ tool: 'Bash', command: 'sleep 60' })
  await w.clock.settle()
  expect(w.last()).toMatchObject({ state: 'tool', text: '在跑命令…' }) // 刚问出去，还没举手
  await w.clock.advance(700)
  expect(w.last()).toMatchObject({ state: 'asking', text: '可以跑这个命令吗？', sound: 'ask' })

  await w.clock.advance(4_300) // 你点了批准；引擎记下这个决定
  await $.telemetry.log({ to: 'collector', event: 'tool_decision', attributes: { tool_name: 'Bash', decision: 'accept' }, loggedAt: '2026-01-01T00:00:00.000Z' })
  await w.clock.settle()
  expect(w.last()).toMatchObject({ state: 'tool', text: '在跑命令…' }) // 手放下，接着显示在跑命令
  expect(w.last().sound).toBeUndefined()

  await w.clock.advance(60_000)
  await call
  expect(w.last()).toMatchObject({ state: 'thinking' })
  expect(w.states().filter(s => s.startsWith('asking'))).toHaveLength(1)
})

test('without the decision record, her hand comes down when the call ends', async ($, on) => {
  const w = world(on)
  on('tool.check', () => ({ decision: 'ask' }))
  on('classic.PermissionRequest', async () => {
    await w.clock.sleep(5_000)
    return { decision: { behavior: 'allow' } }
  })
  on('tool.call', () => ({ result: {} }))
  await $.session.start(START)
  await $.turn.start(turn())
  const call = $.tool.call({ tool: 'Edit', file_path: '/work/my-project/src/app.ts', old_string: 'a', new_string: 'b' })
  await w.clock.advance(1_000)
  expect(w.last()).toMatchObject({ state: 'asking', text: '可以改 app.ts 吗？' })
  await w.clock.advance(4_000)
  await call
  expect(w.last()).toMatchObject({ state: 'thinking' })
})

test('a call that is waved through quickly never raises her hand', async ($, on) => {
  const w = world(on)
  on('tool.check', () => ({ decision: 'ask' }))
  on('classic.PermissionRequest', async () => {
    await w.clock.sleep(200) // 0.2 秒就放行了（比如宿主自动批准）
    return { decision: { behavior: 'allow' } }
  })
  on('tool.call', () => ({ result: {} }))
  await $.session.start(START)
  await $.turn.start(turn())
  const call = $.tool.call({ tool: 'Edit', file_path: '/a.ts', old_string: 'a', new_string: 'b' })
  await w.clock.advance(300)
  await call
  await w.clock.advance(2_000)
  expect(w.states().filter(s => s.startsWith('asking'))).toEqual([])
  expect(w.last()).toMatchObject({ state: 'thinking' })
})

test('a refusal lowers her hand without counting as her error', async ($, on) => {
  const w = world(on)
  on('tool.check', () => ({ decision: 'ask' }))
  on('classic.PermissionRequest', async () => {
    await w.clock.sleep(3_000)
    return { decision: { behavior: 'deny', message: 'The user doesn\'t want to proceed with this tool use.' } }
  })
  on('tool.call', () => ({ result: {} }))
  await $.session.start(START)
  await $.turn.start(turn())
  const call = $.tool.call({ tool: 'Bash', command: 'rm -rf build' })
  await w.clock.advance(1_000)
  expect(w.last()).toMatchObject({ state: 'asking' })
  await w.clock.advance(2_000)
  await call
  expect(w.last()).toMatchObject({ state: 'thinking' })
  expect(w.last().flash).toBeUndefined()
})

test('in auto mode only a real dialog raises her hand', async ($, on) => {
  const w = world(on)
  on('tool.check', () => ({ decision: 'ask' }))
  on('classic.PermissionRequest', async () => {
    await w.clock.sleep(5_000)
    return { decision: { behavior: 'allow' } }
  })
  on('tool.call', () => ({ result: {} }))
  await $.session.start(START)
  await $.classic.UserPromptSubmit({ prompt: 'go', permission_mode: 'auto' })
  await $.turn.start(turn())
  const call = $.tool.call({ tool: 'Bash', command: 'make' })
  await w.clock.advance(1_000)
  expect(w.last()).toMatchObject({ state: 'asking', text: '可以跑这个命令吗？' })
  await w.clock.advance(4_000)
  await call
  await w.clock.advance(2_000)
  expect(w.last()).toMatchObject({ state: 'thinking' }) // 调用结束，手放下了
  expect(w.states().filter(s => s.startsWith('asking'))).toHaveLength(1)
})

test('asking a question or presenting a plan waits for you right away', async ($, on) => {
  const w = world(on)
  on('tool.call', () => ({ result: {} }))
  on('tool.check', () => ({ decision: 'allow' }))
  await $.session.start(START)
  await $.turn.start(turn())
  const from = w.files.length
  await $.tool.call({ tool: 'AskUserQuestion', questions: [] } as never)
  expect(w.files[from]!.record).toMatchObject({ state: 'asking', text: '有问题想问你～', sound: 'ask' })
  expect(w.last()).toMatchObject({ state: 'thinking', flash: { text: '明白啦！' } })
})

test('the reply being written shows as writing', async ($, on) => {
  const w = world(on)
  on('turn.step', async function* (_, e) {
    yield { kind: 'thinking', index: 0, text: 'hmm' }
    yield { kind: 'thinking', index: 0, text: 'ok' }
    yield { kind: 'text', index: 1, text: 'Here ' }
    yield { kind: 'text', index: 1, text: 'you go' }
    return {
      turnId: e.turnId,
      index: e.index,
      answer: 'Here you go',
      toolUses: [],
      stopReason: 'end_turn',
      usage: { input_tokens: 1200, output_tokens: 3400, cache_read_input_tokens: 0, cache_creation_input_tokens: 0, model: 'm' },
    }
  })
  await $.session.start(START)
  await $.turn.start(turn())
  const from = w.files.length
  // 把一次模型请求的流读完：每一块的种类，和最后的结果
  const drain = async (input: Parameters<typeof $.turn.step>[0]) => {
    const stream = $.turn.step(input)
    const kinds: string[] = []
    let step = await stream.next()
    while (!step.done) {
      kinds.push(step.value.kind)
      step = await stream.next()
    }
    await w.clock.settle()
    return { kinds, result: step.value }
  }
  const main = await drain({ turnId: 'T1', index: 0, model: 'm', messageCount: 1 })
  expect(main.kinds).toEqual(['thinking', 'thinking', 'text', 'text']) // 模型的输出原样经过
  expect(main.result.answer).toBe('Here you go')
  expect(w.states().slice(from)).toEqual(['writing:在写回复…'])
  expect(w.status[w.status.length - 1]).toContain('3.4k') // 读完之后照常记了账

  // 子代理写字不算
  await drain({ turnId: 'T2', index: 0, model: 'm', messageCount: 1, agentId: 'A1' })
  expect(w.states().slice(from)).toEqual(['writing:在写回复…'])
})

test('helpers are counted while they work', async ($, on) => {
  const w = world(on)
  let finish: (() => void) | undefined
  on('tool.call', async () => {
    await new Promise<void>(resolve => (finish = resolve))
    return { result: {} }
  })
  on('tool.check', () => ({ decision: 'allow' }))
  await $.session.start(START)
  await $.turn.start(turn())
  const call = $.tool.call({ tool: 'Agent', description: 'look', prompt: 'look around', subagent_type: 'Explore' } as never)
  await w.clock.settle()
  expect(w.last()).toMatchObject({ state: 'delegating', text: '派小助手干活中…' })
  await $.turn.start({ text: 'look', turnId: 'A-1', agentId: 'A1' })
  await $.turn.start({ text: 'look', turnId: 'A-2', agentId: 'A2' })
  await w.clock.settle()
  expect(w.last()).toMatchObject({ state: 'delegating', text: '小助手×2 干活中…' })
  await $.turn.complete(done({ turnId: 'A-1', agentId: 'A1' }))
  await w.clock.settle()
  expect(w.last()).toMatchObject({ state: 'delegating', text: '派小助手干活中…' })
  finish!()
  await call
  expect(w.last()).toMatchObject({ state: 'thinking' })
})

test('what you say changes her mood', async ($, on) => {
  const w = world(on)
  await $.session.start(START)
  const say = async (text: string, turnId?: string) => {
    await w.clock.advance(10_000)
    await $.prompt.submit({ text, wait: false, origin: { kind: 'composer' }, turnId })
    await $.turn.start(turn())
    return w.last().flash
  }
  expect(await say('太棒了，谢谢你！')).toMatchObject({ sprite: 'blush' })
  expect(await say('还是不行，又报错了')).toMatchObject({ sprite: 'guilty' })
  expect(await say('你好')).toMatchObject({ sprite: 'wave', text: '你好呀～' })
  expect(await say('请解释一下不对称加密')).toBeUndefined()
  expect(await say('帮我把 hello world 改成中文')).toBeUndefined()

  // 一轮正在跑的时候发来的消息：应一声，而且马上送到
  await w.clock.advance(10_000)
  const from = w.files.length
  await $.prompt.submit({ text: '顺便把 README 也更新一下', wait: false, origin: { kind: 'composer' }, turnId: 'T1' })
  await w.clock.settle()
  expect(w.files.length).toBe(from + 1)
  expect(w.last()).toMatchObject({ state: 'thinking', flash: { text: '收到～等我忙完' } })
  expect(w.last().flash!.sprite).toBeUndefined()
})

test('compacting tidies her memory, then picks up where she was', async ($, on) => {
  const w = world(on)
  const messages: SessionMessage[] = [{ role: 'user', text: 'hi', toolUses: [] }]
  on('session.compact', () => ({ messages }))
  await $.session.start(START)
  const from = w.files.length
  await $.session.compact({ trigger: 'manual', messages })
  expect(w.states().slice(from)).toEqual(['compacting:整理记忆中…', 'waiting:整理好啦，轮到你～'])

  await $.turn.start(turn())
  const mid = w.files.length
  await $.session.compact({ trigger: 'auto', messages })
  expect(w.states().slice(mid)).toEqual(['compacting:整理记忆中…', 'thinking:思考中…'])

  const quiet = w.files.length
  await $.session.compact({ trigger: 'precompute', messages })
  expect(w.files.length).toBe(quiet) // 提前悄悄算的那种不打扰
})

test('how a turn ends decides the last look', async ($, on) => {
  const w = world(on, { context: 31 })
  await $.session.start(START)
  await $.turn.start(turn())
  await $.turn.complete(done({ durationMs: 200_000 }))
  expect(w.last()).toMatchObject({ state: 'celebrate', sound: 'done' })
  await $.turn.start(turn())
  await $.turn.complete(done({ reason: 'aborted', isAborted: true }))
  expect(w.last()).toMatchObject({ state: 'aborted' })
  expect(w.last().sound).toBeUndefined()
  await $.turn.start(turn())
  await $.turn.complete(done({ reason: 'error' }))
  expect(w.last()).toMatchObject({ state: 'error', sound: 'error' })
})

test('a nearly full context and a nearly spent quota are mentioned', async ($, on) => {
  const w = world(on, { context: 91, fiveHour: 95 })
  await $.session.start(START)
  await $.turn.start(turn())
  await $.turn.complete(done())
  expect(w.last()).toMatchObject({ state: 'full', text: '轮到你啦！上下文 91%', sound: 'done' })
})

test('a nearly spent quota alone is mentioned too', async ($, on) => {
  const w = world(on, { context: 40, fiveHour: 95 })
  await $.session.start(START)
  await $.turn.start(turn())
  await $.turn.complete(done())
  expect(w.last()).toMatchObject({ state: 'waiting', text: '轮到你～5h 额度 95%' })
})

test('ending says goodbye; /clear starts over in a new state file', async ($, on) => {
  const w = world(on)
  await $.session.start(START)
  await $.session.end({ reason: 'clear', sessionId: 'S1', resume: { id: 'S1' } } as never)
  expect(w.last()).toMatchObject({ state: 'hello', text: '清空啦，重新开始～' })
  expect(w.last().greet).toBeUndefined()

  w.switchSession('S2') // /clear 之后进程还在，会话 id 换了
  await $.turn.start(turn())
  expect(w.files[w.files.length - 1]!.path).toBe('/run/pet/sessions/S2.json')

  await $.session.end({ reason: 'other', sessionId: 'S2', resume: { id: 'S2' } } as never)
  expect(w.last()).toMatchObject({ state: 'bye', text: '下次见～' })
})
