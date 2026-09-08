"""工具抽象基类。

对应 Suna 的 internal/tools。这里定义"工具长什么样"：
每个工具都有名字、描述、参数 schema，以及它对应的执行函数。

为什么这么做（对应架构文档"新模型可见工具优先以 tools.Provider 接入"）：
  - 模型只能看到工具的"声明"（name/description/parameters），看不到实现。
  - 这样模型与执行彻底解耦：我们可以加/删工具、换语言实现，都不影响模型协议。
  - 参数 schema 用标准的 JSON Schema，OpenAI API 原生支持，零翻译成本。
"""
from typing import Callable, Any, Dict, Optional

# ---------- 工具输出限流（阶段 1.1）----------
# 对应 Suna 的 maxToolOutputLines=500 / maxToolOutputBytes=50KB。
# 为什么必须限流：工具（尤其 run_command）可能返回几 MB 的输出，
# 而每次调用模型都要把完整上下文重发一遍。输出太长会：
#   1) 撑爆上下文窗口，直接报错；
#   2) 白白烧 token 钱。
# 所以所有工具结果在进模型上下文之前，都要先过这道闸。
MAX_TOOL_OUTPUT_LINES = 500      # 最多保留多少行
MAX_TOOL_OUTPUT_BYTES = 50 * 1024  # 最多保留多少字节（50KB）


def truncate_output(text: str) -> str:
    """把工具输出截断到安全范围，并在末尾注明被截断了。

    两道闸：先按行数截，再按字节截。
    注意：截断后要【明确告诉模型】"这里被截断了"，
    否则模型会误以为看到的就是完整输出，从而得出错误结论。
    """
    # 闸 1：行数限制（最直观，先做）
    lines = text.splitlines()
    if len(lines) > MAX_TOOL_OUTPUT_LINES:
        lines = lines[:MAX_TOOL_OUTPUT_LINES]
        lines.append(f"...[输出已截断：超过 {MAX_TOOL_OUTPUT_LINES} 行]")
        text = "\n".join(lines)

    # 闸 2：字节限制（防止单行超长，比如一坨 base64）
    data = text.encode("utf-8", errors="ignore")
    if len(data) > MAX_TOOL_OUTPUT_BYTES:
        data = data[:MAX_TOOL_OUTPUT_BYTES]
        text = data.decode("utf-8", errors="ignore")
        text += f"\n...[输出已截断：超过 {MAX_TOOL_OUTPUT_BYTES} 字节]"
    return text


def _type_hint(name: str) -> Optional[str]:
    """示例用：根据参数名猜一个最简单的 JSON 类型。真实工程里每个工具应手写 schema。"""
    return {
        "path": "string",
        "query": "string",
    }.get(name, "string")


class Tool:
    """一个工具的声明 + 执行逻辑。"""

    def __init__(
        self,
        name: str,                    # 工具名，模型通过这个名字调用
        description: str,             # 给模型看的说明，决定它何时该用这个工具
        parameters: Dict[str, Any],   # JSON Schema 描述参数
        fn: Callable[..., str],       # 真正执行的函数，返回给模型的字符串
    ):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.fn = fn

    @property
    def schema(self) -> Dict[str, Any]:
        """返回给模型的"函数声明"。这就是 tools 参数里每个元素的样子。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def run(self, **kwargs: Any) -> str:
        """执行工具并返回结果字符串。执行失败也返回错误文本，而不是抛异常让循环崩掉。

        阶段 1.1 新增：成功的结果在返回前统一过 truncate_output 限流，
        保证任何工具（哪怕 run_command 输出 1MB）都只把安全范围给模型。
        """
        try:
            result = self.fn(**kwargs)
        except Exception as exc:  # 工具错误要转为对模型可见的信息
            return f"工具执行出错：{exc}"
        return truncate_output(result)

    # 一个便捷构造器：只用 name/desc/参数名列表 就能建工具，示例用。
    #
    # 【关于 cls 参数】
    # 因为加了 @classmethod，Python 调用时会【自动】把“类本身”塞进第一个参数 cls，
    # 所以调用方【不需要写】cls，只管写 name/description/param_names/required。
    # 例如 builtin.py 里 `Tool.make("read_file", ...)` 等价于：
    #   Tool.make(cls=Tool, name="read_file", ...)
    #
    # 为什么用 cls 而不是写死 Tool？——为了对子类友好：
    #   若写成 `return Tool(...)`，则子类 MyTool.make(...) 也会创建父类 Tool，就错了；
    #   写成 `return cls(...)`，子类调用时 cls=MyTool，会创建子类实例，自动正确。
    # 这就是“工厂 + @classmethod”的标准写法。
    @classmethod
    def make(
        cls,
        name: str,
        description: str,
        param_names: list,
        required: Optional[list] = None,
    ) -> "Tool":
        # 【关于返回标注 -> "Tool"】
        # 这只是“类型提示”，告诉读代码的人“本函数返回一个 Tool 对象”，
        # 不影响运行、运行时不检查。真正返回 Tool 是下面 `return cls(...)` 那行。
        #
        # 为什么 Tool 要加引号成 "Tool"？——因为这是 forward reference（前向引用）：
        #   Python 在“定义”make 这行时就要解析类型标注里的名字，而此时 Tool 类
        #   还在定义中、尚未建完整；不加引号 `-> Tool:` 会当场报错找不 Tool。
        #   加上引号后，Python 先存成字符串，运行到 return 时才查找 Tool，
        #   那时类已定义好，就能找到。
        params: Dict[str, Any] = {
            "type": "object",
            "properties": {
                p: {"type": _type_hint(p)} for p in param_names
            },
        }
        if required:
            params["required"] = required
        return cls(name, description, params, fn=_UNSET)

    def bind(self, fn):
        """把执行函数绑上去（make 建出来的工具先用占位函数，之后 bind）。"""
        self.fn = fn
        return self


def _UNSET(**kwargs):
    return "未绑定实现"


__all__ = ["Tool"]
