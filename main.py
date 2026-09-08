#!/usr/bin/env python3
"""程序入口。

对应 Suna 的 main.go：只负责"启动"，不承载业务逻辑。
业务逻辑（模型调用、工具执行、记忆、配置）都被拆分到 myagent 包里的各个模块。
这样做的目的和你后面会读到的架构文档一致：入口越薄，越容易测试和理解。
"""
import sys

# 把本文件所在目录加入模块搜索路径，保证从任何目录运行时都能 import myagent
sys.path.insert(0, __file__ + "/../")

from myagent.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
