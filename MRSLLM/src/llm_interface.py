# LLM 接口模块
# 定义 LLM 抽象接口，并实现多个 Provider（OpenAI、Anthropic、Mock）
# 支持通过环境变量切换不同的 LLM 服务

import json
import os
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
import logging

logger = logging.getLogger(__name__)


class LLMInterface(ABC):
    """LLM 抽象接口"""

    @abstractmethod
    def call(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """
        调用 LLM 生成响应

        参数：
            prompt: 用户提示
            system_prompt: 系统提示（可选）

        返回：
            LLM 的文本响应
        """
        pass

    @abstractmethod
    def call_with_json(self, prompt: str, system_prompt: Optional[str] = None, max_retries: int = 3) -> Dict[str, Any]:
        """
        调用 LLM 生成 JSON 响应，带重试机制

        参数：
            prompt: 用户提示
            system_prompt: 系统提示（可选）
            max_retries: 最大重试次数

        返回：
            解析后的 JSON 字典

        异常：
            ValueError: 如果多次重试后仍无法获得有效的 JSON
        """
        pass


class OpenAILLM(LLMInterface):
    """OpenAI LLM Provider（可通过 base_url 调用兼容的服务，例如 DeepSeek）"""

    def __init__(self, model: str = "gpt-4o", api_key: Optional[str] = None, base_url: Optional[str] = None, timeout: int = 60):
        """
        初始化 OpenAI LLM

        参数：
            model: 模型名称（默认 gpt-4o）
            api_key: API Key（如果为 None，则从环境变量 LLM_API_KEY 读取）
            base_url: 可选的 OpenAI 兼容 API 基址（如 DeepSeek 提供的 base URL）
            timeout: HTTP 请求超时时间（秒），由底层客户端使用
        """
        self.model = model
        self.api_key = api_key or os.getenv('LLM_API_KEY')

        if not self.api_key:
            raise ValueError('缺少 LLM_API_KEY 环境变量或 api_key 参数')

        # 支持兼容性环境变量名
        self.base_url = base_url or os.getenv('OPENAI_BASE_URL') or os.getenv('OPENAI_API_BASE')
        self.timeout = timeout

        try:
            import openai
            if self.base_url:
                self.client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url)
            else:
                self.client = openai.OpenAI(api_key=self.api_key)
        except ImportError:
            raise ImportError('请安装 openai 库：pip install openai')

    def call(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """调用 OpenAI API"""
        messages = []

        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})

        messages.append({'role': 'user', 'content': prompt})

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.7
        )

        return response.choices[0].message.content

    def call_with_json(self, prompt: str, system_prompt: Optional[str] = None, max_retries: int = 3) -> Dict[str, Any]:
        """调用 OpenAI API 生成 JSON，带重试机制"""
        messages = []

        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})

        messages.append({'role': 'user', 'content': prompt})

        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.7,
                    response_format={"type": "json_object"}
                )

                response_text = response.choices[0].message.content
                return json.loads(response_text)

            except (json.JSONDecodeError, ValueError) as e:
                if attempt < max_retries - 1:
                    logger.warning(f'JSON 解析失败，重试中... ({attempt + 1}/{max_retries})')
                    continue
                else:
                    raise ValueError(f'经过 {max_retries} 次重试后仍无法获得有效的 JSON: {e}')


class AnthropicLLM(LLMInterface):
    """Anthropic Claude LLM Provider"""

    def __init__(self, model: str = "claude-opus-4-7", api_key: Optional[str] = None):
        """
        初始化 Anthropic LLM

        参数：
            model: 模型名称（默认 claude-opus-4-7）
            api_key: API Key（如果为 None，则从环境变量 LLM_API_KEY 读取）
        """
        self.model = model
        self.api_key = api_key or os.getenv('LLM_API_KEY')

        if not self.api_key:
            raise ValueError('缺少 LLM_API_KEY 环境变量或 api_key 参数')

        try:
            import anthropic
            self.client = anthropic.Anthropic(api_key=self.api_key)
        except ImportError:
            raise ImportError('请安装 anthropic 库：pip install anthropic')

    def call(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """调用 Anthropic API"""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system_prompt or "",
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        return response.content[0].text

    def call_with_json(self, prompt: str, system_prompt: Optional[str] = None, max_retries: int = 3) -> Dict[str, Any]:
        """调用 Anthropic API 生成 JSON，带重试机制"""
        for attempt in range(max_retries):
            try:
                response_text = self.call(prompt, system_prompt)
                # 尝试从响应中提取 JSON
                # Claude 可能会在文本中包含 JSON，需要提取
                json_start = response_text.find('{')
                json_end = response_text.rfind('}') + 1
                if json_start >= 0 and json_end > json_start:
                    json_str = response_text[json_start:json_end]
                    return json.loads(json_str)
                else:
                    return json.loads(response_text)

            except (json.JSONDecodeError, ValueError) as e:
                if attempt < max_retries - 1:
                    logger.warning(f'JSON 解析失败，重试中... ({attempt + 1}/{max_retries})')
                    continue
                else:
                    raise ValueError(f'经过 {max_retries} 次重试后仍无法获得有效的 JSON: {e}')


class QwenLLM(LLMInterface):
    """Qwen LLM Provider（阿里云通义千问）"""

    def __init__(self, model: str = "qwen-plus", api_key: Optional[str] = None):
        """
        初始化 Qwen LLM

        参数：
            model: 模型名称（默认 qwen-plus）
            api_key: API Key（如果为 None，则从环境变量 LLM_API_KEY 读取）
        """
        self.model = model
        self.api_key = api_key or os.getenv('LLM_API_KEY')

        if not self.api_key:
            raise ValueError('缺少 LLM_API_KEY 环境变量或 api_key 参数')

        try:
            from openai import OpenAI
            # Qwen 提供 OpenAI 兼容的 API
            self.client = OpenAI(
                api_key=self.api_key,
                base_url='https://dashscope.aliyuncs.com/compatible-mode/v1'
            )
        except ImportError:
            raise ImportError('请安装 openai 库：pip install openai')

    def call(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """调用 Qwen API"""
        messages = []

        if system_prompt:
            messages.append({'role': 'system', 'content': system_prompt})

        messages.append({'role': 'user', 'content': prompt})

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.7
        )

        return response.choices[0].message.content

    def call_with_json(self, prompt: str, system_prompt: Optional[str] = None, max_retries: int = 3) -> Dict[str, Any]:
        """调用 Qwen API 生成 JSON，带重试机制"""
        for attempt in range(max_retries):
            try:
                response_text = self.call(prompt, system_prompt)
                # 尝试从响应中提取 JSON
                json_start = response_text.find('{')
                json_end = response_text.rfind('}') + 1
                if json_start >= 0 and json_end > json_start:
                    json_str = response_text[json_start:json_end]
                    return json.loads(json_str)
                else:
                    return json.loads(response_text)

            except (json.JSONDecodeError, ValueError) as e:
                if attempt < max_retries - 1:
                    logger.warning(f'JSON 解析失败，重试中... ({attempt + 1}/{max_retries})')
                    continue
                else:
                    raise ValueError(f'经过 {max_retries} 次重试后仍无法获得有效的 JSON: {e}')


class MockLLM(LLMInterface):
    """Mock LLM - 不调用真实的 API，用于测试"""

    def __init__(self):
        """初始化 Mock LLM"""
        self.call_count = 0

    def call(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """返回预设的响应"""
        self.call_count += 1
        return f"Mock response #{self.call_count}"

    def call_with_json(self, prompt: str, system_prompt: Optional[str] = None, max_retries: int = 3) -> Dict[str, Any]:
        """返回预设的 JSON 响应，根据 prompt 内容智能匹配"""
        self.call_count += 1

        # 区分调用场景：任务规划 / 代码生成 / 失败分析
        if 'operations' in prompt and 'robot_assignments' in prompt:
            # 任务规划 → 返回与场景匹配的操作列表
            return self._mock_task_plan(prompt)
        elif 'def robot_behavior' in prompt or '原子技能原语' in prompt:
            # 代码生成 → 返回模拟的行为代码
            return self._mock_code_generation(prompt)
        elif '失败' in prompt or '故障' in prompt or 'failure' in prompt.lower():
            # 失败分析
            return {
                "root_cause": "Mock 模式下未检测到真实失败",
                "adjustments": ["无需调整"]
            }
        else:
            return {
                "status": "success",
                "message": f"Mock JSON response #{self.call_count}",
                "data": {}
            }

    def _mock_task_plan(self, prompt: str) -> Dict[str, Any]:
        """生成模拟的任务计划"""
        # 从 prompt 中提取操作 ID
        import re
        op_ids = re.findall(r'op_\d+', prompt)
        if not op_ids:
            op_ids = [f'op_{i}' for i in range(1, 9)]

        # 从 prompt 中提取机械臂 ID
        arm_ids = re.findall(r'arm_\d+', prompt)
        if not arm_ids:
            arm_ids = ['arm_0', 'arm_1', 'arm_2']

        # 分配：每个操作分配其第一个允许的机械臂
        assignments = {}
        for i, op_id in enumerate(op_ids):
            assignments[op_id] = [arm_ids[i % len(arm_ids)]]

        return {
            "operations": op_ids,
            "robot_assignments": assignments,
            "reasoning": "Mock LLM 预设任务分解：按操作 ID 顺序分配机械臂"
        }

    def _mock_code_generation(self, prompt: str) -> Dict[str, Any]:
        """生成模拟的机械臂行为代码"""
        import re
        robot_match = re.search(r'arm_\d+', prompt)
        robot_id = robot_match.group(0) if robot_match else 'arm_0'

        # 从 prompt 提取操作信息
        op_matches = re.findall(r'(op_\d+).*?\[([\d.]+),\s*([\d.]+)\]', prompt)
        if not op_matches:
            op_matches = [('op_1', '0.0', '93.0')]

        lines = []
        lines.append('def robot_behavior(robot_state, world_state):')
        lines.append(f'    """{robot_id} 的模拟行为代码"""')
        for op_id, start, end in op_matches:
            dur = float(end) - float(start)
            lines.append(f'    operate("task_execution", "{op_id}", "loc_1")')
        lines.append('    return robot_state')

        return {
            "code": '\n'.join(lines),
            "explanation": f"Mock LLM 生成的 {robot_id} 行为代码"
        }





class LLMFactory:
    """LLM 工厂类 - 根据配置创建合适的 LLM 实例"""

    _PROVIDERS = {
        'openai': OpenAILLM,
        'anthropic': AnthropicLLM,
        'qwen': QwenLLM,
        'mock': MockLLM
    }

    @classmethod
    def create(cls, provider: Optional[str] = None, model: Optional[str] = None, api_key: Optional[str] = None) -> LLMInterface:
        """
        创建 LLM 实例

        参数：
            provider: LLM Provider（从环境变量 LLM_PROVIDER 读取，默认 mock）
            model: 模型名称（从环境变量 LLM_MODEL 读取，如果为 None 则使用 provider 的默认值）
            api_key: API Key（从环境变量 LLM_API_KEY 读取）

        返回：
            LLMInterface 的实例
        """
        provider = provider or os.getenv('LLM_PROVIDER', 'mock').lower()

        if provider not in cls._PROVIDERS:
            raise ValueError(f'未知的 LLM Provider: {provider}。支持的 Provider: {list(cls._PROVIDERS.keys())}')

        provider_class = cls._PROVIDERS[provider]

        # Mock 不需要 model 和 api_key
        if provider == 'mock':
            return provider_class()

        # 其他 Provider 需要 model 和 api_key
        model = model or os.getenv('LLM_MODEL')
        api_key = api_key or os.getenv('LLM_API_KEY')

        # 读取可选配置：base_url（OpenAI 兼容 API 地址）和 timeout（HTTP 超时）
        base_url = os.getenv('OPENAI_BASE_URL') or os.getenv('OPENAI_API_BASE')
        try:
            timeout = int(os.getenv('LLM_TIMEOUT') or os.getenv('OPENAI_TIMEOUT') or 60)
        except Exception:
            timeout = 60

        # 支持使用 OpenAI 客户端调用兼容服务（例如 DeepSeek）
        # 当 provider 是 openai，或模型名中包含 deepseek 时，使用 OpenAILLM
        if provider == 'openai' or (model and 'deepseek' in model.lower()):
            return OpenAILLM(model=model or 'gpt-4o', api_key=api_key, base_url=base_url, timeout=timeout)
        elif provider == 'anthropic':
            return AnthropicLLM(model=model or 'claude-opus-4-7', api_key=api_key)
        elif provider == 'qwen':
            return QwenLLM(model=model or 'qwen-plus', api_key=api_key)

    @classmethod
    def list_providers(cls) -> List[str]:
        """列出所有支持的 Provider"""
        return list(cls._PROVIDERS.keys())
