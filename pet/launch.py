# 把桌宠作为独立进程启动，然后立刻退出（mod 那边就不会被卡住）
# Windows 用 pythonw 跑 pet.pyw；Linux 用当前的 python 跑 pet_linux.py
# 用法：python launch.py <pet 脚本路径> <运行目录>
import os
import subprocess
import sys

pet_script, run_dir = sys.argv[1], sys.argv[2]

os.makedirs(run_dir, exist_ok=True)
with open(os.path.join(run_dir, 'launch.log'), 'w', encoding='utf-8') as log:
    if os.name != 'nt':
        log.write(f'python={sys.executable}\n')
        try:
            # 新开一个会话，和 Claude 的进程组脱开，会话结束时桌宠不会被一起关掉
            proc = subprocess.Popen([sys.executable, pet_script, run_dir], start_new_session=True, close_fds=True,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log.write(f'启动成功 pid={proc.pid}\n')
        except OSError as err:
            log.write(f'启动失败：{err}\n')
        sys.exit(0)

    pythonw = os.path.join(os.path.dirname(sys.executable), 'pythonw.exe')
    if not os.path.exists(pythonw):
        pythonw = sys.executable

    DETACHED = 0x00000008
    NEW_GROUP = 0x00000200
    BREAKAWAY = 0x01000000  # 脱离 Claude 的作业对象，会话结束时桌宠不会被一起关掉

    log.write(f'python={sys.executable}\npythonw={pythonw}\n')
    for flags in (DETACHED | NEW_GROUP | BREAKAWAY, DETACHED | NEW_GROUP):
        try:
            proc = subprocess.Popen([pythonw, pet_script, run_dir], creationflags=flags, close_fds=True,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            log.write(f'启动成功 pid={proc.pid} flags={flags:#x}\n')
            break
        except OSError as err:
            log.write(f'flags={flags:#x} 失败：{err}\n')
