"""pytest 配置：把项目根目录加入模块搜索路径，测试里可直接 import myagent。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
