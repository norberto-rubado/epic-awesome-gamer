# -*- coding: utf-8 -*-
import os
import sys
import asyncio
from pathlib import Path

# === 引入所需库 ===
from hcaptcha_challenger.agent import AgentConfig
from pydantic import Field, SecretStr
from pydantic_settings import SettingsConfigDict
# 引入 loguru 以便在补丁中打印清晰日志
from loguru import logger

# --- 核心路径定义 ---
PROJECT_ROOT = Path(__file__).parent
VOLUMES_DIR = PROJECT_ROOT.joinpath("volumes")
LOG_DIR = VOLUMES_DIR.joinpath("logs")
USER_DATA_DIR = VOLUMES_DIR.joinpath("user_data")
RUNTIME_DIR = VOLUMES_DIR.joinpath("runtime")
SCREENSHOTS_DIR = VOLUMES_DIR.joinpath("screenshots")
RECORD_DIR = VOLUMES_DIR.joinpath("record")
HCAPTCHA_DIR = VOLUMES_DIR.joinpath("hcaptcha")

# === 配置类定义 ===
class EpicSettings(AgentConfig):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    # 类型修复：必须是 SecretStr
    GEMINI_API_KEY: SecretStr | None = Field(
        default_factory=lambda: os.getenv("GEMINI_API_KEY"),
        description="AiHubMix 的令牌",
    )
    
    GEMINI_BASE_URL: str = Field(
        default=os.getenv("GEMINI_BASE_URL", "https://aihubmix.com"),
        description="中转地址",
    )
    
    GEMINI_MODEL: str = Field(
        default=os.getenv("GEMINI_MODEL", "gemini-2.5-pro"),
        description="模型名称",
    )

    EPIC_EMAIL: str = Field(default_factory=lambda: os.getenv("EPIC_EMAIL"))
    EPIC_PASSWORD: SecretStr = Field(default_factory=lambda: os.getenv("EPIC_PASSWORD"))
    DISABLE_BEZIER_TRAJECTORY: bool = Field(default=True)

    cache_dir: Path = HCAPTCHA_DIR.joinpath(".cache")
    challenge_dir: Path = HCAPTCHA_DIR.joinpath(".challenge")
    captcha_response_dir: Path = HCAPTCHA_DIR.joinpath(".captcha")

    ENABLE_APSCHEDULER: bool = Field(default=True)
    TASK_TIMEOUT_SECONDS: int = Field(default=900)
    REDIS_URL: str = Field(default="redis://redis:6379/0")
    CELERY_WORKER_CONCURRENCY: int = Field(default=1)
    CELERY_TASK_TIME_LIMIT: int = Field(default=1200)
    CELERY_TASK_SOFT_TIME_LIMIT: int = Field(default=900)

    @property
    def user_data_dir(self) -> Path:
        target_ = USER_DATA_DIR.joinpath(self.EPIC_EMAIL)
        target_.mkdir(parents=True, exist_ok=True)
        return target_

settings = EpicSettings()
settings.ignore_request_questions = ["Please drag the crossing to complete the lines"]

# ==========================================
# [增强版] AiHubMix 终极补丁
# ==========================================
def _apply_aihubmix_patch():
    if not settings.GEMINI_API_KEY:
        return

    try:
        # 1. 尝试导入核心库
        from google import genai
        from google.genai import types
        
        # 2. 优先劫持 Client 初始化 (这是最关键的一步，必须成功)
        orig_init = genai.Client.__init__
        def new_init(self, *args, **kwargs):
            # 解密 Key
            if hasattr(settings.GEMINI_API_KEY, 'get_secret_value'):
                api_key = settings.GEMINI_API_KEY.get_secret_value()
            else:
                api_key = str(settings.GEMINI_API_KEY)
            
            kwargs['api_key'] = api_key
            
            # 路径修正
            base_url = settings.GEMINI_BASE_URL.rstrip('/')
            if base_url.endswith('/v1'): base_url = base_url[:-3]
            if not base_url.endswith('/gemini'): base_url = f"{base_url}/gemini"
            
            kwargs['http_options'] = types.HttpOptions(base_url=base_url)
            logger.info(f"🚀 AiHubMix 补丁已应用 | 模型: {settings.GEMINI_MODEL} | 地址: {base_url}")
            orig_init(self, *args, **kwargs)
        
        genai.Client.__init__ = new_init

        # 3. 尝试劫持文件上传 (这步如果失败，不应该影响上面的 URL 劫持)
        try:
            # 这里的导入比较脆弱，可能会因为版本更新而失败
            from google.genai._common import _contents_to_list
            
            file_cache = {}

            async def patched_upload(self_files, file, **kwargs):
                if hasattr(file, 'read'): content = file.read()
                elif isinstance(file, (str, Path)):
                    with open(file, 'rb') as f: content = f.read()
                else: content = bytes(file)
                
                if asyncio.iscoroutine(content): content = await content
                
                file_id = f"bypass_{id(content)}"
                file_cache[file_id] = content
                return types.File(name=file_id, uri=file_id, mime_type="image/png")

            orig_generate = genai.models.AsyncModels.generate_content
            async def patched_generate(self_models, model, contents, **kwargs):
                normalized = _contents_to_list(contents)
                for content in normalized:
                    for i, part in enumerate(content.parts):
                        if part.file_data and part.file_data.file_uri in file_cache:
                            data = file_cache[part.file_data.file_uri]
                            content.parts[i] = types.Part.from_bytes(data=data, mime_type="image/png")
                return await orig_generate(self_models, model, normalized, **kwargs)

            genai.files.AsyncFiles.upload = patched_upload
            genai.models.AsyncModels.generate_content = patched_generate
            logger.info("🚀 Base64 文件绕过补丁加载成功")
            
        except ImportError as ie:
            # 如果仅仅是内部工具导入失败，不要崩坏，只打印警告
            logger.warning(f"⚠️ 文件绕过补丁加载失败 (可能库版本不兼容): {ie}")
            logger.warning("⚠️ 程序将尝试使用原生上传，如果遇到 400 错误请更新库版本")

    except Exception as e:
        # 这里打印出具体的错误信息，方便我们调试
        logger.error(f"❌ 严重：AiHubMix 补丁加载完全失败! 原因: {e}")

# 执行补丁
_apply_aihubmix_patch()
