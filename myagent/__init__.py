"""myagent 包。

这个包内部的结构刻意模仿了 Suna 的分层，目的不是为了"抄"，而是为了让你
学习到一套真实的 LLM Agent 应该怎么组织代码。每个模块对应一个清晰职责：

    config  -> 配置读取（对应 Suna internal/config）
    memory  -> 会话历史与上下文裁剪（对应 Suna internal/memory）
    tools   -> 工具目录与 schema（对应 Suna internal/tools）
    runner  -> 模型调用循环（对应 Suna internal/runner）
    agent   -> 编排：串起上面所有模块（对应 Suna internal/agent）
    cli     -> 命令行交互入口
"""
