#!/usr/bin/env python
# -*- coding: utf-8 -*-
# LLM 接口测试脚本

import sys
import os

# 修复 Windows 上的 UTF-8 编码问题
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from llm_interface import LLMFactory, MockLLM
from dotenv import load_dotenv
load_dotenv()

def test_llm_factory():
    """测试 LLM 工厂"""
    print("=" * 60)
    print("测试 1：LLM 工厂")
    print("=" * 60)

    # 列出支持的 Provider
    providers = LLMFactory.list_providers()
    print(f"支持的 Provider: {', '.join(providers)}")
    print()

    # 使用 Mock Provider
    print("创建 Mock LLM...")
    llm = LLMFactory.create(provider='mock')
    print(f"✓ 已创建 {type(llm).__name__} 实例")
    print()


def test_mock_llm():
    """测试 Mock LLM"""
    print("=" * 60)
    print("测试 2：Mock LLM")
    print("=" * 60)

    llm = MockLLM()

    # 测试 call 方法
    print("调用 llm.call()...")
    response = llm.call("你好，请分解一个任务")
    print(f"✓ 响应: {response}")
    print()

    # 测试 call_with_json 方法
    print("调用 llm.call_with_json()...")
    response_json = llm.call_with_json("生成一个调度方案")
    print(f"✓ 响应 JSON: {response_json}")
    print()


def test_mock_llm_with_retry():
    """测试 Mock LLM 的重试机制"""
    print("=" * 60)
    print("测试 3：Mock LLM 重试机制")
    print("=" * 60)

    llm = MockLLM()

    print("多次调用 call_with_json()...")
    for i in range(3):
        response_json = llm.call_with_json("测试重试")
        print(f"  调用 #{i+1}: {response_json}")

    print(f"✓ 共调用 {llm.call_count} 次")
    print()


def test_llm_factory_default():
    """测试 LLM 工厂的默认行为"""
    print("=" * 60)
    print("测试 4：LLM 工厂的默认行为")
    print("=" * 60)

    # 不设置环境变量，使用默认的 Mock
    os.environ.pop('LLM_PROVIDER', None)
    os.environ.pop('LLM_MODEL', None)
    os.environ.pop('LLM_API_KEY', None)

    print("创建 LLM（使用默认 Mock Provider）...")
    llm = LLMFactory.create()
    print(f"✓ 已创建 {type(llm).__name__} 实例")

    response = llm.call("测试")
    print(f"✓ 测试调用成功: {response}")
    print()


def test_real_llm():
    """测试真实 LLM（DeepSeek/OpenAI）连接"""
    print("=" * 60)
    print("测试 5：真实 LLM 连接")
    print("=" * 60)

    # 从环境变量读取配置
    provider = os.getenv('LLM_PROVIDER', 'openai')
    model = os.getenv('LLM_MODEL', 'deepseek-v4-pro')
    api_key = os.getenv('LLM_API_KEY')
    base_url = os.getenv('OPENAI_BASE_URL', 'https://api.deepseek.com')

    # 检查是否设置了有效的 API Key
    if not api_key or api_key == 'sk-xxx':
        print("⚠️ 警告: LLM_API_KEY 未设置或为占位值，跳过真实 LLM 测试")
        return

    print(f"Provider: {provider}")
    print(f"Model: {model}")
    print(f"Base URL: {base_url}")
    # 只显示前后部分，避免泄露完整 Key
    print(f"API Key: {api_key[:8]}...{api_key[-4:]}")

    try:
        # 通过工厂创建真实 LLM（根据当前环境变量）
        llm = LLMFactory.create(provider=provider)
        print(f"✓ 已创建 {type(llm).__name__} 实例")

        # 发送一个简单的请求
        response = llm.call("你好，请用一句中文介绍你自己。")
        print(f"✓ 响应: {response}")

        print("\n✅ 真实 LLM 连接成功！")
    except Exception as e:
        print(f"\n❌ 真实 LLM 连接失败: {e}")
        raise


def main():
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 15 + "LLM 接口测试" + " " * 30 + "║")
    print("╚" + "=" * 58 + "╝")
    print()

    try:
        # 先运行基础的 Mock 测试（不依赖外部环境）
        test_llm_factory()
        test_mock_llm()
        test_mock_llm_with_retry()
        test_llm_factory_default()

        # 检查是否需要测试真实 LLM
        # 通过环境变量 TEST_REAL_LLM 控制，设为 "true" 或 "1" 时执行
        test_real_flag = os.getenv("TEST_REAL_LLM", "false").lower() in ("true", "1")
        if test_real_flag:
            # 注意：test_llm_factory_default() 删除了环境变量，但测试已结束，不影响
            # 但为确保配置完整，我们重新加载 .env（如果使用了 python-dotenv）
            # 简单起见，我们可以直接使用当前环境变量（用户可能已经设置了）
            # 如果之前被删除，可以尝试重新读取 .env
            try:
                from dotenv import load_dotenv
                load_dotenv()  # 重新加载 .env 覆盖环境变量
            except ImportError:
                pass  # 如果没有 dotenv，则忽略

            test_real_llm()
        else:
            print("\n💡 提示: 设置环境变量 TEST_REAL_LLM=true 可测试真实 LLM 连接")
            print("   例如在 PowerShell: $env:TEST_REAL_LLM='true'; python test_llm_interface.py")

        print("=" * 60)
        print("✅ 所有 LLM 接口测试通过！")
        print("=" * 60)
        print()

    except Exception as e:
        print("=" * 60)
        print("❌ 测试失败！")
        print("=" * 60)
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()